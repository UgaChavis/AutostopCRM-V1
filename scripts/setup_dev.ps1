param(
    [switch]$InstallGitHooks
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$devRequirementsPath = Join-Path $projectRoot "requirements-dev.txt"

if (-not (Test-Path $pythonExe)) {
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
}

& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

$installRequirementsPath = if (Test-Path $devRequirementsPath) {
    $devRequirementsPath
} else {
    $requirementsPath
}
& $pythonExe -m pip install -r $installRequirementsPath
if ($LASTEXITCODE -ne 0) { throw "Failed to install project dependencies." }

if ($InstallGitHooks) {
    & $pythonExe -m pre_commit install
    if ($LASTEXITCODE -ne 0) { throw "Failed to install pre-commit hook." }
}
