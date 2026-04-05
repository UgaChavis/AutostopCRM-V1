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
$pythonExe = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
$releaseRoot = Join-Path $projectRoot "release"
$portableExeName = "Start Kanban.exe"
$portableExeSource = Join-Path $releaseRoot "MinimalKanban.exe"
$portableExeTarget = Join-Path $releaseRoot $portableExeName

& (Join-Path $PSScriptRoot "build_app.ps1")
Assert-LastExitCode "Build portable app"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter was not found in the virtual environment: $pythonExe"
}

if (Test-Path $releaseRoot) {
    Remove-Item -Recurse -Force $releaseRoot
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

Copy-Item -Recurse (Join-Path $projectRoot "dist\\MinimalKanban\\*") $releaseRoot

if (-not (Test-Path $portableExeSource)) {
    throw "Portable executable was not found: $portableExeSource"
}

Move-Item -Path $portableExeSource -Destination $portableExeTarget

if (-not (Test-Path $portableExeTarget)) {
    throw "Portable entry point was not created: $portableExeTarget"
}

$optionalDocs = @(
    "CHATGPT_CONNECTOR_SETUP.md",
    "INTERNET_PUBLISH_GUIDE.md"
)
foreach ($docName in $optionalDocs) {
    $docSource = Join-Path $projectRoot $docName
    if (Test-Path $docSource) {
        Copy-Item -Force $docSource (Join-Path $releaseRoot $docName)
    }
}

$guideScript = @'
from pathlib import Path

release_root = Path(r"__RELEASE_ROOT__")
portable_exe_name = "__PORTABLE_EXE_NAME__"
guide_name = "HOW_TO_START.txt"
guide_text = (
    "\u041c\u0438\u043d\u0438\u043c\u0430\u043b\u044c\u043d\u0430\u044f \u043a\u0430\u043d\u0431\u0430\u043d-\u0434\u043e\u0441\u043a\u0430\n\n"
    "\u042d\u0442\u0430 \u0432\u0435\u0440\u0441\u0438\u044f \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442 \u0431\u0435\u0437 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0438.\n\n"
    "\u041a\u0430\u043a \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c:\n"
    f"1. \u0414\u0432\u0430\u0436\u0434\u044b \u0449\u0451\u043b\u043a\u043d\u0438\u0442\u0435 \u043f\u043e \u0444\u0430\u0439\u043b\u0443 {portable_exe_name}.\n"
    "2. \u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u0438\u044f \u043e\u043a\u043d\u0430 \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b.\n"
    "3. \u041d\u0438\u043a\u0430\u043a\u0438\u0435 \u0442\u0435\u0440\u043c\u0438\u043d\u0430\u043b\u044b, npm, python, node \u0438\u043b\u0438 \u0434\u0440\u0443\u0433\u0438\u0435 \u0440\u0443\u0447\u043d\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u043d\u0435 \u043d\u0443\u0436\u043d\u044b.\n\n"
    "\u0427\u0442\u043e \u0432\u0430\u0436\u043d\u043e:\n"
    "- \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0439 API \u0441\u0442\u0430\u0440\u0442\u0443\u0435\u0442 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438\n"
    "- \u0434\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u044f\u044e\u0442\u0441\u044f \u043c\u0435\u0436\u0434\u0443 \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a\u0430\u043c\u0438\n"
    "- \u0437\u0430\u043a\u0440\u044b\u0442\u0438\u0435 \u043e\u043a\u043d\u0430 \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e \u043e\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u0435\u0442 \u0432\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u0438\u0435 \u043a\u043e\u043c\u043f\u043e\u043d\u0435\u043d\u0442\u044b\n"
)
(release_root / guide_name).write_text(guide_text, encoding="utf-8")
'@

$guideScript = $guideScript.Replace("__RELEASE_ROOT__", $releaseRoot)
$guideScript = $guideScript.Replace("__PORTABLE_EXE_NAME__", $portableExeName)
$guideScript | & $pythonExe -
Assert-LastExitCode "Create release quick-start guide"
