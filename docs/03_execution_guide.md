# 执行说明 — 如何完成一次 Batch 建模

> 本文档覆盖从本地测试到 Cloud Run 生产部署的完整执行流程。

---

## 前置条件

首次运行前，先完成：[07_prerequisites.md](07_prerequisites.md)

---

## 1. 本地调试（推荐起点）

推荐从 `bq_local_local` 场景开始（BQ 读取 + 本地输出，不写回云端）。

```powershell
cd scripts/gcp_python_modeling

# 最小验证（3 个实体）
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_channel_ma_week/config_v001.yaml `
  --max-entities 3

# 指定算法
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_channel_ma_week/config_v001.yaml `
  --model-type lightgbm `
  --max-entities 3

# item_day 模型线
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_day/config_v001.yaml `
  --max-entities 3

# 全量实体（去掉 --max-entities）
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_channel_ma_week/config_v001.yaml
```

### 使用 runners/ wrapper 脚本

`runners/` 目录下的 `.ps1` 脚本封装了带心跳日志、PROGRESS 实时输出的完整运行链路：

```powershell
cd scripts/gcp_python_modeling/runners

# 简单单次运行
.\run_item_channel_ma_week_v001.ps1

# 完整 DT + LGBM（支持 -AlgorithmMode both/dt/lgbm）
.\run_item_channel_ma_week_full_dt_lgbm.ps1 -AlgorithmMode both

# 串行 DT → LGBM，完成后输出对比摘要
.\run_item_channel_ma_week_full_dt_then_lgbm.ps1
```

---

## 2. 本地全离线场景（无需 GCP 连接）

```powershell
# 先在 config/profiles/<model_line>/config_v001.yaml 的
# scenario_profiles.local_local_local 里填写 source_csv_local_path
python main.py `
  --scenario local_local_local `
  --config config/profiles/item_day/config_v001.yaml `
  --max-entities 2
```

---

## 3. Cloud Run 部署与执行

### 第一步：构建 Docker 镜像

```powershell
cd scripts/gcp_python_modeling

gcloud builds submit `
  --tag europe-west4-docker.pkg.dev/ingka-cn-cop-stage/cloud-run-jobs/gcp-python-modeling-demo:latest `
  .
```

### 第二步：部署 Cloud Run Job

```powershell
gcloud run jobs deploy gcp-python-modeling-demo `
  --image europe-west4-docker.pkg.dev/ingka-cn-cop-stage/cloud-run-jobs/gcp-python-modeling-demo:latest `
  --region europe-west4 `
  --cpu 2 `
  --memory 4Gi `
  --set-env-vars `
    PROJECT_ID=ingka-cn-cop-stage,`
    GCS_OUTPUT_URI=gs://ocp_model_archive/gcp_python_modeling,`
    BQ_PRED_TABLE=ingka-cn-cop-stage.ikea_da_test.dt_pred_train_test_detail,`
    BQ_MODEL_META_TABLE=ingka-cn-cop-stage.ikea_da_test.dt_model_metadata,`
    BQ_FEAT_IMP_TABLE=ingka-cn-cop-stage.ikea_da_test.dt_feature_importance_detail
```

### 第三步：执行 Job

```powershell
# BQ 读 + GCS + BQ 写（生产场景）
gcloud run jobs execute gcp-python-modeling-demo `
  --region europe-west4 --wait `
  --args=--scenario=bq_gcp_bq `
  --args=--config=config/profiles/item_channel_ma_week/config_v001.yaml

# 查看日志
gcloud run jobs logs read gcp-python-modeling-demo `
  --region europe-west4 --limit 100
```

---

## 4. GCS CSV 模式（Cloud Run 使用本地数据）

Cloud Run 容器无法直接读取本地文件，需要先导出到 GCS：

```powershell
# Step 1: 导出 BQ 数据到 GCS
python runners/export_source_data_to_gcs.py

# Step 2: 将输出的 GCS URI 填入 profile config
# config/profiles/<model_line>/config_v001.yaml
#   source.gcs_gcp_gcs.source_csv_uri: "gs://..."

# Step 3: 执行
gcloud run jobs execute gcp-python-modeling-demo `
  --region europe-west4 --wait `
  --args=--scenario=gcs_gcp_gcs `
  --args=--config=config/profiles/item_channel_ma_week/config_v001.yaml `
  --args=--max-entities=1
```

---

## 5. 运行验证

运行完成后建议核对：

1. 本地 `<local_output_dir>/runs/<RUN_TAG>/` 目录是否有产物
2. `decision_tree_batch_summary_<RUN_TS>.json` 是否存在
3. 评估 CSV（`dt_eval_metrics_*`）是否存在
4. （BQ 场景）三张 BQ 表是否有当前 `run_id` 数据

详细输出规范见：[04_output_contract.md](04_output_contract.md)

---

## 6. 常见错误排查

| 错误 | 可能原因 | 解决 |
|------|---------|------|
| `PERMISSION_DENIED: bigquery.datasets.get` | 缺少 BQ 权限 | 补充 `roles/bigquery.dataEditor` |
| `PERMISSION_DENIED: storage.objects.create` | 缺少 GCS 权限 | 补充 `roles/storage.objectCreator` |
| `[SKIP] Insufficient train rows` | 训练数据不足 | 降低 `min_train_rows` 或扩展时间范围 |
| Docker 构建超时 | 网络 / 资源不足 | 增加 CPU 或使用预构建基础镜像 |
| `FileNotFoundError: config not found` | config 路径错误 | 检查 `--config` 路径是否相对于 `gcp_python_modeling/` 根目录 |
