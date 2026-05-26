from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import pandas as pd
from google.cloud import bigquery

from modeling.batch_reporting import (
    compute_metrics,
    generate_same_structure_report,
)
from modeling.writers import append_run_eval_metrics_to_bq
from common.config_loader import (
    get_model_identity,
    get_model_type_params,
    load_unified_config,
    normalize_model_key,
    resolve_active_model,
)
from common.bq_table_manager import TableSpec, build_bq_schema, resolve_bq_table
from common.gcs_artifact_manager import (
    to_gcs_uri_for_local_file,
    upload_dir_to_gcs,
    upload_file_to_gcs_by_model,
)
from common.data_source import (
    discover_entity_filters_from_bq,
    discover_entity_filters_from_dataframe,
    fetch_data_from_bq,
    fetch_data_from_dataframe,
    get_available_features,
    get_available_features_from_dataframe,
    load_source_csv_dataframe,
    validate_bq_source_non_empty,
)
from common.output_schema_defs import (
    FEAT_IMP_SCHEMA_DEFS,
    METRICS_BY_SPLIT_SCHEMA_DEFS,
    MODEL_META_SCHEMA_DEFS,
    PRED_SCHEMA_DEFS,
    RUN_EVAL_METRICS_SCHEMA_DEFS,
)
from modeling.pipeline import PipelineRuntimeContext, train_and_save_predictions
from modeling.tuning import TuningConfig, TuningObjective


# ============================== 环境变量解析 ==============================
# 通用规则：环境变量优先；缺失时从 runtime 配置读取；仍缺失则快速失败。
# 目的：同一份代码可在本地 / Cloud Run 通过不同 ENV 覆盖行为。

# GCP 项目 ID：用于初始化 BigQuery Client 和 GCS 上传上下文。
PROJECT_ID = os.getenv("PROJECT_ID", "ingka-cn-cop-stage")

# 源数据默认配置（仅作为兜底；实际读取由 scenario_profiles 决定）。
SOURCE_TABLE = os.getenv(
    "SOURCE_TABLE",
    "ingka-cn-cop-stage.ikea_da_temp.temp_store_ma582_sampled_items_modeling_202605",
)

# GCS 产物根路径：优先环境变量；否则从 runtime 配置读取。
ENV_GCS_OUTPUT_URI = os.getenv("GCS_OUTPUT_URI", "").strip()

# 本地输出根路径：优先环境变量；否则从 runtime 配置读取。
ENV_LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "").strip()
# ======================================================================


# 模型算法别名映射：用户输入的模型名 → 简化别名（用于输出前缀）
# 例："decision_tree" → "dt", "lightgbm" → "lgbm"
MODEL_ALGORITHM_ALIASES = {
    "decision_tree": "dt",
    "lightgbm": "lgbm",
    "xgboost": "xgbt",
    "random_forest": "rf",
    "gradient_boosting": "gb",
    "catboost": "cb",
}


def get_output_prefix_for_model(model_key: str) -> str:
    """根据模型类型生成简化的评估输出前缀。
    
    将完整的模型名称映射为简化的别名，然后生成形如 '<alias>_eval' 的前缀。
    例：
      model_key='decision_tree' → output_prefix='dt_eval'
      model_key='lightgbm' → output_prefix='lgbm_eval'
    
    Args:
        model_key: 模型类型标识（如 'decision_tree', 'lightgbm'）
        
    Returns:
        str: 形如 '<alias>_eval' 的输出前缀
    """
    alias = MODEL_ALGORITHM_ALIASES.get(model_key.lower(), model_key.lower())
    return f"{alias}_eval"


# Use millisecond precision to reduce collision risk for near-simultaneous runs.
RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
OUT_DIR = Path(".")
# 模型参数在 main() 中从配置文件加载
MODEL_PARAMS = None
# SOURCE_REF 是运行时数据源标识，会写入产物与 BQ 元数据。
# 根据 source_mode 不同，它可能是 BigQuery 表名或 CSV 的 URI/本地路径。
SOURCE_REF = SOURCE_TABLE


# BigQuery 输出表注册表。
# 新增输出表时只需在这里补充 TableSpec，
# 其余建表/兼容检查/解析逻辑会自动复用。
TABLE_SPECS: Dict[str, TableSpec] = {
    "pred": TableSpec(
        key="pred",
        label="prediction table",
        env_name="BQ_PRED_TABLE",
        schema=build_bq_schema(PRED_SCHEMA_DEFS),
        partition_expr="TIMESTAMP_TRUNC(run_ts, DAY)",
        partition_field="run_ts",
        cluster_fields=["model_name", "data_split", "run_id"],
    ),
    "model_meta": TableSpec(
        key="model_meta",
        label="model metadata table",
        env_name="BQ_MODEL_META_TABLE",
        schema=build_bq_schema(MODEL_META_SCHEMA_DEFS),
        partition_expr="TIMESTAMP_TRUNC(run_ts, DAY)",
        partition_field="run_ts",
        cluster_fields=["model_name", "model_type", "run_id"],
    ),
    "feat_imp": TableSpec(
        key="feat_imp",
        label="feature importance table",
        env_name="BQ_FEAT_IMP_TABLE",
        schema=build_bq_schema(FEAT_IMP_SCHEMA_DEFS),
        partition_expr="TIMESTAMP_TRUNC(run_ts, DAY)",
        partition_field="run_ts",
        cluster_fields=["model_name", "feature", "run_id"],
    ),
    "metrics_by_split": TableSpec(
        key="metrics_by_split",
        label="entity metrics by split table",
        env_name="BQ_METRICS_BY_SPLIT_TABLE",
        schema=build_bq_schema(METRICS_BY_SPLIT_SCHEMA_DEFS),
        partition_expr="TIMESTAMP_TRUNC(run_ts, DAY)",
        partition_field="run_ts",
        cluster_fields=["model_name", "data_split", "run_id"],
    ),
    "run_eval_metrics": TableSpec(
        key="run_eval_metrics",
        label="run eval metrics table",
        env_name="BQ_RUN_EVAL_METRICS_TABLE",
        schema=build_bq_schema(RUN_EVAL_METRICS_SCHEMA_DEFS),
        partition_expr="TIMESTAMP_TRUNC(run_ts, DAY)",
        partition_field="run_ts",
        cluster_fields=["model_type", "level", "run_id"],
    ),
}


def run_dry_run(*, gcs_output_uri: str) -> None:
    marker = OUT_DIR / f"dry_run_{RUN_TS}.txt"
    marker.write_text("hello from gcp_python_modeling demo\n", encoding="utf-8")
    uploaded = upload_dir_to_gcs(
        OUT_DIR,
        gcs_output_uri,
        run_ts=RUN_TS,
        project_id=PROJECT_ID,
    )
    print("[DRY_RUN] uploaded files:", flush=True)
    for path in uploaded:
        print(f"  - {path}", flush=True)


def _resolve_config_input_path(path_arg: Optional[str], default_path: str) -> Path:
    raw = str(path_arg).strip() if path_arg else default_path
    candidate = Path(raw)
    if candidate.exists():
        return candidate.resolve()
    # Try relative to main.py directory (gcp_python_modeling/)
    alt = Path(__file__).resolve().parent / raw
    if alt.exists():
        return alt.resolve()
    # Try relative to gcp_python_modeling/config/ (mirrors config_loader._resolve_config_path)
    from common.config_loader import get_config_dir
    alt2 = get_config_dir() / raw
    if alt2.exists():
        return alt2.resolve()
    return candidate.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_config_snapshot(
    *,
    out_dir: Path,
    run_ts: str,
    config_path: Path,
    system_defaults_path: Path,
    versioning_meta: Dict[str, Any],
) -> Dict[str, str]:
    snapshot_dir = out_dir / "config_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    config_snapshot = snapshot_dir / f"config_{run_ts}.yaml"
    sys_snapshot = snapshot_dir / f"system_defaults_{run_ts}.yaml"
    shutil.copy2(config_path, config_snapshot)
    shutil.copy2(system_defaults_path, sys_snapshot)

    config_hash = _sha256_file(config_path)
    sys_hash = _sha256_file(system_defaults_path)

    snapshot_meta = {
        "run_id": run_ts,
        "config_source": str(config_path),
        "system_defaults_source": str(system_defaults_path),
        "config_snapshot": str(config_snapshot),
        "system_defaults_snapshot": str(sys_snapshot),
        "config_sha256": config_hash,
        "system_defaults_sha256": sys_hash,
        "versioning": versioning_meta,
    }
    snapshot_meta_path = snapshot_dir / f"config_snapshot_meta_{run_ts}.json"
    snapshot_meta_path.write_text(json.dumps(snapshot_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "snapshot_dir": str(snapshot_dir),
        "config_source": str(config_path),
        "system_defaults_source": str(system_defaults_path),
        "config_snapshot": str(config_snapshot),
        "system_defaults_snapshot": str(sys_snapshot),
        "config_sha256": config_hash,
        "system_defaults_sha256": sys_hash,
        "snapshot_meta_json": str(snapshot_meta_path),
    }


def _append_run_registry_csv(*, output_root: Path, row: Dict[str, Any]) -> Path:
    registry_path = output_root / "run_registry.csv"
    row_df = pd.DataFrame([row])
    row_df.to_csv(
        registry_path,
        mode="a",
        header=not registry_path.exists(),
        index=False,
        encoding="utf-8",
    )
    return registry_path


def _require_runtime_field(config: Dict, key: str):
    if key not in config:
        raise ValueError(f"runtime config missing required field: '{key}'")
    return config[key]


def _require_bool(config: Dict, key: str) -> bool:
    value = _require_runtime_field(config, key)
    if not isinstance(value, bool):
        raise ValueError(f"runtime config field '{key}' must be bool")
    return value


def _load_scenario_profile(scenario_profiles: Dict, scenario: str) -> Dict:
    if not isinstance(scenario_profiles, dict) or not scenario_profiles:
        raise ValueError("scenario_profiles must be a non-empty mapping")
    if scenario not in scenario_profiles:
        raise ValueError(
            "unknown scenario: "
            + scenario
            + "; available="
            + ", ".join(sorted(str(x) for x in scenario_profiles.keys()))
        )
    profile = scenario_profiles[scenario]
    if not isinstance(profile, dict):
        raise ValueError(f"scenario '{scenario}' must be a mapping")
    return profile


def _load_required_columns(runtime_cfg: Dict, key: str) -> List[str]:
    value = _require_runtime_field(runtime_cfg, key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"runtime config '{key}' must be a non-empty list")
    return [str(v).strip() for v in value if str(v).strip()]


def _load_validation_config(runtime_cfg: Dict) -> Dict[str, object]:
    cfg = runtime_cfg.get("in_sample_validation", {})
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError("runtime config 'in_sample_validation' must be a mapping")

    enabled = bool(cfg.get("enabled", False))
    ratio = float(cfg.get("validation_ratio", 0.2))
    mode = str(cfg.get("split_mode", "random")).strip().lower() or "random"
    seed = int(cfg.get("random_seed", 42))
    if mode not in {"random", "time_tail"}:
        raise ValueError("in_sample_validation.split_mode must be one of: random, time_tail")
    if not 0 < ratio < 1:
        raise ValueError("in_sample_validation.validation_ratio must be between 0 and 1")

    return {
        "enabled": enabled,
        "validation_ratio": ratio,
        "split_mode": mode,
        "random_seed": seed,
    }


def _load_evaluation_config(runtime_cfg: Dict) -> Dict[str, object]:
    evaluation_cfg = runtime_cfg.get("evaluation", {})
    if evaluation_cfg is None:
        evaluation_cfg = {}
    if not isinstance(evaluation_cfg, dict):
        raise ValueError("runtime config 'evaluation' must be a mapping")

    raw = evaluation_cfg.get("split_name_map", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("runtime config 'evaluation.split_name_map' must be a mapping")

    labels = {
        "train": str(raw.get("train", "train")).strip() or "train",
        "validation": str(raw.get("validation", "validation")).strip() or "validation",
        "test": str(raw.get("test", "test")).strip() or "test",
    }

    requested_dims = evaluation_cfg.get("requested_dimensions", [])
    if requested_dims is None:
        requested_dims = []
    if not isinstance(requested_dims, list):
        raise ValueError("runtime config 'evaluation.requested_dimensions' must be a list")

    include_total = evaluation_cfg.get("include_total", True)
    include_entity = evaluation_cfg.get("include_entity", True)
    if not isinstance(include_total, bool):
        raise ValueError("runtime config 'evaluation.include_total' must be bool")
    if not isinstance(include_entity, bool):
        raise ValueError("runtime config 'evaluation.include_entity' must be bool")

    # output_prefix 的决策逻辑：
    # 1. 如果配置中有显式的 output_prefix，使用它（向后兼容）
    # 2. 否则，从模型类型自动生成（推荐）
    explicit_prefix = str(evaluation_cfg.get("output_prefix", "")).strip()
    if explicit_prefix:
        output_prefix = explicit_prefix
    else:
        # 需要在 main() 中传入当前的 model_key 才能自动生成
        # 这里先保留为 "evaluation" 作为兜底
        output_prefix = "evaluation"

    return {
        "split_label_map": labels,
        "requested_dimensions": [str(x).strip() for x in requested_dims if str(x).strip()],
        "include_total": include_total,
        "include_entity": include_entity,
        "output_prefix": output_prefix,
    }


def _load_tuning_config(runtime_cfg: Dict) -> TuningConfig:
    tuning_cfg = runtime_cfg.get("tuning", {})
    if tuning_cfg is None:
        tuning_cfg = {}
    if not isinstance(tuning_cfg, dict):
        raise ValueError("runtime config 'tuning' must be a mapping")

    objective_cfg = tuning_cfg.get("objective", {})
    if objective_cfg is None:
        objective_cfg = {}
    if not isinstance(objective_cfg, dict):
        raise ValueError("runtime config 'tuning.objective' must be a mapping")

    objective = TuningObjective(
        primary=str(objective_cfg.get("primary", "mae_min")).strip() or "mae_min",
        secondary=str(objective_cfg.get("secondary", "accuracy_strict_nonzero_max")).strip()
        or "accuracy_strict_nonzero_max",
        mae_tie_tolerance=float(objective_cfg.get("mae_tie_tolerance", 1e-9)),
    )

    search_space_raw = tuning_cfg.get("search_space", {})
    if search_space_raw is None:
        search_space_raw = {}
    if not isinstance(search_space_raw, dict):
        raise ValueError("runtime config 'tuning.search_space' must be a mapping")

    return TuningConfig(
        enabled=bool(tuning_cfg.get("enabled", False)),
        method=str(tuning_cfg.get("method", "random")).strip().lower() or "random",
        n_iter=int(tuning_cfg.get("n_iter", 20)),
        random_seed=int(tuning_cfg.get("random_seed", 42)),
        internal_validation_ratio=float(tuning_cfg.get("internal_validation_ratio", 0.2)),
        internal_split_mode=str(tuning_cfg.get("internal_split_mode", "random")).strip().lower() or "random",
        objective=objective,
        search_space={str(k): list(v) for k, v in search_space_raw.items()} if search_space_raw else None,
    )


def _resolve_output_dir(scenario_profile: Dict) -> Path:
    if ENV_LOCAL_OUTPUT_DIR:
        return Path(ENV_LOCAL_OUTPUT_DIR)

    configured = ""
    storage_cfg = scenario_profile.get("storage", {})
    if isinstance(storage_cfg, dict):
        configured = str(storage_cfg.get("local_output_dir", "")).strip()

    if not configured:
        raise ValueError(
            "Missing local output root. Configure one of: "
            "env LOCAL_OUTPUT_DIR or output.local_output_dir in profile config."
        )

    return Path(configured)


def _resolve_gcs_output_uri(scenario_profile: Dict) -> str:
    if ENV_GCS_OUTPUT_URI:
        return ENV_GCS_OUTPUT_URI

    storage_cfg = scenario_profile.get("storage", {})
    if isinstance(storage_cfg, dict):
        configured = str(storage_cfg.get("gcs_output_uri", "")).strip()
        if configured:
            return configured

    return ""  # GCS not required for this scenario (e.g. bq_local_local)


def _resolve_gcs_sync_local_outputs(runtime_cfg: Dict, scenario_profile: Dict) -> bool:
    storage_cfg = scenario_profile.get("storage", {})
    if isinstance(storage_cfg, dict) and "gcs_sync_local_outputs" in storage_cfg:
        value = storage_cfg.get("gcs_sync_local_outputs")
        if not isinstance(value, bool):
            raise ValueError("scenario storage 'gcs_sync_local_outputs' must be bool")
        return value

    defaults_cfg = runtime_cfg.get("output_defaults", {})
    if isinstance(defaults_cfg, dict) and "gcs_sync_local_outputs" in defaults_cfg:
        value = defaults_cfg.get("gcs_sync_local_outputs")
        if not isinstance(value, bool):
            raise ValueError("output_defaults 'gcs_sync_local_outputs' must be bool")
        return value

    return False


def _build_run_tag(run_id: str, model_line: str, model_key: str) -> str:
    line = str(model_line).strip() or "unknown_model_line"
    algo = str(model_key).strip() or "unknown_model"
    return f"{run_id}__{line}__{algo}"


def _write_artifact_manifest(
    *,
    out_dir: Path,
    run_id: str,
    run_tag: str,
    algorithm_name: str,
    scenario: str,
    output_root: Path,
    gcs_output_uri: str,
    gcs_sync_local_outputs: bool,
    summary_path: Path,
    report_info: Dict[str, Any],
    registry_path: Path,
    summary: Dict[str, Any],
    config_snapshot_info: Dict[str, Any],
) -> Path:
    """生成 artifact_manifest_<run_id>.json，完整记录本次运行产生的所有文件及其存储位置。

    修复的三个问题：
    1. GCS URI 使用 out_dir 作为 local_dir（而非 output_root），避免 runs/<tag> 重复出现。
    2. selective_gcs_upload 同时记录 local_path 和 gcs_uri。
    3. 补全 config_snapshot、effective_params、model 文件、model metrics 等缺失条目。
    """
    def _local_to_gcs(local_path: Path) -> str:
        """将 out_dir 下的本地路径映射为 GCS URI（无路径重复）。"""
        return to_gcs_uri_for_local_file(
            out_dir, local_path, gcs_output_uri, run_ts=run_id, run_tag=run_tag
        )

    def _mirror_policy() -> str:
        return "local+gcs_mirror" if gcs_sync_local_outputs else "local_only"

    artifacts: List[Dict[str, str]] = []

    # ── 1. 运行汇总 ───────────────────────────────────────────────────────────
    artifacts.append(
        {
            "artifact_type": "summary",
            "local_path": str(summary_path),
            "gcs_uri": _local_to_gcs(summary_path) if gcs_sync_local_outputs else "",
            "storage_policy": _mirror_policy(),
            "description": "本次运行汇总 JSON，包含所有实体的训练结果",
        }
    )

    # ── 2. 全局运行注册表（output_root 级别，不在 out_dir 下）─────────────────
    artifacts.append(
        {
            "artifact_type": "run_registry",
            "local_path": str(registry_path),
            "gcs_uri": "",
            "storage_policy": "local_only",
            "description": "全局运行注册表 CSV，追踪所有历史运行记录",
        }
    )

    # ── 3. 配置快照（config_snapshot/ 子目录）────────────────────────────────
    for snap_key, snap_desc in [
        ("config_snapshot", "统一配置快照（YAML）"),
        ("system_defaults_snapshot", "系统场景默认配置快照（YAML）"),
        ("snapshot_meta_json", "配置快照元数据，包含所有配置文件的 SHA256 哈希"),
    ]:
        snap_path_str = config_snapshot_info.get(snap_key, "")
        if not snap_path_str:
            continue
        snap_path = Path(snap_path_str)
        artifacts.append(
            {
                "artifact_type": "config_snapshot",
                "name": snap_key,
                "local_path": str(snap_path),
                "gcs_uri": _local_to_gcs(snap_path) if gcs_sync_local_outputs else "",
                "storage_policy": _mirror_policy(),
                "description": snap_desc,
            }
        )

    # ── 5. 评估报告（lgbm_eval_* 等，仅本地）────────────────────────────────
    for key, path in report_info.items():
        if not isinstance(path, str) or not path.strip():
            continue
        # 跳过非路径占位符（如历史遗留的 "deprecated" 值）
        if not (path.startswith("\\") or path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            continue
        p = Path(path)
        artifacts.append(
            {
                "artifact_type": f"report_{key}",
                "local_path": str(path),
                "gcs_uri": _local_to_gcs(p) if gcs_sync_local_outputs else "",
                "storage_policy": _mirror_policy(),
                "description": f"评估报告：{key}",
            }
        )

    # ── 6. 模型级产物（每个实体 model_* 子目录）──────────────────────────────
    # 从 summary["results"] 中提取每个实体的模型文件、元数据、预测、特征重要性、指标
    for result in summary.get("results", []):
        if result.get("status") != "SUCCESS":
            continue
        model_name = result.get("model_name", "")

        # 模型文件 .pkl
        pkl_local = result.get("model_pkl_local", "")
        pkl_gcs = result.get("model_pkl_gcs", "")
        if pkl_local:
            artifacts.append(
                {
                    "artifact_type": "model_file",
                    "model_name": model_name,
                    "local_path": pkl_local,
                    "gcs_uri": pkl_gcs if pkl_gcs else (
                        _local_to_gcs(Path(pkl_local)) if gcs_sync_local_outputs else ""
                    ),
                    "storage_policy": "selective_gcs" if pkl_gcs else _mirror_policy(),
                    "description": f"{model_name} 训练好的模型文件（用于后续批量推理）",
                }
            )

        # 模型元数据 JSON
        meta_local = result.get("model_metadata_json_local", "")
        meta_gcs = result.get("model_metadata_json", "")
        # model_metadata_json 字段在有 gcs 时存 gcs uri，无则存本地路径，需要区分
        if meta_gcs and meta_gcs.startswith("gs://"):
            meta_gcs_uri = meta_gcs
        else:
            meta_gcs_uri = _local_to_gcs(Path(meta_local)) if (meta_local and gcs_sync_local_outputs) else ""
        if meta_local:
            artifacts.append(
                {
                    "artifact_type": "model_metadata",
                    "model_name": model_name,
                    "local_path": meta_local,
                    "gcs_uri": meta_gcs_uri,
                    "storage_policy": "selective_gcs" if (meta_gcs and meta_gcs.startswith("gs://")) else _mirror_policy(),
                    "description": f"{model_name} 模型元数据（特征列表、参数、时间戳等）",
                }
            )

        # 预测结果 CSV
        pred_local = result.get("pred_train_test_csv_local", "")
        pred_gcs = result.get("pred_train_test_csv", "")
        pred_gcs_uri = pred_gcs if (pred_gcs and pred_gcs.startswith("gs://")) else ""
        if pred_local:
            artifacts.append(
                {
                    "artifact_type": "predictions",
                    "model_name": model_name,
                    "local_path": pred_local,
                    "gcs_uri": pred_gcs_uri if pred_gcs_uri else (
                        _local_to_gcs(Path(pred_local)) if gcs_sync_local_outputs else ""
                    ),
                    "storage_policy": "selective_gcs" if pred_gcs_uri else _mirror_policy(),
                    "description": f"{model_name} 训练集+测试集预测明细",
                }
            )

        # 特征重要性 CSV
        feat_local = result.get("feature_importance_csv_local", "")
        feat_gcs = result.get("feature_importance_csv", "")
        feat_gcs_uri = feat_gcs if (feat_gcs and feat_gcs.startswith("gs://")) else ""
        if feat_local:
            artifacts.append(
                {
                    "artifact_type": "feature_importance",
                    "model_name": model_name,
                    "local_path": feat_local,
                    "gcs_uri": feat_gcs_uri if feat_gcs_uri else (
                        _local_to_gcs(Path(feat_local)) if gcs_sync_local_outputs else ""
                    ),
                    "storage_policy": "selective_gcs" if feat_gcs_uri else _mirror_policy(),
                    "description": f"{model_name} 特征重要性排名",
                }
            )

        # 参数记录文件
        eff_params_local = result.get("effective_params_json_local", "")
        if eff_params_local:
            artifacts.append(
                {
                    "artifact_type": "effective_params",
                    "model_name": model_name,
                    "local_path": eff_params_local,
                    "gcs_uri": _local_to_gcs(Path(eff_params_local)) if gcs_sync_local_outputs else "",
                    "storage_policy": _mirror_policy(),
                    "description": f"{model_name} 实际生效超参数",
                }
            )
        # 模型指标文件
        for metrics_key, metrics_desc in [
            ("metrics_by_split_csv_local", "按数据集划分的指标（CSV）"),
        ]:
            m_local = result.get(metrics_key, "")
            if m_local:
                artifacts.append(
                    {
                        "artifact_type": "model_metrics",
                        "model_name": model_name,
                        "local_path": m_local,
                        "gcs_uri": _local_to_gcs(Path(m_local)) if gcs_sync_local_outputs else "",
                        "storage_policy": _mirror_policy(),
                        "description": f"{model_name} {metrics_desc}",
                    }
                )

    # ── 7. manifest 文件本身（最后写入，路径提前声明）────────────────────────
    manifest_path = out_dir / f"{algorithm_name.lower()}_artifact_manifest_{run_id}.json"
    artifacts.append(
        {
            "artifact_type": "artifact_manifest",
            "local_path": str(manifest_path),
            "gcs_uri": "",
            "storage_policy": "local_only",
            "description": "本次运行所有产物的位置与存储策略索引（本文件）",
        }
    )

    manifest = {
        "run_id": run_id,
        "run_tag": run_tag,
        "scenario": scenario,
        "output_root": str(output_root),
        "out_dir": str(out_dir),
        "gcs_output_uri": gcs_output_uri,
        "gcs_sync_local_outputs": gcs_sync_local_outputs,
        "artifacts": artifacts,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _build_entity_filters(
    runtime_cfg: Dict,
    entity_id_columns: List[str],
) -> List[Dict[str, str]]:
    entity_values = runtime_cfg.get("entity_values_to_process")
    if entity_values is None:
        raise ValueError("runtime config requires 'entity_values_to_process' for current entity_id_columns")

    if not isinstance(entity_values, list) or not entity_values:
        raise ValueError("runtime config 'entity_values_to_process' must be a non-empty list")

    entity_filters: List[Dict[str, str]] = []
    for raw in entity_values:
        if not isinstance(raw, dict):
            raise ValueError("each item in 'entity_values_to_process' must be a mapping")
        missing = [c for c in entity_id_columns if c not in raw]
        if missing:
            raise ValueError(f"entity_values_to_process item missing keys: {missing}")
        entity_filters.append({c: str(raw[c]).strip() for c in entity_id_columns})
    return entity_filters


def _resolve_entity_filters(
    runtime_cfg: Dict,
    entity_id_columns: List[str],
    *,
    source_mode: str,
    source_df: Optional[pd.DataFrame],
    client: Optional[bigquery.Client],
    source_table: str,
    time_column: str,
    train_start: str,
    test_end: str,
    source_filters: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    # 规则：
    # 1) 若配置了 entity_values_to_process，优先使用手工实体清单。
    # 2) 若 entity_discovery.enabled=true，则从当前数据源自动提取所有去重组合。
    # 3) 若未获得实体，直接报错终止。
    if runtime_cfg.get("entity_values_to_process") is not None:
        return _build_entity_filters(runtime_cfg, entity_id_columns)

    discovery_cfg = runtime_cfg.get("entity_discovery", {})
    discovery_enabled = True
    if isinstance(discovery_cfg, dict) and "enabled" in discovery_cfg:
        if not isinstance(discovery_cfg["enabled"], bool):
            raise ValueError("runtime config 'entity_discovery.enabled' must be bool")
        discovery_enabled = discovery_cfg["enabled"]

    if discovery_enabled:
        if source_mode == "csv":
            if source_df is None:
                raise ValueError("CSV source dataframe is required for entity discovery")
            entities = discover_entity_filters_from_dataframe(source_df, entity_id_columns)
        elif source_mode == "bq":
            if client is None:
                raise ValueError("BigQuery client is required for entity discovery in bq mode")
            entities = discover_entity_filters_from_bq(
                client,
                source_table=source_table,
                entity_id_columns=entity_id_columns,
                time_column=time_column,
                train_start=train_start,
                test_end=test_end,
                source_filters=source_filters,
            )
        else:
            raise ValueError(f"Unsupported source_mode for entity discovery: {source_mode}")
        if entities:
            print(f"[INFO] entity_discovery found {len(entities)} combinations", flush=True)
            return entities

    raise ValueError(
        "No entities resolved. Provide 'entity_values_to_process' or enable/verify entity discovery source data."
    )


def main() -> None:
    global MODEL_PARAMS
    global SOURCE_REF
    global OUT_DIR

    # 端到端建模流程总览：
    # (1) 特征宽表读取：来源可为 BigQuery 源表或 CSV。
    # (2) 模型调优/训练：加载配置参数并按 entity（entity_id_columns 维度组合）训练 DecisionTree。
    # (3) 模型预测：输出 train+test 预测明细。
    # (4) 模型效果评估：计算指标并生成评估产物/报告。
    
    parser = argparse.ArgumentParser(description="Batch modeling demo for Cloud Run Job")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only create/upload a marker file to GCS"
    )
    parser.add_argument(
        "--max-entities",
        type=int,
        default=None,
        help="Limit number of entities for quick tests",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="Execution scenario key (e.g. bq_local_local, gcs_gcp_gcs). Defined in config/system/scenario_defaults.yaml.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to unified profile config file (e.g. config/profiles/item_channel_ma_week/config_v001.yaml)",
    )
    parser.add_argument(
        "--model-type",
        type=str,
        default=None,
        help="Single model key to run (for example: decision_tree or lightgbm). Overrides model.active in config.",
    )
    args = parser.parse_args()

    # (2) 加载统一配置文件
    config_file = args.config if args.config else "config/profiles/item_channel_ma_week/config_v001.yaml"
    cfg = load_unified_config(config_file)
    runtime_cfg = cfg["runtime"]
    active_model_key = resolve_active_model(cfg["model"], args.model_type)
    MODEL_PARAMS = get_model_type_params(active_model_key, cfg["model"])
    model_identity = get_model_identity(active_model_key, cfg["model"])
    algorithm_name = model_identity["algorithm_name"]
    algorithm_version = model_identity["algorithm_version"]
    model_key_normalized = normalize_model_key(active_model_key)
    model_label_map = {
        "decision_tree": "Decision Tree",
        "lightgbm": "LightGBM",
    }
    model_label = model_label_map.get(model_key_normalized, algorithm_name)
    print(f"[CONFIG] Loaded unified config: {config_file}", flush=True)
    print(f"[CONFIG] model_key={model_key_normalized}, model_label={model_label}", flush=True)
    print(f"[CONFIG] model_params = {MODEL_PARAMS}", flush=True)
    print(f"[CONFIG] algorithm_name={algorithm_name}, algorithm_version={algorithm_version}", flush=True)
    sample_key_columns = _load_required_columns(runtime_cfg, "sample_key_columns")
    entity_id_columns = _load_required_columns(runtime_cfg, "entity_id_columns")
    model_name_columns = _load_required_columns(runtime_cfg, "model_name_columns")
    features_default = _require_runtime_field(runtime_cfg, "features")
    label_column = str(runtime_cfg.get("label_column", "item_qty")).strip() or "item_qty"
    time_column = str(runtime_cfg.get("time_column", "day_date")).strip() or "day_date"
    min_train_rows = int(_require_runtime_field(runtime_cfg, "min_train_rows"))
    validation_cfg = _load_validation_config(runtime_cfg)
    evaluation_cfg = _load_evaluation_config(runtime_cfg)
    tuning_cfg = _load_tuning_config(runtime_cfg)
    
    # 如果 evaluation_cfg 中的 output_prefix 是默认值（"evaluation"），则自动根据当前模型类型生成
    # 这样不同算法会自动生成对应的前缀，如 "lgbm_eval", "dt_eval" 等
    if evaluation_cfg.get("output_prefix") == "evaluation":
        evaluation_cfg["output_prefix"] = get_output_prefix_for_model(model_key_normalized)
    
    split_label_map = cast(Dict[str, str], evaluation_cfg["split_label_map"])
    time_windows = _require_runtime_field(runtime_cfg, "time_windows")
    scenario_profile = _load_scenario_profile(cfg["scenario_profiles"], args.scenario)
    table_defaults = cfg["bq_tables"]
    train_start = str(_require_runtime_field(time_windows, "train_start"))
    train_end = str(_require_runtime_field(time_windows, "train_end"))
    test_start = str(_require_runtime_field(time_windows, "test_start"))
    test_end = str(_require_runtime_field(time_windows, "test_end"))

    if not isinstance(features_default, list) or not features_default:
        raise ValueError("runtime config 'features' must be a non-empty list")
    features = [str(x) for x in features_default]

    source_mode = str(_require_runtime_field(scenario_profile, "source_mode")).strip().lower()
    source_filters_raw = scenario_profile.get("source_filters", {})
    if source_filters_raw is None:
        source_filters_raw = {}
    if not isinstance(source_filters_raw, dict):
        raise ValueError(f"scenario '{args.scenario}' field 'source_filters' must be a mapping")
    source_filters = {
        str(k).strip(): str(v).strip()
        for k, v in source_filters_raw.items()
        if str(k).strip() and str(v).strip()
    }
    source_table = ""
    source_csv_uri = ""
    source_csv_local_path = ""
    if source_mode == "bq":
        source_table = str(_require_runtime_field(scenario_profile, "source_table")).strip()
        if not source_table:
            raise ValueError(f"scenario '{args.scenario}' requires non-empty source_table")
        SOURCE_REF = source_table
    elif source_mode == "csv":
        source_csv_uri = str(scenario_profile.get("source_csv_uri", "")).strip()
        source_csv_local_path = str(scenario_profile.get("source_csv_local_path", "")).strip()
        if not source_csv_uri and not source_csv_local_path:
            raise ValueError(
                f"scenario '{args.scenario}' requires one of source_csv_uri or source_csv_local_path"
            )
        if source_csv_uri and source_csv_local_path:
            raise ValueError(
                f"scenario '{args.scenario}' allows only one of source_csv_uri or source_csv_local_path"
            )
        SOURCE_REF = source_csv_uri if source_csv_uri else source_csv_local_path
    else:
        raise ValueError(f"scenario '{args.scenario}' has unsupported source_mode: {source_mode}")

    storage_cfg = _require_runtime_field(scenario_profile, "storage")
    if not isinstance(storage_cfg, dict):
        raise ValueError(f"scenario '{args.scenario}' storage must be a mapping")
    store_pred_to_bq = _require_bool(storage_cfg, "store_pred_to_bq")
    store_model_meta_to_bq = _require_bool(storage_cfg, "store_model_meta_to_bq")
    store_feat_imp_to_bq = _require_bool(storage_cfg, "store_feat_imp_to_bq")
    store_metrics_by_split_to_bq = _require_bool(storage_cfg, "store_metrics_by_split_to_bq")
    store_run_eval_metrics_to_bq = _require_bool(storage_cfg, "store_run_eval_metrics_to_bq")
    store_pred_to_gcs = _require_bool(storage_cfg, "store_pred_to_gcs")
    store_model_meta_to_gcs = _require_bool(storage_cfg, "store_model_meta_to_gcs")
    store_feat_imp_to_gcs = _require_bool(storage_cfg, "store_feat_imp_to_gcs")
    store_model_to_gcs = _require_bool(storage_cfg, "store_model_to_gcs")
    gcs_sync_local_outputs = _resolve_gcs_sync_local_outputs(runtime_cfg, scenario_profile)
    gcs_output_uri = _resolve_gcs_output_uri(scenario_profile)

    versioning_meta = cfg.get("versioning") or {}
    if not isinstance(versioning_meta, dict):
        versioning_meta = {}
    model_line = str(versioning_meta.get("model_line", "")).strip()
    run_tag = _build_run_tag(RUN_TS, model_line, model_key_normalized)

    output_root = _resolve_output_dir(scenario_profile)
    OUT_DIR = output_root / "runs" / run_tag
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    config_snapshot_info = _write_config_snapshot(
        out_dir=OUT_DIR,
        run_ts=RUN_TS,
        config_path=_resolve_config_input_path(config_file, "config/profiles/item_channel_ma_week/config_v001.yaml"),
        system_defaults_path=_resolve_config_input_path(
            "config/system/scenario_defaults.yaml",
            "config/system/scenario_defaults.yaml",
        ),
        versioning_meta=versioning_meta,
    )

    requested_bq_pred_table = str(table_defaults.get("bq_pred_table", "")).strip()
    requested_bq_model_meta_table = str(table_defaults.get("bq_model_meta_table", "")).strip()
    requested_bq_feat_imp_table = str(table_defaults.get("bq_feat_imp_table", "")).strip()
    requested_bq_metrics_by_split_table = str(table_defaults.get("bq_metrics_by_split_table", "")).strip()
    requested_bq_run_eval_metrics_table = str(table_defaults.get("bq_run_eval_metrics_table", "")).strip()

    print(f"[START] ts={RUN_TS} | project={PROJECT_ID} | source_mode={source_mode} | source={SOURCE_REF}", flush=True)
    print(f"[START] run_tag={run_tag}", flush=True)
    print(f"[START] scenario={args.scenario}", flush=True)
    print(f"[START] output_root={output_root}", flush=True)
    print(f"[START] out_dir={OUT_DIR}", flush=True)
    print(f"[START] gcs_output={gcs_output_uri}", flush=True)
    requested_tables: Dict[str, str] = {
        "pred": requested_bq_pred_table,
        "model_meta": requested_bq_model_meta_table,
        "feat_imp": requested_bq_feat_imp_table,
        "metrics_by_split": requested_bq_metrics_by_split_table,
        "run_eval_metrics": requested_bq_run_eval_metrics_table,
    }

    if not store_pred_to_bq:
        requested_tables["pred"] = ""
    if not store_model_meta_to_bq:
        requested_tables["model_meta"] = ""
    if not store_feat_imp_to_bq:
        requested_tables["feat_imp"] = ""
    if not store_metrics_by_split_to_bq:
        requested_tables["metrics_by_split"] = ""
    if not store_run_eval_metrics_to_bq:
        requested_tables["run_eval_metrics"] = ""

    print(f"[START] bq_pred_table_requested={requested_bq_pred_table}", flush=True)
    print(f"[START] bq_model_meta_table_requested={requested_bq_model_meta_table}", flush=True)
    print(f"[START] bq_feat_imp_table_requested={requested_bq_feat_imp_table}", flush=True)
    if source_filters:
        print(f"[START] source_filters={json.dumps(source_filters, ensure_ascii=True)}", flush=True)
    print(
        f"[START] runtime_window=train:{train_start}~{train_end}, test:{test_start}~{test_end}; "
        f"features={len(features)}; min_train_rows={min_train_rows}; label_column={label_column}; "
        f"sample_key_columns={json.dumps(sample_key_columns, ensure_ascii=False)}; "
        f"entity_id_columns={json.dumps(entity_id_columns, ensure_ascii=False)}; "
        f"model_name_columns={json.dumps(model_name_columns, ensure_ascii=False)}; time_column={time_column}",
        flush=True,
    )
    print(
        "[START] in_sample_validation="
        f"enabled={validation_cfg['enabled']}, ratio={validation_cfg['validation_ratio']}, "
        f"mode={validation_cfg['split_mode']}, random_seed={validation_cfg['random_seed']}",
        flush=True,
    )
    print(f"[START] split_label_map={json.dumps(split_label_map, ensure_ascii=False)}", flush=True)
    print(
        "[START] evaluation="
        f"requested_dimensions={json.dumps(evaluation_cfg['requested_dimensions'], ensure_ascii=False)}, "
        f"include_total={evaluation_cfg['include_total']}, include_entity={evaluation_cfg['include_entity']}, "
        f"output_prefix={evaluation_cfg['output_prefix']}",
        flush=True,
    )
    print(
        "[START] tuning="
        f"enabled={tuning_cfg.enabled}, method={tuning_cfg.method}, n_iter={tuning_cfg.n_iter}, "
        f"objective_primary={tuning_cfg.objective.primary}, objective_secondary={tuning_cfg.objective.secondary}",
        flush=True,
    )
    print(f"[START] model_key={model_key_normalized}, model_label={model_label}", flush=True)
    if tuning_cfg.enabled:
        print(
            "[INFO] tuning is enabled, configured model params are ignored and per-entity best params are used",
            flush=True,
        )
    print(
        "[START] store_flags="
        f"pred_bq={store_pred_to_bq}, model_meta_bq={store_model_meta_to_bq}, feat_imp_bq={store_feat_imp_to_bq}, "
        f"metrics_by_split_bq={store_metrics_by_split_to_bq}, run_eval_metrics_bq={store_run_eval_metrics_to_bq}, "
        f"pred_gcs={store_pred_to_gcs}, model_meta_gcs={store_model_meta_to_gcs}, feat_imp_gcs={store_feat_imp_to_gcs}, "
        f"model_pkl_gcs={store_model_to_gcs}",
        flush=True,
    )
    print(f"[START] gcs_sync_local_outputs={gcs_sync_local_outputs}", flush=True)

    if args.dry_run:
        run_dry_run(gcs_output_uri=gcs_output_uri)
        print("[DONE] dry run completed.", flush=True)
        return

    need_bq_client = (
        source_mode == "bq"
        or store_pred_to_bq
        or store_model_meta_to_bq
        or store_feat_imp_to_bq
        or store_metrics_by_split_to_bq
        or store_run_eval_metrics_to_bq
    )
    client: Optional[bigquery.Client] = None
    if need_bq_client:
        client = bigquery.Client(project=PROJECT_ID)

    source_df: Optional[pd.DataFrame] = None
    # (1) 特征宽表读取：选择数据源并识别可用特征列
    if source_mode == "csv":
        # CSV 模式下，数据只加载一次，后续按 entity 在内存中切片复用。
        source_df = load_source_csv_dataframe(
            source_csv_uri=source_csv_uri,
            source_csv_local_path=source_csv_local_path,
            sample_key_columns=sample_key_columns,
            label_column=label_column,
            out_dir=OUT_DIR,
            run_ts=RUN_TS,
            project_id=PROJECT_ID,
        )
        # 本地数据当前不支持按条件筛选；要求输入即为已筛选数据。
        if source_df.empty:
            raise RuntimeError("Source precheck failed: local CSV dataframe is empty, cannot continue modeling")
        feature_cols = get_available_features_from_dataframe(source_df, features)
    else:
        if client is None:
            raise ValueError("BigQuery client is required for bq source mode")
        if source_filters:
            table = client.get_table(source_table)
            schema_cols = {field.name for field in table.schema}
            missing_filter_cols = [col for col in source_filters.keys() if col not in schema_cols]
            if missing_filter_cols:
                raise ValueError(
                    "source_filters contains columns not found in source table: "
                    f"{missing_filter_cols}; source_table={source_table}"
                )
        validate_bq_source_non_empty(
            client,
            source_table=source_table,
            time_column=time_column,
            train_start=train_start,
            test_end=test_end,
            source_filters=source_filters,
        )
        feature_cols = get_available_features(client, features, source_table=source_table)

    entity_filters = _resolve_entity_filters(
        runtime_cfg,
        entity_id_columns,
        source_mode=source_mode,
        source_df=source_df,
        client=client,
        source_table=source_table,
        time_column=time_column,
        train_start=train_start,
        test_end=test_end,
        source_filters=source_filters,
    )
    print(f"[INFO] entities_to_process={len(entity_filters)}", flush=True)

    if not feature_cols:
        raise RuntimeError("No available features found in source table.")

    resolved_tables: Dict[str, str] = {"pred": "", "model_meta": "", "feat_imp": ""}
    # 仅对启用 BQ 写入的目标表执行解析，避免无权限场景触发无意义的 BQ API 调用。
    for key, spec in TABLE_SPECS.items():
        requested = requested_tables.get(key, "")
        if not requested.strip():
            continue
        if client is None:
            raise ValueError("BigQuery client is required when BigQuery output tables are enabled")
        resolved = resolve_bq_table(client, requested, spec, RUN_TS)
        resolved_tables[key] = resolved
        if resolved:
            print(f"[INFO] bq_{key}_table_resolved={resolved}", flush=True)

    runtime_context = PipelineRuntimeContext(
        run_ts=RUN_TS,
        run_tag=run_tag,
        out_dir=OUT_DIR,
        project_id=PROJECT_ID,
        source_ref=SOURCE_REF,
        gcs_output_uri=gcs_output_uri,
        model_params=MODEL_PARAMS,
        model_key=model_key_normalized,
        algorithm_name=algorithm_name,
        algorithm_version=algorithm_version,
    )
    cfg_name = config_file
    summary = train_and_save_predictions(
        client=client,
        runtime=runtime_context,
        entity_filters=entity_filters,
        min_train_rows=min_train_rows,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        feature_cols=feature_cols,
        sample_key_columns=sample_key_columns,
        entity_id_columns=entity_id_columns,
        model_name_columns=model_name_columns,
        label_column=label_column,
        time_column=time_column,
        source_table=source_table,
        source_filters=source_filters,
        source_df=source_df,
        max_entities=args.max_entities,
        bq_pred_table=resolved_tables["pred"],
        bq_model_meta_table=resolved_tables["model_meta"],
        bq_feat_imp_table=resolved_tables["feat_imp"],
        store_pred_to_bq=store_pred_to_bq,
        store_model_meta_to_bq=store_model_meta_to_bq,
        store_feat_imp_to_bq=store_feat_imp_to_bq,
        store_metrics_by_split_to_bq=store_metrics_by_split_to_bq,
        bq_metrics_by_split_table=resolved_tables["metrics_by_split"],
        store_pred_to_gcs=store_pred_to_gcs,
        store_model_meta_to_gcs=store_model_meta_to_gcs,
        store_feat_imp_to_gcs=store_feat_imp_to_gcs,
        store_model_to_gcs=store_model_to_gcs,
        enable_in_sample_validation=cast(bool, validation_cfg["enabled"]),
        validation_ratio=cast(float, validation_cfg["validation_ratio"]),
        validation_split_mode=cast(str, validation_cfg["split_mode"]),
        validation_random_seed=cast(int, validation_cfg["random_seed"]),
        split_label_map=split_label_map,
        tuning_config=tuning_cfg,
        config_name=cfg_name,
    )
    # (4) 模型效果评估报告：主报告始终生成；baseline 仅在条件满足时附加。
    report_info = generate_same_structure_report(
        summary=summary,
        sample_key_columns=sample_key_columns,
        time_column=time_column,
        client=client,
        source_table=source_table,
        out_dir=OUT_DIR,
        run_ts=RUN_TS,
        model_key=model_key_normalized,
        model_label=model_label,
        test_split_name=split_label_map.get("test", "test"),
        entity_id_columns=entity_id_columns,
        requested_dimensions=cast(List[str], evaluation_cfg["requested_dimensions"]),
        include_total=cast(bool, evaluation_cfg["include_total"]),
        include_entity=cast(bool, evaluation_cfg["include_entity"]),
        output_prefix=cast(str, evaluation_cfg["output_prefix"]),
    )

    # (5) run 级评估指标写入 BQ（dt_run_eval_metrics）
    if store_run_eval_metrics_to_bq and resolved_tables.get("run_eval_metrics", "").strip():
        _metrics_csv = report_info.get("metrics_csv", "")
        if _metrics_csv:
            _run_eval_written = append_run_eval_metrics_to_bq(
                client=client,
                table_id=resolved_tables["run_eval_metrics"],
                metrics_csv=_metrics_csv,
                run_id=RUN_TS,
                source_ref=SOURCE_REF,
                algorithm_name=algorithm_name,
                algorithm_version=algorithm_version,
                config_name=cfg_name,
            )
            print(
                f"[INFO] Written run_eval_metrics rows to {resolved_tables['run_eval_metrics']}: {_run_eval_written}",
                flush=True,
            )

    summary["config_snapshot"] = config_snapshot_info
    summary["versioning"] = versioning_meta
    summary_path = OUT_DIR / f"{algorithm_name.lower()}_run_summary_{RUN_TS}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    success_count = sum(1 for r in summary.get("results", []) if r.get("status") == "SUCCESS")
    skip_no_data_count = sum(1 for r in summary.get("results", []) if r.get("status") == "SKIPPED_NO_DATA")
    registry_row: Dict[str, Any] = {
        "run_id": RUN_TS,
        "run_tag": run_tag,
        "scenario": args.scenario,
        "model_line": str(versioning_meta.get("model_line", "")),
        "runtime_version": str(versioning_meta.get("runtime_version", "")),
        "model_params_version": str(versioning_meta.get("model_params_version", "")),
        "source_mode": source_mode,
        "source_ref": SOURCE_REF,
        "entities_planned": len(entity_filters[: args.max_entities] if args.max_entities else entity_filters),
        "entities_success": success_count,
        "entities_skipped_no_data": skip_no_data_count,
        "config_sha256": config_snapshot_info["config_sha256"],
        "system_defaults_sha256": config_snapshot_info["system_defaults_sha256"],
        "summary_json": str(summary_path),
        "metrics_csv": str(report_info.get("metrics_csv", "")),
        "output_root": str(output_root),
    }
    registry_path = _append_run_registry_csv(output_root=output_root, row=registry_row)

    if gcs_sync_local_outputs:
        mirrored = upload_dir_to_gcs(
            OUT_DIR,
            gcs_output_uri,
            run_ts=RUN_TS,
            run_tag=run_tag,
            project_id=PROJECT_ID,
        )
        summary.setdefault("uploaded_files", [])
        summary["uploaded_files"].extend(mirrored)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = _write_artifact_manifest(
        out_dir=OUT_DIR,
        run_id=RUN_TS,
        run_tag=run_tag,
        algorithm_name=algorithm_name,
        scenario=args.scenario,
        output_root=output_root,
        gcs_output_uri=gcs_output_uri,
        gcs_sync_local_outputs=gcs_sync_local_outputs,
        summary_path=summary_path,
        report_info=report_info,
        registry_path=registry_path,
        summary=summary,
        config_snapshot_info=config_snapshot_info,
    )

    print(f"[INFO] report_outputs={json.dumps(report_info, ensure_ascii=False)}", flush=True)
    print(f"[INFO] run_registry_csv={registry_path}", flush=True)
    print(f"[INFO] artifact_manifest={manifest_path}", flush=True)

    uploaded_count = len(summary.get("uploaded_files", []))
    print(f"[DONE] uploaded {uploaded_count} selected files to GCS.", flush=True)


if __name__ == "__main__":
    main()
