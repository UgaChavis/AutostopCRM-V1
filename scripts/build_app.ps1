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
$pythonExe = Join-Path $venvPath "Scripts\\python.exe"
$distPath = Join-Path $projectRoot "dist"
$buildPath = Join-Path $projectRoot "build"
$distStagingPath = Join-Path $projectRoot "dist.staging"
$buildStagingPath = Join-Path $projectRoot "build.staging"

if (-not (Test-Path $pythonExe)) {
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
    Assert-LastExitCode "Create virtual environment"
}

& $pythonExe -m pip install --upgrade pip
Assert-LastExitCode "Upgrade pip"
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
Assert-LastExitCode "Install dependencies"

if (Test-Path $distStagingPath) { Remove-Item -Recurse -Force $distStagingPath }
if (Test-Path $buildStagingPath) { Remove-Item -Recurse -Force $buildStagingPath }

& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name MinimalKanban `
    --distpath $distStagingPath `
    --workpath $buildStagingPath `
    --paths (Join-Path $projectRoot "src") `
    (Join-Path $projectRoot "main.py")
Assert-LastExitCode "Build production app"

if (Test-Path $buildPath) {
    try {
        Remove-Item -Recurse -Force -ErrorAction Stop $buildPath
    } catch {
        throw "Build directory is locked: $buildPath. Close the process that uses files from build and rerun build_app.ps1. Fresh build remains available in dist.staging and build.staging."
    }
}
Move-Item -Path $buildStagingPath -Destination $buildPath

if (Test-Path $distPath) {
    try {
        Remove-Item -Recurse -Force -ErrorAction Stop $distPath
    } catch {
        throw "Dist directory is locked: $distPath. Close the running app started from dist and rerun build_app.ps1. Fresh build remains available in dist.staging."
    }
}
Move-Item -Path $distStagingPath -Destination $distPath
