from __future__ import annotations

from typing import Dict, List, Tuple

_PRED_SCHEMA_PREFIX: List[Tuple[str, str]] = [
    ("run_id", "STRING"),
    ("run_ts", "TIMESTAMP"),
    ("model_type", "STRING"),
    ("model_version", "STRING"),
    ("model_name", "STRING"),
    ("entity_id_json", "STRING"),
    ("source_table", "STRING"),
    ("sample_key_json", "STRING"),
]

_PRED_SCHEMA_SUFFIX: List[Tuple[str, str]] = [
    ("data_split", "STRING"),
    ("label_value", "FLOAT64"),
    ("pred_value", "FLOAT64"),
    ("error", "FLOAT64"),
    ("abs_error", "FLOAT64"),
    ("feature_count", "INT64"),
    ("config_name", "STRING"),
    ("gcs_run_uri", "STRING"),
]

_SAMPLE_KEY_TYPE_HINTS: Dict[str, str] = {
    "week_no": "INT64",
}


def build_pred_schema_defs(sample_key_columns: List[str]) -> List[Tuple[str, str]]:
    """Build prediction table schema dynamically from sample_key_columns.

    All sample IDs are externalized as explicit columns, with optional type hints.
    Unrecognized sample keys default to STRING.
    """

    dynamic_sample_fields: List[Tuple[str, str]] = []
    for col in sample_key_columns:
        field_type = _SAMPLE_KEY_TYPE_HINTS.get(col, "STRING")
        dynamic_sample_fields.append((col, field_type))
    return [*_PRED_SCHEMA_PREFIX, *dynamic_sample_fields, *_PRED_SCHEMA_SUFFIX]


# 模型元数据表 Schema 定义
MODEL_META_SCHEMA_DEFS: List[Tuple[str, str]] = [
    ("run_id", "STRING"),
    ("run_ts", "TIMESTAMP"),
    ("model_name", "STRING"),
    ("entity_id_json", "STRING"),
    ("model_type", "STRING"),
    ("model_version", "STRING"),
    ("source_table", "STRING"),
    ("feature_count", "INT64"),
    ("features_json", "STRING"),
    ("params_json", "STRING"),
    ("model_pkl_path", "STRING"),
    ("model_metadata_json_path", "STRING"),
    ("config_name", "STRING"),
    ("gcs_run_uri", "STRING"),
]


# 特征重要性明细表 Schema 定义
FEAT_IMP_SCHEMA_DEFS: List[Tuple[str, str]] = [
    ("run_id", "STRING"),
    ("run_ts", "TIMESTAMP"),
    ("model_type", "STRING"),
    ("model_version", "STRING"),
    ("model_name", "STRING"),
    ("entity_id_json", "STRING"),
    ("source_table", "STRING"),
    ("feature", "STRING"),
    ("model_importance", "FLOAT64"),
    ("feature_rank", "INT64"),
    ("config_name", "STRING"),
    ("gcs_run_uri", "STRING"),
    ("feature_importance_csv_path", "STRING"),
]


# 实体级分 split 指标表 Schema 定义
# 每行 = 一个 (run_id, entity, split) 三元组的 11 个指标
METRICS_BY_SPLIT_SCHEMA_DEFS: List[Tuple[str, str]] = [
    ("run_id", "STRING"),
    ("run_ts", "TIMESTAMP"),
    ("model_type", "STRING"),
    ("model_version", "STRING"),
    ("model_name", "STRING"),
    ("entity_id_json", "STRING"),
    ("source_table", "STRING"),
    ("config_name", "STRING"),
    ("data_split", "STRING"),
    ("MAE", "FLOAT64"),
    ("RMSE", "FLOAT64"),
    ("MAE_nonzero", "FLOAT64"),
    ("MAPE_pct", "FLOAT64"),
    ("MAPE_nonzero_pct", "FLOAT64"),
    ("WAPE_pct", "FLOAT64"),
    ("sMAPE_pct", "FLOAT64"),
    ("accuracy_strict_pct", "FLOAT64"),
    ("accuracy_standard_pct", "FLOAT64"),
    ("accuracy_loose_pct", "FLOAT64"),
    ("accuracy_ext_pct", "FLOAT64"),
]


# run 级别批量评估指标表 Schema 定义
# 每行 = 一个 (run_id, level, group_key) 的聚合指标；level 可为 total/entity/dimension
RUN_EVAL_METRICS_SCHEMA_DEFS: List[Tuple[str, str]] = [
    ("run_id", "STRING"),
    ("run_ts", "TIMESTAMP"),
    ("model_type", "STRING"),
    ("model_version", "STRING"),
    ("source_table", "STRING"),
    ("config_name", "STRING"),
    ("level", "STRING"),
    ("data_split", "STRING"),
    ("model_name", "STRING"),
    ("entity_id_json", "STRING"),
    ("MAE", "FLOAT64"),
    ("RMSE", "FLOAT64"),
    ("MAE_nonzero", "FLOAT64"),
    ("MAPE_pct", "FLOAT64"),
    ("MAPE_nonzero_pct", "FLOAT64"),
    ("WAPE_pct", "FLOAT64"),
    ("sMAPE_pct", "FLOAT64"),
    ("accuracy_strict_pct", "FLOAT64"),
    ("accuracy_standard_pct", "FLOAT64"),
    ("accuracy_loose_pct", "FLOAT64"),
    ("accuracy_ext_pct", "FLOAT64"),
]
