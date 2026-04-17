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
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseRoot = Join-Path $projectRoot "release"
$stagingRoot = Join-Path $projectRoot "release.staging"
$releaseParent = Split-Path -Parent $releaseRoot
$portableExeName = "Start Kanban.exe"
$portableExeSource = Join-Path $stagingRoot "MinimalKanban.exe"
$portableExeTarget = Join-Path $stagingRoot $portableExeName

& (Join-Path $PSScriptRoot "build_app.ps1")
Assert-LastExitCode "Build portable app"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter was not found in the virtual environment: $pythonExe"
}

if (Test-Path $stagingRoot) {
    Remove-Item -Recurse -Force $stagingRoot
}

New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

Copy-Item -Recurse (Join-Path $projectRoot "dist\MinimalKanban\*") $stagingRoot

if (-not (Test-Path $portableExeSource)) {
    throw "Portable executable was not found: $portableExeSource"
}

Move-Item -Path $portableExeSource -Destination $portableExeTarget

if (-not (Test-Path $portableExeTarget)) {
    throw "Portable entry point was not created: $portableExeTarget"
}

$optionalDocs = @(
    "CHATGPT_CONNECTOR_SETUP.md"
)
foreach ($docName in $optionalDocs) {
    $docSource = Join-Path $projectRoot $docName
    if (Test-Path $docSource) {
        Copy-Item -Force $docSource (Join-Path $stagingRoot $docName)
    }
}

$guidePath = Join-Path $stagingRoot "HOW_TO_START.txt"
$guideText = @"
AutoStop CRM

This build runs without installation.

How to start:
1. Double-click $portableExeName.
2. Wait for the desktop window to open.
3. No terminal, npm, python, node, or manual commands are required.

Notes:
- the local API starts automatically
- data persists between restarts
- closing the window shuts down internal services cleanly
"@
Set-Content -LiteralPath $guidePath -Value $guideText -Encoding UTF8

if (Test-Path $releaseRoot) {
    try {
        Remove-Item -Recurse -Force -ErrorAction Stop $releaseRoot
    } catch {
        throw "Release directory is locked: $releaseRoot. Close the running app that was started from release and rerun prepare_release.ps1. Fresh build remains available in dist\MinimalKanban and release.staging."
    }
}

Move-Item -Path $stagingRoot -Destination $releaseParent
Rename-Item -Path (Join-Path $releaseParent (Split-Path -Leaf $stagingRoot)) -NewName (Split-Path -Leaf $releaseRoot)
