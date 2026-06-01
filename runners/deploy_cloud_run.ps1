# =============================================================================
# deploy_cloud_run.ps1  —  Cloud Run Job 构建 / 部署 / 执行 一体化脚本
#
# 镜像 Tag 规则：{MODEL_TAG}-{git_short_hash}
#   例：bgg-v001-898a3e5
#       bll-v002-1a2b3c4
#
# 用法：
#   .\runners\deploy_cloud_run.ps1 -ModelTag bgg-v001 -Action build_deploy_run
#   .\runners\deploy_cloud_run.ps1 -ModelTag bgg-v001 -Action deploy_run   # 已有镜像，不重建
#   .\runners\deploy_cloud_run.ps1 -ModelTag bgg-v001 -Action run_only     # 只执行
#
# Action 选项：
#   build_deploy_run  —  重建镜像 → 更新 Job → 执行（代码有变更时使用）
#   deploy_run        —  用现有镜像更新 Job → 执行（只改 Cloud Run 参数时使用）
#   run_only          —  直接执行当前 Job（代码和参数都没变时使用）
#   build_only        —  只构建镜像，不更新 Job
# =============================================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$ModelTag,          # 例: bgg-v001  (model_line-version)

    [ValidateSet("build_deploy_run", "deploy_run", "run_only", "build_only")]
    [string]$Action = "build_deploy_run",

    [string]$Memory   = "32Gi",
    [string]$Cpu      = "4",
    [string]$Timeout  = "7200",

    [switch]$ResumeRunId        # 可选：指定 --resume-run-id 续跑
)

Set-Location "$PSScriptRoot\.."

# ── GCP 配置 ──────────────────────────────────────────────────────────────────
$PROJECT  = "ingka-cn-cop-stage"
$REGION   = "europe-west4"
$REPO     = "refresh-model"
$BASE_IMG = "$REGION-docker.pkg.dev/$PROJECT/$REPO/refresh-model"

# ── Cloud Run Job 名称映射（ModelTag → JobName）──────────────────────────────
$JOB_MAP = @{
    "bgg-v001" = "refresh-model-bgg-v001-20260601"
    # 新增模型时在这里登记：
    # "bll-v001" = "refresh-model-bll-v001-20260601"
}

if (-not $JOB_MAP.ContainsKey($ModelTag)) {
    Write-Error "未找到 ModelTag='$ModelTag' 对应的 Job 名称，请在 JOB_MAP 中登记后再运行。"
    exit 1
}
$JOB = $JOB_MAP[$ModelTag]

# ── Git hash（用于镜像 tag 追溯）────────────────────────────────────────────
$GIT_HASH  = git rev-parse --short HEAD 2>$null
if (-not $GIT_HASH) { $GIT_HASH = "nogit" }
$IMAGE_TAG = "$ModelTag-$GIT_HASH"
$IMAGE     = "${BASE_IMG}:${IMAGE_TAG}"

# ── 打印部署摘要 ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Cloud Run 部署摘要" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ModelTag  : $ModelTag"
Write-Host "  Git Hash  : $GIT_HASH"
Write-Host "  Image     : $IMAGE"
Write-Host "  Job       : $JOB"
Write-Host "  Memory    : $Memory  CPU: $Cpu  Timeout: ${Timeout}s"
Write-Host "  Action    : $Action"
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: 构建镜像 ──────────────────────────────────────────────────────────
if ($Action -in @("build_deploy_run", "build_only")) {
    Write-Host "[1/3] 构建镜像: $IMAGE" -ForegroundColor Yellow
    gcloud builds submit . --tag=$IMAGE --project=$PROJECT
    if ($LASTEXITCODE -ne 0) { Write-Error "镜像构建失败"; exit 1 }
    Write-Host "[OK] 镜像构建完成" -ForegroundColor Green
} elseif ($Action -in @("deploy_run")) {
    # deploy_run: 使用已存在的最新同 ModelTag 镜像
    # 列出该 ModelTag 下所有 tag，选最新
    Write-Host "[1/3] 跳过构建，查找现有镜像 tag: $ModelTag-*" -ForegroundColor Yellow
    $EXISTING = gcloud artifacts docker tags list $BASE_IMG `
        --project=$PROJECT `
        --filter="tag~'^$ModelTag-'" `
        --format="value(TAG)" 2>$null | Select-Object -Last 1
    if (-not $EXISTING) {
        Write-Error "找不到 tag '$ModelTag-*' 的镜像，请先运行 build_deploy_run。"
        exit 1
    }
    $IMAGE = "${BASE_IMG}:${EXISTING}"
    Write-Host "[OK] 使用现有镜像: $IMAGE" -ForegroundColor Green
}

if ($Action -eq "build_only") {
    Write-Host "Action=build_only，构建完成，退出。" -ForegroundColor Green
    exit 0
}

# ── Step 2: 更新 Cloud Run Job ────────────────────────────────────────────────
if ($Action -in @("build_deploy_run", "deploy_run")) {
    Write-Host "[2/3] 更新 Cloud Run Job: $JOB" -ForegroundColor Yellow
    gcloud run jobs update $JOB `
        --image=$IMAGE `
        --memory=$Memory `
        --cpu=$Cpu `
        --task-timeout=$Timeout `
        --region=$REGION `
        --project=$PROJECT
    if ($LASTEXITCODE -ne 0) { Write-Error "Job 更新失败"; exit 1 }
    Write-Host "[OK] Job 更新完成" -ForegroundColor Green
}

# ── Step 3: 执行 Job ──────────────────────────────────────────────────────────
Write-Host "[3/3] 执行 Job: $JOB" -ForegroundColor Yellow
gcloud run jobs execute $JOB --region=$REGION --project=$PROJECT
if ($LASTEXITCODE -ne 0) { Write-Error "Job 执行失败"; exit 1 }
Write-Host "[OK] Job 已提交执行" -ForegroundColor Green

# ── 打印日志查看命令 ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "查看日志：" -ForegroundColor Cyan
Write-Host "  gcloud logging read `"resource.type=cloud_run_job AND resource.labels.job_name=$JOB`" --project=$PROJECT --order=asc --format=`"value(timestamp,textPayload)`" --freshness=30m"
