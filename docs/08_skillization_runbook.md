# Skillization Runbook (Sales Forecast)

本文档用于把 sales_forecast 抽象为“可复用技能（skill）”的执行蓝图，并记录一次真实运行样本，供后续技能开发直接复刻。

## 1. 结论：是否可行

可行，并且非常适合技能化。

原因：
- 输入结构稳定：数据源、特征列表、目标变量、实体粒度、时间窗、算法、调参、输出位置。
- 执行入口统一：main.py + unified config。
- 产物结构稳定：run_summary、metrics、artifact_manifest、run_registry。
- 场景可参数化：bq_local_local / bq_gcp_bq / local_local_local / gcs_gcp_gcs。

## 2. 技能最小输入协议（建议）

一次训练任务至少要采集以下字段。

### 2.1 数据与样本
- source_table: GCP BigQuery 特征宽表
- feature_list: 使用的特征列
- source_filters: 建模样本过滤条件（可空）
- label_column: 预测目标
- sample_key_columns: 样本唯一键
- entity_id_columns: 模型实体粒度
- model_name_columns: 模型命名粒度

### 2.2 时间与验证
- time_column
- time_windows.train_start / train_end
- time_windows.test_start / test_end
- in_sample_validation.enabled / ratio / split_mode / random_seed

### 2.3 模型与调参
- model.active (decision_tree / lightgbm)
- tuning.enabled
- tuning.method
- tuning.n_iter
- tuning.search_space
- tuning.objective

### 2.4 输出与落盘
- output.local_output_dir
- output.gcs_output_uri (可选)
- bq_tables.* (当场景需要写回 BQ)
- scenario (bq_local_local / bq_gcp_bq / ...)

## 3. 技能执行标准流程

1. 校验配置完整性
- unified config 必须包含 runtime 与 model 两段。
- 特征列必须与源表列做交集校验，记录 missing columns。

2. 校验环境
- Python 环境可用。
- requirements 可安装。
- 如 Python=3.12，PyYAML 需使用 6.0.2 及以上 wheel 版本。

3. 预跑（小规模）
- max-entities=1 或 3。
- 仅验证端到端可运行和产物生成，不追求指标最优。

4. 正式跑
- 去掉 max-entities。
- 保留 run_id、run_summary、metrics、artifact_manifest。

5. 沉淀运行记录
- 记录输入参数快照。
- 记录运行日志关键行。
- 记录 warning 与改进项。

## 4. 一次真实训练记录（2026-06-01）

### 4.1 本次目标
- 将 refresh_model 作为独立模型线执行一次最小真实训练。

### 4.2 使用配置
- 配置文件: config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml
- 场景: bq_local_local
- 算法: lightgbm
- 调参: 开启（random, n_iter=50）
- max-entities: 1

### 4.3 关键输入
- source_table: ingka-cn-cop-stage.ikea_da_test.archen_model_base_with_y_r4w
- source_filters:
  - sales_channel: Store
  - product_cluster: 热销商品
- label_column: item_qty_weekly
- entity_id_columns:
  - sales_channel
  - market_area_no_orig
  - hfb_code
  - product_cluster
- time_windows:
  - train: 202401~202552
  - test: 202601~202617

### 4.4 真实运行命令

```powershell
cd sales_forecast
c:/Users/arche24/Documents/Model_Project/.venv/Scripts/python.exe main.py \
  --scenario bq_local_local \
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml \
  --model-type lightgbm \
  --max-entities 1
```

### 4.5 关键日志摘录
- Loaded unified config 成功。
- model_key=lightgbm。
- tuning=enabled, method=random, n_iter=50。
- entity_discovery found 634 combinations。
- max-entities=1 实际执行了第 1 个实体。
- tuned entity best_mae=10.988967。
- 生成 summary、metrics、artifact_manifest。

### 4.6 产物位置（本地）
- run 目录:
  - C:\Users\arche24\KnowledgeBase\model_predict_by_weekly\20_modeling\bq_local_runs\refresh_model\v001_20260601\runs\20260601_133450_091__refresh_model__lightgbm
- 关键文件:
  - lgbm_run_summary_20260601_133450_091.json
  - lgbm_eval_metrics_by_group_20260601_133450_091.csv
  - lgbm_artifact_manifest_20260601_133450_091.json
- 全局索引:
  - C:\Users\arche24\KnowledgeBase\model_predict_by_weekly\20_modeling\bq_local_runs\refresh_model\v001_20260601\run_registry.csv

### 4.7 本次发现的问题与处理

问题 1：依赖安装失败
- 现象：requirements.txt 中 PyYAML==6.0 在 Python 3.12 下触发源码构建失败。
- 处理：改为 PyYAML==6.0.2。
- 结果：依赖安装恢复正常。

问题 2：配置缺少 model 段
- 现象：unified config 若缺少 model 段会直接报错。
- 处理：在 refresh_model 配置补齐 model.active 与 lightgbm/decision_tree 参数段。

问题 3：部分特征列缺失
- 现象：日志提示 missing columns 33 个（已自动跳过），最终使用 99 个特征。
- 结论：流程可继续，但建议后续做“特征可用性前置校验”并输出差异报告。

## 5. 技能化时建议增加的自动校验

1. 配置校验
- 必填段：versioning/runtime/model/source。
- runtime 必填键：label_column/time_column/entity_id_columns/time_windows/features。

2. 特征校验
- 输出三类集合：requested / available / missing。
- 当 missing 占比高于阈值（如 >20%）时给出阻断或二次确认。

3. 依赖校验
- 在训练前执行 import smoke test（pandas, sklearn, lightgbm, google.cloud.bigquery, yaml）。

4. 输出校验
- 训练结束后必须存在 summary + metrics + manifest + run_registry 行。

## 6. 给技能开发的输入问卷（可直接复用）

1. 数据源与特征
- 你的 BigQuery 特征宽表是哪个？
- 使用哪些特征（来源文件或字段列表）？
- 是否需要样本筛选条件（如 sales_channel=Store）？

2. 模型基本信息
- 预测目标 y 列名？
- entity 粒度字段有哪些？
- sample 粒度字段有哪些？
- 建模范围是全样本还是筛选样本？

3. 模型设置
- train/test 时间窗口？
- 是否开启 in-sample validation？比例多少？
- 使用哪种算法（DT/LGBM）？
- 是否开启调参？调参方法与迭代数？

4. 输出设置
- 输出到本地、GCS、BQ 哪些位置？
- BQ 输出表名分别是什么？

5. 执行规模
- 先 smoke test（max-entities=1/3）还是直接全量？

---

以上就是将 sales_forecast 技能化所需的结构化输入、执行流程和真实样本记录。后续可据此开发交互式 skill（问卷 -> 生成 config -> 预跑 -> 正式跑 -> 归档）。
