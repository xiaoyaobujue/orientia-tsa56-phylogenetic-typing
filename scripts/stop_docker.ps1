$ErrorActionPreference = "Stop"
$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed." }

Push-Location $project
try {
    & docker compose down
    if ($LASTEXITCODE -ne 0) { throw "docker compose down failed." }
} finally { Pop-Location }

Write-Host "Local containers stopped. The result volume was preserved."
