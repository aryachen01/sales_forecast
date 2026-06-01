# Refresh Model 模型线

## 概述

`refresh_model` 是一个新的模型线迭代方向，与 `item_channel_ma_week` 和 `item_day` 并行存在。

## 与其他模型线的区别

| 维度 | item_channel_ma_week | item_day | refresh_model |
|------|:---:|:---:|:---:|
| **数据源表** | temp_ir_sales_feature_all_with_order_level_discount_enriched | - | **archen_model_base_with_y_r4w** |
| **特征数量** | ~40 个 | - | **132 个** |
| **粒度** | 商品-渠道-市场区域-周 | 商品-日 | **商品篮-渠道-周** |
| **目标变量** | item_qty | - | **item_qty_weekly** (本周实际销量) |
| **版本创建日期** | 已有 | 已有 | **2026-06-01** |

## 配置文件

- `config_bll_lgbm_v001_20260601.yaml` - BQ读取 + 本地执行 + LightGBM

## 快速开始

### 本地测试（推荐起点）

```powershell
cd C:\Users\arche24\Documents\Model_Project\sales_forecast

# 小规模验证（3 个实体）
python main.py `
  --scenario bq_local_local `
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml `
  --model-type lightgbm `
  --max-entities 3

# 全量运行
python main.py `
  --scenario bq_local_local `
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml `
  --model-type lightgbm
```

## 特征来源

特征列表从以下文件提取：
- 源文件：`10_iteration_archive/v1_refresh_model_20260601/feature_list_20260601_refresh_model.csv`
- 筛选条件：`is_in_refresh_model = 1`
- 总计：132 个特征

## 输出位置

- 本地输出目录：`C:\Users\arche24\KnowledgeBase\model_predict_by_weekly\20_modeling\bq_local_runs\refresh_model\v001_20260601`
- BigQuery 输出表（仅 bq_gcp_bq 场景）：
  - `ingka-cn-cop-stage.ikea_da_test.refresh_model_pred_train_test_detail`
  - `ingka-cn-cop-stage.ikea_da_test.refresh_model_metadata`
  - `ingka-cn-cop-stage.ikea_da_test.refresh_model_feature_importance_detail`
  - 等等...

## 模型参数

- **算法**：LightGBM
- **超参优化**：随机搜索，50 次迭代
- **训练数据**：202401 - 202552（78 周）
- **测试数据**：202601 - 202617（17 周）
- **内部验证比例**：20%
- **最小训练样本数**：95

## 后续配置计划

可创建其他配置文件用于不同场景：
- `config_bgg_lgbm_v001_*.yaml` - BQ读取 + GCP执行 + BigQuery回写
- `config_lll_lgbm_v001_*.yaml` - 本地CSV + 本地执行
- `config_bll_dt_v001_*.yaml` - Decision Tree 算法
- 等等...
