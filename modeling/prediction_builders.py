"""预测数据构建模块。

包含将模型输出组织为标准 DataFrame 的辅助函数，
用于预测明细与特征重要性产物构建。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

import pandas as pd


def build_prediction_dataframes(
    sample_key_columns: List[str],
    model_name: str,
    entity_id_json: str,
    df_train_fit: pd.DataFrame,
    df_test: pd.DataFrame,
    y_train_fit,
    y_test,
    y_pred_train_fit,
    y_pred_test,
    df_validation: Optional[pd.DataFrame] = None,
    y_validation=None,
    y_pred_validation=None,
    split_label_map: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    split_label_map = split_label_map or {"train": "train", "validation": "validation", "test": "test"}

    pred_df = df_test[sample_key_columns].copy()
    pred_df["model_name"] = model_name
    pred_df["entity_id_json"] = entity_id_json
    pred_df["sample_key_json"] = pred_df[sample_key_columns].apply(
        lambda row: json.dumps({col: row[col] for col in sample_key_columns}, ensure_ascii=False, default=str),
        axis=1,
    )
    pred_df["label_value"] = y_test
    pred_df["pred_value"] = y_pred_test
    pred_df["error"] = y_test - y_pred_test

    pred_train_df = df_train_fit[sample_key_columns].copy()
    pred_train_df["data_split"] = split_label_map.get("train", "train")
    pred_train_df["model_name"] = model_name
    pred_train_df["entity_id_json"] = entity_id_json
    pred_train_df["sample_key_json"] = pred_train_df[sample_key_columns].apply(
        lambda row: json.dumps({col: row[col] for col in sample_key_columns}, ensure_ascii=False, default=str),
        axis=1,
    )
    pred_train_df["label_value"] = y_train_fit
    pred_train_df["pred_value"] = y_pred_train_fit
    pred_train_df["error"] = y_train_fit - y_pred_train_fit

    pred_test_df = pred_df.copy()
    pred_test_df["data_split"] = split_label_map.get("test", "test")

    pred_frames = [pred_train_df]
    if (
        df_validation is not None
        and y_validation is not None
        and y_pred_validation is not None
        and len(df_validation) > 0
    ):
        pred_val_df = df_validation[sample_key_columns].copy()
        pred_val_df["data_split"] = split_label_map.get("validation", "validation")
        pred_val_df["model_name"] = model_name
        pred_val_df["entity_id_json"] = entity_id_json
        pred_val_df["sample_key_json"] = pred_val_df[sample_key_columns].apply(
            lambda row: json.dumps({col: row[col] for col in sample_key_columns}, ensure_ascii=False, default=str),
            axis=1,
        )
        pred_val_df["label_value"] = y_validation
        pred_val_df["pred_value"] = y_pred_validation
        pred_val_df["error"] = y_validation - y_pred_validation
        pred_frames.append(pred_val_df)

    pred_frames.append(pred_test_df)

    pred_all_df = pd.concat(pred_frames, ignore_index=True)

    return pred_df, pred_all_df


def build_feature_importance_df(feature_cols: List[str], feature_importances) -> pd.DataFrame:
    feat_imp_df = pd.DataFrame(
        {
            "feature": feature_cols,
            "model_importance": feature_importances,
        }
    ).sort_values("model_importance", ascending=False).reset_index(drop=True)
    feat_imp_df["feature_rank"] = feat_imp_df.index + 1
    return feat_imp_df
