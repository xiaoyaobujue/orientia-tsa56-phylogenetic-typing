param([switch]$NoBrowser, [switch]$NoBuild)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$report = & (Join-Path $project "scripts\check_environment.ps1") -Json | ConvertFrom-Json
if (-not $report.docker_ready) { throw $report.recommendation_en }

Push-Location $project
try {
    $arguments = @("compose", "up", "--detach")
    if (-not $NoBuild) { $arguments += "--build" }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }
    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3200/" -TimeoutSec 3
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { & docker compose ps; throw "The local site did not become ready. Run 'docker compose logs' for details." }
} finally { Pop-Location }

$url = "http://localhost:3200/"
Write-Host "Local publication tool is ready: $url" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $url }
