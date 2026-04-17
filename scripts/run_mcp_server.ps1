param(
    [string]$ApiHost = "",
    [int]$ApiPort = 0,
    [string]$ApiBaseUrl = "",
    [string]$McpHost = "",
    [int]$McpPort = 0,
    [string]$McpPath = "",
    [string]$PublicBaseUrl = "",
    [string]$PublicMcpUrl = "",
    [string]$ApiBearerToken = "",
    [string]$McpBearerToken = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_bootstrap.ps1")

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
    New-ProjectVirtualEnvironment -VenvPath $venvPath | Out-Null
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

$env:PYTHONUNBUFFERED = "1"
if (-not $env:MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS) {
    $env:MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS = "1"
}

if (-not $ApiHost) {
    $ApiHost = $env:MINIMAL_KANBAN_API_HOST
}
if (-not $ApiHost) {
    $ApiHost = "0.0.0.0"
}
$env:MINIMAL_KANBAN_API_HOST = $ApiHost

if ($ApiPort -gt 0) {
    $env:MINIMAL_KANBAN_API_PORT = [string]$ApiPort
}
if ($ApiBaseUrl) {
    $env:MINIMAL_KANBAN_API_BASE_URL = $ApiBaseUrl.Trim().TrimEnd("/")
}
if ($ApiBearerToken) {
    $env:MINIMAL_KANBAN_API_BEARER_TOKEN = $ApiBearerToken
}

if (-not $McpHost) {
    $McpHost = $env:MINIMAL_KANBAN_MCP_HOST
}
if (-not $McpHost) {
    $McpHost = "0.0.0.0"
}
$env:MINIMAL_KANBAN_MCP_HOST = $McpHost

if ($McpPort -gt 0) {
    $env:MINIMAL_KANBAN_MCP_PORT = [string]$McpPort
}
if ($McpPath) {
    $env:MINIMAL_KANBAN_MCP_PATH = $McpPath
}
if ($PublicBaseUrl) {
    $env:MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL = $PublicBaseUrl.Trim().TrimEnd("/")
}
if ($PublicMcpUrl) {
    $env:MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL = $PublicMcpUrl.Trim().TrimEnd("/")
}
if ($McpBearerToken) {
    $env:MINIMAL_KANBAN_MCP_BEARER_TOKEN = $McpBearerToken
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
& $pythonExe -m minimal_kanban.mcp.main
Assert-LastExitCode "Run MCP server"
