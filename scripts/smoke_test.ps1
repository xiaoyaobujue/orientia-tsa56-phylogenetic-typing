$ErrorActionPreference = "Stop"

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8200/health" -TimeoutSec 10
$ready = $null
try { $ready = Invoke-RestMethod -Uri "http://127.0.0.1:8200/ready" -TimeoutSec 10 } catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 503) { throw }
}
$config = Invoke-RestMethod -Uri "http://127.0.0.1:8200/api/config" -TimeoutSec 10
$openapi = Invoke-RestMethod -Uri "http://127.0.0.1:8200/openapi.json" -TimeoutSec 10
$site = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:3200/" -TimeoutSec 10

if ($health.project_id -ne "orientia-tsa56-phylogenetic-typing") { throw "Unexpected API project identity." }
if ($health.project_version -ne "1.1.0") { throw "Unexpected API version." }
if ($health.genotype_evidence_source -ne "phylogenetic_tree_only") { throw "Unexpected genotype evidence source." }
if ($config.default_mode -ne "v1_strict_article") { throw "Strict V1 is not the fixed public mode." }
if (@($config.available_modes).Count -ne 1) { throw "The public API advertises more than one mode." }
if ($openapi.paths.PSObject.Properties.Name -match "/v2") { throw "A V2 route is exposed." }
if ($site.StatusCode -ne 200) { throw "Frontend is unavailable." }

Write-Host "SMOKE_OK" -ForegroundColor Green
Write-Host "Project: $($health.project_id) $($health.project_version)"
Write-Host "Tools: MAFFT=$($health.mafft_available), IQ-TREE=$($health.iqtree_available)"
Write-Host "Ready: $($ready.status)"
