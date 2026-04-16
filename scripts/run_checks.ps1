param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    & (Join-Path $PSScriptRoot "setup_dev.ps1")
}

$changedPythonFiles = @(
    git diff --name-only --diff-filter=ACMR HEAD -- |
        Where-Object { $_ -like '*.py' }
)
$untrackedPythonFiles = @(
    git ls-files --others --exclude-standard -- |
        Where-Object { $_ -like '*.py' }
)
$targets = @($changedPythonFiles + $untrackedPythonFiles) | Sort-Object -Unique

if ($targets.Count -gt 0) {
    & $pythonExe -m ruff format --check @targets
    if ($LASTEXITCODE -ne 0) { throw "ruff format check failed." }

    & $pythonExe -m ruff check @targets
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed." }
}
else {
    Write-Host "No changed Python files found for ruff checks."
}
