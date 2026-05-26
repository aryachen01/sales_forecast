"""决策树建模流程的预处理模块。

包含特征预处理辅助函数：将 train/test 的混合类型 DataFrame
转换为模型训练与推理所需的数值数组。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


def split_train_validation(
    df_train: pd.DataFrame,
    *,
    enabled: bool,
    validation_ratio: float,
    mode: str,
    time_column: str,
    random_seed: int,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """将训练集拆分为 train_fit 与可选 validation。"""
    if not enabled:
        return df_train.copy(), None

    if not 0 < validation_ratio < 1:
        raise ValueError("validation_ratio must be between 0 and 1")
    if len(df_train) < 2:
        raise ValueError("train rows too small for validation split")

    val_n = max(1, int(round(len(df_train) * validation_ratio)))
    val_n = min(val_n, len(df_train) - 1)
    mode_norm = str(mode).strip().lower()

    if mode_norm == "time_tail":
        if time_column not in df_train.columns:
            raise ValueError(f"time_tail mode requires time column '{time_column}' in train dataframe")
        ordered = df_train.sort_values(time_column).reset_index(drop=True)
        df_val = ordered.tail(val_n).copy()
        df_fit = ordered.iloc[:-val_n].copy()
        return df_fit, df_val

    if mode_norm != "random":
        raise ValueError("validation split mode must be one of: random, time_tail")

    val_idx = df_train.sample(n=val_n, random_state=random_seed).index
    df_val = df_train.loc[val_idx].copy()
    df_fit = df_train.drop(index=val_idx).copy()
    return df_fit, df_val


def prepare_train_test_features(
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    feature_cols: List[str],
    df_validation: Optional[pd.DataFrame] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
    """将混合类型特征转为模型可直接使用的数值数组。"""
    train_features = df_train[feature_cols].copy()
    test_features = df_test[feature_cols].copy()
    val_features = df_validation[feature_cols].copy() if df_validation is not None else None

    for col in feature_cols:
        train_num = pd.to_numeric(train_features[col], errors="coerce")
        test_num = pd.to_numeric(test_features[col], errors="coerce")
        val_num = pd.to_numeric(val_features[col], errors="coerce") if val_features is not None else None

        train_num_rate = float(train_num.notna().mean()) if len(train_num) else 0.0
        test_num_rate = float(test_num.notna().mean()) if len(test_num) else 0.0
        val_num_rate = float(val_num.notna().mean()) if val_num is not None and len(val_num) else 0.0
        val_numeric_ok = val_features is None or val_num_rate >= 0.8
        if train_num_rate >= 0.8 and test_num_rate >= 0.8 and val_numeric_ok:
            train_features[col] = train_num.fillna(0)
            test_features[col] = test_num.fillna(0)
            if val_features is not None and val_num is not None:
                val_features[col] = val_num.fillna(0)
            continue

        train_cat = train_features[col].astype("string").fillna("__MISSING__")
        test_cat = test_features[col].astype("string").fillna("__MISSING__")
        val_cat = val_features[col].astype("string").fillna("__MISSING__") if val_features is not None else None
        categories = pd.Index(train_cat.unique())
        cat_to_id = {value: idx for idx, value in enumerate(categories)}
        train_features[col] = train_cat.map(cat_to_id).fillna(-1)
        test_features[col] = test_cat.map(cat_to_id).fillna(-1)
        if val_features is not None and val_cat is not None:
            val_features[col] = val_cat.map(cat_to_id).fillna(-1)

    x_train = train_features.to_numpy(dtype=np.float32)
    x_test = test_features.to_numpy(dtype=np.float32)
    x_val = val_features.to_numpy(dtype=np.float32) if val_features is not None else None
    y_train = pd.to_numeric(df_train["label_value"], errors="coerce").fillna(0).to_numpy(dtype=np.float64)
    y_test = pd.to_numeric(df_test["label_value"], errors="coerce").fillna(0).to_numpy(dtype=np.float64)
    y_val = (
        pd.to_numeric(df_validation["label_value"], errors="coerce").fillna(0).to_numpy(dtype=np.float64)
        if df_validation is not None
        else None
    )
    return x_train, y_train, x_test, y_test, x_val, y_val
