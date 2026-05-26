# OUTPUT CONTRACT - gcp_python_modeling

本文档定义 `main.py` 的输出契约：

1. BigQuery 输出表（表结构、表名解析与兼容策略）
2. 输出目录结构与文件（本地 + GCS）
3. 产物清单 Manifest
4. 运行后核对项
5. 实体组合筛选说明

说明：

- 对外持久化输出可包括：本地目录、BigQuery 表与 GCS 文件（取决于场景配置）。
- 输出路径优先级：环境变量 > 场景配置 > shared 默认配置。
- 若本地或 GCS 输出根路径无法解析，程序启动阶段会直接失败（fail-fast）。

## 1) BigQuery 输出表

字段语义说明（适用于三张输出表）：

- `run_id`：一次运行实例 ID（技术主键）
- `model_name`：业务模型名（由 `model_name_columns` 拼接）
- `model_type`：算法名（来自 `config/model_params.yaml` 的 `algorithm_name`）
- `model_version`：算法版本（来自 `config/model_params.yaml` 的 `version`，默认 `Default`）
- `entity_id_json`：训练实体键值对（对应 `entity_id_columns`）
- `sample_key_json`：样本粒度键值对（对应 `sample_key_columns`）

### 1.1 预测明细表（train + test）

默认表名：

`ingka-cn-cop-stage.ikea_da_test.dt_pred_train_test_detail`

核心字段：

- `run_id`, `run_ts`, `model_type`, `model_version`, `source_table`
- `model_name`, `entity_id_json`, `sample_key_json`, `data_split`
- `label_value`, `pred_value`, `error`, `abs_error`
- `feature_count`, `config_name`, `gcs_run_uri`

分区与聚簇：

- `PARTITION BY TIMESTAMP_TRUNC(run_ts, DAY)`
- `CLUSTER BY model_name, data_split, run_id`

### 1.2 模型元数据表

默认表名：

`ingka-cn-cop-stage.ikea_da_test.dt_model_metadata`

核心字段：

- `run_id`, `run_ts`, `model_name`, `entity_id_json`, `model_type`, `model_version`, `source_table`
- `feature_count`, `features_json`, `params_json`
- `model_pkl_path`, `model_metadata_json_path`
- `config_name`, `gcs_run_uri`

字段 `model_pkl_path` 与 `model_metadata_json_path` 持久化为 GCS URI，不写本地路径。

分区与聚簇：

- `PARTITION BY TIMESTAMP_TRUNC(run_ts, DAY)`
- `CLUSTER BY model_name, model_type, run_id`

### 1.3 特征重要性明细表

默认表名：

`ingka-cn-cop-stage.ikea_da_test.dt_feature_importance_detail`

核心字段：

- `run_id`, `run_ts`, `model_type`, `model_version`, `source_table`
- `model_name`, `entity_id_json`, `feature`, `model_importance`, `feature_rank`
- `config_name`, `gcs_run_uri`, `feature_importance_csv_path`

分区与聚簇：

- `PARTITION BY TIMESTAMP_TRUNC(run_ts, DAY)`
- `CLUSTER BY model_name, feature, run_id`

### 1.4 实体级分 split 指标表

默认表名：

`ingka-cn-cop-stage.ikea_da_test.dt_metrics_by_split`

核心字段：

- `run_id`, `run_ts`, `model_type`, `model_version`, `model_name`, `entity_id_json`, `source_table`, `config_name`
- `data_split`（train / validation / test）
- 11 个指标列：`MAE`, `RMSE`, `MAE_nonzero`, `MAPE_pct`, `MAPE_nonzero_pct`, `WAPE_pct`, `sMAPE_pct`, `accuracy_strict_pct`, `accuracy_standard_pct`, `accuracy_loose_pct`, `accuracy_ext_pct`

数据来源：`evaluate_and_save_outputs` 输出的 `metrics_by_split` CSV，每次训练完每个实体后追加写入。

分区与聚簇：

- `PARTITION BY TIMESTAMP_TRUNC(run_ts, DAY)`
- `CLUSTER BY model_name, data_split, run_id`

### 1.5 run 级评估指标表

默认表名：

`ingka-cn-cop-stage.ikea_da_test.dt_run_eval_metrics`

核心字段：

- `run_id`, `run_ts`, `model_type`, `model_version`, `source_table`, `config_name`
- `level`（total / entity / dimension）、`data_split`
- `model_name`, `entity_id_json`
- 11 个指标列（同上）

数据来源：`generate_same_structure_report` 输出的 `metrics_by_group` CSV，每次 run 结束后整体追加一次。

分区与聚簇：

- `PARTITION BY TIMESTAMP_TRUNC(run_ts, DAY)`
- `CLUSTER BY model_type, level, run_id`

### 1.6 表名参数优先级

每张输出表都按以下优先级解析：

1. `config/profiles/<model_line>/config_v001.yaml` 的 `bq_tables.*`
2. 环境变量（若对应值在代码路径启用）
3. 代码默认值

对应参数：

- 预测明细：`bq_tables.bq_pred_table` / `BQ_PRED_TABLE`
- 模型元数据：`bq_tables.bq_model_meta_table` / `BQ_MODEL_META_TABLE`
- 特征重要性：`bq_tables.bq_feat_imp_table` / `BQ_FEAT_IMP_TABLE`
- 实体级指标：`bq_tables.bq_metrics_by_split_table` / `BQ_METRICS_BY_SPLIT_TABLE`
- run 级指标：`bq_tables.bq_run_eval_metrics_table` / `BQ_RUN_EVAL_METRICS_TABLE`

### 1.7 表存在性与兼容策略

对每张目标表执行以下逻辑：

1. 表不存在：自动创建后写入
2. 表存在且结构一致：直接追加写入（WRITE_APPEND）
3. 表存在但结构不一致：
   在当前表名后追加 `_<RUN_TS>` 新建表，本次运行写入新表

结构一致性检查包含：

- 字段名/类型/mode
- 分区字段
- 聚簇字段

## 2) 输出目录结构与文件（本地 + GCS）

本地与 GCS 文件结构完全对应，路径同构：

| 存储层 | 根路径 |
|---|---|
| 本地 | `<local_output_dir>/runs/<RUN_TAG>/` |
| GCS | `gs://<gcs_output_uri>/runs/<RUN_TAG>/` |

其中 `RUN_TAG = <run_id>__<model_line>__<algorithm>`。

**GCS 上传规则：**

- 仅当场景开启对应 `store_*_to_gcs=true` 时才选择性上传对应产物。
- 若 `gcs_sync_local_outputs=true`，本地 run 目录会按同构路径全量镜像上传到 GCS。
- GCS 根路径优先级：`GCS_OUTPUT_URI`（环境变量）> `scenario_profiles.<scenario>.storage.gcs_output_uri`。

模型子目录命名规则：

`model_<model_slug>_<algorithm_name>`

说明：`<algorithm_name>` 为全名小写（如 `decision_tree`、`lightgbm`），`<model_slug>` 为经过字符清洗的 model_name。示例：`model_00334701_decision_tree`、`model_00334701_lightgbm`。

**run 目录根级文件 — 批量评估报告（`batch_reporting.py` 输出）：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `<output_prefix>_agg_numerators_denominators_<RUN_TS>.csv` | 全层级（total/entity/dimension）合并的原始分子分母聚合表，含 `level`、`row_cnt`、`abs_err_sum`、`strict_hit_sum` 等中间列，可按 `level` 列筛选或自定义重算指标 | ❌ | 可选 — 分子分母中间列可支持自定义重算；若只看聚合指标，`metrics_by_group` 已足够，不必单独上 BQ |
| `<output_prefix>_metrics_by_group_<RUN_TS>.csv` | 全层级合并的指标表，含 `level`、MAE、WAPE、accuracy_strict 等；按 `level` 列可分别查看 total / entity / dimension 各行 | ❌ | ✅ **高优先** — 建议新表 `dt_run_eval_metrics`；跨 run 横向比较整体效果最直接，是迭代分析的核心查询对象 |

**run 目录根级文件 — 调参记录：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `tuning_trials_<RUN_TS>.csv` | 所有实体调参试验明细：每次试验的参数组合与对应指标（MAE、accuracy_strict_nonzero 等）；**仅当 `tuning.enabled=true`** | ❌ | 可选 — 建议新表 `dt_tuning_trials`；数据量为 n_iter × entity 数，分析调参效率与参数敏感度有用，但日常迭代不必须 |

**run 目录根级文件 — 运行元数据与配置存档：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `<algorithm_name>_run_summary_<RUN_TS>.json` | 本次运行整体汇总：所有实体训练状态（SUCCESS / SKIPPED / FAILED）、tuning 输出路径、GCS 上传文件列表 | ❌ | 低优先 — 建议新表 `dt_run_registry`；run 级别元信息（实体数、成功/失败比、配置名、时间窗口等），便于追踪历史运行 |
| `config_snapshot/config_<RUN_TS>.yaml` | 本次运行使用的 profile config 副本 | ❌ | ❌ 不建议 — 配置文本文件，非结构化行数据，BQ 无查询价值；保持文件形式即可 |
| `config_snapshot/system_defaults_<RUN_TS>.yaml` | 本次运行使用的 system scenario defaults 副本 | ❌ | ❌ 不建议 — 同上 |
| `config_snapshot/config_snapshot_meta_<RUN_TS>.json` | 快照元数据：两份配置的 SHA256 哈希与原始路径，用于审计可重现性 | ❌ | ❌ 不建议 — 内部审计文件，不需要在 BQ 中查询 |

**模型子目录内文件 — 模型存档：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `<algorithm_name>_model_<RUN_TS>.pkl` | 训练好的模型对象（pickle 序列化），用于后续推理复用 | ❌ | ❌ 不建议 — 二进制文件，BQ 无法存储；GCS 是唯一选择 |
| `<algorithm_name>_model_metadata_<RUN_TS>.json` | 模型元数据：特征列表、实体键、超参数、算法版本、训练时间戳 | ✅ 同源 | ➕ 扩展现有 `dt_model_metadata` 表 — 补充 `params_source`、`tuning_best_mae`、`tuning_best_accuracy` 字段，无需新建独立表 |

**模型子目录内文件 — 预测结果与评估指标：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `<algorithm_name>_predictions_train_test_<RUN_TS>.csv` | 训练集 + 测试集（+ 验证集）预测明细，含 `label_value`、`pred_value`、`data_split` 等字段 | ✅ 已有 | ✅ 已覆盖 — 对应 `dt_pred_train_test_detail` 表 |
| `<algorithm_name>_metrics_by_split_<RUN_TS>.csv` | 所有 split（train / validation / test）各自一行，列出相同指标（MAE、RMSE、MAPE、WAPE、sMAPE、accuracy_strict/standard/loose/ext）；用于对比各阶段效果、判断是否过拟合 | ❌ | ✅ **高优先** — 建议新表 `dt_metrics_by_split`；最直接服务模型迭代，可按 run / 实体 / split 查询效果趋势、判断过拟合 |
| `<algorithm_name>_feature_importance_<RUN_TS>.csv` | 特征重要性排名，含 `feature`、`model_importance`、`feature_rank` | ✅ 已有 | ✅ 已覆盖 — 对应 `dt_feature_importance_detail` 表 |
| `<algorithm_name>_eval_entity_partial_<RUN_TS>.csv` | test split 断点文件，含预测明细与分子/分母中间列；batch 中断后可直接拼接各实体文件重建汇总报告 | ❌ | ❌ 不建议 — 断点临时文件，完整运行后无独立分析价值 |

**模型子目录内文件 — 参数记录：**

| 文件名 | 内容说明 | 已在 BQ | 建议上 BQ / 说明 |
|---|---|---|---|
| `<algorithm_name>_effective_params_<RUN_TS>.json` | 本实体实际生效的超参数；`params_source` 字段标记来源（`configured` 或 `tuned`）；当 `params_source=tuned` 时额外包含 `tuning_best_metrics`（`best_mae`、`best_accuracy_strict_nonzero_pct`）；**每次都生成** | 部分 | ➕ 扩展现有 `dt_model_metadata` 表 — 新增 `params_source`、`tuning_best_mae`、`tuning_best_accuracy` 字段；无需新建独立表 |

说明：
- `<algorithm_name>` = 算法全名小写（如 `decision_tree`、`lightgbm`）
- `<output_prefix>` = 算法别名 + `_eval`（如 `dt_eval`、`lgbm_eval`），由 `evaluation.output_prefix` 配置或自动生成
- 评估聚合产物统一为 CSV（UTF-8），不再输出 Markdown 报告

调参参数优先级：

- 当 `tuning.enabled=false`：使用 `model_params.yaml` 静态参数。
- 当 `tuning.enabled=true`：按实体搜索最优参数，最终训练参数以最优参数为准。

## 3) 产物清单 Manifest

每次运行会在 run 目录写入：

`<algorithm_name>_artifact_manifest_<run_id>.json`

清单字段用于解释个性化输出差异（本地/GCS/BQ 不必强制 1:1）：

- `artifact_type`
- `local_path`
- `gcs_uri`
- `storage_policy`

## 4) 运行后核对项

建议每次运行后核对：

1. 日志中是否输出 `bq_pred_table_resolved`、`bq_model_meta_table_resolved`、`bq_feat_imp_table_resolved`、`bq_metrics_by_split_table_resolved`、`bq_run_eval_metrics_table_resolved`
2. 五张 BQ 表是否存在当前 `run_id` 数据（按场景开关，`bq_gcp_bq` 场景全部 5 张均应写入）
3. GCS 是否有当前 `RUN_TAG` 目录及对应文件（`bq_gcp_bq` 场景通过 `gcs_sync_local_outputs=true` 全量镜像）

## 5) 实体组合筛选说明（待定）

- 当前文档仅定义输出契约，不强绑定实体组合筛选算法。
- 三维实体组合（例如 `hfb_no x sales_channel x sales_category`）如何从数据源筛选，待业务口径讨论后再固化。
- 口径未定前，建议通过 `config/profiles/<model_line>/config_v001.yaml` 显式维护实体清单输入。
