param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$workspaceRoot = Split-Path -Parent $project
$frontend = Join-Path $project "frontend"
$runtimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$projectPython = Join-Path $project ".venv\Scripts\python.exe"
$workspacePython = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { $workspacePython }
$bundledNode = Join-Path $runtimeRoot "node\bin\node.exe"
$systemNode = Get-Command node -ErrorAction SilentlyContinue
$node = if (Test-Path -LiteralPath $bundledNode) { $bundledNode } elseif ($systemNode) { $systemNode.Source } else { $bundledNode }
$logDir = Join-Path $project "runtime\local"
$pidFile = Join-Path $logDir "services.json"

if (-not (Test-Path -LiteralPath $python)) { throw "Python environment is missing: $python" }
if (-not (Test-Path -LiteralPath $node)) { throw "Node.js runtime is missing: $node" }

$vinextCli = Get-ChildItem -LiteralPath (Join-Path $frontend "node_modules\.pnpm") -Directory -Filter "vinext@*" |
    Select-Object -First 1 |
    ForEach-Object { Join-Path $_.FullName "node_modules\vinext\dist\cli.js" }
if (-not $vinextCli -or -not (Test-Path -LiteralPath $vinextCli)) {
    throw "Frontend dependencies are unavailable. Run npm ci in frontend first."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
foreach ($name in @("api.out.log", "api.err.log", "site.out.log", "site.err.log")) {
    Remove-Item -LiteralPath (Join-Path $logDir $name) -Force -ErrorAction SilentlyContinue
}

function Test-Url {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch { return $false }
}

function Wait-Url {
    param([string]$Url, [int]$Attempts = 60)
    for ($index = 0; $index -lt $Attempts; $index += 1) {
        if (Test-Url -Url $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-ListenerProcessId {
    param([int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    if ($listener) { return [int]$listener }
    return $null
}

if (Test-Url -Url "http://127.0.0.1:8200/health") {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
    if ($health.project_id -ne "orientia-tsa56-phylogenetic-typing") {
        throw "Port 8200 is occupied by another HTTP service."
    }
}

$servicePids = [ordered]@{}
$env:PHYLO_PUBLIC_FRONTEND_URL = "http://localhost:3200/"
$env:PHYLO_PUBLIC_RESULTS_DIR = Join-Path $project "results"
$env:PHYLO_PUBLIC_RETENTION_HOURS = "24"
$env:PHYLO_PUBLIC_MAX_CONCURRENT_JOBS = "1"
$env:PHYLO_PUBLIC_IQTREE_THREADS = if ($env:PHYLO_PUBLIC_IQTREE_THREADS) { $env:PHYLO_PUBLIC_IQTREE_THREADS } else { "AUTO" }

if (-not (Test-Url -Url "http://127.0.0.1:8200/health")) {
    $apiProcess = Start-Process `
        -WindowStyle Hidden `
        -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8200") `
        -WorkingDirectory $project `
        -RedirectStandardOutput (Join-Path $logDir "api.out.log") `
        -RedirectStandardError (Join-Path $logDir "api.err.log") `
        -PassThru
    $servicePids.api = $apiProcess.Id
}

if (-not (Wait-Url -Url "http://127.0.0.1:8200/health")) {
    throw "Public V1 API failed to start. See runtime\local\api.err.log."
}

if (-not (Test-Url -Url "http://localhost:3200/")) {
    $env:VITE_PHYLO_PUBLIC_API_BASE_URL = "http://127.0.0.1:8200"
    $siteProcess = Start-Process `
        -WindowStyle Hidden `
        -FilePath $node `
        -ArgumentList @($vinextCli, "dev", "--host", "127.0.0.1", "--port", "3200") `
        -WorkingDirectory $frontend `
        -RedirectStandardOutput (Join-Path $logDir "site.out.log") `
        -RedirectStandardError (Join-Path $logDir "site.err.log") `
        -PassThru
    $servicePids.site = $siteProcess.Id
}

if (-not (Wait-Url -Url "http://localhost:3200/")) {
    throw "Public V1 frontend failed to start. See runtime\local\site.err.log."
}

$apiListener = Get-ListenerProcessId -Port 8200
$siteListener = Get-ListenerProcessId -Port 3200
if ($apiListener) { $servicePids.api_listener = [int]$apiListener }
if ($siteListener) { $servicePids.site_listener = [int]$siteListener }
$servicePids.updated_at = (Get-Date).ToString("o")
$servicePids | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

$url = "http://localhost:3200/"
Write-Host "Orientia TSA56 typing website is running:" -ForegroundColor Green
Write-Host $url -ForegroundColor Cyan

if (-not $NoBrowser) { Start-Process $url }
