# =============================================================================
# deploy_cloud_run.ps1 - Build / deploy / run a Cloud Run Job
#
# Tag format: {model-name}-v{version}-{yyyymmdd}
# Example:
#   model-refresh-v1-20260601
#
# The date segment is the debugging batch identifier. It does not have to be
# the current calendar day. Use -BuildDate to keep the same tag across days.
# =============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$ModelTag,

    [ValidateSet("build_deploy_run", "deploy_run", "run_only", "build_only")]
    [string]$Action = "build_deploy_run",

    [string]$Memory = "32Gi",
    [string]$Cpu = "8",
    [string]$Timeout = "7200",

    [string]$ResumeRunId = "",

    [string]$BuildDate = ""
)

Set-Location "$PSScriptRoot\.."

$PROJECT = "ingka-cn-cop-stage"
$REGION = "europe-west4"
$REPO = "refresh-model"
$BASE_IMG = "$REGION-docker.pkg.dev/$PROJECT/$REPO/refresh-model"

$JOB_MAP = @{
    "model-refresh-v1" = "refresh-model-bgg-v001-20260601"
}

if (-not $JOB_MAP.ContainsKey($ModelTag)) {
    Write-Error "Unknown ModelTag '$ModelTag'. Please register it in JOB_MAP first."
    exit 1
}

$JOB = $JOB_MAP[$ModelTag]

if ([string]::IsNullOrWhiteSpace($BuildDate)) {
    $BUILD_DATE = Get-Date -Format "yyyyMMdd"
} else {
    $BUILD_DATE = $BuildDate
}

$IMAGE_TAG = "$ModelTag-$BUILD_DATE"
$IMAGE = "${BASE_IMG}:${IMAGE_TAG}"

$tagParts = $ModelTag.Split('-')
if ($tagParts.Length -lt 2) {
    Write-Error "ModelTag '$ModelTag' must look like 'model-refresh-v1'."
    exit 1
}
$LATEST_TAG = "$($tagParts[0])-$($tagParts[1])-latest"
$IMAGE_LATEST = "${BASE_IMG}:${LATEST_TAG}"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Cloud Run Deployment Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ModelTag  : $ModelTag"
Write-Host "  Build Date: $BUILD_DATE"
Write-Host "  Image     : $IMAGE"
Write-Host "  Latest Tag: $IMAGE_LATEST"
Write-Host "  Job       : $JOB"
Write-Host "  Memory    : $Memory  CPU: $Cpu  Timeout: ${Timeout}s"
Write-Host "  Action    : $Action"
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($Action -eq "build_deploy_run" -or $Action -eq "build_only") {
    Write-Host "[1/3] Build image: $IMAGE" -ForegroundColor Yellow
    gcloud builds submit . --tag=$IMAGE --project=$PROJECT
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Image build failed."
        exit 1
    }

    gcloud artifacts docker tags add $IMAGE $IMAGE_LATEST --project=$PROJECT
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to update latest tag alias."
        exit 1
    }

    Write-Host "[OK] Image built and latest alias updated: $IMAGE_LATEST" -ForegroundColor Green
} elseif ($Action -eq "deploy_run") {
    Write-Host "[1/3] Skip build and use image: $IMAGE" -ForegroundColor Yellow
} elseif ($Action -eq "run_only") {
    Write-Host "[1/3] Skip build" -ForegroundColor Yellow
}

if ($Action -eq "build_only") {
    Write-Host "Action=build_only, exiting after build." -ForegroundColor Green
    exit 0
}

if ($Action -eq "build_deploy_run" -or $Action -eq "deploy_run") {
    Write-Host "[2/3] Update Cloud Run Job: $JOB" -ForegroundColor Yellow
    gcloud run jobs update $JOB `
        --image=$IMAGE `
        --memory=$Memory `
        --cpu=$Cpu `
        --task-timeout=$Timeout `
        --region=$REGION `
        --project=$PROJECT
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Job update failed."
        exit 1
    }
    Write-Host "[OK] Job updated" -ForegroundColor Green
} else {
    Write-Host "[2/3] Skip job update" -ForegroundColor Yellow
}

Write-Host "[3/3] Execute Job: $JOB" -ForegroundColor Yellow

$executeArgs = @(
    'run', 'jobs', 'execute', $JOB,
    "--region=$REGION",
    "--project=$PROJECT"
)

if (-not [string]::IsNullOrWhiteSpace($ResumeRunId)) {
    $executeArgs += "--args=--resume-run-id=$ResumeRunId"
}

& gcloud @executeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Job execution failed."
    exit 1
}

Write-Host "[OK] Job execution submitted" -ForegroundColor Green
Write-Host ""
Write-Host "View logs:" -ForegroundColor Cyan
Write-Host '  gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name='"$JOB"'" --project='"$PROJECT"' --order=asc --format="value(timestamp,textPayload)" --freshness=30m'
