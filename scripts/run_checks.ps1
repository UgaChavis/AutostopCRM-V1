param(
    [ValidateSet("changed", "ci")]
    [string]$Profile = "changed"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
Set-Location $projectRoot

if (-not (Test-Path $pythonExe)) {
    & (Join-Path $PSScriptRoot "setup_dev.ps1")
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StepName,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $pythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Step '$StepName' failed with exit code $LASTEXITCODE."
    }
}

if ($Profile -eq "changed") {
    $changedPythonFiles = @(
        git diff --name-only --diff-filter=ACMR HEAD -- |
            Where-Object { $_ -like '*.py' }
    )
    $untrackedPythonFiles = @(
        git ls-files --others --exclude-standard -- |
            Where-Object { $_ -like '*.py' }
    )
    $targets = @($changedPythonFiles + $untrackedPythonFiles) | Sort-Object -Unique

    if ($targets.Count -gt 0) {
        Invoke-Python -StepName "Changed-file Ruff format" -Arguments (@(
                "-m", "ruff", "format", "--check", "--"
            ) + $targets)
        Invoke-Python -StepName "Changed-file Ruff lint" -Arguments (@(
                "-m", "ruff", "check", "--"
            ) + $targets)
    }
    else {
        Write-Host "No changed Python files found for ruff checks."
    }

    Invoke-Python -StepName "Generated browser JavaScript" -Arguments @(
        "scripts/check_web_assets_js.py"
    )
    Invoke-Python -StepName "Repository code health" -Arguments @(
        "scripts/code_health_audit.py", "--include-untracked"
    )
    return
}

$managedEnvironmentNames = @(
    "PYTHONPYCACHEPREFIX",
    "COVERAGE_FILE",
    "QT_QPA_PLATFORM",
    "QTWEBENGINE_DISABLE_SANDBOX",
    "AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR"
)
$savedEnvironment = @{}
foreach ($environmentName in $managedEnvironmentNames) {
    $savedEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable(
        $environmentName,
        [EnvironmentVariableTarget]::Process
    )
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$ciRunId = [Guid]::NewGuid().ToString("N")
$ciPycacheName = "autostopcrm-ci-pycache-$ciRunId"
$ciPycachePath = [IO.Path]::GetFullPath((Join-Path $tempRoot $ciPycacheName))
$tempRootPrefix = $tempRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if (
    -not $ciPycachePath.StartsWith($tempRootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $ciPycachePath) -ne $ciPycacheName
) {
    throw "Refusing to create the isolated Python cache outside the system temp directory."
}

try {
    [void](New-Item -ItemType Directory -Path $ciPycachePath -ErrorAction Stop)
    $env:PYTHONPYCACHEPREFIX = $ciPycachePath
    $env:COVERAGE_FILE = Join-Path $ciPycachePath ".coverage"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:QTWEBENGINE_DISABLE_SANDBOX = "1"
    $env:AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR = Join-Path (
        Join-Path $projectRoot "output"
    ) "browser-smoke-core-$ciRunId"

    Invoke-Python -StepName "Full Ruff format" -Arguments @("-m", "ruff", "format", "--check", ".")
    Invoke-Python -StepName "Full Ruff lint" -Arguments @("-m", "ruff", "check", ".")
    Invoke-Python -StepName "Documentation audit" -Arguments @(
        "scripts/docs_audit.py", "--format", "text"
    )

    Invoke-Python -StepName "Erase stale runtime coverage" -Arguments @(
        "-m", "coverage", "erase"
    )
    Invoke-Python -StepName "Runtime branch coverage" -Arguments @(
        "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "tests", "-v"
    )
    Invoke-Python -StepName "Combine runtime coverage" -Arguments @(
        "-m", "coverage", "combine"
    )
    Invoke-Python -StepName "Report runtime coverage" -Arguments @(
        "-m", "coverage", "report", "--show-missing"
    )
    Invoke-Python -StepName "Write runtime coverage JSON" -Arguments @(
        "-m", "coverage", "json", "-o", "coverage-runtime.json"
    )
    Invoke-Python -StepName "Write runtime coverage XML" -Arguments @(
        "-m", "coverage", "xml", "-o", "coverage-runtime.xml"
    )
    Invoke-Python -StepName "Write runtime coverage HTML" -Arguments @(
        "-m", "coverage", "html", "-d", "htmlcov"
    )

    Invoke-Python -StepName "Erase stale release coverage" -Arguments @(
        "-m", "coverage", "erase"
    )
    Invoke-Python -StepName "Release branch coverage" -Arguments @(
        "-m", "coverage", "run", "--source=scripts", "-m", "unittest",
        "tests.test_agent_release_backup", "tests.test_agent_release_retention", "-v"
    )
    Invoke-Python -StepName "Combine release coverage" -Arguments @(
        "-m", "coverage", "combine"
    )
    Invoke-Python -StepName "Report release coverage" -Arguments @(
        "-m", "coverage", "report", "--show-missing"
    )
    Invoke-Python -StepName "Write release coverage JSON" -Arguments @(
        "-m", "coverage", "json", "-o", "coverage-release.json"
    )
    Invoke-Python -StepName "Coverage ratchet" -Arguments @(
        "scripts/coverage_audit.py", "--format", "text"
    )

    Invoke-Python -StepName "Repository code health" -Arguments @(
        "scripts/code_health_audit.py", "--format", "text"
    )
    Invoke-Python -StepName "Localization audit" -Arguments @("scripts/audit_localization.py")
    Invoke-Python -StepName "Generated browser JavaScript" -Arguments @(
        "scripts/check_web_assets_js.py"
    )
    Invoke-Python -StepName "CRM capability parity" -Arguments @(
        "scripts/crm_capability_parity.py", "--require-complete"
    )
    Invoke-Python -StepName "Change-feed producer parity" -Arguments @(
        "scripts/crm_change_feed_producer_parity.py", "--require-complete"
    )
    Invoke-Python -StepName "Mandatory core browser smoke" -Arguments @(
        "scripts/browser_smoke.py", "--profile", "core", "--attempts", "1"
    )
    Invoke-Python -StepName "Compile release probe scripts" -Arguments @(
        "-m", "py_compile", "scripts/perf_probe.py", "scripts/perf_workflows.py",
        "scripts/finance_audit_report.py", "scripts/browser_smoke.py"
    )
    Invoke-Python -StepName "Local temp performance probe" -Arguments @(
        "scripts/perf_probe.py", "--local-temp-server", "--iterations", "1",
        "--max-snapshot-gzip-ms", "1200", "--max-snapshot-gzip-bytes", "120000",
        "--max-revision-ms", "800", "--max-get-card-ms", "800"
    )
    Invoke-Python -StepName "Stage 1 production-scale performance gates" -Arguments @(
        "scripts/perf_workflows.py", "--synthetic-state-profile", "current-production",
        "--stage1-only", "--skip-browser", "--warmup-iterations", "2", "--iterations", "20",
        "--max-backend-write-ms", "600", "--max-storage-write-ms", "550",
        "--max-revision-server-ms", "20", "--max-get-card-direct-ms", "20",
        "--max-list-cashboxes-ms", "50", "--max-feed-read-ms", "50",
        "--max-feed-replay-ms", "20"
    )
}
finally {
    foreach ($environmentName in $managedEnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $environmentName,
            $savedEnvironment[$environmentName],
            [EnvironmentVariableTarget]::Process
        )
    }

    if (Test-Path -LiteralPath $ciPycachePath) {
        Remove-Item -LiteralPath $ciPycachePath -Recurse -Force
    }
}

Write-Host "Local CI profile passed. Hosted CI is still required for:"
Write-Host "- Ubuntu/Python 3.12 harness"
Write-Host "- production Compose configuration"
Write-Host "- docker-runtime-assets"
