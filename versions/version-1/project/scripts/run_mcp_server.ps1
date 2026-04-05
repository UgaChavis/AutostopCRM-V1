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
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    py -3.13 -m venv $venvPath
    Assert-LastExitCode "Create virtual environment"
    & $pythonExe -m pip install --upgrade pip
    Assert-LastExitCode "Upgrade pip"
    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
    Assert-LastExitCode "Install dependencies"
}

& $pythonExe -m pip install --upgrade pip | Out-Null
Assert-LastExitCode "Upgrade pip"
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt") | Out-Null
Assert-LastExitCode "Install dependencies"

$env:PYTHONPATH = Join-Path $projectRoot "src"
& $pythonExe -m minimal_kanban.mcp.main
Assert-LastExitCode "Run MCP server"
