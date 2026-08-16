param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$startLocal = Join-Path $project "scripts\start_local.ps1"
$runtimeDir = Join-Path $project "runtime\local"
$tokenFile = Join-Path $runtimeDir "cloudflared.token.dpapi"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$publicUrl = "https://orientia-tsutsugamushi-typing.org/"
$publicHealthUrl = "https://orientia-tsutsugamushi-typing.org/api/health"

if (-not (Test-Path -LiteralPath $startLocal)) { throw "Local service launcher is missing: $startLocal" }
if (-not (Test-Path -LiteralPath $cloudflared)) { throw "cloudflared is missing: $cloudflared" }
if (-not (Test-Path -LiteralPath $tokenFile)) { throw "The protected Cloudflare Tunnel token is missing: $tokenFile" }

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
& $startLocal -NoBrowser

function Test-PublicHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $publicHealthUrl -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Get-TunnelProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "cloudflared.exe" -and
            $_.ExecutablePath -eq $cloudflared -and
            $_.CommandLine -match "\btunnel\s+run\b"
        }
}

$tunnelProcesses = @(Get-TunnelProcesses)
if ($tunnelProcesses.Count -gt 0 -and -not (Test-PublicHealth)) {
    foreach ($process in $tunnelProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 750
    $tunnelProcesses = @()
}

if ($tunnelProcesses.Count -eq 0) {
    $encryptedToken = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    $secureToken = ConvertTo-SecureString $encryptedToken
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $previousToken = $env:TUNNEL_TOKEN
    try {
        $env:TUNNEL_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
        $tunnelProcess = Start-Process `
            -WindowStyle Hidden `
            -FilePath $cloudflared `
            -ArgumentList @("tunnel", "run") `
            -WorkingDirectory $project `
            -RedirectStandardOutput (Join-Path $runtimeDir "cloudflared.out.log") `
            -RedirectStandardError (Join-Path $runtimeDir "cloudflared.err.log") `
            -PassThru
    } finally {
        if ($null -eq $previousToken) { Remove-Item Env:TUNNEL_TOKEN -ErrorAction SilentlyContinue }
        else { $env:TUNNEL_TOKEN = $previousToken }
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
}

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
    if (Test-PublicHealth) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $ready) {
    throw "The local services started, but the public Tunnel did not become ready. See runtime\local\cloudflared.err.log."
}

if ($OpenBrowser) { Start-Process $publicUrl }
