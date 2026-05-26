# METRICS - 评估指标定义与口径

本文档定义 `main.py` 的评估指标与对标口径。

## 1) 指标来源

指标计算逻辑在：

- `modeling/batch_reporting.py`

## 2) 基础误差指标

| 指标 | 公式 | 说明 |
|------|------|------|
| MAE | $\frac{1}{n}\sum\|y-\hat{y}\|$ | 平均绝对误差 |
| RMSE | $\sqrt{\frac{1}{n}\sum(y-\hat{y})^2}$ | 均方根误差 |
| MAPE | $\frac{1}{n}\sum\left\|\frac{y-\hat{y}}{y}\right\| \times 100\%$ | 平均绝对百分比误差 |
| WAPE | $\frac{\sum\|y-\hat{y}\|}{\sum\|y\|} \times 100\%$ | 加权绝对百分比误差 |
| sMAPE | $\frac{2\|y-\hat{y}\|}{(\|y\|+\|\hat{y}\|)} \times 100\%$ | 对称 MAPE |

## 3) 准确率标志位

脚本计算以下准确率口径：

1. `accuracy_strict_pct`
strict: 预测值在 $[0.8y, 1.2y]$ 范围内。

2. `accuracy_standard_pct`
standard: 预测值在 $[0.8y, 1.2y]$ 范围内（零销量场景会按脚本定义处理）。

3. `accuracy_loose_pct`
loose: 预测值在 $[y-1, y+1]$ 范围内。

4. `accuracy_ext_pct`
ext: standard 与 loose 的组合口径。

## 4) 对标方法

模型会与以下基线对标：

- `same_wd_median_8`：过去 8 周同一工作日中位数
- `same_wd_mean_8`：过去 8 周同一工作日平均值

## 5) 输出位置

指标产物主要包括：

- `dt_metrics_<RUN_TS>.json`
- `dt_metrics_by_split_<RUN_TS>.json`
- `dt_metrics_by_split_<RUN_TS>.csv`
- `decision_tree_item_model_metrics_<RUN_TS>.csv`
- `decision_tree_item_model_metrics_non_zero_actual_<RUN_TS>.csv`
- `decision_tree_summary_model_metrics_<RUN_TS>.csv`

详细文件命名规则见：

- [04_output_contract.md](04_output_contract.md)
