param([switch]$Json)

$ErrorActionPreference = "Stop"

function Get-ToolVersion {
    param([string[]]$Names, [string[]]$Arguments = @("--version"))
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $output = & $command.Source @Arguments 2>&1 | Select-Object -First 1
            return [pscustomobject]@{ available = $true; command = $command.Source; version = [string]$output }
        } catch {
            return [pscustomobject]@{ available = $false; command = $command.Source; version = $_.Exception.Message }
        }
    }
    return [pscustomobject]@{ available = $false; command = $null; version = $null }
}

function Test-LocalPortFree {
    param([int]$Port)
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $null }
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    return -not [bool]$listener
}

$docker = Get-ToolVersion -Names @("docker")
$compose = [pscustomobject]@{ available = $false; command = $null; version = $null }
$dockerEngine = $false
if ($docker.available) {
    try {
        $composeVersion = & docker compose version --short 2>&1 | Select-Object -First 1
        if ($LASTEXITCODE -eq 0) {
            $compose = [pscustomobject]@{ available = $true; command = "docker compose"; version = [string]$composeVersion }
        }
    } catch { }
    try {
        & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        $dockerEngine = $LASTEXITCODE -eq 0
    } catch { }
}

$python = Get-ToolVersion -Names @("python", "python3")
$node = Get-ToolVersion -Names @("node")
$mafft = Get-ToolVersion -Names @("mafft")
$iqtree = Get-ToolVersion -Names @("iqtree3", "iqtree2", "iqtree")
$dockerReady = $docker.available -and $compose.available -and $dockerEngine
$nativeReady = $python.available -and $node.available -and $mafft.available -and $iqtree.available

$recommendationEn = if ($dockerReady) {
    "Docker is ready. Run scripts/start_docker.ps1."
} elseif ($nativeReady) {
    "The native toolchain is present. Run scripts/setup_native.ps1 once, then scripts/start_local.ps1."
} else {
    "Recommended: install Docker Desktop, start it, and rerun this check. https://docs.docker.com/desktop/setup/install/windows-install/"
}
$report = [pscustomobject]@{
    checked_at = (Get-Date).ToString("o")
    operating_system = [System.Environment]::OSVersion.VersionString
    cpu_logical_processors = [System.Environment]::ProcessorCount
    docker_ready = $dockerReady
    native_ready = $nativeReady
    docker = $docker
    docker_engine_running = $dockerEngine
    docker_compose = $compose
    python = $python
    node = $node
    mafft = $mafft
    iqtree = $iqtree
    port_3200_free = Test-LocalPortFree -Port 3200
    port_8200_free = Test-LocalPortFree -Port 8200
    iqtree_threads = "AUTO"
    recommendation_en = $recommendationEn
}

if ($Json) { $report | ConvertTo-Json -Depth 5; exit 0 }

Write-Host "Orientia TSA56 environment check" -ForegroundColor Cyan
$report | Format-List operating_system, cpu_logical_processors, docker_ready, native_ready, docker_engine_running, port_3200_free, port_8200_free, iqtree_threads
Write-Host "Docker: $($docker.version)"
Write-Host "Compose: $($compose.version)"
Write-Host "Python: $($python.version)"
Write-Host "Node.js: $($node.version)"
Write-Host "MAFFT: $($mafft.version)"
Write-Host "IQ-TREE: $($iqtree.version)"
Write-Host ""
Write-Host $recommendationEn -ForegroundColor Yellow
