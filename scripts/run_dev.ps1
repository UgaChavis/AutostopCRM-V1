param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Get-ProjectVirtualEnvironmentPythonPath -VenvPath $venvPath

if (-not (Test-Path $pythonExe)) {
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
    & $pythonExe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Failed to install project dependencies." }
}

& $pythonExe (Join-Path $projectRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "Application exited with code $LASTEXITCODE." }
