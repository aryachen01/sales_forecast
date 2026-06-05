# Cloud Run 执行流程与避坑手册（sales_forecast）

本文档沉淀当前项目在 Cloud Run 上执行 sales forecast 的稳定流程。
目标：减少重复确认，统一镜像管理、执行命令、状态查询与故障处理方式。

---

## 1. 适用范围

- 项目目录：sales_forecast
- 运行方式：Cloud Run Job
- 典型 Job：refresh-model-bgg-v001-20260601
- 典型脚本：[sales_forecast/runners/deploy_cloud_run.ps1](sales_forecast/runners/deploy_cloud_run.ps1)
- 典型配置：[sales_forecast/config/profiles/refresh_model/config_bgg_lgbm_v001_20260601.yaml](sales_forecast/config/profiles/refresh_model/config_bgg_lgbm_v001_20260601.yaml)

---

## 2. Windows 执行流程（主流程）

### 2.1 变更后先做本地确认

1. 确认配置和代码变更已保存
2. 确认本次执行使用的 scenario 和 config
3. 如涉及 sample 维度，确认 sample_key_columns 是否正确

建议至少检查：
- [sales_forecast/config/profiles/refresh_model/config_bgg_lgbm_v001_20260601.yaml](sales_forecast/config/profiles/refresh_model/config_bgg_lgbm_v001_20260601.yaml)
- [sales_forecast/main.py](sales_forecast/main.py)
- [sales_forecast/modeling/pipeline.py](sales_forecast/modeling/pipeline.py)

### 2.2 镜像管理（统一约定）

Tag 规则：
- model-refresh-v1-20260601

说明：
- 日期段是模型调试批次标识，不强制等于当天日期
- 同一批次内允许覆盖同 tag（更新 digest）
- 如果明确要新批次，再改日期或版本号

参考规范：
- [sales_forecast/image_registry/README.md](sales_forecast/image_registry/README.md)
- [sales_forecast/image_registry/image_index.md](sales_forecast/image_registry/image_index.md)
- [sales_forecast/image_registry/run_log.md](sales_forecast/image_registry/run_log.md)

### 2.3 构建 + 部署 + 执行（推荐）

在 PowerShell 执行：

```powershell
cd C:\Users\arche24\Documents\Model_Project\sales_forecast
.\runners\deploy_cloud_run.ps1 -ModelTag model-refresh-v1 -Action build_deploy_run -BuildDate 20260601
```

说明：
- build_deploy_run: 重建镜像、更新 Job、立即执行
- BuildDate 20260601: 继续覆盖既有批次 tag

仅执行不重建：

```powershell
.\runners\deploy_cloud_run.ps1 -ModelTag model-refresh-v1 -Action run_only -BuildDate 20260601
```

### 2.4 状态查询（标准命令）

1. 查最近 execution：

```powershell
gcloud run jobs executions list --job=refresh-model-bgg-v001-20260601 --region=europe-west4 --project=ingka-cn-cop-stage
```

2. 查某个 execution 详细状态：

```powershell
gcloud run jobs executions describe refresh-model-bgg-v001-20260601-<execution-suffix> --region=europe-west4 --project=ingka-cn-cop-stage --format="yaml(status.conditions,status.runningCount,status.succeededCount,status.failedCount,status.cancelledCount,status.startTime,status.completionTime,status.logUri)"
```

3. 查 Job 日志（近 50 条）：

```powershell
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=refresh-model-bgg-v001-20260601" --project=ingka-cn-cop-stage --order=desc --limit=50 --format="value(timestamp,textPayload)"
```

4. 查 checkpoint 进度（用于判断跑到第几个 entity）：

```powershell
gsutil cat "gs://ocp_model_archive/refresh_model/v001_20260601/checkpoints/lgbm_checkpoint_<run_id>.json"
```

---

## 3. 结果落地检查（执行后）

### 3.1 GCS 产物

```powershell
gsutil ls "gs://ocp_model_archive/refresh_model/v001_20260601/runs/"
```

### 3.2 BigQuery 写入结果

本项目当前策略：
- 预测结果会外显 sample id 列
- 表结构与 sample_key_columns 动态对齐
- 如目标表与本次 schema 不兼容，自动创建 fallback 表（表名后缀 _run_ts）

关键实现：
- [sales_forecast/common/output_schema_defs.py](sales_forecast/common/output_schema_defs.py)
- [sales_forecast/modeling/writers.py](sales_forecast/modeling/writers.py)
- [sales_forecast/main.py](sales_forecast/main.py)

可用 SQL 快速检查是否外显：

```sql
SELECT
  run_id,
  sales_channel,
  market_area_no_orig,
  hfb_code,
  product_cluster,
  week_no,
  basket_id_sk
FROM ingka-cn-cop-stage.ikea_da_test.refresh_model_pred_train_test_detail
WHERE run_id = '<run_id>'
LIMIT 100;
```

---

## 4. 断点续跑流程

场景：执行中 OOM / 中断 / 手动停止。

已实现机制：
- 每个 entity 完成后写 checkpoint
- checkpoint 同步到 GCS
- 可用 run_id 续跑跳过已完成实体

建议命令（按你们当前 Job 方式执行）：

```powershell
gcloud run jobs execute refresh-model-bgg-v001-20260601 --region=europe-west4 --project=ingka-cn-cop-stage --args="--resume-run-id=<run_id>"
```

---

## 5. 常见问题与解决方案（本机实战）

### 5.1 OOM（signal 9）

现象：运行到中后段实体被杀，execution fail。

处理：
1. Cloud Run 资源调高到 8 CPU / 32Gi
2. entity 循环后强制 gc.collect
3. 调参降低峰值：减少 n_iter，收敛候选参数空间
4. 保持 checkpoint + resume 流程

### 5.2 BigQuery warning：BigQuery Storage module not found, fallback REST

现象：日志提示未安装 storage module，退回 REST。

结论：
- 权限一般没问题（常见已有 readSessionUser）
- 根因多为镜像依赖缺包

处理：
1. requirements 增加 google-cloud-bigquery-storage
2. 重建并覆盖当前镜像 tag

依赖文件：
- [sales_forecast/requirements.txt](sales_forecast/requirements.txt)

### 5.3 sample id 未外显导致下游 join 困难

现象：此前仅有 sample_key_json，下游 SQL 不便。

处理：
- 已改为按 sample_key_columns 动态外显列，并同步到 BQ schema

### 5.4 4 CPU + 32Gi 参数不兼容

现象：Cloud Run update 时报资源组合无效。

处理：
- 32Gi 走 8 CPU 配置
- 统一使用 deploy 脚本默认值

### 5.5 终端差异导致命令失败

现象：同样 gcloud logging 命令在 bash/PowerShell 行为不一致。

处理：
- Windows 环境优先使用 PowerShell 命令格式
- 跨终端时避免反引号换行混用

---

## 6. Windows 每次执行前检查清单

1. 当前 config 是否是目标 profile
2. sample_key_columns 是否符合本次规则
3. requirements 是否包含本次新增依赖
4. deploy 脚本参数是否正确（ModelTag / BuildDate / Action）
5. image_registry 是否准备记录本次执行

---

## 7. Mac 执行流程（占位）

### 7.1 环境准备（TODO）

待补充。

### 7.2 构建与部署命令（TODO）

待补充。

### 7.3 状态查询命令（TODO）

待补充。

### 7.4 常见问题（TODO）

待补充。

---

## 8. 维护建议

1. 每次执行后更新 run_log
2. 若有新坑，优先写入本手册第 5 节
3. 变更脚本参数语义时，同步更新本手册与 image_registry 规范
