param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

function Test-PythonLauncherVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    try {
        & py @Arguments 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

Write-Host "== Local Environment =="
Write-Host ("PowerShell: {0}" -f $PSVersionTable.PSVersion)
Write-Host ("Git: {0}" -f (git --version))

$launcher = Get-PreferredPythonLauncher
Write-Host ("Preferred Python launcher: {0}" -f $launcher.Label)
try {
    $preferredVersion = & $launcher.Command @($launcher.BaseArgs + @("--version")) 2>$null
    if ($preferredVersion) {
        Write-Host ("Preferred Python version: {0}" -f ($preferredVersion -join " "))
    }
}
catch {
}

if (Test-Path $pythonExe) {
    Write-Host ("Venv Python: {0}" -f (& $pythonExe --version))
}
else {
    Write-Host "Venv Python: missing"
}

foreach ($tool in @("ruff", "pre-commit")) {
    try {
        $moduleName = if ($tool -eq "pre-commit") { "pre_commit" } else { $tool }
        $output = & $pythonExe -m $moduleName --version 2>$null
        if ($output) {
            Write-Host ("{0}: {1}" -f $tool, ($output -join " "))
        }
    }
    catch {
        Write-Host ("{0}: not installed in .venv" -f $tool)
    }
}

Write-Host ""
Write-Host "== Repo State =="
Write-Host ("Root: {0}" -f $projectRoot)
Write-Host ("Branch: {0}" -f (git branch --show-current))
Write-Host ("HEAD: {0}" -f (git rev-parse --short HEAD))
Write-Host ("Status: {0}" -f ((git status --short --branch) -join [Environment]::NewLine))

Write-Host ""
Write-Host "== Recommendation Check =="
Write-Host ("PowerShell 7 available: {0}" -f [bool](Get-Command pwsh -ErrorAction SilentlyContinue))
Write-Host ("Python 3.12 available: {0}" -f (Test-PythonLauncherVersion -Arguments @("-3.12", "--version")))
Write-Host ("Python 3.13 available: {0}" -f (Test-PythonLauncherVersion -Arguments @("-3.13", "--version")))
