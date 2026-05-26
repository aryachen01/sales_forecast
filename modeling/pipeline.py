"""决策树建模主流程模块。

包含批量建模编排函数与运行时上下文对象：
- PipelineRuntimeContext：承载 run_ts、路径、来源与参数等运行时信息
- train_and_save_predictions：按 entity（entity_id_columns 定义的维度组合）执行训练、预测、评估与产物持久化
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, cast

import pandas as pd
from google.cloud import bigquery
from sklearn.tree import DecisionTreeRegressor

from common.data_source import fetch_data_from_bq, fetch_data_from_dataframe
from modeling.artifacts import (
    save_feature_importance_csv,
    save_model_and_metadata,
    save_predictions_csv,
)
from modeling.batch_reporting import save_entity_eval_partial
from modeling.evaluation import evaluate_and_save_outputs
from modeling.preprocess import prepare_train_test_features, split_train_validation
from modeling.prediction_builders import build_feature_importance_df, build_prediction_dataframes
from modeling.tuning import TuningConfig, resolve_algorithm_key, tune_entity_params
from modeling.writers import (
    append_feature_importance_to_bq,
    append_metrics_by_split_to_bq,
    append_model_metadata_to_bq,
    append_train_test_predictions_to_bq,
)


@dataclass(frozen=True)
class PipelineRuntimeContext:
    run_ts: str
    run_tag: str
    out_dir: Path
    project_id: str
    source_ref: str
    gcs_output_uri: str
    model_params: Optional[Dict]
    model_key: str
    algorithm_name: str
    algorithm_version: str


def _build_estimator(algorithm_key: str, params: Dict[str, Any]):
    if algorithm_key == "decision_tree":
        return DecisionTreeRegressor(**params)
    if algorithm_key == "lightgbm":
        try:
            from lightgbm import LGBMRegressor  # type: ignore[reportMissingImports]
        except ImportError as exc:
            raise ImportError(
                "LightGBM is selected but not installed in current environment. "
                "Install it with: pip install lightgbm"
            ) from exc
        return LGBMRegressor(**params)
    raise ValueError(f"Unsupported algorithm key: {algorithm_key}")


def _write_checkpoint(
    checkpoint_path: Path,
    run_id: str,
    run_tag: str,
    results: List[Dict],
    total_entities: int,
) -> None:
    """每个 entity 完成后立即写入 checkpoint，支持断点续跑。"""
    completed_keys = [r["entity_id_json"] for r in results]
    payload = {
        "run_id": run_id,
        "run_tag": run_tag,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "completed_count": len(completed_keys),
        "total_entities": total_entities,
        "completed_entity_keys": completed_keys,
        "results": results,
    }
    checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def train_and_save_predictions(
    client: Optional[bigquery.Client],
    runtime: PipelineRuntimeContext,
    entity_filters: List[Dict[str, str]],
    min_train_rows: int,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    feature_cols: List[str],
    sample_key_columns: List[str],
    entity_id_columns: List[str],
    model_name_columns: List[str],
    label_column: str,
    time_column: str,
    source_table: str,
    source_filters: Optional[Dict[str, str]] = None,
    source_df: Optional[pd.DataFrame] = None,
    max_entities: int | None = None,
    bq_pred_table: str = "",
    bq_model_meta_table: str = "",
    bq_feat_imp_table: str = "",
    store_pred_to_bq: bool = False,
    store_model_meta_to_bq: bool = False,
    store_feat_imp_to_bq: bool = False,
    store_pred_to_gcs: bool = False,
    store_model_meta_to_gcs: bool = True,
    store_feat_imp_to_gcs: bool = True,
    store_model_to_gcs: bool = False,
    store_metrics_by_split_to_bq: bool = False,
    bq_metrics_by_split_table: str = "",
    enable_in_sample_validation: bool = False,
    validation_ratio: float = 0.2,
    validation_split_mode: str = "random",
    validation_random_seed: int = 42,
    split_label_map: Optional[Dict[str, str]] = None,
    tuning_config: Optional[TuningConfig] = None,
    config_name: str = "config/model_params.yaml",
    checkpoint_path: Optional[Path] = None,
    resume_results: Optional[List[Dict]] = None,
    completed_entity_keys: Optional[Set[str]] = None,
) -> Dict:
    """按 entity（entity_id_columns 定义的维度组合）执行建模流程，并持久化产物与 BQ 输出。"""
    results: List[Dict] = list(resume_results) if resume_results else []
    _completed_keys: Set[str] = set(completed_entity_keys) if completed_entity_keys else set()
    model_meta_rows = []
    bq_pred_written_rows_total = 0
    bq_feat_imp_written_rows_total = 0
    bq_metrics_by_split_written_rows_total = 0
    uploaded_files: List[str] = []
    entities = entity_filters[:max_entities] if max_entities else entity_filters
    model_params = runtime.model_params if isinstance(runtime.model_params, dict) else {}
    split_label_map = split_label_map or {"train": "train", "validation": "validation", "test": "test"}
    tuning_config = tuning_config or TuningConfig()
    algorithm_key = resolve_algorithm_key(runtime.algorithm_name)

    tuning_trials_rows: List[Dict[str, object]] = []

    if tuning_config.enabled:
        print(
            "[INFO] tuning enabled: configured model params will be ignored; best params per entity will be used",
            flush=True,
        )

    def _sanitize_name(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z_-]", "_", value)[:120]

    def _build_model_name(df_train: pd.DataFrame, fallback: Dict[str, str]) -> str:
        values = []
        for col in model_name_columns:
            if col in df_train.columns and not df_train.empty:
                values.append(str(df_train[col].iloc[0]))
            else:
                values.append(str(fallback.get(col, "NA")))
        return "|".join(values)

    for idx, entity_filter in enumerate(entities, 1):
        entity_id_json = json.dumps(entity_filter, ensure_ascii=True)
        if entity_id_json in _completed_keys:
            print(f"[RESUME] Skipping completed entity ({idx}/{len(entities)}): {entity_id_json}", flush=True)
            continue
        print(f"[{runtime.algorithm_name}] {idx}/{len(entities)} entity={entity_id_json}", flush=True)
        try:
            if source_df is not None:
                df_train, df_test = fetch_data_from_dataframe(
                    entity_filter,
                    source_df,
                    feature_cols,
                    sample_key_columns=sample_key_columns,
                    entity_id_columns=entity_id_columns,
                    model_name_columns=model_name_columns,
                    label_column=label_column,
                    time_column=time_column,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            else:
                if client is None:
                    raise ValueError("BigQuery client is required when source_df is not provided")
                df_train, df_test = fetch_data_from_bq(
                    entity_filter,
                    client,
                    feature_cols,
                    sample_key_columns=sample_key_columns,
                    entity_id_columns=entity_id_columns,
                    model_name_columns=model_name_columns,
                    label_column=label_column,
                    time_column=time_column,
                    source_table=source_table,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    source_filters=source_filters,
                )
            if len(df_train) + len(df_test) == 0:
                print(
                    f"[WARN] 当前模型没有数据，跳过本轮建模: entity={entity_id_json}",
                    flush=True,
                )
                results.append(
                    {
                        "model_name": "",
                        "entity_id_json": entity_id_json,
                        "status": "SKIPPED_NO_DATA",
                        "algorithm": runtime.model_key,
                        "train_rows": 0,
                        "validation_rows": 0,
                        "test_rows": 0,
                    }
                )
                if checkpoint_path is not None:
                    _write_checkpoint(checkpoint_path, runtime.run_ts, runtime.run_tag, results, len(entities))
                continue
            if len(df_train) < min_train_rows or len(df_test) < 10:
                raise RuntimeError(
                    f"Insufficient data for entity {entity_id_json} "
                    f"(train={len(df_train)}, test={len(df_test)})"
                )

            df_train_fit, df_validation = split_train_validation(
                df_train,
                enabled=enable_in_sample_validation,
                validation_ratio=validation_ratio,
                mode=validation_split_mode,
                time_column=time_column,
                random_seed=validation_random_seed,
            )

            model_name = _build_model_name(df_train_fit, entity_filter)
            model_slug = _sanitize_name(model_name)
            algorithm_slug = _sanitize_name(str(runtime.algorithm_name).lower())

            x_train, y_train, x_test, y_test, x_val, y_val = prepare_train_test_features(
                df_train_fit,
                df_test,
                feature_cols,
                df_validation=df_validation,
            )

            effective_params = dict(model_params)
            params_source = "configured"
            tuning_best_metrics: Dict[str, float] = {}
            if tuning_config.enabled:
                if df_validation is not None and len(df_validation) > 0:
                    tune_train_df = df_train_fit
                    tune_val_df = df_validation
                else:
                    tune_train_df, tune_val_df = split_train_validation(
                        df_train,
                        enabled=True,
                        validation_ratio=tuning_config.internal_validation_ratio,
                        mode=tuning_config.internal_split_mode,
                        time_column=time_column,
                        random_seed=tuning_config.random_seed,
                    )
                if tune_val_df is None or tune_val_df.empty:
                    raise RuntimeError(
                        f"Tuning validation split is empty for entity {entity_id_json}; cannot tune"
                    )
                best_params, best_metrics, trial_rows = tune_entity_params(
                    algorithm_key=algorithm_key,
                    base_params=model_params,
                    tuning_cfg=tuning_config,
                    df_train=tune_train_df,
                    df_val=tune_val_df,
                    feature_cols=feature_cols,
                )
                effective_params = best_params
                params_source = "tuned"
                tuning_best_metrics = best_metrics

                for tr in trial_rows:
                    tuning_trials_rows.append(
                        {
                            "run_id": runtime.run_ts,
                            "model_name": model_name,
                            "entity_id_json": entity_id_json,
                            **tr,
                        }
                    )
                print(
                    f"[INFO] tuned entity={entity_id_json} best_mae={best_metrics.get('mae'):.6f} "
                    f"best_strict_nonzero={best_metrics.get('accuracy_strict_nonzero_pct')}",
                    flush=True,
                )

            model = _build_estimator(algorithm_key, cast(Dict[str, Any], effective_params))
            model.fit(x_train, y_train)

            y_pred_train = model.predict(x_train)
            y_pred = model.predict(x_test)
            y_pred_val = model.predict(x_val) if x_val is not None else None

            item_dir = runtime.out_dir / f"model_{model_slug}_{algorithm_slug}"
            item_dir.mkdir(parents=True, exist_ok=True)

            model_artifacts = save_model_and_metadata(
                model_name=model_name,
                entity_id_json=entity_id_json,
                item_dir=item_dir,
                out_dir=runtime.out_dir,
                run_ts=runtime.run_ts,
                run_tag=runtime.run_tag,
                source_ref=runtime.source_ref,
                feature_cols=feature_cols,
                model_params=effective_params,
                model=model,
                model_key=runtime.algorithm_name.lower(),
                algorithm_name=runtime.algorithm_name,
                algorithm_version=runtime.algorithm_version,
                gcs_output_uri=runtime.gcs_output_uri,
                project_id=runtime.project_id,
                config_name=config_name,
                store_model_meta_to_gcs=store_model_meta_to_gcs,
                store_model_to_gcs=store_model_to_gcs,
            )
            model_meta_json = model_artifacts["model_meta_json"]
            model_meta_json_gcs = model_artifacts["model_meta_json_gcs"]
            model_pkl = model_artifacts["model_pkl"]
            model_pkl_gcs = model_artifacts["model_pkl_gcs"]
            if model_pkl_gcs:
                uploaded_files.append(model_pkl_gcs)
            if model_meta_json_gcs:
                uploaded_files.append(model_meta_json_gcs)
            model_meta_rows.append(model_artifacts["model_meta_row"])

            pred_df, pred_all_df = build_prediction_dataframes(
                sample_key_columns=sample_key_columns,
                model_name=model_name,
                entity_id_json=entity_id_json,
                df_train_fit=df_train_fit,
                df_test=df_test,
                y_train_fit=y_train,
                y_test=y_test,
                y_pred_train_fit=y_pred_train,
                y_pred_test=y_pred,
                df_validation=df_validation,
                y_validation=y_val,
                y_pred_validation=y_pred_val,
                split_label_map=split_label_map,
            )
            pred_artifacts = save_predictions_csv(
                pred_all_df=pred_all_df,
                item_dir=item_dir,
                out_dir=runtime.out_dir,
                run_ts=runtime.run_ts,
                run_tag=runtime.run_tag,
                gcs_output_uri=runtime.gcs_output_uri,
                project_id=runtime.project_id,
                model_key=runtime.algorithm_name.lower(),
                store_pred_to_gcs=store_pred_to_gcs,
            )
            pred_all_csv = pred_artifacts["pred_all_csv"]
            pred_all_csv_gcs = pred_artifacts["pred_all_csv_gcs"]
            if pred_all_csv_gcs:
                uploaded_files.append(pred_all_csv_gcs)

            bq_written_rows = 0
            if store_pred_to_bq and bq_pred_table.strip():
                if client is None:
                    raise ValueError("BigQuery client is required for prediction BQ write")
                bq_written_rows = append_train_test_predictions_to_bq(
                    client=client,
                    table_id=bq_pred_table.strip(),
                    pred_all_df=pred_all_df,
                    feature_cols=feature_cols,
                    config_name=config_name,
                    run_id=runtime.run_ts,
                    run_tag=runtime.run_tag,
                    source_ref=runtime.source_ref,
                    gcs_output_uri=runtime.gcs_output_uri,
                    algorithm_name=runtime.algorithm_name,
                    algorithm_version=runtime.algorithm_version,
                )
                bq_pred_written_rows_total += bq_written_rows

            eval_outputs = evaluate_and_save_outputs(
                item_dir=item_dir,
                run_ts=runtime.run_ts,
                model_key=runtime.algorithm_name.lower(),
                split_predictions={
                    str(split_name): split_df.copy()
                    for split_name, split_df in pred_all_df.groupby("data_split")
                },
            )
            metrics = eval_outputs["metrics"]
            metrics_split_csv = eval_outputs["metrics_split_csv"]

            # 保存每实体 eval 断点：batch 中断后可直接用这些文件拼接重建汇总报告
            _test_key = split_label_map.get("test", "test")
            _test_df = pred_all_df[pred_all_df["data_split"] == _test_key].copy() if "data_split" in pred_all_df.columns else pred_all_df.copy()
            if not _test_df.empty:
                save_entity_eval_partial(
                    pred_test_df=_test_df,
                    item_dir=item_dir,
                    output_prefix=f"{runtime.algorithm_name.lower()}_eval",
                    run_ts=runtime.run_ts,
                )

            feat_imp_df = build_feature_importance_df(feature_cols, model.feature_importances_)
            feat_imp_artifacts = save_feature_importance_csv(
                feat_imp_df=feat_imp_df,
                item_dir=item_dir,
                out_dir=runtime.out_dir,
                run_ts=runtime.run_ts,
                run_tag=runtime.run_tag,
                gcs_output_uri=runtime.gcs_output_uri,
                project_id=runtime.project_id,
                model_key=runtime.algorithm_name.lower(),
                store_feat_imp_to_gcs=store_feat_imp_to_gcs,
            )
            feat_imp_csv = feat_imp_artifacts["feat_imp_csv"]
            feat_imp_csv_gcs = feat_imp_artifacts["feat_imp_csv_gcs"]
            if feat_imp_csv_gcs:
                uploaded_files.append(feat_imp_csv_gcs)

            bq_feat_imp_written_rows = 0
            if store_feat_imp_to_bq and bq_feat_imp_table.strip():
                if client is None:
                    raise ValueError("BigQuery client is required for feature importance BQ write")
                bq_feat_imp_written_rows = append_feature_importance_to_bq(
                    client=client,
                    table_id=bq_feat_imp_table.strip(),
                    model_name=model_name,
                    entity_id_json=entity_id_json,
                    feat_imp_df=feat_imp_df,
                    config_name=config_name,
                    feature_importance_csv_gcs=feat_imp_csv_gcs if feat_imp_csv_gcs else str(feat_imp_csv),
                    run_id=runtime.run_ts,
                    run_tag=runtime.run_tag,
                    source_ref=runtime.source_ref,
                    gcs_output_uri=runtime.gcs_output_uri,
                    algorithm_name=runtime.algorithm_name,
                    algorithm_version=runtime.algorithm_version,
                )
                bq_feat_imp_written_rows_total += bq_feat_imp_written_rows

            bq_metrics_by_split_written_rows = 0
            if store_metrics_by_split_to_bq and bq_metrics_by_split_table.strip():
                if client is None:
                    raise ValueError("BigQuery client is required for metrics_by_split BQ write")
                bq_metrics_by_split_written_rows = append_metrics_by_split_to_bq(
                    client=client,
                    table_id=bq_metrics_by_split_table.strip(),
                    metrics_split_csv=str(metrics_split_csv),
                    model_name=model_name,
                    entity_id_json=entity_id_json,
                    config_name=config_name,
                    run_id=runtime.run_ts,
                    source_ref=runtime.source_ref,
                    algorithm_name=runtime.algorithm_name,
                    algorithm_version=runtime.algorithm_version,
                )
                bq_metrics_by_split_written_rows_total += bq_metrics_by_split_written_rows

            # ── Per-entity 参数文件 → entity subdir ─────────────────────────
            _algo_lower = runtime.algorithm_name.lower()
            _eff_params_payload = dict(effective_params)
            if params_source == "tuned":
                _eff_params_payload["tuning_best_metrics"] = {
                    "best_mae": tuning_best_metrics.get("mae"),
                    "best_accuracy_strict_nonzero_pct": tuning_best_metrics.get(
                        "accuracy_strict_nonzero_pct"
                    ),
                }
            _eff_params_json_path = item_dir / f"{_algo_lower}_effective_params_{runtime.run_ts}.json"
            _eff_params_json_path.write_text(
                json.dumps(_eff_params_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            results.append(
                {
                    "model_name": model_name,
                    "entity_id_json": entity_id_json,
                    "status": "SUCCESS",
                    "algorithm": runtime.model_key,
                    "params_source": params_source,
                    "effective_params": effective_params,
                    "tuning_best_metrics": tuning_best_metrics,
                    "train_rows": int(len(df_train_fit)),
                    "validation_rows": int(len(df_validation)) if df_validation is not None else 0,
                    "test_rows": int(len(df_test)),
                    "metrics": metrics,
                    "pred_train_test_csv": pred_all_csv_gcs if pred_all_csv_gcs else str(pred_all_csv),
                    "pred_train_test_csv_local": str(pred_all_csv),
                    "bq_pred_table": bq_pred_table.strip(),
                    "bq_written_rows": bq_written_rows,
                    "metrics_by_split_csv": str(metrics_split_csv),
                    "metrics_by_split_csv_local": str(metrics_split_csv),
                    "model_pkl": model_pkl_gcs if model_pkl_gcs else str(model_pkl),
                    "model_pkl_local": str(model_pkl),
                    "model_pkl_gcs": model_pkl_gcs,
                    "model_metadata_json": model_meta_json_gcs if model_meta_json_gcs else str(model_meta_json),
                    "model_metadata_json_local": str(model_meta_json),
                    "feature_importance_csv": feat_imp_csv_gcs if feat_imp_csv_gcs else str(feat_imp_csv),
                    "feature_importance_csv_local": str(feat_imp_csv),
                    "bq_feat_imp_table": bq_feat_imp_table.strip(),
                    "bq_feat_imp_written_rows": bq_feat_imp_written_rows,
                    "bq_metrics_by_split_table": bq_metrics_by_split_table.strip(),
                    "bq_metrics_by_split_written_rows": bq_metrics_by_split_written_rows,
                    "effective_params_json_local": str(_eff_params_json_path),
                }
            )
            if checkpoint_path is not None:
                _write_checkpoint(checkpoint_path, runtime.run_ts, runtime.run_tag, results, len(entities))
        except Exception as exc:
            err_msg = f"Entity processing failed: {entity_id_json}; reason={exc}"
            print(f"[ERROR] {err_msg}", flush=True)
            results.append(
                {
                    "model_name": "",
                    "entity_id_json": entity_id_json,
                    "status": "FAILED",
                    "algorithm": runtime.model_key,
                    "train_rows": 0,
                    "validation_rows": 0,
                    "test_rows": 0,
                    "error": str(exc),
                }
            )
            if checkpoint_path is not None:
                _write_checkpoint(checkpoint_path, runtime.run_ts, runtime.run_tag, results, len(entities))
            continue

    if store_pred_to_bq and bq_pred_table.strip():
        print(
            f"[INFO] Written pred rows to {bq_pred_table}: {bq_pred_written_rows_total}",
            flush=True,
        )
    bq_model_meta_written_rows = 0
    if store_model_meta_to_bq and bq_model_meta_table.strip():
        if client is None:
            raise ValueError("BigQuery client is required for model metadata BQ write")
        bq_model_meta_written_rows = append_model_metadata_to_bq(
            client=client,
            table_id=bq_model_meta_table.strip(),
            rows=model_meta_rows,
        )
        print(
            f"[INFO] Written model metadata rows to {bq_model_meta_table}: {bq_model_meta_written_rows}",
            flush=True,
        )
    if store_feat_imp_to_bq and bq_feat_imp_table.strip():
        print(
            f"[INFO] Written feature importance rows to {bq_feat_imp_table}: {bq_feat_imp_written_rows_total}",
            flush=True,
        )
    if store_metrics_by_split_to_bq and bq_metrics_by_split_table.strip():
        print(
            f"[INFO] Written metrics_by_split rows to {bq_metrics_by_split_table}: {bq_metrics_by_split_written_rows_total}",
            flush=True,
        )

    tuning_outputs: Dict[str, str] = {}
    if tuning_trials_rows:
        trials_path = runtime.out_dir / f"tuning_trials_{runtime.run_ts}.csv"
        pd.DataFrame(tuning_trials_rows).to_csv(trials_path, index=False, encoding="utf-8")
        tuning_outputs["tuning_trials_csv"] = str(trials_path)


    summary = {
        "timestamp": runtime.run_ts,
        "model_family": runtime.model_key,
        "algorithm_name": runtime.algorithm_name,
        "algorithm_version": runtime.algorithm_version,
        "source_table": runtime.source_ref,
        "model_params": runtime.model_params,
        "bq_pred_table": bq_pred_table,
        "bq_pred_written_rows": bq_pred_written_rows_total,
        "bq_model_meta_table": bq_model_meta_table,
        "bq_model_meta_written_rows": bq_model_meta_written_rows,
        "bq_feat_imp_table": bq_feat_imp_table,
        "bq_feat_imp_written_rows": bq_feat_imp_written_rows_total,
        "bq_metrics_by_split_table": bq_metrics_by_split_table,
        "bq_metrics_by_split_written_rows": bq_metrics_by_split_written_rows_total,
        "uploaded_files": uploaded_files,
        "results": results,
        "tuning_enabled": tuning_config.enabled,
        "tuning_outputs": tuning_outputs,
    }
    summary_path = runtime.out_dir / f"{runtime.algorithm_name.lower()}_run_summary_{runtime.run_ts}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] summary={summary_path}", flush=True)
    return summary
