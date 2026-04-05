param()

$ErrorActionPreference = "Stop"

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
$pythonExe = Join-Path $venvPath "Scripts\\python.exe"
$distPath = Join-Path $projectRoot "dist"
$buildPath = Join-Path $projectRoot "build"

if (-not (Test-Path $pythonExe)) {
    py -3.13 -m venv $venvPath
    Assert-LastExitCode "Create virtual environment"
}

& $pythonExe -m pip install --upgrade pip
Assert-LastExitCode "Upgrade pip"
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
Assert-LastExitCode "Install dependencies"

if (Test-Path $distPath) { Remove-Item -Recurse -Force $distPath }
if (Test-Path $buildPath) { Remove-Item -Recurse -Force $buildPath }

& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name MinimalKanban `
    --paths (Join-Path $projectRoot "src") `
    (Join-Path $projectRoot "main.py")
Assert-LastExitCode "Build production app"
