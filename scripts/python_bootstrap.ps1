function Get-PreferredPythonLauncher {
    $candidates = @(
        @{ Command = "py"; BaseArgs = @("-3.13"); Label = "py -3.13" },
        @{ Command = "py"; BaseArgs = @("-3.12"); Label = "py -3.12" },
        @{ Command = "py"; BaseArgs = @("-3.11"); Label = "py -3.11" },
        @{ Command = "python"; BaseArgs = @(); Label = "python" }
    )
    $installPathCandidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
            continue
        }
        try {
            & $candidate.Command @($candidate.BaseArgs + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")) 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    Command = $candidate.Command
                    BaseArgs = [string[]]$candidate.BaseArgs
                    Label   = $candidate.Label
                }
            }
        }
        catch {
        }
    }

    foreach ($path in $installPathCandidates) {
        if (-not (Test-Path $path)) {
            continue
        }
        try {
            & $path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    Command = $path
                    BaseArgs = [string[]]@()
                    Label   = $path
                }
            }
        }
        catch {
        }
    }

    throw "Python 3.11+ was not found. Install Python 3.13 or 3.12 and rerun."
}

function New-ProjectVirtualEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvPath
    )

    $launcher = Get-PreferredPythonLauncher
    & $launcher.Command @($launcher.BaseArgs + @("-m", "venv", $VenvPath))
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create a virtual environment with $($launcher.Label)."
    }
    return $launcher
}

function Invoke-PreferredPythonStdinScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptText
    )

    $launcher = Get-PreferredPythonLauncher
    $ScriptText | & $launcher.Command @($launcher.BaseArgs + @("-"))
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run an inline Python script with $($launcher.Label)."
    }
}
