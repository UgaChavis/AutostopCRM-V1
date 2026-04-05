param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\\python.exe"

if (-not (Test-Path $pythonExe)) {
    py -3.13 -m venv $venvPath
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
}

& $pythonExe (Join-Path $projectRoot "main.py")
