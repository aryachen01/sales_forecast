# 参数配置说明（gcp_python_modeling）

本文档是 `gcp_python_modeling` 参数配置的唯一入口文档。

---

## 1. 设计概览

配置采用 **2 层结构**：

| 层 | 文件 | 维护者 | 说明 |
|---|---|---|---|
| 系统层 | `config/system/scenario_defaults.yaml` | 平台管理 | 定义 4 个标准场景的读写策略与默认路径；用户不修改 |
| 用户层 | `config/profiles/<model_line>/config_v001.yaml` | 用户维护 | 统一包含 output / source / bq_tables / runtime / model 全部参数 |

运行时，`load_unified_config()` 加载用户 profile → 注入数据来源与路径覆盖 → 合并生成有效场景配置。

---

## 2. CLI 参数

```powershell
python main.py \
  --scenario <name> \
  --config   <profile_config_path> \
  [--model-type <model_key>] \
  [--max-entities <n>] \
  [--dry-run]
```

| 参数 | 是否必填 | 默认值 | 说明 |
|---|---|---|---|
| `--scenario` | ✅ | — | 场景名，见系统层定义（如 `bq_local_local`） |
| `--config` | 可选 | `config/profiles/item_channel_ma_week/config_v001.yaml` | 用户 profile config 路径 |
| `--model-type` | 可选 | 读 `model.active` | 覆盖 profile 中的 `model.active` |
| `--max-entities` | 可选 | 无限制 | 快速测试时限制实体数量 |
| `--dry-run` | 可选 | false | 仅做最小运行链路验证 |

---

## 3. 系统层：scenario_defaults.yaml

文件：`config/system/scenario_defaults.yaml`

定义 4 个标准场景，**用户不需要修改此文件**：

| 场景名 | source_mode | 存储策略 | 典型用途 |
|---|---|---|---|
| `bq_local_local` | bq | 本地落盘，不同步 GCS/BQ | 本地调试（推荐日常用） |
| `bq_gcp_bq` | bq | GCS + BQ 写入 | 生产 Cloud Run |
| `gcs_gcp_gcs` | csv (GCS) | GCS 写入 | GCS CSV 模式 Cloud Run |
| `local_local_local` | csv (本地) | 仅本地落盘 | 离线全本地测试 |

路径覆盖规则（`load_unified_config` 自动处理）：
- `output.local_output_dir` → 覆盖有 `local_output_dir` 的场景存储路径
- `output.gcs_output_uri` → 覆盖有 `gcs_output_uri` 的场景存储路径
- `source.<scenario_name>.*` → 注入各场景的数据来源字段

---

## 4. 用户层：profile config 结构

文件：`config/profiles/<model_line>/config_vXXX.yaml`

### 顶层结构

```yaml
versioning:   # 版本元数据
output:       # 输出路径覆盖（可选）
source:       # 数据来源（按场景）
bq_tables:    # BQ 输出表名（仅 bq_gcp_bq 场景）
runtime:      # 业务运行参数
model:        # 模型超参
```

---

### 4.1 versioning

```yaml
versioning:
  model_line: item_channel_ma_week   # 模型线标识，写入产物元数据
  version: v001
```

---

### 4.2 output（可选）

覆盖系统默认路径。未填则使用 `scenario_defaults.yaml` 中的系统默认值。

```yaml
output:
  local_output_dir: 'C:\path\to\your\output'
  # gcs_output_uri: 'gs://bucket/prefix'  # 如需覆盖 GCS 路径，取消注释
```

---

### 4.3 source

按场景名分块，各场景只需填写自己会用到的来源字段：

```yaml
source:
  bq_local_local:
    source_table: "ingka-cn-cop-stage.ikea_da_temp.your_table"
    source_filters:
      sales_channel: ""        # 值为 "" 表示不限制（自动忽略）
      sales_category: "热销商品"

  bq_gcp_bq:
    source_table: "ingka-cn-cop-stage.ikea_da_temp.your_table"

  gcs_gcp_gcs:
    source_csv_uri: ""         # 填入 runners/export_source_data_to_gcs.py 输出的 GCS URI

  local_local_local:
    source_csv_local_path: ""  # 填入本地 CSV 路径
```

`source_filters` 说明：
- 键存在 + 值为非空字符串 → 追加为 `WHERE` 条件
- 值为 `""` → 忽略（不限制）
- 填写了不存在的列名 → 运行 fail-fast 并提示

---

### 4.4 bq_tables

仅在 `bq_gcp_bq` 场景（写回 BigQuery）时使用：

```yaml
bq_tables:
  bq_pred_table: "ingka-cn-cop-stage.ikea_da_test.dt_pred_train_test_detail"
  bq_model_meta_table: "ingka-cn-cop-stage.ikea_da_test.dt_model_metadata"
  bq_feat_imp_table: "ingka-cn-cop-stage.ikea_da_test.dt_feature_importance_detail"
```

---

### 4.5 runtime

业务运行参数，与模型超参分离：

```yaml
runtime:
  label_column: item_qty       # 真实值字段名
  time_column: week_no         # 时间切分字段
  min_train_rows: 95           # 实体最少训练行数

  sample_key_columns: [...]    # 样本粒度键（一条样本记录的定义）
  entity_id_columns: [...]     # 训练实体分组键
  model_name_columns: [...]    # 模型命名字段

  entity_discovery:
    enabled: true              # true → 自动从数据源发现所有实体组合

  time_windows:
    train_start: "2024-01-01"
    train_end:   "2025-12-31"
    test_start:  "2026-01-01"
    test_end:    "2026-04-30"

  in_sample_validation:
    enabled: true
    validation_ratio: 0.2
    split_mode: random         # random | time_tail
    random_seed: 42

  tuning:
    enabled: true
    method: random             # random | grid
    n_iter: 20
    random_seed: 42
    internal_validation_ratio: 0.2
    internal_split_mode: random
    objective:
      primary: mae_min
      secondary: accuracy_strict_nonzero_max
      mae_tie_tolerance: 1.0e-9
    search_space: {}           # 空 = 使用代码内置默认搜索空间

  evaluation:
    requested_dimensions: []
    include_total: true
    include_entity: true
    split_name_map:
      train: train
      validation: validation
      test: test

  features:
    - lag_qty_7
    - lag_qty_14
    # ... 更多特征
```

#### runtime 关键字段说明

| 字段 | 说明 |
|---|---|
| `label_column` | 真实值字段（如 `item_qty`），映射为内部 `label_value` |
| `sample_key_columns` | 定义"一条样本记录"的联合主键 |
| `entity_id_columns` | 定义"一个模型覆盖哪些样本"的分组键 |
| `entity_discovery.enabled` | `true`：自动发现；`false`：必须提供 `entity_values_to_process` |
| `entity_values_to_process`（可选） | 手工指定实体列表，优先于自动发现 |
| `tuning.search_space` | 空字典 = 使用代码内置默认空间；显式填写则覆盖 |

---

### 4.6 model

```yaml
model:
  active: decision_tree        # 当前运行算法（可被 --model-type 覆盖）

  decision_tree:
    algorithm_name: "DT"       # 写入产物 model_type 字段
    version: "v001"            # 写入产物 model_version 字段
    max_depth: 6
    min_samples_split: 24
    min_samples_leaf: 12
    max_features: "sqrt"
    random_state: 42

  lightgbm:
    algorithm_name: "LGBM"
    version: "v001"
    num_leaves: 31
    max_depth: 5
    learning_rate: 0.05
    n_estimators: 200
    # ...
```

说明：
- `algorithm_name` / `version` 写入产物元数据，不作为模型超参传入
- `active` 字段可被 `--model-type` CLI 参数覆盖

---

## 5. 运行示例

### 场景 1：本地调试（BQ 读 + 本地存，推荐日常用）

```powershell
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_channel_ma_week/config_v001.yaml `
  --max-entities 5
```

### 场景 2：指定算法

```powershell
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_channel_ma_week/config_v001.yaml `
  --model-type lightgbm `
  --max-entities 5
```

### 场景 3：item_day 模型线

```powershell
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_day/config_v001.yaml `
  --max-entities 3
```

### 场景 4：生产 Cloud Run（BQ 读 + GCS + BQ 存）

```powershell
gcloud run jobs execute gcp-python-modeling-demo `
  --region europe-west4 --wait `
  --args=--scenario=bq_gcp_bq `
  --args=--config=config/profiles/item_channel_ma_week/config_v001.yaml
```

### 场景 5：GCS CSV 模式（先导出源数据再运行）

```powershell
# Step 1: 导出源数据到 GCS
python runners/export_source_data_to_gcs.py

# Step 2: 复制输出的 GCS URI，填入 profile config 的 source.gcs_gcp_gcs.source_csv_uri

# Step 3: 运行
python main.py `
  --scenario gcs_gcp_gcs `
  --config config/profiles/item_channel_ma_week/config_v001.yaml `
  --max-entities 1
```

---

## 6. 版本管理

### 配置版本化规则

1. **每次调参或口径变更**，新建版本文件（`config_v002.yaml`、`config_v003.yaml`...）
2. 历史版本不覆盖，保证可追溯
3. 更新 `config/active/active_config_versions.yaml` 指向新版本
4. 更新 `config/active/run_*.ps1` wrapper

### 运行产物（自动追溯）

每次运行自动在 `out_dir/config_snapshot/` 生成：

| 文件 | 内容 |
|---|---|
| `config_<RUN_TS>.yaml` | 本次使用的 profile config 快照 |
| `system_defaults_<RUN_TS>.yaml` | system scenario_defaults 快照 |
| `config_snapshot_meta_<RUN_TS>.json` | 两份快照的路径 + SHA256 哈希 |

输出根目录下的 `run_registry.csv` 每次运行自动追加一行，包含：
`run_id`、`scenario`、`model_line`、`config_sha256`、`system_defaults_sha256`、结果路径等。

---

## 7. 评估输出文件（CSV UTF-8）

输出前缀由模型类型自动生成（`dt_eval` / `lgbm_eval`），位于 run 目录下：

- `<prefix>_agg_numerators_denominators_<run_ts>.csv`
- `<prefix>_metrics_by_group_<run_ts>.csv`
- `<prefix>_metrics_total_<run_ts>.csv`
- `<prefix>_metrics_entity_<run_ts>.csv`
- `<prefix>_metrics_dimension_<run_ts>.csv`

调参输出（`tuning.enabled=true` 时）：

- `tuning_trials_<run_ts>.csv`
- `tuning_best_params_<run_ts>.csv`
- `effective_params_by_entity_<run_ts>.csv`
- `effective_model_params_<run_ts>.json`

---

## 8. 已删除的旧参数

以下 CLI 参数和文件在本次重构中已全部移除：

**CLI 参数（已删除）：**
- `--runtime-config`
- `--base-runtime-config`
- `--source-table` / `--source-csv-uri` / `--source-csv-local-path`
- `--bq-pred-table` / `--bq-model-meta-table` / `--bq-feat-imp-table`
- `--store-pred-to-bq` 等存储开关
- `--disable-base-runtime-merge`
- `--max-items`（已统一为 `--max-entities`）

**配置文件（已删除）：**
- `config/base/runtime_shared_v001.yaml`
- `config/base/runtime_shared_v002.yaml`
- `config/profiles/*/runtime_v001.yaml`
- `config/profiles/*/model_v001.yaml`
- `config/runtime_params.yaml`
- `config/model_params.yaml`

旧参数请统一迁移至 profile config 对应 section。
