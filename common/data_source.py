"""建模输入数据源公共工具。

本模块将数据源相关逻辑从主脚本中抽离，主脚本只负责编排流程。

支持两种来源：
1) BigQuery 特征宽表
    - 按表结构识别可用特征列
    - 按 entity（entity_id_columns 配置的维度组合）和时间窗切分 train/test 数据

2) CSV（GCS URI 或本地路径）
    - 读取并校验必需列
    - 按 dataframe 列识别可用特征
    - 在内存中按 entity（entity_id_columns 配置的维度组合）和时间窗切分 train/test 数据

推荐调用顺序：
数据源选择 -> 可用特征检测 -> 按 entity 获取 train/test 切片。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from google.cloud import bigquery, storage


def parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """解析 gs://bucket/path，返回 bucket 和对象前缀/路径。"""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    body = uri[5:]
    parts = body.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix.rstrip("/")


def download_gcs_file_to_local(gcs_uri: str, local_path: Path, project_id: str) -> Path:
    """下载单个 GCS 对象到本地路径（CSV 模式使用）。"""
    bucket_name, object_name = parse_gcs_uri(gcs_uri)
    if not object_name:
        raise ValueError(f"Invalid GCS object URI: {gcs_uri}")
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path))
    return local_path


def load_source_csv_dataframe(
    source_csv_uri: str = "",
    source_csv_local_path: str = "",
    sample_key_columns: List[str] | None = None,
    label_column: str = "item_qty",
    *,
    out_dir: Path,
    run_ts: str,
    project_id: str,
) -> pd.DataFrame:
    """从 CSV 读取源数据（支持 GCS URI 或本地路径）。"""
    if source_csv_uri and source_csv_local_path:
        raise ValueError("Provide only one of source_csv_uri or source_csv_local_path.")

    if source_csv_uri:
        local_csv = out_dir / "source" / f"source_{run_ts}.csv"
        csv_path = download_gcs_file_to_local(source_csv_uri, local_csv, project_id=project_id)
    elif source_csv_local_path:
        csv_path = Path(source_csv_local_path)
        if (not csv_path.exists() or csv_path.is_dir()) and csv_path.suffix == "":
            csv_with_suffix = csv_path.with_suffix(".csv")
            if csv_with_suffix.exists():
                print(
                    f"[INFO] Local source not found, fallback to: {csv_with_suffix}",
                    flush=True,
                )
                csv_path = csv_with_suffix
    else:
        raise ValueError("CSV source is empty.")

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV source not found: {csv_path}")

    df = pd.read_csv(csv_path)
    sample_key_columns = sample_key_columns or ["item_no", "day_date"]
    required_cols = set(sample_key_columns) | {label_column}
    missing_required = [col for col in sorted(required_cols) if col not in df.columns]
    if missing_required:
        raise ValueError(f"CSV source missing required columns: {missing_required}")

    if "day_date" in df.columns:
        df["day_date"] = pd.to_datetime(df["day_date"], errors="coerce")
        df = df[df["day_date"].notna()].copy()

    if "item_no" in df.columns:
        df["item_no"] = df["item_no"].astype(str).str.zfill(8)

    df = df.reset_index(drop=True)
    return df


def _build_time_mask(
    series: pd.Series,
    *,
    start: str,
    end: str,
) -> pd.Series:
    num_series = pd.to_numeric(series, errors="coerce")
    start_num = pd.to_numeric(pd.Series([start]), errors="coerce").iloc[0]
    end_num = pd.to_numeric(pd.Series([end]), errors="coerce").iloc[0]
    if pd.notna(start_num) and pd.notna(end_num) and num_series.notna().all():
        return (num_series >= float(start_num)) & (num_series <= float(end_num))

    dt_series = pd.to_datetime(series, errors="coerce")
    if dt_series.notna().all():
        return (dt_series >= pd.to_datetime(start)) & (dt_series <= pd.to_datetime(end))

    return (series.astype(str) >= str(start)) & (series.astype(str) <= str(end))


def _build_bq_filter_clauses(source_filters: Optional[Dict[str, str]]) -> List[str]:
    clauses: List[str] = []
    if not source_filters:
        return clauses
    for col, raw in source_filters.items():
        col_name = str(col).strip()
        if not col_name:
            continue
        escaped = str(raw).replace("'", "''")
        clauses.append(f"{col_name} = '{escaped}'")
    return clauses


def _build_bq_time_between_clause(*, time_column: str, start: str, end: str) -> str:
    """Build a BigQuery BETWEEN clause that matches numeric/date/string windows safely."""
    start_num = pd.to_numeric(pd.Series([start]), errors="coerce").iloc[0]
    end_num = pd.to_numeric(pd.Series([end]), errors="coerce").iloc[0]
    if pd.notna(start_num) and pd.notna(end_num):
        # week_no-like windows are numeric and should not be quoted.
        return f"{time_column} BETWEEN {int(start_num)} AND {int(end_num)}"

    start_escaped = str(start).replace("'", "''")
    end_escaped = str(end).replace("'", "''")
    return f"{time_column} BETWEEN '{start_escaped}' AND '{end_escaped}'"


def validate_bq_source_non_empty(
    client: bigquery.Client,
    *,
    source_table: str,
    time_column: str,
    train_start: str,
    test_end: str,
    source_filters: Optional[Dict[str, str]] = None,
) -> None:
    """校验 BQ 源数据在时间窗（含可选过滤条件）下非空。"""
    where_parts = [_build_bq_time_between_clause(time_column=time_column, start=train_start, end=test_end)]
    where_parts.extend(_build_bq_filter_clauses(source_filters))
    query = f"""
    SELECT 1 AS has_data
    FROM `{source_table}`
    WHERE {' AND '.join(where_parts)}
    LIMIT 1
    """
    probe_df = client.query(query).to_dataframe()
    if probe_df.empty:
        filters_text = " and ".join(_build_bq_filter_clauses(source_filters))
        suffix = f"; filters=({filters_text})" if filters_text else ""
        raise RuntimeError(
            "Source precheck failed: no rows from BQ source "
            f"within window {train_start}~{test_end}{suffix}"
        )


def fetch_data_from_bq(
    entity_filter: Dict[str, str],
    client: bigquery.Client,
    feature_cols: List[str],
    sample_key_columns: List[str],
    entity_id_columns: List[str],
    model_name_columns: List[str],
    label_column: str = "item_qty",
    time_column: str = "day_date",
    *,
    source_table: str,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    source_filters: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """从 BigQuery 源表读取单个训练实体的 train/test 时间窗数据。"""
    key_columns = list(dict.fromkeys(sample_key_columns + entity_id_columns + model_name_columns))
    select_cols = [*key_columns, f"{label_column} AS label_value", *feature_cols]
    where_parts: List[str] = []
    for col in entity_id_columns:
        escaped = str(entity_filter[col]).replace("'", "''")
        where_parts.append(f"{col} = '{escaped}'")
    where_parts.extend(_build_bq_filter_clauses(source_filters))
    where_parts.append(_build_bq_time_between_clause(time_column=time_column, start=train_start, end=test_end))

    query = f"""
    SELECT
      {', '.join(select_cols)}
    FROM `{source_table}`
    WHERE {' AND '.join(where_parts)}
    ORDER BY {time_column}
    """
    df_all = client.query(query).to_dataframe()
    train_mask = _build_time_mask(df_all[time_column], start=train_start, end=train_end)
    test_mask = _build_time_mask(df_all[time_column], start=test_start, end=test_end)
    return df_all[train_mask].copy().reset_index(drop=True), df_all[test_mask].copy().reset_index(drop=True)


def fetch_data_from_dataframe(
    entity_filter: Dict[str, str],
    source_df: pd.DataFrame,
    feature_cols: List[str],
    sample_key_columns: List[str],
    entity_id_columns: List[str],
    model_name_columns: List[str],
    label_column: str = "item_qty",
    time_column: str = "day_date",
    *,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """从内存 dataframe 中读取单个训练实体的 train/test 时间窗数据。"""
    key_columns = list(dict.fromkeys(sample_key_columns + entity_id_columns + model_name_columns))
    selected_cols = [*key_columns, label_column, *feature_cols]
    df_all = source_df[selected_cols].copy()
    for col in entity_id_columns:
        df_all = df_all[df_all[col].astype(str) == str(entity_filter[col])]
    df_all = df_all.rename(columns={label_column: "label_value"})

    train_mask = _build_time_mask(df_all[time_column], start=train_start, end=train_end)
    test_mask = _build_time_mask(df_all[time_column], start=test_start, end=test_end)
    return df_all[train_mask].copy().reset_index(drop=True), df_all[test_mask].copy().reset_index(drop=True)


def get_available_features(client: bigquery.Client, requested_features: List[str], *, source_table: str) -> List[str]:
    """按 BigQuery 表结构筛选可用特征列。"""
    table = client.get_table(source_table)
    existing = {field.name for field in table.schema}
    available = [col for col in requested_features if col in existing]
    missing = [col for col in requested_features if col not in existing]
    if missing:
        print(f"[WARN] Missing columns skipped: {missing}", flush=True)
    print(f"[INFO] Using {len(available)} features.", flush=True)
    return available


def get_available_features_from_dataframe(source_df: pd.DataFrame, requested_features: List[str]) -> List[str]:
    """按 dataframe 列筛选可用特征列。"""
    existing = set(source_df.columns)
    available = [col for col in requested_features if col in existing]
    missing = [col for col in requested_features if col not in existing]
    if missing:
        print(f"[WARN] Missing columns skipped: {missing}", flush=True)
    print(f"[INFO] Using {len(available)} features.", flush=True)
    return available


def discover_entity_filters_from_dataframe(
    source_df: pd.DataFrame,
    entity_id_columns: List[str],
) -> List[Dict[str, str]]:
    """从 dataframe 中按 entity_id_columns 提取去重后的实体组合。"""
    missing = [col for col in entity_id_columns if col not in source_df.columns]
    if missing:
        raise ValueError(f"Entity discovery missing columns in dataframe: {missing}")

    keys_df = source_df[entity_id_columns].dropna().drop_duplicates().copy()
    for col in entity_id_columns:
        keys_df[col] = keys_df[col].astype(str).str.strip()
        if col == "item_no":
            keys_df[col] = keys_df[col].str.zfill(8)

    keys_df = keys_df.sort_values(entity_id_columns).reset_index(drop=True)
    entities: List[Dict[str, str]] = []
    for _, row in keys_df.iterrows():
        entities.append({col: str(row[col]) for col in entity_id_columns})
    return entities


def discover_entity_filters_from_bq(
    client: bigquery.Client,
    source_table: str,
    entity_id_columns: List[str],
    *,
    time_column: str,
    train_start: str,
    test_end: str,
    source_filters: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """从 BigQuery 源表提取去重后的实体组合（按运行时间窗裁剪）。"""
    select_cols = ", ".join(entity_id_columns)
    where_non_null = " AND ".join([f"{col} IS NOT NULL" for col in entity_id_columns])
    where_parts = [_build_bq_time_between_clause(time_column=time_column, start=train_start, end=test_end), where_non_null]
    where_parts.extend(_build_bq_filter_clauses(source_filters))
    query = f"""
    SELECT DISTINCT {select_cols}
    FROM `{source_table}`
    WHERE {' AND '.join(where_parts)}
    ORDER BY {select_cols}
    """
    keys_df = client.query(query).to_dataframe()
    return discover_entity_filters_from_dataframe(keys_df, entity_id_columns)
