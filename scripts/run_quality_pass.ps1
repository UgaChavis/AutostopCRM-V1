param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StepName
    )

    if ($LASTEXITCODE -ne 0) {
        throw "Step '$StepName' failed with exit code $LASTEXITCODE."
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
$devRequirementsPath = Join-Path $projectRoot "requirements-dev.txt"

if (-not (Test-Path $pythonExe)) {
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
    Assert-LastExitCode "Create virtual environment"
}

& $pythonExe -m pip install --upgrade pip
Assert-LastExitCode "Upgrade pip"
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
Assert-LastExitCode "Install dependencies"
if (Test-Path $devRequirementsPath) {
    & $pythonExe -m pip install -r $devRequirementsPath
    Assert-LastExitCode "Install dev dependencies"
}

& (Join-Path $PSScriptRoot "doctor.ps1")
Assert-LastExitCode "Run environment doctor"

& (Join-Path $PSScriptRoot "run_checks.ps1")
Assert-LastExitCode "Run incremental ruff checks"

& $pythonExe -m unittest discover -s (Join-Path $projectRoot "tests") -v
Assert-LastExitCode "Run unit tests"
& $pythonExe (Join-Path $projectRoot "scripts\\audit_localization.py")
Assert-LastExitCode "Run localization audit"
& (Join-Path $PSScriptRoot "prepare_release.ps1")
Assert-LastExitCode "Prepare release artifacts"
$releaseExecutable = Join-Path $projectRoot "release\\Start Kanban.exe"
& $pythonExe (Join-Path $projectRoot "scripts\\post_build_verification.py") --app-executable $releaseExecutable
Assert-LastExitCode "Verify portable launch"
