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
$webAssetSourcePath = Join-Path $projectRoot "src\\minimal_kanban\\web_app_assets\\source"
$staticAssetPath = Join-Path $projectRoot "src\\minimal_kanban\\static"

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

$pythonBasePrefixJson = & $pythonExe -c "import json, sys; print(json.dumps(sys.base_prefix))"
Assert-LastExitCode "Locate Python base installation"
$pythonBasePrefix = $pythonBasePrefixJson | ConvertFrom-Json
$originalBuildSearchPath = $env:PATH
try {
    # Foreign native tools can shadow Windows DLLs with incompatible ICU exports.
    $env:PATH = @(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
        $pythonBasePrefix
        (Join-Path $pythonBasePrefix "DLLs")
        (Split-Path -Parent $pythonExe)
    ) -join [IO.Path]::PathSeparator
    & $pythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name MinimalKanban `
        --distpath $distStagingPath `
        --workpath $buildStagingPath `
        --paths (Join-Path $projectRoot "src") `
        --add-data "$webAssetSourcePath;minimal_kanban/web_app_assets/source" `
        --add-data "$staticAssetPath;minimal_kanban/static" `
        (Join-Path $projectRoot "main.py")
    Assert-LastExitCode "Build production app"
}
finally {
    $env:PATH = $originalBuildSearchPath
}

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
