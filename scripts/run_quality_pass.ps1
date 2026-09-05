param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

# Test the same pinned environment that the portable build will use.
& (Join-Path $PSScriptRoot "setup_dev.ps1")
& $pythonExe -m playwright install chromium --only-shell
if ($LASTEXITCODE -ne 0) {
    throw "Failed to prepare the headless browser for release checks."
}

# Reuse the canonical checks instead of maintaining a second test/lint pipeline.
& (Join-Path $PSScriptRoot "run_checks.ps1") -Profile ci
& (Join-Path $PSScriptRoot "prepare_release.ps1")

$releaseExecutable = Join-Path $projectRoot "release\Start Kanban.exe"
& $pythonExe (Join-Path $PSScriptRoot "post_build_verification.py") --app-executable $releaseExecutable
if ($LASTEXITCODE -ne 0) {
    throw "Portable launch verification failed with exit code $LASTEXITCODE."
}
