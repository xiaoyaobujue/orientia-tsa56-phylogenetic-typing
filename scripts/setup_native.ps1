$ErrorActionPreference = "Stop"
$project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$frontend = Join-Path $project "frontend"
$venv = Join-Path $project ".venv"

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
if (-not $pythonCommand -or -not $nodeCommand -or -not $npmCommand) {
    throw "Native setup requires Python 3.12 and Node.js 22+. For a zero-environment setup, use Docker."
}

$pythonVersion = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pythonVersion -ne "3.12") { throw "Python 3.12 is required; detected $pythonVersion." }
$nodeMajor = [int]((& $nodeCommand.Source -p "process.versions.node.split('.')[0]").Trim())
if ($nodeMajor -lt 22) { throw "Node.js 22 or later is required." }

& $pythonCommand.Source -m venv $venv
$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $project "requirements.lock")

Push-Location $frontend
try {
    & $npmCommand.Source ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
} finally { Pop-Location }

& (Join-Path $project "scripts\check_environment.ps1")
Write-Host "Python and frontend dependencies are installed. MAFFT 7.525 and IQ-TREE 3.0.1 must also be on PATH or available through the configured WSL paths."
