param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$workspaceRoot = Split-Path -Parent $project
$frontend = Join-Path $project "frontend"
$frontendProxy = Join-Path $project "scripts\frontend_proxy.mjs"
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
if (-not (Test-Path -LiteralPath $frontendProxy)) { throw "Frontend production proxy is missing: $frontendProxy" }

$vinextCli = Get-ChildItem -LiteralPath (Join-Path $frontend "node_modules\.pnpm") -Directory -Filter "vinext@*" |
    Select-Object -First 1 |
    ForEach-Object { Join-Path $_.FullName "node_modules\vinext\dist\cli.js" }
if (-not $vinextCli -or -not (Test-Path -LiteralPath $vinextCli)) {
    throw "Frontend dependencies are unavailable. Run npm ci in frontend first."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
foreach ($name in @("api.out.log", "api.err.log", "site.build.out.log", "site.build.err.log", "site.internal.out.log", "site.internal.err.log", "site.out.log", "site.err.log")) {
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

function Test-WslExecutable {
    param([string]$Path)
    if (-not $Path -or -not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
    & wsl.exe --exec sh -lc 'test -x "$1"' sh $Path 2>$null
    return $LASTEXITCODE -eq 0
}

if (Test-Url -Url "http://127.0.0.1:8200/health") {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
    if ($health.project_id -ne "orientia-tsa56-phylogenetic-typing") {
        throw "Port 8200 is occupied by another HTTP service."
    }
}

$servicePids = [ordered]@{}
$logicalProcessors = [Math]::Max(1, [Environment]::ProcessorCount)
$reservedProcessors = if ($logicalProcessors -ge 8) { 2 } else { 1 }
$balancedThreads = [Math]::Max(1, [Math]::Min(8, $logicalProcessors - $reservedProcessors))
$env:PHYLO_PUBLIC_FRONTEND_URL = "http://localhost:3200/"
$env:PHYLO_PUBLIC_RESULTS_DIR = Join-Path $project "results"
$env:PHYLO_PUBLIC_RETENTION_HOURS = "24"
$env:PHYLO_PUBLIC_MAX_CONCURRENT_JOBS = "1"
$env:PHYLO_PUBLIC_IQTREE_THREADS = if ($env:PHYLO_PUBLIC_IQTREE_THREADS) { $env:PHYLO_PUBLIC_IQTREE_THREADS } else { [string]$balancedThreads }

if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $wslHome = (& wsl.exe --exec sh -lc 'printf "%s" "$HOME"' 2>$null).Trim()
    $defaultMafft = "$wslHome/miniconda3/bin/mafft"
    $defaultIqtree = "$wslHome/miniconda3/bin/iqtree3"
    if (-not $env:PHYLO_PUBLIC_MAFFT_WSL_BINARY -and (Test-WslExecutable -Path $defaultMafft)) {
        $env:PHYLO_PUBLIC_MAFFT_WSL_BINARY = $defaultMafft
    }
    if (-not $env:PHYLO_PUBLIC_IQTREE_WSL_BINARY -and (Test-WslExecutable -Path $defaultIqtree)) {
        $env:PHYLO_PUBLIC_IQTREE_WSL_BINARY = $defaultIqtree
    }
}

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
    $internalSiteReady = Test-Url -Url "http://127.0.0.1:3210/"
    if (-not $internalSiteReady) {
        $buildProcess = Start-Process `
            -WindowStyle Hidden `
            -FilePath $node `
            -ArgumentList @($vinextCli, "build") `
            -WorkingDirectory $frontend `
            -RedirectStandardOutput (Join-Path $logDir "site.build.out.log") `
            -RedirectStandardError (Join-Path $logDir "site.build.err.log") `
            -Wait `
            -PassThru
        if ($buildProcess.ExitCode -ne 0) {
            throw "Public V1 frontend production build failed. See runtime\local\site.build.err.log."
        }
        $internalSiteProcess = Start-Process `
            -WindowStyle Hidden `
            -FilePath $node `
            -ArgumentList @($vinextCli, "start", "--hostname", "127.0.0.1", "--port", "3210") `
            -WorkingDirectory $frontend `
            -RedirectStandardOutput (Join-Path $logDir "site.internal.out.log") `
            -RedirectStandardError (Join-Path $logDir "site.internal.err.log") `
            -PassThru
        $servicePids.site_internal = $internalSiteProcess.Id
        if (-not (Wait-Url -Url "http://127.0.0.1:3210/")) {
            throw "Public V1 internal rendering service failed to start. See runtime\local\site.internal.err.log."
        }
    }
    $siteProcess = Start-Process `
        -WindowStyle Hidden `
        -FilePath $node `
        -ArgumentList @($frontendProxy, "--hostname", "localhost", "--port", "3200", "--upstream-host", "127.0.0.1", "--upstream-port", "3210") `
        -WorkingDirectory $project `
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
$internalSiteListener = Get-ListenerProcessId -Port 3210
if ($apiListener) { $servicePids.api_listener = [int]$apiListener }
if ($siteListener) { $servicePids.site_listener = [int]$siteListener }
if ($internalSiteListener) { $servicePids.site_internal_listener = [int]$internalSiteListener }
$servicePids.updated_at = (Get-Date).ToString("o")
$servicePids | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

$url = "http://localhost:3200/"
Write-Host "Orientia TSA56 typing website is running:" -ForegroundColor Green
Write-Host $url -ForegroundColor Cyan
Write-Host "Balanced IQ-TREE threads: $($env:PHYLO_PUBLIC_IQTREE_THREADS) / $logicalProcessors logical processors" -ForegroundColor DarkCyan

if (-not $NoBrowser) { Start-Process $url }
