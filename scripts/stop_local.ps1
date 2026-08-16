$ErrorActionPreference = "Stop"

$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $project "runtime\local\services.json"
$resultsDir = Join-Path $project "results"
$apiBaseUrl = "http://127.0.0.1:8200"
$expectedPorts = @(8200, 3200, 3210)

function Test-Url {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch { return $false }
}

function Get-ListenerProcessId {
    param([int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    if ($listener) { return [int]$listener }
    $pattern = "[:.]$Port\s+.*LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano)) {
        if ($line -match $pattern) { return [int]$Matches[1] }
    }
    return $null
}

function Request-ActiveJobCancellation {
    if (-not (Test-Url -Url "$apiBaseUrl/health") -or -not (Test-Path -LiteralPath $resultsDir)) { return }
    $cancelled = 0
    foreach ($jobFile in Get-ChildItem -LiteralPath $resultsDir -Recurse -Filter "job.json" -File -ErrorAction SilentlyContinue) {
        try {
            $job = Get-Content -LiteralPath $jobFile.FullName -Raw | ConvertFrom-Json
            if ($job.status -in @("queued", "running") -and $job.job_id) {
                Invoke-RestMethod -Method Post -Uri "$apiBaseUrl/api/jobs/$($job.job_id)/cancel" -TimeoutSec 3 | Out-Null
                $cancelled += 1
            }
        } catch { }
    }
    if ($cancelled -gt 0) { Start-Sleep -Seconds 2 }
}

function Stop-TrackedWslJobs {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue) -or -not (Test-Path -LiteralPath $resultsDir)) { return }
    foreach ($trackedPid in Get-ChildItem -LiteralPath $resultsDir -Recurse -Filter "*.pid" -File -Force -ErrorAction SilentlyContinue) {
        & wsl.exe sh -lc '
            pidfile="$(wslpath -a "$1")"
            [ -f "$pidfile" ] || exit 0
            pid="$(tr -cd "0-9" < "$pidfile")"
            [ -n "$pid" ] || exit 0
            kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
            sleep 0.5
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        ' sh $trackedPid.FullName 2>$null
    }
}

function Test-ExpectedProcess {
    param([int]$ProcessId)
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $info) { return $false }
    $commandLine = [string]$info.CommandLine
    return $commandLine.IndexOf($project, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $commandLine -match "--port\s+(8200|3200|3210)\b"
}

function Stop-ExpectedProcessTree {
    param([int]$ProcessId)
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    if (-not (Test-ExpectedProcess -ProcessId $ProcessId)) {
        Write-Warning "Skipped stale or unrelated process ID $ProcessId."
        return
    }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

Request-ActiveJobCancellation
Stop-TrackedWslJobs

$processIds = @()
if (Test-Path -LiteralPath $pidFile) {
    try {
        $services = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        $processIds += @($services.site, $services.site_internal, $services.api, $services.site_listener, $services.site_internal_listener, $services.api_listener)
    } catch { Write-Warning "The service registry was unreadable; listener discovery will be used." }
}
foreach ($port in $expectedPorts) { $processIds += Get-ListenerProcessId -Port $port }
foreach ($processId in ($processIds | Where-Object { $_ } | Select-Object -Unique)) {
    Stop-ExpectedProcessTree -ProcessId ([int]$processId)
}

if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
Write-Host "Public V1.1 local typing services stopped." -ForegroundColor Green
