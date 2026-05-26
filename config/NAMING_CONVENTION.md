# Config 文件命名规范

## 目录结构

```
config/
├── system/
│   └── scenario_defaults.yaml          # 系统级场景默认配置（平台维护，勿随意修改）
└── profiles/
    └── <model_line>/                   # 实体粒度 / 模型线（如 item_day、item_channel_ma_week）
        └── config_<scenario>_<algo>_<desc>_<YYYYMMDD>.yaml
```

## 文件命名规则

```
config_<scenario>_<algo>_<desc>_<YYYYMMDD>.yaml
```

| 字段 | 说明 | 取值示例 |
|---|---|---|
| `scenario` | 主用场景简写（见下表） | `bll`、`lll`、`bgg` |
| `algo` | 主用算法简写（见下表） | `dt`、`lgbm` |
| `desc` | 用途/数据集简短描述（英文、下划线连接） | `v001`、`kr_test`、`cn_store_prod` |
| `YYYYMMDD` | 创建日期 | `20260526` |

### 场景简写对照

| 场景 key | 简写 | 数据源 → 执行 → 落盘 |
|---|---|---|
| `bq_local_local` | `bll` | BigQuery → 本地 → 本地 |
| `bq_gcp_bq` | `bgg` | BigQuery → GCP → BigQuery + GCS |
| `gcs_gcp_gcs` | `ggs` | GCS CSV → GCP → GCS |
| `local_local_local` | `lll` | 本地 CSV → 本地 → 本地 |

### 算法简写对照

| 算法 key | 简写 |
|---|---|
| `decision_tree` | `dt` |
| `lightgbm` | `lgbm` |

## 示例

| 文件名 | 含义 |
|---|---|
| `config_bll_dt_v001_20260501.yaml` | BQ 读取 + 本地执行，Decision Tree，主版本，2026-05-01 创建 |
| `config_lll_dt_kr_test_20260526.yaml` | 本地 CSV，Decision Tree，Korea 测试数据，2026-05-26 创建 |
| `config_bgg_lgbm_cn_store_prod_20260601.yaml` | BQ → GCP 生产写回，LightGBM，CN Store 线上场景 |

## 关于 `model_line` 文件夹

`model_line` 文件夹名称描述**实体粒度**，应与 `versioning.model_line` 保持一致：

| 文件夹名 | 含义 |
|---|---|
| `item_day` | 商品 × 日粒度 |
| `item_channel_ma_week` | 商品 × 渠道 × 门店区域 × 周粒度 |

## 存量文件备注

`config_v001.yaml` 为早期未规范命名的历史文件，建议在下次修改时按上述规范重命名。
