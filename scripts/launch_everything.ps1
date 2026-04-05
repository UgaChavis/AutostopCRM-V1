param()

$ErrorActionPreference = "Stop"
$launchMutex = [System.Threading.Mutex]::new($false, "Local\MinimalKanbanLaunchEverything")
if (-not $launchMutex.WaitOne(0)) {
    exit 0
}

function Get-EffectiveMcpUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SettingsPath
    )

    if (-not (Test-Path $SettingsPath)) {
        return ""
    }

    try {
        $payload = Get-Content -Path $SettingsPath -Raw | ConvertFrom-Json
    }
    catch {
        return ""
    }

    if ($null -eq $payload -or $null -eq $payload.mcp) {
        return ""
    }

    $mcp = $payload.mcp
    $mcpPath = [string]$mcp.mcp_path
    if (-not $mcpPath) {
        $mcpPath = "/mcp"
    }
    if (-not $mcpPath.StartsWith("/")) {
        $mcpPath = "/$mcpPath"
    }

    $fullOverride = [string]$mcp.full_mcp_url_override
    if ($fullOverride) {
        return $fullOverride.Trim().TrimEnd("/")
    }

    $publicBaseUrl = [string]$mcp.public_https_base_url
    if ($publicBaseUrl) {
        return "$($publicBaseUrl.Trim().TrimEnd('/'))$mcpPath"
    }

    $tunnelUrl = [string]$mcp.tunnel_url
    if ($tunnelUrl) {
        return "$($tunnelUrl.Trim().TrimEnd('/'))$mcpPath"
    }

    $localMcpUrl = [string]$mcp.local_mcp_url
    if ($localMcpUrl) {
        return $localMcpUrl.Trim().TrimEnd("/")
    }

    return ""
}

function Get-EffectiveLocalApiUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SettingsPath
    )

    if (-not (Test-Path $SettingsPath)) {
        return "http://127.0.0.1:41731"
    }

    try {
        $payload = Get-Content -Path $SettingsPath -Raw | ConvertFrom-Json
    }
    catch {
        return "http://127.0.0.1:41731"
    }

    if ($null -eq $payload -or $null -eq $payload.local_api) {
        return "http://127.0.0.1:41731"
    }

    $effectiveLocalApiUrl = [string]$payload.local_api.effective_local_api_url
    if (-not $effectiveLocalApiUrl) {
        return "http://127.0.0.1:41731"
    }

    return $effectiveLocalApiUrl
}

function Get-ConnectorAuthMode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SettingsPath
    )

    if (-not (Test-Path $SettingsPath)) {
        return "none"
    }

    try {
        $payload = Get-Content -Path $SettingsPath -Raw | ConvertFrom-Json
    }
    catch {
        return "none"
    }

    if ($null -eq $payload -or $null -eq $payload.mcp) {
        return "none"
    }

    $authMode = [string]$payload.mcp.mcp_auth_mode
    if ($authMode.Trim().ToLower() -eq "bearer") {
        return "bearer"
    }

    return "none"
}

function Get-ConnectorAuthLabel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AuthMode
    )

    if ($AuthMode.Trim().ToLower() -eq "bearer") {
        return "Bearer token"
    }

    return "No authentication"
}

function Test-McpUrlReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$McpUrl
    )

    if (-not $McpUrl -or $McpUrl -notlike "https://*") {
        return $false
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $McpUrl -TimeoutSec 15
        return $response.StatusCode -in @(200, 401, 406)
    }
    catch {
        if ($_.Exception.Response) {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode.value__
                return $statusCode -in @(200, 401, 406)
            }
            catch {
                return $false
            }
        }
        return $false
    }
}

function Get-ReadyPublicMcpUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SettingsPath,
        [Parameter(Mandatory = $true)]
        [string]$TunnelStatePath
    )

    $effectiveMcpUrl = Get-EffectiveMcpUrl -SettingsPath $SettingsPath
    if (Test-McpUrlReady -McpUrl $effectiveMcpUrl) {
        return $effectiveMcpUrl
    }

    $persistedTunnelState = Get-PersistedTunnelState -StatePath $TunnelStatePath
    if ($null -eq $persistedTunnelState) {
        return ""
    }

    $publicUrl = [string]$persistedTunnelState.public_url
    if (-not $publicUrl) {
        return ""
    }

    $publicMcpUrl = "$($publicUrl.TrimEnd('/'))/mcp"
    if (Test-McpUrlReady -McpUrl $publicMcpUrl) {
        return $publicMcpUrl
    }

    return ""
}

function Write-ConnectorFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$McpUrl,
        [Parameter(Mandatory = $true)]
        [string]$LocalApiUrl,
        [string]$AuthMode = "none"
    )

    $desktopPath = [Environment]::GetFolderPath("Desktop")
    $hostLabel = "current-connector"
    $authLabel = Get-ConnectorAuthLabel -AuthMode $AuthMode
    try {
        $hostLabel = ([Uri]$McpUrl).Host
    }
    catch {
    }

    $connectionCardPath = Join-Path $desktopPath "GPT_MCP_CONNECTION_CARD.txt"
    $connectorJsonPath = Join-Path $desktopPath "chatgpt-connector.json"
    $authNotePath = Join-Path $desktopPath "Minimal Kanban Auth Note.txt"
    $urlPath = Join-Path $desktopPath "Minimal Kanban URL.txt"

    $connectionCard = @"
Minimal Kanban / This Board Only ($hostLabel) -> ChatGPT / MCP

[KEY VALUES]
connector_auth_mode = $AuthMode
effective_mcp_url = $McpUrl
effective_local_api_url = $LocalApiUrl

Connection flow:
1. Start the app from the desktop shortcut.
2. Open ChatGPT -> Settings -> Apps & Connectors -> Connectors -> Create.
3. Paste effective_mcp_url.
4. Choose $authLabel.
5. Create the connector.
6. In a new chat call ping_connector, then bootstrap_context.
"@

    $connectorJson = @"
{
  "name": "Minimal Kanban / This Board Only ($hostLabel)",
  "description": "Single-board connector for the current Minimal Kanban board only.",
  "connector_url": "$McpUrl",
  "auth_mode": "$AuthMode",
  "notes": [
    "Use the public HTTPS /mcp URL.",
    "Authentication mode: $authLabel.",
    "First call should be ping_connector.",
    "Second call should be bootstrap_context."
  ]
}
"@

    $authNote = @"
ChatGPT connector

URL:
$McpUrl

Authentication:
$authLabel

First checks:
1. ping_connector
2. bootstrap_context
"@

    Write-Utf8TextNoBom -Path $connectionCardPath -Value $connectionCard
    Write-Utf8TextNoBom -Path $connectorJsonPath -Value $connectorJson
    Write-Utf8TextNoBom -Path $authNotePath -Value $authNote
    Write-Utf8TextNoBom -Path $urlPath -Value $McpUrl

    try {
        Set-Clipboard -Value $McpUrl
    }
    catch {
    }
}

function Write-PendingConnectorFiles {
    param(
        [string]$AuthMode = "none",
        [string]$LocalApiUrl = "http://127.0.0.1:41731"
    )

    $desktopPath = [Environment]::GetFolderPath("Desktop")
    $authLabel = Get-ConnectorAuthLabel -AuthMode $AuthMode
    $connectionCard = @"
Minimal Kanban / This Board Only (current-connector) -> ChatGPT / MCP

[KEY VALUES]
connector_auth_mode = $AuthMode
effective_mcp_url = 
effective_local_api_url = $LocalApiUrl

Connection flow:
1. Start the app from the desktop shortcut.
2. Open ChatGPT -> Settings -> Apps & Connectors -> Connectors -> Create.
3. Paste effective_mcp_url after the public HTTPS MCP URL appears.
4. Choose $authLabel.
5. Create the connector.
6. In a new chat call ping_connector, then bootstrap_context.
"@

    $connectorJson = @"
{
  "name": "Minimal Kanban / This Board Only (current-connector)",
  "description": "Single-board connector for the current Minimal Kanban board only.",
  "connector_url": "",
  "auth_mode": "$AuthMode",
  "notes": [
    "Wait for the public HTTPS /mcp URL to appear.",
    "Authentication mode: $authLabel.",
    "First call should be ping_connector.",
    "Second call should be bootstrap_context."
  ]
}
"@

    $authNote = @"
ChatGPT connector

URL:


Authentication:
$authLabel

First checks:
1. ping_connector
2. bootstrap_context
"@

    Write-Utf8TextNoBom -Path (Join-Path $desktopPath "GPT_MCP_CONNECTION_CARD.txt") -Value $connectionCard
    Write-Utf8TextNoBom -Path (Join-Path $desktopPath "chatgpt-connector.json") -Value $connectorJson
    Write-Utf8TextNoBom -Path (Join-Path $desktopPath "Minimal Kanban Auth Note.txt") -Value $authNote
    Write-Utf8TextNoBom -Path (Join-Path $desktopPath "Minimal Kanban URL.txt") -Value ""
}

function Write-Utf8TextNoBom {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $utf8NoBom)
}

function Get-PersistedTunnelState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePath
    )

    if (-not (Test-Path $StatePath)) {
        return $null
    }

    try {
        $payload = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }

    if ($null -eq $payload) {
        return $null
    }

    $processId = 0
    try {
        $processId = [int]$payload.pid
    }
    catch {
        return $null
    }

    if ($processId -le 0) {
        return $null
    }

    try {
        Get-Process -Id $processId -ErrorAction Stop | Out-Null
    }
    catch {
        return $null
    }

    $publicUrl = [string]$payload.public_url
    if (-not $publicUrl) {
        return $null
    }

    $publicMcpUrl = "$($publicUrl.TrimEnd('/'))/mcp"
    if (-not (Test-McpUrlReady -McpUrl $publicMcpUrl)) {
        return $null
    }

    return $payload
}

function Start-PublicTunnelFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$RepoPython
    )

    if (-not (Test-Path $RepoPython)) {
        return ""
    }

    $script = @'
import json
import sys
from pathlib import Path

src = (Path.cwd() / "src").resolve()
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from minimal_kanban.logging_setup import close_logger, configure_logging
from minimal_kanban.settings_service import SettingsService
from minimal_kanban.settings_store import SettingsStore
from minimal_kanban.tunnel_runtime import TunnelRuntimeController

logger = configure_logging()
try:
    service = SettingsService(SettingsStore(logger=logger), logger)
    settings = service.load()
    controller = TunnelRuntimeController(logger=logger)
    state = controller.start(settings)
    print(json.dumps({
        "running": bool(state.running),
        "public_url": state.public_url,
        "error": state.error,
        "details": state.details,
    }, ensure_ascii=False))
finally:
    close_logger(logger)
'@

    Push-Location $ProjectRoot
    try {
        $result = $script | & $RepoPython -
        if ($LASTEXITCODE -ne 0 -or -not $result) {
            return ""
        }
    }
    finally {
        Pop-Location
    }

    try {
        $payload = $result | ConvertFrom-Json
    }
    catch {
        return ""
    }

    if (-not $payload.running) {
        return ""
    }

    return [string]$payload.public_url
}

function Update-TunnelSettings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SettingsPath,
        [Parameter(Mandatory = $true)]
        [string]$PublicBaseUrl,
        [string]$RepoPython = ""
    )

    $publicBaseUrl = [string]$PublicBaseUrl
    $publicBaseUrl = $publicBaseUrl.Trim().TrimEnd("/")
    if (-not $publicBaseUrl) {
        return
    }

    $settingsPathEscaped = $SettingsPath.Replace("\", "\\")
    $publicBaseUrlEscaped = $publicBaseUrl.Replace("\", "\\")
    $script = @"
import json
from pathlib import Path

settings_path = Path(r"$settingsPathEscaped")
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
else:
    data = {}

public_base_url = r"$publicBaseUrlEscaped"
mcp = data.setdefault("mcp", {})
mcp["tunnel_url"] = public_base_url

settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
"@

    if ($RepoPython -and (Test-Path $RepoPython)) {
        $script | & $RepoPython -
        return
    }

    $script | py -3.13 -
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$appExe = Join-Path $projectRoot "dist\\MinimalKanban\\MinimalKanban.exe"
$appWorkingDirectory = Split-Path -Parent $appExe
$repoPython = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
$mainPy = Join-Path $projectRoot "main.py"
$mainPyArgument = "main.py"
$persistedTunnelStatePath = Join-Path $env:TEMP "minimal-kanban\\tunnel-state.json"
$persistedTunnelState = Get-PersistedTunnelState -StatePath $persistedTunnelStatePath
$persistedTunnelPid = $null
if ($null -ne $persistedTunnelState) {
    $persistedTunnelPid = [int]$persistedTunnelState.pid
}

if (-not (Test-Path $mainPy)) {
    throw "Application entrypoint was not found: $mainPy"
}

$mainPyPattern = [regex]::Escape($mainPy)
$mainPyArgumentPattern = '(?:^|[\s"''])main\.py(?:$|[\s"''])'
$runningApp = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -ieq "MinimalKanban.exe" -or
    (
        $_.Name -in @("python.exe", "pythonw.exe") -and
        ($_.CommandLine -match $mainPyPattern -or $_.CommandLine -match $mainPyArgumentPattern)
    )
} | Select-Object -First 1
$shouldLaunchApp = $null -eq $runningApp

if ($shouldLaunchApp) {
    # Remove stale headless MCP runs so the GUI app can own backend and MCP lifecycle.
    $staleProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq "python.exe" -and $_.CommandLine -match "main_mcp\.py|minimal_kanban\.mcp\.main"
    }

    foreach ($process in $staleProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    $staleTunnels = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq "cloudflared.exe" -and $_.CommandLine -match "tunnel" -and $_.CommandLine -match "127\.0\.0\.1:41831"
    }

    foreach ($process in $staleTunnels) {
        if ($null -ne $persistedTunnelPid -and $process.ProcessId -eq $persistedTunnelPid) {
            continue
        }
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    if ($null -eq $persistedTunnelPid -and (Test-Path $persistedTunnelStatePath)) {
        Remove-Item -Path $persistedTunnelStatePath -Force -ErrorAction SilentlyContinue
    }
}

$settingsDirectory = Join-Path $env:APPDATA "Minimal Kanban"
$settingsPath = Join-Path $settingsDirectory "settings.json"
New-Item -ItemType Directory -Force -Path $settingsDirectory | Out-Null

$desktopPath = [Environment]::GetFolderPath("Desktop")
$urlPath = Join-Path $desktopPath "Minimal Kanban URL.txt"
if ($shouldLaunchApp -and $null -eq $persistedTunnelPid) {
    Write-PendingConnectorFiles `
        -AuthMode (Get-ConnectorAuthMode -SettingsPath $settingsPath) `
        -LocalApiUrl (Get-EffectiveLocalApiUrl -SettingsPath $settingsPath)
}

$settingsPathEscaped = $settingsPath.Replace("\", "\\")
$clearTunnelState = if ($null -eq $persistedTunnelPid) { "True" } else { "False" }
$pythonScript = @"
import json
from pathlib import Path

clear_tunnel_state = $clearTunnelState

settings_path = Path(r"$settingsPathEscaped")
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
else:
    data = {}

data.setdefault("general", {})
data.setdefault("local_api", {})
data.setdefault("mcp", {})
data.setdefault("auth", {})

existing_allowed_hosts = list(data["mcp"].get("allowed_hosts") or [])
existing_allowed_origins = list(data["mcp"].get("allowed_origins") or [])

for host_pattern in ("*.trycloudflare.com", "*.ngrok-free.dev"):
    if host_pattern not in existing_allowed_hosts:
        existing_allowed_hosts.append(host_pattern)

for origin_pattern in ("https://*.trycloudflare.com", "https://*.ngrok-free.dev"):
    if origin_pattern not in existing_allowed_origins:
        existing_allowed_origins.append(origin_pattern)

data["general"]["integration_enabled"] = True
data["general"]["use_local_api"] = True
data["general"]["auto_connect_on_startup"] = True
data["general"]["test_mode"] = True
data["local_api"]["local_api_auth_mode"] = data["local_api"].get("local_api_auth_mode") or "none"
data["mcp"]["mcp_enabled"] = True
data["mcp"]["allowed_hosts"] = existing_allowed_hosts
data["mcp"]["allowed_origins"] = existing_allowed_origins
data["mcp"]["mcp_auth_mode"] = data["mcp"].get("mcp_auth_mode") or "none"
data["mcp"]["mcp_bearer_token"] = data["mcp"].get("mcp_bearer_token") or ""
if clear_tunnel_state:
    data["mcp"]["tunnel_url"] = ""
data["auth"]["auth_mode"] = data["auth"].get("auth_mode") or "none"
data["auth"]["mcp_bearer_token"] = data["auth"].get("mcp_bearer_token") or data["mcp"]["mcp_bearer_token"]

settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
"@

if (Test-Path $repoPython) {
    $pythonScript | & $repoPython -
}
else {
    $pythonScript | py -3.13 -
}

if ($shouldLaunchApp) {
    if (Test-Path $repoPython) {
        Start-Process -FilePath $repoPython -ArgumentList @($mainPyArgument) -WorkingDirectory $projectRoot -WindowStyle Hidden
    }
    elseif (Test-Path $appExe) {
        Start-Process -FilePath $appExe -WorkingDirectory $appWorkingDirectory
    }
    else {
        throw "Neither source runtime nor packaged application was found."
    }
}

$effectiveMcpUrl = Get-EffectiveMcpUrl -SettingsPath $settingsPath
if ($effectiveMcpUrl -notlike "https://*") {
    $effectiveMcpUrl = ""
}

if ($effectiveMcpUrl) {
    Write-ConnectorFiles `
        -McpUrl $effectiveMcpUrl `
        -LocalApiUrl (Get-EffectiveLocalApiUrl -SettingsPath $settingsPath) `
        -AuthMode (Get-ConnectorAuthMode -SettingsPath $settingsPath)
}

if ($null -ne $launchMutex) {
    try {
        $launchMutex.ReleaseMutex()
    }
    catch {
    }
    $launchMutex.Dispose()
}
