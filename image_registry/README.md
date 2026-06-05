# Image Registry — 镜像管理规范

## 1. 命名规范

### Tag 格式
```
{model-name}-v{version}-{yyyymmdd}
```

| 字段 | 说明 | 示例 |
|---|---|---|
| model-name | 模型功能描述（小写横线分隔） | `model-refresh`、`model-forecast` |
| version | 模型版本号，对应 config 文件版本 | `v1`、`v2` |
| yyyymmdd | 模型调试批次起始日期（标识该模型版本从哪天开始调试，**不是每次 build 的日期**） | `20260601` |

**示例：**
```
model-refresh-v1-20260601
model-refresh-v2-20260615
```

### Latest 别名 Tag（每次 build 自动更新）
```
model-refresh-latest    ← 始终指向该模型最新 build
```

### Image Name（固定）
```
europe-west4-docker.pkg.dev/ingka-cn-cop-stage/refresh-model/refresh-model
```

### 完整引用格式
```
europe-west4-docker.pkg.dev/ingka-cn-cop-stage/refresh-model/refresh-model:bgg-v001-20260601
```

---

## 2. 什么时候更新已有 Image / 建新 Tag

### 更新已有 Image（tag 不变，覆盖 digest）
**定义**：重新 build 并 push 到**同一个 tag**，digest 被新 build 覆盖。tag 在 Artifact Registry 中保持不变，但指向的内容已更新。

适用场景（满足任一即可）：
- **Config 修复**：配置参数写错（如缺少列、参数值有误），代码逻辑未变
- **Bug fix（轻微）**：不影响模型版本语义的小修复
- **同一天内的调试迭代**：尚未产生可信结果

**操作**：直接执行 `deploy_cloud_run.ps1 -Action build_deploy_run`，tag 日期与已有 tag 相同，自动覆盖。

**前提**：`run_log.md` 中该 tag **无 SUCCESS 记录**。若已有 SUCCESS，必须建新 tag。

---

### 建新 Tag（tag 变化）
以下情况必须新建 tag（不得覆盖旧 tag）：
- **`run_log.md` 中该 tag 已有 SUCCESS 记录** → 新日期或升版本号
- **模型实质性升级**（算法逻辑、特征工程、评估方式改变）→ 升版本号：`v2`
- **跨天继续迭代同一版本** → 日期自然更新：`model-refresh-v1-20260602`

---

### 决策速查表

| 场景 | run_log 有 SUCCESS？ | 操作 |
|---|---|---|
| Config 写错 / 小 bug | 否 | **更新已有 image**（同 tag 覆盖） |
| Config 写错 / 小 bug | 是 | **建新 tag**（新日期） |
| 跨天继续同一批次调试 | 否 | **更新已有 image**（同 tag 覆盖，日期不变） |
| 模型升级 | 任意 | **建新 tag**（升版本号） |

---

## 3. 标准操作流程

### Step 1：Build 镜像
```powershell
$TAG   = "bgg-v001-20260601"   # 按命名规范填写
$IMAGE = "europe-west4-docker.pkg.dev/ingka-cn-cop-stage/refresh-model/refresh-model:$TAG"

cd C:\Users\arche24\Documents\Model_Project\sales_forecast
gcloud builds submit . --tag=$IMAGE --project=ingka-cn-cop-stage
```
Build 完成后 → **登记 image_index.md**（填入 digest、git hash、说明）

### Step 2：更新 Cloud Run Job
```powershell
gcloud run jobs update refresh-model-bgg-v001-20260601 `
  --image=$IMAGE `
  --memory=32Gi --cpu=4 --task-timeout=7200 `
  --region=europe-west4 --project=ingka-cn-cop-stage
```

### Step 3：执行
```powershell
gcloud run jobs execute refresh-model-bgg-v001-20260601 `
  --region=europe-west4 --project=ingka-cn-cop-stage
```
执行后 → **登记 run_log.md**（填入 run_id、状态、备注）

---

## 4. 查询现有镜像
```powershell
# 查看所有 tag
gcloud artifacts docker tags list `
  europe-west4-docker.pkg.dev/ingka-cn-cop-stage/refresh-model/refresh-model `
  --project=ingka-cn-cop-stage

# 查看某 tag 的 digest
gcloud artifacts docker images describe `
  europe-west4-docker.pkg.dev/ingka-cn-cop-stage/refresh-model/refresh-model:bgg-v001-20260601 `
  --project=ingka-cn-cop-stage
```
