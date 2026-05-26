Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 统一终端编码，避免中文日志在 Windows 控制台乱码
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    Write-Host "[WARN] Failed to set UTF-8 console encoding: $($_.Exception.Message)"
}

function Invoke-ModelRun {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("decision_tree", "lightgbm")]
        [string]$ModelType,

        [Parameter(Mandatory = $true)]
        [string]$RootDir,

        [Parameter(Mandatory = $true)]
        [string]$PythonExe,

        [Parameter(Mandatory = $true)]
        [string]$RunnerPath,

        [Parameter(Mandatory = $true)]
        [string]$Config,

        [Parameter(Mandatory = $true)]
        [string]$LogDir
    )

    $start = Get-Date
    $stamp = $start.ToString("yyyyMMdd_HHmmss")
    $outLog = Join-Path $LogDir ("{0}_{1}_stdout.log" -f $ModelType, $stamp)
    $errLog = Join-Path $LogDir ("{0}_{1}_stderr.log" -f $ModelType, $stamp)

    $args = @(
        $RunnerPath,
        "--scenario", "bq_local_local",
        "--config", $Config,
        "--model-type", $ModelType
    )

    Write-Host "[START] algorithm=$ModelType"
    Write-Host "[START] stdout_log=$outLog"
    Write-Host "[START] stderr_log=$errLog"

    $proc = Start-Process -FilePath $PythonExe `
                          -ArgumentList $args `
                          -WorkingDirectory $RootDir `
                          -RedirectStandardOutput $outLog `
                          -RedirectStandardError $errLog `
                          -PassThru

    $lastLineCount = 0
    $lastHeartbeat = Get-Date
    $lastComboStatus = "(none yet)"

    while (-not $proc.HasExited) {
        if (Test-Path $outLog) {
            $lines = Get-Content -Path $outLog
            $lineCount = $lines.Count

            if ($lineCount -gt $lastLineCount) {
                $newLines = $lines[$lastLineCount..($lineCount - 1)]
                foreach ($line in $newLines) {
                    if ($line -match '^\[(DT|LGBM)\]\s+(\d+)/(\d+)\s+entity=(.+)$') {
                        $algoTag = $matches[1]
                        $idx = $matches[2]
                        $total = $matches[3]
                        $entityJson = $matches[4]
                        $lastComboStatus = "$algoTag combo $idx/$total entity=$entityJson"
                        Write-Host "[PROGRESS] $lastComboStatus"
                        continue
                    }

                    if ($line -match '^\[INFO\]\s+tuned entity=(.+?)\s+best_mae=(.+?)\s+best_strict_nonzero=(.+)$') {
                        $entityJson = $matches[1]
                        $bestMae = $matches[2]
                        $bestStrict = $matches[3]
                        Write-Host "[PROGRESS] tuned entity=$entityJson best_mae=$bestMae best_strict_nonzero=$bestStrict"
                        continue
                    }

                    if ($line -match '^\[DONE\]') {
                        Write-Host "[PROGRESS] $line"
                        continue
                    }
                }
                $lastLineCount = $lineCount
            }
        }

        $now = Get-Date
        if (($now - $lastHeartbeat).TotalMinutes -ge 5) {
            $elapsed = [Math]::Round(($now - $start).TotalMinutes, 2)
            Write-Host "[HEARTBEAT] algorithm=$ModelType elapsed_min=$elapsed last_combo_status=$lastComboStatus"
            $lastHeartbeat = $now
        }

        Start-Sleep -Seconds 10
    }

    $end = Get-Date
    $durationSec = [Math]::Round(($end - $start).TotalSeconds, 2)

    Write-Host "[END] algorithm=$ModelType exit_code=$($proc.ExitCode) duration_sec=$durationSec"

    if ($proc.ExitCode -ne 0) {
        Write-Host "[ERROR] algorithm=$ModelType failed. Check logs:"
        Write-Host "  stdout: $outLog"
        Write-Host "  stderr: $errLog"
        throw "Model run failed: $ModelType"
    }

    if (Test-Path $outLog) {
        $tail = Get-Content -Path $outLog -Tail 80
        $summaryLine = $tail | Select-String -Pattern '^\[OK\]\s+summary=' | Select-Object -Last 1
        $reportLine = $tail | Select-String -Pattern '^\[INFO\]\s+report_outputs=' | Select-Object -Last 1
        if ($summaryLine) {
            Write-Host "[RESULT] $($summaryLine.Line)"
        }
        if ($reportLine) {
            Write-Host "[RESULT] $($reportLine.Line)"
        }
    }

    return @{
        ModelType = $ModelType
        ExitCode = $proc.ExitCode
        StdOutLog = $outLog
        StdErrLog = $errLog
        DurationSec = $durationSec
    }
}

# 脚本入口
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = (Resolve-Path (Join-Path $scriptDir "..")).Path

$pythonExe = "c:\Users\arche24\Jupyter Lab Script\00 Project File\Cursor_Project\.venv_fix\Scripts\python.exe"
$runnerPath = Join-Path $rootDir "main.py"
$config = Join-Path $rootDir "config\profiles\item_channel_ma_week\config_v001.yaml"

$runStamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$logDir = Join-Path $rootDir ("logs\batch_runs\dt_then_lgbm_" + $runStamp)
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Host "[INFO] root_dir=$rootDir"
Write-Host "[INFO] log_dir=$logDir"
Write-Host "[INFO] config=$config"

$dt = Invoke-ModelRun -ModelType "decision_tree" `
                      -RootDir $rootDir `
                      -PythonExe $pythonExe `
                      -RunnerPath $runnerPath `
                      -Config $config `
                      -LogDir $logDir

$lgbm = Invoke-ModelRun -ModelType "lightgbm" `
                        -RootDir $rootDir `
                        -PythonExe $pythonExe `
                        -RunnerPath $runnerPath `
                        -Config $config `
                        -LogDir $logDir

Write-Host "[ALL_DONE] decision_tree_sec=$($dt.DurationSec) lightgbm_sec=$($lgbm.DurationSec)"
Write-Host "[ALL_DONE] logs=$logDir"
