param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\\python.exe"

if (-not (Test-Path $pythonExe)) {
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
}

& $pythonExe (Join-Path $projectRoot "main.py")
