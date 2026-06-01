"""衍生功能模块 (add_func): 模型预测趋势图与周汇总明细。

按 time_column（如 week_no，格式 yyyyww）聚合真实销量和预测销量，生成：
  - 周汇总 CSV  → item_dir / {model_key}_weekly_trend_{run_ts}.csv
  - 趋势组合图  → item_dir / {model_key}_weekly_trend_{run_ts}.png
      横轴: week_no（yyyyww，不连续整数，等距排列）
      柱状图: 预测销量，按 data_split 分色（train/validation/test）
      折线图: 真实销量（汇总所有 split）

Usage (from pipeline.py):
    from modeling.add_func_trend_chart import save_weekly_trend
    save_weekly_trend(
        pred_all_df=pred_all_df,
        item_dir=item_dir,
        time_column=time_column,
        model_name=model_name,
        model_key=runtime.algorithm_name.lower(),
        run_ts=runtime.run_ts,
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

import platform

import matplotlib
matplotlib.use("Agg")   # non-interactive backend, safe for pipeline contexts
import matplotlib.pyplot as plt
import matplotlib.font_manager as _fm
import numpy as np
import pandas as pd

# ── CJK font setup (Chinese characters in model_name / product_cluster) ──────
def _configure_cjk_font() -> None:
    """Pick the first available CJK-capable font and apply it globally."""
    candidates = (
        ["Microsoft YaHei", "SimHei", "SimSun"]           # Windows
        if platform.system() == "Windows"
        else ["WenQuanYi Micro Hei", "Noto Sans CJK SC", "AR PL UMing CN"]
    )
    available = {f.name for f in _fm.fontManager.ttflist}
    for font in candidates:
        if font in available:
            plt.rcParams["font.family"] = font
            return
    # Fallback: suppress the missing-glyph warning rather than crash
    import warnings
    warnings.filterwarnings("ignore", message="Glyph.*missing from font")

_configure_cjk_font()

logger = logging.getLogger(__name__)

# ── Color palette ────────────────────────────────────────────────────────────
_SPLIT_COLORS: Dict[str, str] = {
    "train":      "#7BAFD4",   # steel blue
    "validation": "#F0A070",   # soft orange
    "test":       "#5CB85C",   # green
}
_DEFAULT_SPLIT_COLOR = "#AAAAAA"

_ACTUAL_COLOR     = "#1A1A2E"   # dark navy
_ACTUAL_LINEWIDTH = 2.0
_ACTUAL_MARKERSIZE = 4
_SPLIT_DRAW_ORDER = ["train", "validation", "test"]


# ── Internal helpers ─────────────────────────────────────────────────────────

def _make_weekly_summary(
    pred_all_df: pd.DataFrame,
    time_column: str,
) -> pd.DataFrame:
    """
    Aggregate by (time_column, data_split) → actual_sum, pred_sum, row_count.
    Returns rows sorted by time_column (string sort on yyyyww is lexicographic = chronological).
    """
    grp = (
        pred_all_df
        .groupby([time_column, "data_split"], sort=False)
        .agg(
            actual_sum=("label_value", "sum"),
            pred_sum=("pred_value", "sum"),
            row_count=(time_column, "count"),
        )
        .reset_index()
    )
    # Ensure week_no is stored as string for consistent labelling
    grp[time_column] = grp[time_column].astype(str)
    grp = grp.sort_values(time_column).reset_index(drop=True)
    return grp


def _draw_trend_chart(
    weekly_df: pd.DataFrame,
    time_column: str,
    model_name: str,
) -> plt.Figure:
    """
    Combo chart:
      - Bars  : pred_sum by data_split (different colours, non-overlapping date ranges)
      - Line  : actual_sum aggregated across all splits
    x-axis: equally-spaced sorted week labels (original yyyyww strings)
    """
    sorted_weeks: List[str] = sorted(weekly_df[time_column].unique())
    n_weeks = len(sorted_weeks)
    week_to_x: Dict[str, int] = {w: i for i, w in enumerate(sorted_weeks)}
    x_pos = np.arange(n_weeks)

    # Actual totals (sum across splits per week)
    actual_by_week = (
        weekly_df.groupby(time_column)["actual_sum"]
        .sum()
        .reindex(sorted_weeks, fill_value=0)
    )

    # Figure sizing: wider when many weeks
    fig_w = max(14, n_weeks * 0.45)
    fig, ax = plt.subplots(figsize=(fig_w, 6))

    # Draw bars per split (train first so test bars are visually "on top" if they overlap — they generally don't)
    splits_in_data = set(weekly_df["data_split"].unique())
    split_order = [s for s in _SPLIT_DRAW_ORDER if s in splits_in_data]
    split_order += [s for s in splits_in_data if s not in split_order]

    for split in split_order:
        sub = weekly_df[weekly_df["data_split"] == split].set_index(time_column)
        bar_heights = sub["pred_sum"].reindex(sorted_weeks, fill_value=0).values
        color = _SPLIT_COLORS.get(split, _DEFAULT_SPLIT_COLOR)
        ax.bar(
            x_pos,
            bar_heights,
            color=color,
            alpha=0.75,
            width=0.7,
            label=f"Predicted ({split})",
            zorder=2,
        )

    # Draw actual line (on top of bars)
    ax.plot(
        x_pos,
        actual_by_week.values,
        color=_ACTUAL_COLOR,
        linewidth=_ACTUAL_LINEWIDTH,
        marker="o",
        markersize=_ACTUAL_MARKERSIZE,
        label="Actual",
        zorder=4,
    )

    # x-axis tick density: avoid clutter
    if n_weeks <= 30:
        step = 1
    elif n_weeks <= 60:
        step = 2
    else:
        step = 4

    tick_positions = x_pos[::step]
    tick_labels = [sorted_weeks[i] for i in range(0, n_weeks, step)]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=7)
    ax.set_xlim(-0.7, n_weeks - 0.3)

    ax.set_xlabel("Week (yyyyww)")
    ax.set_ylabel("Total Quantity")
    ax.set_title(f"Weekly Actual vs Predicted — {model_name}", fontsize=11)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4, zorder=1)

    fig.tight_layout()
    return fig


# ── Public API ────────────────────────────────────────────────────────────────

def save_weekly_trend(
    pred_all_df: pd.DataFrame,
    item_dir: Path,
    time_column: str,
    model_name: str,
    model_key: str,
    run_ts: str,
) -> Dict[str, Path]:
    """
    Generate weekly-aggregated CSV and trend chart PNG, both saved under item_dir.

    Args:
        pred_all_df:  Full prediction DataFrame (all splits) with columns
                      [time_column, data_split, label_value, pred_value, ...].
        item_dir:     Output directory for this model (already exists).
        time_column:  Name of the week column (e.g. "week_no", format yyyyww).
        model_name:   Human-readable model name (used in chart title).
        model_key:    Algorithm slug used as filename prefix (e.g. "lightgbm").
        run_ts:       Run timestamp string used as filename suffix.

    Returns:
        Dict with keys:
          'weekly_trend_csv' → Path to saved CSV
          'weekly_trend_png' → Path to saved PNG
    """
    if time_column not in pred_all_df.columns:
        logger.warning(
            "add_func_trend_chart: time_column '%s' not found in pred_all_df; skipping trend chart.",
            time_column,
        )
        return {}

    weekly_df = _make_weekly_summary(pred_all_df, time_column)

    # Save CSV
    csv_path = item_dir / f"{model_key}_weekly_trend_{run_ts}.csv"
    weekly_df.to_csv(csv_path, index=False)

    # Save PNG
    try:
        fig = _draw_trend_chart(weekly_df, time_column, model_name)
        png_path = item_dir / f"{model_key}_weekly_trend_{run_ts}.png"
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    except Exception as exc:
        logger.warning("add_func_trend_chart: chart rendering failed (%s); CSV was saved.", exc)
        png_path = None

    result: Dict[str, Path] = {"weekly_trend_csv": csv_path}
    if png_path is not None:
        result["weekly_trend_png"] = png_path

    return result
