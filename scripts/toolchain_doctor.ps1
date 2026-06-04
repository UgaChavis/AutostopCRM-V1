param(
    [ValidateSet("text", "json")]
    [string]$Format = "text",
    [switch]$SkipServer,
    [switch]$Strict,
    [string]$ServerHost = "crm.autostopcrm.ru",
    [string]$ServerRepoPath = "/opt/autostopcrm"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolBin = Join-Path $env:LOCALAPPDATA "Programs\AutostopCRMTools\bin"
if ((Test-Path -LiteralPath $toolBin) -and (($env:Path -split ";") -notcontains $toolBin)) {
    $env:Path = "$toolBin;$env:Path"
}

$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateSet("pass", "warn", "fail", "skip")]
        [string]$Status,
        [Parameter(Mandatory = $true)]
        [string]$Summary,
        [hashtable]$Details = @{}
    )

    $checks.Add([pscustomobject]@{
        name    = $Name
        status  = $Status
        summary = $Summary
        details = $Details
    }) | Out-Null
}

function Invoke-CapturedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [string[]]$Arguments = @()
    )

    try {
        $output = & $Command @Arguments 2>&1
        $exitCode = if ($null -ne $global:LASTEXITCODE) { $global:LASTEXITCODE } else { 0 }
        return @{
            ExitCode = $exitCode
            Output   = @($output | ForEach-Object { $_.ToString() })
        }
    }
    catch {
        return @{
            ExitCode = 1
            Output   = @($_.Exception.Message)
        }
    }
}

function Test-CommandVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string[]]$Arguments = @("--version"),
        [switch]$Required
    )

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $status = if ($Required) { "fail" } else { "skip" }
        Add-Check -Name "command:$Name" -Status $status -Summary "$Name is not on PATH."
        return
    }

    $result = Invoke-CapturedCommand -Command $cmd.Source -Arguments $Arguments
    $status = if ($result.ExitCode -eq 0) { "pass" } else { "warn" }
    $firstLine = @($result.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    $summary = if ($firstLine.Count -gt 0) { $firstLine[0] } else { "$Name found." }
    Add-Check -Name "command:$Name" -Status $status -Summary $summary -Details @{
        path      = $cmd.Source
        exit_code = $result.ExitCode
        output    = $result.Output
    }
}

function Resolve-SshKeyPath {
    $current = $env:AUTOSTOPCRM_SSH_KEY
    if ($current -and (Test-Path -LiteralPath $current)) {
        return $current
    }

    $userValue = [Environment]::GetEnvironmentVariable("AUTOSTOPCRM_SSH_KEY", "User")
    if ($userValue -and (Test-Path -LiteralPath $userValue)) {
        return $userValue
    }

    $candidate = Join-Path $env:USERPROFILE ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }

    return $null
}

Set-Location $projectRoot

Add-Check -Name "repo:root" -Status "pass" -Summary $projectRoot

$gitStatus = Invoke-CapturedCommand -Command "git" -Arguments @("status", "--short", "--branch")
$gitHead = Invoke-CapturedCommand -Command "git" -Arguments @("rev-parse", "--short", "HEAD")
$gitRemote = Invoke-CapturedCommand -Command "git" -Arguments @("ls-remote", "origin", "refs/heads/autostopcrm-v1")
$repoStatus = if (($gitStatus.ExitCode -eq 0) -and ($gitHead.ExitCode -eq 0) -and ($gitRemote.ExitCode -eq 0)) { "pass" } else { "fail" }
Add-Check -Name "repo:git" -Status $repoStatus -Summary (($gitStatus.Output + $gitHead.Output) -join " | ") -Details @{
    status = $gitStatus.Output
    head   = $gitHead.Output
    remote = $gitRemote.Output
}

Test-CommandVersion -Name "git" -Required
Test-CommandVersion -Name "gh" -Required
Test-CommandVersion -Name "jq" -Required
Test-CommandVersion -Name "7z" -Arguments @() -Required
Test-CommandVersion -Name "node"
Test-CommandVersion -Name "npm" -Arguments @("--version")
Test-CommandVersion -Name "pwsh" -Arguments @("--version")
Test-CommandVersion -Name "docker"

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonVersion = Invoke-CapturedCommand -Command $venvPython -Arguments @("--version")
    $pipCheck = Invoke-CapturedCommand -Command $venvPython -Arguments @("-m", "pip", "check")
    $ruffVersion = Invoke-CapturedCommand -Command $venvPython -Arguments @("-m", "ruff", "--version")
    $preCommitVersion = Invoke-CapturedCommand -Command $venvPython -Arguments @("-m", "pre_commit", "--version")
    $playwrightVersion = Invoke-CapturedCommand -Command $venvPython -Arguments @("-m", "playwright", "--version")
    $status = if (($pythonVersion.ExitCode -eq 0) -and ($pipCheck.ExitCode -eq 0) -and ($ruffVersion.ExitCode -eq 0) -and ($preCommitVersion.ExitCode -eq 0) -and ($playwrightVersion.ExitCode -eq 0)) { "pass" } else { "fail" }
    Add-Check -Name "python:venv" -Status $status -Summary (($pythonVersion.Output + $pipCheck.Output) -join " | ") -Details @{
        python     = $pythonVersion.Output
        pip_check  = $pipCheck.Output
        ruff       = $ruffVersion.Output
        pre_commit = $preCommitVersion.Output
        playwright = $playwrightVersion.Output
    }
}
else {
    Add-Check -Name "python:venv" -Status "fail" -Summary "Missing .venv Python: $venvPython"
}

$hookPath = Join-Path $projectRoot ".git\hooks\pre-commit"
if (Test-Path -LiteralPath $hookPath) {
    Add-Check -Name "git:pre-commit-hook" -Status "pass" -Summary "pre-commit hook is installed."
}
else {
    Add-Check -Name "git:pre-commit-hook" -Status "warn" -Summary "pre-commit hook is not installed."
}

$playwrightRoot = Join-Path $env:LOCALAPPDATA "ms-playwright"
$chromiumDirs = @()
if (Test-Path -LiteralPath $playwrightRoot) {
    $chromiumDirs = @(Get-ChildItem -LiteralPath $playwrightRoot -Directory -Filter "chromium*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
}
if ($chromiumDirs.Count -gt 0) {
    Add-Check -Name "playwright:chromium" -Status "pass" -Summary "Chromium browser files are installed." -Details @{ directories = $chromiumDirs }
}
else {
    Add-Check -Name "playwright:chromium" -Status "warn" -Summary "Chromium browser files were not found under $playwrightRoot."
}

$gh = Get-Command "gh" -ErrorAction SilentlyContinue
if ($gh) {
    $authStatus = Invoke-CapturedCommand -Command $gh.Source -Arguments @("auth", "status")
    if ($authStatus.ExitCode -eq 0) {
        Add-Check -Name "github:auth" -Status "pass" -Summary "GitHub CLI is authenticated." -Details @{ output = $authStatus.Output }
    }
    else {
        Add-Check -Name "github:auth" -Status "warn" -Summary "GitHub CLI is installed but not authenticated. Run gh auth login when GitHub CLI operations are needed." -Details @{ output = $authStatus.Output }
    }
}
else {
    Add-Check -Name "github:auth" -Status "skip" -Summary "gh is missing."
}

$sshKey = Resolve-SshKeyPath
if ($sshKey) {
    Add-Check -Name "ssh:key" -Status "pass" -Summary "AutoStop CRM SSH key is available." -Details @{ path = $sshKey }
}
else {
    Add-Check -Name "ssh:key" -Status "fail" -Summary "AutoStop CRM SSH key was not found."
}

if ($SkipServer) {
    Add-Check -Name "server:runtime" -Status "skip" -Summary "Server checks skipped by request."
}
elseif (-not $sshKey) {
    Add-Check -Name "server:runtime" -Status "skip" -Summary "Server checks skipped because SSH key is missing."
}
else {
    $remoteScript = @"
set -e
cd "$ServerRepoPath"
echo "__HEAD__"
git rev-parse --short HEAD
echo "__STATUS__"
git status --short --branch --untracked-files=no
echo "__TOOLS__"
for c in git python3 docker jq rg curl tar unzip; do
  if command -v "`$c" >/dev/null 2>&1; then
    printf '%s:%s\n' "`$c" "`$(command -v "`$c")"
  else
    printf '%s:MISSING\n' "`$c"
  fi
done
echo "__COMPOSE__"
docker compose version || true
docker compose ps || true
echo "__UNTRACKED__"
git status --short --untracked-files=all | awk '/^\?\?/ {print}'
"@
    $remoteScript = ($remoteScript -replace "`r`n", "`n").Trim() + "`n"
    $serverResult = Invoke-CapturedCommand -Command "ssh" -Arguments @(
        "-i", $sshKey,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "root@$ServerHost",
        $remoteScript
    )
    if ($serverResult.ExitCode -eq 0) {
        Add-Check -Name "server:runtime" -Status "pass" -Summary "Server SSH, repo, and docker compose checks completed." -Details @{ output = $serverResult.Output }
        $untracked = @($serverResult.Output | Where-Object { $_ -match '^\?\? ' })
        $vpnLike = @($untracked | Where-Object { $_ -match 'amnezia|vpn|udp443|telegram_mtu|telegram_mss|traffic_collector' })
        if ($untracked.Count -gt 0) {
            Add-Check -Name "server:untracked-files" -Status "warn" -Summary "$($untracked.Count) untracked server files found; $($vpnLike.Count) look VPN-related." -Details @{
                untracked = $untracked
                vpn_like  = $vpnLike
            }
        }
        else {
            Add-Check -Name "server:untracked-files" -Status "pass" -Summary "No untracked files found in server repo."
        }
    }
    else {
        Add-Check -Name "server:runtime" -Status "fail" -Summary "Server SSH/runtime check failed." -Details @{
            exit_code = $serverResult.ExitCode
            output    = $serverResult.Output
        }
    }
}

$payload = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    project_root = $projectRoot
    checks       = $checks
}

if ($Format -eq "json") {
    $payload | ConvertTo-Json -Depth 8
}
else {
    Write-Host "== AutoStop CRM Toolchain Doctor =="
    Write-Host ("Generated: {0}" -f $payload.generated_at)
    Write-Host ("Root: {0}" -f $payload.project_root)
    Write-Host ""
    foreach ($check in $checks) {
        Write-Host ("{0,-5} {1} - {2}" -f $check.status.ToUpperInvariant(), $check.name, $check.summary)
    }
}

if ($Strict -and ($checks | Where-Object { $_.status -eq "fail" })) {
    exit 1
}
