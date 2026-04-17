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
$installerScript = Join-Path $projectRoot "installer\\minimal-kanban.iss"
$outputDir = Join-Path $projectRoot "installer-output"

& (Join-Path $PSScriptRoot "build_app.ps1")

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
if (-not $iscc) {
    $defaultPath = "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
    if (Test-Path $defaultPath) {
        $iscc = $defaultPath
    }
}

if (-not $iscc) {
    $localPath = Join-Path $env:LOCALAPPDATA "Programs\\Inno Setup 6\\ISCC.exe"
    if (Test-Path $localPath) {
        $iscc = $localPath
    }
}

if (-not $iscc) {
    throw "Inno Setup compiler was not found. Install it with: winget install JRSoftware.InnoSetup"
}

& $iscc `
    "/DSourceDir=$(Join-Path $projectRoot 'dist\\MinimalKanban')" `
    "/DOutputDir=$outputDir" `
    $installerScript
Assert-LastExitCode "Build installer"
