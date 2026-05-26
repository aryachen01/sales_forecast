"""评估汇总与报表输出模块。

职责说明：
1. 计算样本级命中标记与常见回归指标（MAE/RMSE/MAPE/WAPE/sMAPE 等）。
2. 将预测明细聚合为可审计的“分子分母原始表”（total/entity/dimension）。
3. 在原始分子分母基础上计算各类指标，并输出分组指标表。

主要入口：
- generate_same_structure_report
    输入：主流程 summary + 预测明细文件
    输出：
    - 原始聚合分子分母 CSV（含分层明细）
    - 指标分组 CSV（含 total/entity/dimension）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from google.cloud import bigquery


def compute_flags(actual: np.ndarray, pred: np.ndarray) -> Dict[str, np.ndarray | float]:
    eps = 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(np.abs(actual) > eps, pred / actual, np.nan)
    strict = np.where(np.abs(actual) > eps, (ratio >= 0.8) & (ratio <= 1.2), False)

    lo = np.floor(actual * 0.8)
    hi = np.ceil(actual * 1.2)
    standard = (pred >= lo) & (pred <= hi)

    loose = np.abs(pred - actual) <= 1
    ext = np.where(np.abs(actual) > eps, standard, loose)

    return {
        "accuracy_strict_pct": float(strict.mean() * 100),
        "accuracy_standard_pct": float(standard.mean() * 100),
        "accuracy_loose_pct": float(loose.mean() * 100),
        "accuracy_ext_pct": float(ext.mean() * 100),
        "strict_flag": strict.astype(int),
        "standard_flag": standard.astype(int),
        "loose_flag": loose.astype(int),
        "ext_flag": ext.astype(int),
    }


def compute_metrics(actual: pd.Series | np.ndarray, pred: pd.Series | np.ndarray) -> Dict[str, float]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(pred, dtype=float)
    err = p - y

    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))

    with np.errstate(divide="ignore", invalid="ignore"):
        mape_arr = np.where(np.abs(y) > 1e-12, np.abs(err) / np.abs(y), np.nan)
        smape_arr = np.where(np.abs(y) + np.abs(p) > 1e-12, 2.0 * np.abs(err) / (np.abs(y) + np.abs(p)), np.nan)
    mape_pct = float(np.nanmean(mape_arr) * 100) if np.any(np.isfinite(mape_arr)) else np.nan
    smape_pct = float(np.nanmean(smape_arr) * 100) if np.any(np.isfinite(smape_arr)) else np.nan

    nonzero_mask = np.abs(y) > 1e-12
    if np.any(nonzero_mask):
        mape_nonzero = float(np.mean(np.abs(err[nonzero_mask]) / np.abs(y[nonzero_mask])) * 100)
        mae_nonzero = float(np.mean(np.abs(err[nonzero_mask])))
    else:
        mape_nonzero = 0.0
        mae_nonzero = 0.0

    abs_sum = np.sum(np.abs(y))
    wape = float((np.sum(np.abs(err)) / abs_sum) * 100) if abs_sum > 0 else 0.0

    acc = compute_flags(y, p)
    return {
        "MAE": mae,
        "RMSE": rmse,
        "MAPE_pct": mape_pct,
        "MAPE_nonzero_pct": mape_nonzero,
        "MAE_nonzero": mae_nonzero,
        "WAPE_pct": wape,
        "sMAPE_pct": smape_pct,
        "accuracy_strict_pct": float(acc["accuracy_strict_pct"]),
        "accuracy_standard_pct": float(acc["accuracy_standard_pct"]),
        "accuracy_loose_pct": float(acc["accuracy_loose_pct"]),
        "accuracy_ext_pct": float(acc["accuracy_ext_pct"]),
    }


def fmt_metric(col: str, val: float) -> str:
    if pd.isna(val):
        return "NA"
    if col in {"MAE", "RMSE", "MAPE_pct", "MAPE_nonzero_pct", "MAE_nonzero", "WAPE_pct", "sMAPE_pct"}:
        return f"{val:.2f}"
    if col.endswith("_pct"):
        return f"{val:.2f}%"
    return f"{val:.4f}"


def save_entity_eval_partial(
    *,
    pred_test_df: pd.DataFrame,
    item_dir: Path,
    output_prefix: str,
    run_ts: str,
) -> Path:
    """每个实体训练完成后立即保存其 eval 贡献（预测明细 + 分子/分母列）。

    用途：batch 中断后，已完成实体的产物仍保留在各自子目录，
    续跑完成后可直接拼接所有 *_entity_partial_*.csv 文件重建批量 eval 报告，
    无需重新训练。
    """
    df = pred_test_df.copy()
    label_vals = pd.to_numeric(df["label_value"], errors="coerce").fillna(0).to_numpy(dtype=float)
    pred_vals = pd.to_numeric(df["pred_value"], errors="coerce").fillna(0).to_numpy(dtype=float)
    flags = compute_flags(label_vals, pred_vals)

    df["row_cnt"] = 1.0
    df["nonzero_cnt"] = (np.abs(label_vals) > 1e-12).astype(float)
    df["abs_err_sum"] = np.abs(pred_vals - label_vals)
    df["sq_err_sum"] = (pred_vals - label_vals) ** 2
    df["abs_actual_sum"] = np.abs(label_vals)
    df["abs_err_nonzero_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(pred_vals - label_vals), 0.0)
    df["ape_num_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(pred_vals - label_vals), 0.0)
    df["ape_den_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(label_vals), 0.0)
    df["smape_num_sum"] = 2.0 * np.abs(pred_vals - label_vals)
    df["smape_den_sum"] = np.abs(label_vals) + np.abs(pred_vals)
    df["strict_hit_sum"] = flags["strict_flag"].astype(float)
    df["standard_hit_sum"] = flags["standard_flag"].astype(float)
    df["loose_hit_sum"] = flags["loose_flag"].astype(float)
    df["ext_hit_sum"] = flags["ext_flag"].astype(float)

    out_path = item_dir / f"{output_prefix}_entity_partial_{run_ts}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


def generate_same_structure_report(
    *,
    summary: Dict,
    sample_key_columns: List[str],
    time_column: str,
    client: Optional[bigquery.Client] = None,
    source_table: str = "",
    out_dir: Path,
    run_ts: str,
    model_key: str,
    model_label: str,
    test_split_name: str = "test",
    entity_id_columns: Optional[List[str]] = None,
    requested_dimensions: Optional[List[str]] = None,
    include_total: bool = True,
    include_entity: bool = True,
    output_prefix: str = "evaluation",
) -> Dict[str, str]:
    entity_id_columns = entity_id_columns or []
    requested_dimensions = requested_dimensions or []
    required_group_cols = list(dict.fromkeys([*entity_id_columns, *requested_dimensions]))

    success = [r for r in summary.get("results", []) if r.get("status") == "SUCCESS"]
    if not success:
        raise RuntimeError(f"No successful {model_label} items.")

    pred_frames = []
    for r in success:
        local_candidates = [
            r.get("pred_csv_local"),
            r.get("pred_csv"),
            r.get("pred_train_test_csv_local"),
            r.get("pred_train_test_csv"),
        ]
        local_path = None
        for c in local_candidates:
            if not c:
                continue
            p = Path(str(c))
            if p.exists():
                local_path = p
                break

        if local_path is None:
            raise RuntimeError("Missing local prediction artifact for report generation.")

        if r.get("pred_csv_local") or r.get("pred_csv"):
            df = pd.read_csv(local_path)
        else:
            df = pd.read_csv(local_path)

        required_cols = set(sample_key_columns) | {"label_value", "pred_value"}
        missing_cols = [c for c in sorted(required_cols) if c not in df.columns]
        if missing_cols:
            raise RuntimeError(f"Prediction artifact missing required columns: {missing_cols}")

        if time_column in df.columns:
            try:
                df[time_column] = pd.to_datetime(df[time_column])
            except Exception:
                # 对非日期型时间键（例如 week_no）保留原值，不阻断报告生成。
                pass

        if "model_name" not in df.columns:
            df["model_name"] = str(r.get("model_name", "UNKNOWN"))
        if "entity_id_json" not in df.columns:
            df["entity_id_json"] = str(r.get("entity_id_json", "{}"))
        if "data_split" not in df.columns:
            df["data_split"] = str(test_split_name)

        # Recover grouping columns from entity_id_json when they are not present in prediction artifacts.
        if required_group_cols:
            def _parse_entity_meta(raw: object) -> Dict[str, object]:
                if isinstance(raw, dict):
                    return raw
                try:
                    return json.loads(str(raw))
                except Exception:
                    return {}

            entity_meta_series = df["entity_id_json"].apply(_parse_entity_meta)
            for col in required_group_cols:
                if col not in df.columns:
                    df[col] = entity_meta_series.apply(lambda d, k=col: d.get(k))

        kept_cols = [
            *sample_key_columns,
            *required_group_cols,
            "label_value",
            "pred_value",
            "model_name",
            "entity_id_json",
            "data_split",
        ]
        kept_cols = list(dict.fromkeys(kept_cols))
        pred_frames.append(df[kept_cols].copy())
    pred_all = pd.concat(pred_frames, ignore_index=True)

    merged = pred_all.copy()

    label_vals = pd.to_numeric(merged["label_value"], errors="coerce").fillna(0).to_numpy(dtype=float)
    pred_vals = pd.to_numeric(merged["pred_value"], errors="coerce").fillna(0).to_numpy(dtype=float)
    flags = compute_flags(label_vals, pred_vals)

    merged["row_cnt"] = 1.0
    merged["nonzero_cnt"] = (np.abs(label_vals) > 1e-12).astype(float)
    merged["abs_err_sum"] = np.abs(pred_vals - label_vals)
    merged["sq_err_sum"] = (pred_vals - label_vals) ** 2
    merged["abs_actual_sum"] = np.abs(label_vals)
    merged["abs_err_nonzero_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(pred_vals - label_vals), 0.0)
    merged["ape_num_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(pred_vals - label_vals), 0.0)
    merged["ape_den_sum"] = np.where(np.abs(label_vals) > 1e-12, np.abs(label_vals), 0.0)
    merged["smape_num_sum"] = 2.0 * np.abs(pred_vals - label_vals)
    merged["smape_den_sum"] = np.abs(label_vals) + np.abs(pred_vals)
    merged["strict_hit_sum"] = flags["strict_flag"].astype(float)
    merged["standard_hit_sum"] = flags["standard_flag"].astype(float)
    merged["loose_hit_sum"] = flags["loose_flag"].astype(float)
    merged["ext_hit_sum"] = flags["ext_flag"].astype(float)

    existing_cols = set(merged.columns)
    default_dims = [c for c in entity_id_columns if c in existing_cols]
    requested_existing = [c for c in requested_dimensions if c in existing_cols]
    invalid_requested = [c for c in requested_dimensions if c not in existing_cols]
    if invalid_requested:
        print(f"[WARN] evaluation.requested_dimensions not found and skipped: {invalid_requested}", flush=True)

    if requested_dimensions:
        # Use union(default_dims, requested_dimensions) instead of intersection.
        # Keep stable order and remove duplicates.
        effective_dims = []
        seen = set()
        for c in [*default_dims, *requested_existing]:
            if c not in seen:
                seen.add(c)
                effective_dims.append(c)
        if not requested_existing:
            print("[WARN] no valid requested dimensions found in prediction output; fallback to default dimensions", flush=True)
    else:
        effective_dims = default_dims.copy()

    if not effective_dims:
        print("[WARN] no effective evaluation dimensions resolved; dimension-level table will not be generated", flush=True)

    # Keep metric columns grouped for easier reading in exported CSVs.
    metric_cols = [
        # Error magnitude
        "MAE",
        "RMSE",
        "MAE_nonzero",
        # Percentage/rate errors
        "MAPE_pct",
        "MAPE_nonzero_pct",
        "WAPE_pct",
        "sMAPE_pct",
        # Accuracy flags
        "accuracy_strict_pct",
        "accuracy_standard_pct",
        "accuracy_loose_pct",
        "accuracy_ext_pct",
    ]

    agg_value_cols = [
        "row_cnt",
        "nonzero_cnt",
        "abs_err_sum",
        "sq_err_sum",
        "abs_actual_sum",
        "abs_err_nonzero_sum",
        "ape_num_sum",
        "ape_den_sum",
        "smape_num_sum",
        "smape_den_sum",
        "strict_hit_sum",
        "standard_hit_sum",
        "loose_hit_sum",
        "ext_hit_sum",
    ]

    def _build_agg(level_name: str, group_cols: List[str]) -> pd.DataFrame:
        if not group_cols:
            grouped = merged[agg_value_cols].sum().to_frame().T
        else:
            grouped = merged.groupby(group_cols, dropna=False, as_index=False)[agg_value_cols].sum()
        grouped["level"] = level_name
        return grouped

    raw_frames: List[pd.DataFrame] = []
    if include_total:
        raw_frames.append(_build_agg("total", ["data_split"]))

    if include_entity:
        entity_group_cols = ["data_split"]
        entity_group_cols.extend([c for c in entity_id_columns if c in existing_cols])
        # Always retain entity identity text fields in entity-level outputs when available.
        if "model_name" in existing_cols:
            entity_group_cols.append("model_name")
        if "entity_id_json" in existing_cols:
            entity_group_cols.append("entity_id_json")
        entity_group_cols = list(dict.fromkeys(entity_group_cols))
        raw_frames.append(_build_agg("entity", entity_group_cols))

    if effective_dims:
        raw_frames.append(_build_agg("dimension", ["data_split", *effective_dims]))

    if not raw_frames:
        raise RuntimeError("No evaluation output enabled. At least one of include_total/include_entity/dimension must be available")

    raw_df = pd.concat(raw_frames, ignore_index=True)

    def _metrics_from_raw(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        eps = 1e-12
        out["MAE"] = out["abs_err_sum"] / out["row_cnt"].replace(0, np.nan)
        out["RMSE"] = np.sqrt(out["sq_err_sum"] / out["row_cnt"].replace(0, np.nan))
        out["MAPE_pct"] = np.where(out["ape_den_sum"] > eps, out["ape_num_sum"] / out["ape_den_sum"] * 100.0, np.nan)
        out["MAPE_nonzero_pct"] = out["MAPE_pct"]
        out["MAE_nonzero"] = np.where(out["nonzero_cnt"] > eps, out["abs_err_nonzero_sum"] / out["nonzero_cnt"], np.nan)
        out["WAPE_pct"] = np.where(out["abs_actual_sum"] > eps, out["abs_err_sum"] / out["abs_actual_sum"] * 100.0, 0.0)
        out["sMAPE_pct"] = np.where(out["smape_den_sum"] > eps, out["smape_num_sum"] / out["smape_den_sum"] * 100.0, np.nan)
        out["accuracy_strict_pct"] = np.where(out["row_cnt"] > eps, out["strict_hit_sum"] / out["row_cnt"] * 100.0, np.nan)
        out["accuracy_standard_pct"] = np.where(out["row_cnt"] > eps, out["standard_hit_sum"] / out["row_cnt"] * 100.0, np.nan)
        out["accuracy_loose_pct"] = np.where(out["row_cnt"] > eps, out["loose_hit_sum"] / out["row_cnt"] * 100.0, np.nan)
        out["accuracy_ext_pct"] = np.where(out["row_cnt"] > eps, out["ext_hit_sum"] / out["row_cnt"] * 100.0, np.nan)
        return out

    metrics_df = _metrics_from_raw(raw_df)

    # Keep output columns easy to read: dimensions first, then metrics/raw values.
    dim_cols_priority = [
        "level",
        "data_split",
        *entity_id_columns,
        *effective_dims,
        "model_name",
        "entity_id_json",
    ]
    dim_template_cols: List[str] = []
    seen_dim_cols = set()
    for c in dim_cols_priority:
        if c not in seen_dim_cols:
            seen_dim_cols.add(c)
            dim_template_cols.append(c)

    def _apply_dim_template(df: pd.DataFrame, template_cols: List[str]) -> pd.DataFrame:
        out = df.copy()
        for c in template_cols:
            if c not in out.columns:
                out[c] = pd.NA
        return out

    metrics_view_df = _apply_dim_template(metrics_df, dim_template_cols)
    metrics_view_df = metrics_view_df[[*dim_template_cols, *metric_cols]].copy()

    raw_view_df = _apply_dim_template(raw_df, dim_template_cols)
    raw_view_cols = [*dim_template_cols, *[c for c in agg_value_cols if c in raw_view_df.columns]]
    raw_view_df = raw_view_df[raw_view_cols].copy()

    raw_csv = out_dir / f"{output_prefix}_agg_numerators_denominators_{run_ts}.csv"
    raw_view_df.to_csv(raw_csv, index=False, encoding="utf-8")
    metrics_csv = out_dir / f"{output_prefix}_metrics_by_group_{run_ts}.csv"
    metrics_view_df.to_csv(metrics_csv, index=False, encoding="utf-8")

    output_paths = {
        "raw_agg_csv": str(raw_csv),
        "metrics_csv": str(metrics_csv),
    }

    print(
        "[INFO] evaluation_output="
        f"prefix={output_prefix}, include_total={include_total}, include_entity={include_entity}, effective_dims={effective_dims}",
        flush=True,
    )
    return output_paths
