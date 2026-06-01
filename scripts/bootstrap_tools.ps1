param(
    [switch]$Force,
    [switch]$SkipProjectSetup,
    [switch]$SkipDoctor,
    [switch]$GithubLogin,
    [switch]$InstallDockerDesktop
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $env:LOCALAPPDATA "Programs\AutostopCRMTools"
$bin = Join-Path $toolsRoot "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
if (($env:Path -split ";") -notcontains $bin) {
    $env:Path = "$bin;$env:Path"
}

function Add-UserPathEntry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathEntry
    )

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) {
        $parts = @($userPath -split ";" | Where-Object { $_ })
    }
    if ($parts -notcontains $PathEntry) {
        $newPath = (@($PathEntry) + $parts) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "Added to user PATH: $PathEntry"
    }
}

function Add-CurrentSessionShim {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandName,
        [Parameter(Mandatory = $true)]
        [string]$TargetExe
    )

    $shimDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
    if (-not (Test-Path -LiteralPath $shimDir)) {
        return
    }

    if (($env:Path -split ";") -notcontains $shimDir) {
        return
    }

    $shimPath = Join-Path $shimDir "$CommandName.cmd"
    $content = "@echo off`r`n`"$TargetExe`" %*`r`n"
    Set-Content -LiteralPath $shimPath -Value $content -Encoding ASCII -NoNewline
    Write-Host "Created command shim: $shimPath"
}

function Add-ToolShims {
    foreach ($tool in @("gh", "jq", "7z")) {
        $target = Join-Path $bin "$tool.exe"
        if (Test-Path -LiteralPath $target) {
            Add-CurrentSessionShim -CommandName $tool -TargetExe $target
        }
    }
}

function Get-LatestGithubAsset {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repo,
        [Parameter(Mandatory = $true)]
        [string]$Pattern
    )

    $headers = @{ "User-Agent" = "AutostopCRM-tool-bootstrap" }
    $release = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -match $Pattern } | Select-Object -First 1
    if (-not $asset) {
        throw "No asset matching '$Pattern' found for $Repo latest release."
    }
    return [pscustomobject]@{
        Version = $release.tag_name
        Name    = $asset.name
        Url     = $asset.browser_download_url
    }
}

function Install-PortableGh {
    $target = Join-Path $bin "gh.exe"
    if ((Test-Path -LiteralPath $target) -and (-not $Force)) {
        Write-Host "gh already installed: $target"
        return
    }

    $tmp = Join-Path $env:TEMP ("autostopcrm-gh-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $asset = Get-LatestGithubAsset -Repo "cli/cli" -Pattern "windows_amd64\.zip$"
        $archive = Join-Path $tmp $asset.Name
        Invoke-WebRequest -Uri $asset.Url -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $tmp -Force
        $exe = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "gh.exe" | Select-Object -First 1
        if (-not $exe) {
            throw "gh.exe was not found in $($asset.Name)."
        }
        Copy-Item -LiteralPath $exe.FullName -Destination $target -Force
        Write-Host "Installed gh $($asset.Version): $target"
    }
    finally {
        if (Test-Path -LiteralPath $tmp) {
            Remove-Item -LiteralPath $tmp -Recurse -Force
        }
    }
}

function Install-PortableJq {
    $target = Join-Path $bin "jq.exe"
    if ((Test-Path -LiteralPath $target) -and (-not $Force)) {
        Write-Host "jq already installed: $target"
        return
    }

    $asset = Get-LatestGithubAsset -Repo "jqlang/jq" -Pattern "jq-windows-amd64\.exe$"
    Invoke-WebRequest -Uri $asset.Url -OutFile $target
    Write-Host "Installed jq $($asset.Version): $target"
}

function Install-Portable7z {
    $target = Join-Path $bin "7z.exe"
    if ((Test-Path -LiteralPath $target) -and (-not $Force)) {
        Write-Host "7z already installed: $target"
        return
    }

    Invoke-WebRequest -Uri "https://www.7-zip.org/a/7zr.exe" -OutFile $target
    Write-Host "Installed 7z reduced console: $target"
}

function Set-AutostopSshKey {
    $candidate = Join-Path $env:USERPROFILE ".ssh\autostopcrm_server_ed25519"
    if (-not (Test-Path -LiteralPath $candidate)) {
        Write-Host "AUTOSTOPCRM_SSH_KEY was not set because the key is missing: $candidate"
        return
    }

    [Environment]::SetEnvironmentVariable("AUTOSTOPCRM_SSH_KEY", $candidate, "User")
    $env:AUTOSTOPCRM_SSH_KEY = $candidate
    Write-Host "Set AUTOSTOPCRM_SSH_KEY for current user."
}

Install-PortableGh
Install-PortableJq
Install-Portable7z
Add-UserPathEntry -PathEntry $bin
Add-ToolShims
Set-AutostopSshKey

if (-not $SkipProjectSetup) {
    & (Join-Path $PSScriptRoot "setup_dev.ps1") -InstallGitHooks
    if ($LASTEXITCODE -ne 0) {
        throw "setup_dev.ps1 failed."
    }

    $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
    & $pythonExe -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright Chromium install failed."
    }
}

if ($GithubLogin) {
    & (Join-Path $bin "gh.exe") auth login
}

if ($InstallDockerDesktop) {
    if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        throw "winget is required to install Docker Desktop."
    }
    winget install --id Docker.DockerDesktop --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop installation failed."
    }
}

if (-not $SkipDoctor) {
    & (Join-Path $PSScriptRoot "toolchain_doctor.ps1")
}
