# PREREQUISITES - 首次运行前置条件

本文档用于首次部署前检查。若已成功跑通过一次，后续常规复跑通常不需要重复逐项检查。

## 1. GCP 权限

需要以下权限（参考 `learning_material/PERMISSION_REQUEST_CHECKLIST.md`）：

- `roles/run.admin` - Cloud Run Job 管理
- `roles/cloudbuild.builds.editor` - 镜像构建
- `roles/artifactregistry.writer` - 推送镜像
- `roles/bigquery.dataEditor` - BigQuery 读写
- `roles/storage.objectCreator` - 上传文件到 GCS
- `roles/iam.serviceAccountUser` - 使用服务账号

## 2. 本地环境

```powershell
# 确认工具
gcloud --version
python --version          # >= 3.9
docker --version
```

## 3. GCP 配置

```powershell
# 设置项目
gcloud config set project ingka-cn-cop-stage
gcloud auth login

# 验证 BigQuery 访问
gcloud bq ls
```

## 4. BigQuery 数据准备

需要指定一个包含以下字段的 BigQuery 表：

- `item_no` - 商品编码
- `day_date` - 日期
- `item_qty` - 实际销量
- 特征列（见 [02_config_guide.md](02_config_guide.md) 中 `runtime.features` 配置）
