"""模型评估模块。

包含指标计算与评估产物落盘函数，
统一生成 metrics_by_split CSV 文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from modeling.batch_reporting import compute_metrics


def evaluate_and_save_outputs(
    item_dir: Path,
    run_ts: str,
    model_key: str,
    split_predictions: Dict[str, pd.DataFrame],
) -> Dict:
    if not split_predictions:
        raise ValueError("split_predictions is empty")

    metrics_by_split = {
        split_name: compute_metrics(df["label_value"], df["pred_value"]) for split_name, df in split_predictions.items()
    }

    if "test" in metrics_by_split:
        metrics = metrics_by_split["test"]
    else:
        first_split = next(iter(metrics_by_split.keys()))
        metrics = metrics_by_split[first_split]

    metrics_split_csv = item_dir / f"{model_key}_metrics_by_split_{run_ts}.csv"
    split_rows = [{"data_split": split_name, **m} for split_name, m in metrics_by_split.items()]
    pd.DataFrame(split_rows).to_csv(metrics_split_csv, index=False)

    return {
        "metrics": metrics,
        "metrics_split_csv": metrics_split_csv,
    }
