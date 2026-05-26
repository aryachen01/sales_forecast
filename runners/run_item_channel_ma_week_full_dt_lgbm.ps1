param(
    [string]$PythonExe = "",
    [string]$Scenario = "bq_local_local",
    [string]$Config = "config/profiles/item_channel_ma_week/config_v001.yaml",
    [ValidateSet("both", "dt", "lgbm")]
    [string]$AlgorithmMode = "both",
    [int]$HeartbeatMinutes = 5,
    [int]$PollSeconds = 5,
    [int]$MaxItems = 0
)

$ErrorActionPreference = "Stop"

# 工作目录切到 gcp_python_modeling 根目录
Set-Location "$PSScriptRoot\.."
$workDir = (Get-Location).Path

# 自动探测 Python（优先 .venv_fix）
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidate = Join-Path $workDir "..\..\.venv_fix\Scripts\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = (Resolve-Path $candidate).Path
    } else {
        $PythonExe = "python"
    }
}

$env:PYTHONIOENCODING = "utf-8"

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logRoot = Join-Path $workDir "..\..\results\model_runner\logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

Write-Host "[START] workDir=$workDir"
Write-Host "[START] python=$PythonExe"
Write-Host "[START] config=$Config"
Write-Host "[START] scenario=$Scenario"
Write-Host "[START] algorithm_mode=$AlgorithmMode"
if ($MaxItems -gt 0) {
    Write-Host "[START] max_items=$MaxItems"
}

function Read-NewLines {
    param(
        [string]$Path,
        [ref]$LastIndex
    )

    if (-not (Test-Path $Path)) {
        return @()
    }

    $lines = Get-Content -Path $Path -ErrorAction SilentlyContinue
    if ($null -eq $lines) {
        return @()
    }

    $start = [int]$LastIndex.Value
    $count = $lines.Count
    if ($count -le $start) {
        return @()
    }

    $new = $lines[$start..($count - 1)]
    $LastIndex.Value = $count
    return $new
}

function Print-SummaryByAlgorithm {
    param(
        [string]$SummaryPath,
        [string]$Algorithm
    )

    if (-not (Test-Path $SummaryPath)) {
        Write-Host "[WARN] summary not found: $SummaryPath"
        return
    }

    $summary = Get-Content -Path $SummaryPath -Raw | ConvertFrom-Json
    $results = @($summary.results)

    $ok = @($results | Where-Object { $_.status -eq "SUCCESS" })
    $fail = @($results | Where-Object { $_.status -ne "SUCCESS" })

    Write-Host "[DONE][$Algorithm] success=$($ok.Count), non_success=$($fail.Count)"

    foreach ($r in $results) {
        $modelName = [string]$r.model_name
        $status = [string]$r.status
        $entity = [string]$r.entity_id_json
        Write-Host "[RESULT][$Algorithm] model_combo=$modelName | status=$status | entity=$entity"
    }
}

function Run-OneAlgorithm {
    param(
        [string]$Algorithm
    )

    $outLog = Join-Path $logRoot ("run_{0}_{1}_stdout.log" -f $ts, $Algorithm)
    $errLog = Join-Path $logRoot ("run_{0}_{1}_stderr.log" -f $ts, $Algorithm)

    $args = @(
        "main.py",
        "--scenario", $Scenario,
        "--config", $Config,
        "--model-type", $Algorithm
    )
    if ($MaxItems -gt 0) {
        $args += @("--max-entities", "$MaxItems")
    }

    Write-Host "[RUN][$Algorithm] args=$($args -join ' ')"
    Write-Host "[RUN][$Algorithm] stdout_log=$outLog"

    $proc = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $workDir -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru

    $lastOut = 0
    $lastErr = 0
    $startAt = Get-Date
    $nextHeartbeat = $startAt.AddMinutes($HeartbeatMinutes)

    $lastEntity = ""
    $lastEntityProgress = ""
    $summaryPath = ""
    $reportMetrics = ""

    while (-not $proc.HasExited) {
        $newOut = Read-NewLines -Path $outLog -LastIndex ([ref]$lastOut)
        foreach ($line in $newOut) {
            if ($line -match "^\[(DT|LGBM)\]\s+(\d+)/(\d+)\s+entity=(.+)$") {
                $lastEntityProgress = "$($matches[2])/$($matches[3])"
                $lastEntity = $matches[4]
                Write-Host "[PROGRESS][$Algorithm] entity=$lastEntityProgress | $lastEntity"
            }
            if ($line -match "^\[INFO\]\s+tuned entity=(.+?)\s+best_mae=(.+?)\s+best_strict_nonzero=(.+)$") {
                Write-Host "[TUNED][$Algorithm] entity=$($matches[1]) | best_mae=$($matches[2]) | best_strict_nonzero=$($matches[3])"
            }
            if ($line -match "^\[OK\]\s+summary=(.+)$") {
                $summaryPath = $matches[1].Trim()
            }
            if ($line -match "^\[INFO\]\s+report_outputs=(\{.*\})$") {
                try {
                    $obj = $matches[1] | ConvertFrom-Json
                    $reportMetrics = [string]$obj.metrics_csv
                } catch {
                    # ignore JSON parse failures in logs
                }
            }
        }

        $newErr = Read-NewLines -Path $errLog -LastIndex ([ref]$lastErr)
        foreach ($line in $newErr) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                Write-Host "[STDERR][$Algorithm] $line"
            }
        }

        $now = Get-Date
        if ($now -ge $nextHeartbeat) {
            $elapsed = [math]::Round(($now - $startAt).TotalMinutes, 2)
            $entityInfo = if ([string]::IsNullOrWhiteSpace($lastEntityProgress)) { "n/a" } else { $lastEntityProgress }
            Write-Host "[HEARTBEAT][$Algorithm] elapsed_min=$elapsed | last_entity_progress=$entityInfo"
            $nextHeartbeat = $now.AddMinutes($HeartbeatMinutes)
        }

        Start-Sleep -Seconds $PollSeconds
    }

    # flush remaining logs
    $restOut = Read-NewLines -Path $outLog -LastIndex ([ref]$lastOut)
    foreach ($line in $restOut) {
        if ($line -match "^\[OK\]\s+summary=(.+)$") {
            $summaryPath = $matches[1].Trim()
        }
        if ($line -match "^\[INFO\]\s+report_outputs=(\{.*\})$") {
            try {
                $obj = $matches[1] | ConvertFrom-Json
                $reportMetrics = [string]$obj.metrics_csv
            } catch {
            }
        }
    }

    $restErr = Read-NewLines -Path $errLog -LastIndex ([ref]$lastErr)
    foreach ($line in $restErr) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            Write-Host "[STDERR][$Algorithm] $line"
        }
    }

    # Ensure process handle is fully finalized before reading ExitCode.
    try {
        $null = $proc.WaitForExit()
        $proc.Refresh()
    } catch {
    }

    $exitCode = $proc.ExitCode
    if ($null -eq $exitCode) {
        # If summary exists, treat it as successful completion to avoid false negatives.
        $exitCode = if (-not [string]::IsNullOrWhiteSpace($summaryPath)) { 0 } else { 1 }
    }

    Write-Host "[END][$Algorithm] exit_code=$exitCode"
    if (-not [string]::IsNullOrWhiteSpace($summaryPath)) {
        Write-Host "[END][$Algorithm] summary=$summaryPath"
        Print-SummaryByAlgorithm -SummaryPath $summaryPath -Algorithm $Algorithm
    } else {
        Write-Host "[WARN][$Algorithm] summary path not found in logs"
    }

    if (-not [string]::IsNullOrWhiteSpace($reportMetrics)) {
        Write-Host "[END][$Algorithm] metrics_csv=$reportMetrics"
    }

    if ($exitCode -ne 0) {
        throw "Algorithm run failed: $Algorithm (exit_code=$exitCode)"
    }
}

if ($AlgorithmMode -eq "both" -or $AlgorithmMode -eq "dt") {
    Run-OneAlgorithm -Algorithm "decision_tree"
}

if ($AlgorithmMode -eq "both" -or $AlgorithmMode -eq "lgbm") {
    Run-OneAlgorithm -Algorithm "lightgbm"
}

Write-Host "[ALL_DONE] requested runs finished successfully."
