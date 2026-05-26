"""决策树建模产物的 BigQuery 写入模块。

包含追加写入（append）辅助函数：
预测明细、模型元数据、特征重要性结果、实体级分 split 指标、run 级批量评估指标。
"""

from __future__ import annotations

import os
from typing import Dict, List

import pandas as pd
from google.cloud import bigquery


def append_train_test_predictions_to_bq(
    client: bigquery.Client,
    table_id: str,
    pred_all_df: pd.DataFrame,
    feature_cols: List[str],
    config_name: str,
    run_id: str,
    run_tag: str,
    source_ref: str,
    gcs_output_uri: str,
    algorithm_name: str,
    algorithm_version: str,
) -> int:
    if not table_id.strip():
        return 0

    df_out = pred_all_df.copy()
    df_out["run_id"] = run_id
    df_out["run_ts"] = pd.Timestamp.utcnow()
    df_out["model_type"] = algorithm_name
    df_out["model_version"] = algorithm_version
    df_out["source_table"] = source_ref
    df_out["abs_error"] = pd.to_numeric(df_out["error"], errors="coerce").abs()
    df_out["feature_count"] = len(feature_cols)
    df_out["config_name"] = config_name
    df_out["gcs_run_uri"] = f"{gcs_output_uri.rstrip('/')}/{run_tag}/"

    ordered_cols = [
        "run_id",
        "run_ts",
        "model_type",
        "model_version",
        "model_name",
        "entity_id_json",
        "source_table",
        "sample_key_json",
        "data_split",
        "label_value",
        "pred_value",
        "error",
        "abs_error",
        "feature_count",
        "config_name",
        "gcs_run_uri",
    ]
    df_out = df_out[ordered_cols]

    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    client.load_table_from_dataframe(df_out, table_id, job_config=job_config).result()
    return int(len(df_out))


def append_model_metadata_to_bq(
    client: bigquery.Client,
    table_id: str,
    rows: List[Dict],
) -> int:
    if not table_id.strip() or not rows:
        return 0

    df_out = pd.DataFrame(rows)
    # Keep BQ TIMESTAMP column type stable even if upstream row payload carries run_ts as string.
    parsed_run_ts = pd.to_datetime(df_out.get("run_ts"), errors="coerce", utc=True)
    df_out["run_ts"] = parsed_run_ts.fillna(pd.Timestamp.utcnow())

    ordered_cols = [
        "run_id",
        "run_ts",
        "model_name",
        "entity_id_json",
        "model_type",
        "model_version",
        "source_table",
        "feature_count",
        "features_json",
        "params_json",
        "model_pkl_path",
        "model_metadata_json_path",
        "config_name",
        "gcs_run_uri",
    ]
    df_out = df_out[ordered_cols]

    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    client.load_table_from_dataframe(df_out, table_id, job_config=job_config).result()
    return int(len(df_out))


def append_feature_importance_to_bq(
    client: bigquery.Client,
    table_id: str,
    model_name: str,
    entity_id_json: str,
    feat_imp_df: pd.DataFrame,
    config_name: str,
    feature_importance_csv_gcs: str,
    run_id: str,
    run_tag: str,
    source_ref: str,
    gcs_output_uri: str,
    algorithm_name: str,
    algorithm_version: str,
) -> int:
    if not table_id.strip() or feat_imp_df.empty:
        return 0

    df_out = feat_imp_df.copy()
    df_out["run_id"] = run_id
    df_out["run_ts"] = pd.Timestamp.utcnow()
    df_out["model_type"] = algorithm_name
    df_out["model_version"] = algorithm_version
    df_out["model_name"] = model_name
    df_out["entity_id_json"] = entity_id_json
    df_out["source_table"] = source_ref
    df_out["config_name"] = config_name
    df_out["gcs_run_uri"] = f"{gcs_output_uri.rstrip('/')}/{run_tag}/"
    df_out["feature_importance_csv_path"] = feature_importance_csv_gcs

    ordered_cols = [
        "run_id",
        "run_ts",
        "model_type",
        "model_version",
        "model_name",
        "entity_id_json",
        "source_table",
        "feature",
        "model_importance",
        "feature_rank",
        "config_name",
        "gcs_run_uri",
        "feature_importance_csv_path",
    ]
    df_out = df_out[ordered_cols]

    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    client.load_table_from_dataframe(df_out, table_id, job_config=job_config).result()
    return int(len(df_out))


def append_metrics_by_split_to_bq(
    client: bigquery.Client,
    table_id: str,
    metrics_split_csv: str,
    model_name: str,
    entity_id_json: str,
    config_name: str,
    run_id: str,
    source_ref: str,
    algorithm_name: str,
    algorithm_version: str,
) -> int:
    """实体级分 split 指标写入 BQ（dt_metrics_by_split）。

    读取 evaluate_and_save_outputs 写出的 metrics_by_split CSV，追加运行元数据后写入 BQ。
    CSV 列：data_split + 11 个指标列（compute_metrics 输出）。
    """
    if not table_id.strip() or not metrics_split_csv:
        return 0
    if not os.path.exists(metrics_split_csv):
        return 0

    df_out = pd.read_csv(metrics_split_csv)
    if df_out.empty:
        return 0

    df_out["run_id"] = run_id
    df_out["run_ts"] = pd.Timestamp.utcnow()
    df_out["model_type"] = algorithm_name
    df_out["model_version"] = algorithm_version
    df_out["model_name"] = model_name
    df_out["entity_id_json"] = entity_id_json
    df_out["source_table"] = source_ref
    df_out["config_name"] = config_name

    ordered_cols = [
        "run_id",
        "run_ts",
        "model_type",
        "model_version",
        "model_name",
        "entity_id_json",
        "source_table",
        "config_name",
        "data_split",
        "MAE",
        "RMSE",
        "MAE_nonzero",
        "MAPE_pct",
        "MAPE_nonzero_pct",
        "WAPE_pct",
        "sMAPE_pct",
        "accuracy_strict_pct",
        "accuracy_standard_pct",
        "accuracy_loose_pct",
        "accuracy_ext_pct",
    ]
    df_out = df_out[ordered_cols]

    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    client.load_table_from_dataframe(df_out, table_id, job_config=job_config).result()
    return int(len(df_out))


def append_run_eval_metrics_to_bq(
    client: bigquery.Client,
    table_id: str,
    metrics_csv: str,
    run_id: str,
    source_ref: str,
    algorithm_name: str,
    algorithm_version: str,
    config_name: str,
) -> int:
    """run 级批量评估指标写入 BQ（dt_run_eval_metrics）。

    读取 generate_same_structure_report 输出的 metrics_by_group CSV，
    追加运行元数据后写入 BQ。CSV 中可能含额外维度列，按 schema 列表过滤。
    """
    if not table_id.strip() or not metrics_csv:
        return 0
    if not os.path.exists(metrics_csv):
        return 0

    df_out = pd.read_csv(metrics_csv)
    if df_out.empty:
        return 0

    df_out["run_id"] = run_id
    df_out["run_ts"] = pd.Timestamp.utcnow()
    df_out["model_type"] = algorithm_name
    df_out["model_version"] = algorithm_version
    df_out["source_table"] = source_ref
    df_out["config_name"] = config_name

    # 对齐 RUN_EVAL_METRICS_SCHEMA_DEFS 列顺序；CSV 可能含额外维度列，仅保留 schema 列
    ordered_cols = [
        "run_id",
        "run_ts",
        "model_type",
        "model_version",
        "source_table",
        "config_name",
        "level",
        "data_split",
        "model_name",
        "entity_id_json",
        "MAE",
        "RMSE",
        "MAE_nonzero",
        "MAPE_pct",
        "MAPE_nonzero_pct",
        "WAPE_pct",
        "sMAPE_pct",
        "accuracy_strict_pct",
        "accuracy_standard_pct",
        "accuracy_loose_pct",
        "accuracy_ext_pct",
    ]
    available_cols = [c for c in ordered_cols if c in df_out.columns]
    df_out = df_out[available_cols]

    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
    client.load_table_from_dataframe(df_out, table_id, job_config=job_config).result()
    return int(len(df_out))
