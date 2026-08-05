$ErrorActionPreference = "Stop"

$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $project "runtime\local\services.json"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "No publication V1 services are registered."
    exit 0
}

$services = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
$processIds = @(
    $services.site_listener,
    $services.site,
    $services.api_listener,
    $services.api
) | Where-Object { $_ } | Select-Object -Unique

foreach ($processId in $processIds) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) { Stop-Process -Id $processId -Force }
}

Remove-Item -LiteralPath $pidFile -Force
Write-Host "Publication V1 services stopped."
