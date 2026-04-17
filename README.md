# AutoStop CRM

AutoStop CRM is the current working product on branch `autostopCRM`.

The repository still contains legacy technical names from the earlier `Minimal Kanban` stage. They are still valid and expected:

- Python package: `minimal_kanban`
- local data directory: `%APPDATA%\Minimal Kanban`
- portable executable: `Start Kanban.exe`
- some connector and MCP texts still use the `Minimal Kanban` name for backward compatibility

## What This Branch Contains

- desktop CRM application for workshop operators
- kanban board with cards, custom columns, sticky notes, archive, unread and updated markers
- local HTTP API for the UI, reverse proxy, and automation
- MCP server for ChatGPT / OpenAI tools / connector workflows
- local card cleanup action available from the card indicator
- vehicle profiles and card autofill
- repair orders with works, materials, payments, status flow, and text export
- print module for repair-order documents and inspection sheets
- operator authentication and admin user management
- cashboxes, cash transactions, employees, and payroll reports

## Runtime Modes

- Desktop application: [main.py](main.py)
- MCP server only: [main_mcp.py](main_mcp.py)
- Retired compatibility stub: [main_agent.py](main_agent.py)
- Containerized deployment: [docker-compose.yml](docker-compose.yml)

## Architecture

The project is split into layered runtimes instead of mixing business logic into the UI.

```text
Desktop UI / Browser UI
        ->
local HTTP API
        ->
CardService + focused domain services
        ->
JsonStore / local state files

ChatGPT / OpenAI / external MCP client
        ->
MCP server
        ->
local HTTP API
        ->
same CardService and storage
```

## Code Map

Core entrypoints and runtime assembly:

- [src/minimal_kanban/app.py](src/minimal_kanban/app.py): desktop bootstrap, splash screen, API startup, auth service, settings, MCP and tunnel controllers
- [src/minimal_kanban/config.py](src/minimal_kanban/config.py): paths, ports, environment variables
- [src/minimal_kanban/logging_setup.py](src/minimal_kanban/logging_setup.py): runtime logging

Domain and storage:

- [src/minimal_kanban/models.py](src/minimal_kanban/models.py): cards, tags, attachments, stickies, cashboxes, employees, audit models
- [src/minimal_kanban/vehicle_profile.py](src/minimal_kanban/vehicle_profile.py): vehicle profile schema and normalization
- [src/minimal_kanban/repair_order.py](src/minimal_kanban/repair_order.py): repair-order domain model
- [src/minimal_kanban/storage/json_store.py](src/minimal_kanban/storage/json_store.py): persistent JSON bundle storage

Business logic:

- [src/minimal_kanban/services/card_service.py](src/minimal_kanban/services/card_service.py): main orchestration service
- [src/minimal_kanban/services/column_service.py](src/minimal_kanban/services/column_service.py): column operations
- [src/minimal_kanban/services/snapshot_service.py](src/minimal_kanban/services/snapshot_service.py): board snapshots, GPT wall, search, compact reads
- [src/minimal_kanban/services/vehicle_profile_service.py](src/minimal_kanban/services/vehicle_profile_service.py): vehicle autofill normalization

API and access control:

- [src/minimal_kanban/api/server.py](src/minimal_kanban/api/server.py): local HTTP API, operator session gates, proxy-aware write protection
- [src/minimal_kanban/operator_auth.py](src/minimal_kanban/operator_auth.py): operator login, admin users, session handling

MCP layer:

- [src/minimal_kanban/mcp/server.py](src/minimal_kanban/mcp/server.py): MCP tool surface
- [src/minimal_kanban/mcp/client.py](src/minimal_kanban/mcp/client.py): HTTP client to local API
- [src/minimal_kanban/mcp/runtime.py](src/minimal_kanban/mcp/runtime.py): MCP server runtime
- [src/minimal_kanban/mcp/oauth_provider.py](src/minimal_kanban/mcp/oauth_provider.py): embedded OAuth metadata and provider logic

Retired agent layer:

- old server-agent modules still exist in the repository as legacy code
- they are no longer part of the active product runtime, startup path, or deploy topology
- the visible product keeps only the local card cleanup action

Printing and documents:

- [src/minimal_kanban/printing/service.py](src/minimal_kanban/printing/service.py): print workspace, templates, preview, export
- [src/minimal_kanban/printing/pdf.py](src/minimal_kanban/printing/pdf.py): PDF generation
- [src/minimal_kanban/printing/template_engine.py](src/minimal_kanban/printing/template_engine.py): document rendering

UI:

- [src/minimal_kanban/ui/main_window.py](src/minimal_kanban/ui/main_window.py): desktop shell and operator workspace
- [src/minimal_kanban/ui/settings_window.py](src/minimal_kanban/ui/settings_window.py): integration, MCP, auth, diagnostics settings
- [src/minimal_kanban/web_assets.py](src/minimal_kanban/web_assets.py): browser-facing board UI HTML, CSS, and JS

## Main API Capability Groups

The local API is broader than the original board-only stage. Current route groups include:

- board and card operations
- compact board snapshots and GPT wall
- repair-order CRUD and status transitions
- inspection sheet forms
- print preview, PDF export, and template management
- cashboxes and cash transactions
- employees and payroll
- operator auth and user management
- local card cleanup

The main API implementation lives in [src/minimal_kanban/api/server.py](src/minimal_kanban/api/server.py). Detailed payload docs are in [API_GUIDE.md](API_GUIDE.md).

## MCP Capability Groups

The MCP server exposes the current AutoStop CRM board and services as tools over the local API. In addition to board operations, the current tool surface includes:

- bootstrap and runtime diagnostics
- board review and GPT wall
- cashbox access
- repair-order access and updates
- local card cleanup and bounded board/card reads

See [MCP_GUIDE.md](MCP_GUIDE.md) and [src/minimal_kanban/mcp/server.py](src/minimal_kanban/mcp/server.py).

## Card Cleanup

The visible AI behavior in the product is intentionally narrow:

- only the card indicator remains
- clicking it runs a local deterministic cleanup flow
- the cleanup path stays inside `CardService`
- no internet, no MCP dependency in the user flow, and no server worker are involved

Current cleanup contract:

- `read -> evidence -> patch -> write -> verify`
- normalize description without deleting meaningful user text
- fill only obvious empty local fields
- use patch-only writes
- refresh the card after write

## Local Development

Desktop app:

```powershell
.\scripts\run_dev.ps1
```

MCP server only:

```powershell
.\scripts\run_mcp_server.ps1
```

Retired compatibility entry:

```powershell
.\.venv\Scripts\python.exe main_agent.py
```

## Tests and Verification

Main regression run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Full quality pass:

```powershell
.\scripts\run_quality_pass.ps1
```

Important test areas:

- API: [tests/test_api.py](tests/test_api.py)
- service layer: [tests/test_service.py](tests/test_service.py)
- MCP: [tests/test_mcp.py](tests/test_mcp.py), [tests/test_mcp_main.py](tests/test_mcp_main.py)
- printing: [tests/test_printing_service.py](tests/test_printing_service.py)
- settings and UI: [tests/test_settings_service.py](tests/test_settings_service.py), [tests/test_settings_ui.py](tests/test_settings_ui.py), [tests/test_ui_smoke.py](tests/test_ui_smoke.py)

## Data Paths

Desktop and local runtime:

- `%APPDATA%\Minimal Kanban\state.json`
- `%APPDATA%\Minimal Kanban\settings.json`
- `%APPDATA%\Minimal Kanban\attachments`
- `%APPDATA%\Minimal Kanban\repair-orders`
- `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`

Docker deployment:

- host data directory: `./data`
- container data directory: `/root/.minimal-kanban`

## Deployment

Server deployment is centered around branch `autostopCRM`.

Main files:

- [deploy.sh](deploy.sh)
- [docker-compose.yml](docker-compose.yml)
- [Dockerfile](Dockerfile)
- [AUTOSTOPCRM_FULL_INSTRUCTION.txt](AUTOSTOPCRM_FULL_INSTRUCTION.txt)

The production compose stack currently has one service:

- `autostopcrm`: main app, local API, MCP

## Documentation Map

Read first:

- [00_START_HERE_AUTOSTOP_CRM.md](00_START_HERE_AUTOSTOP_CRM.md): visible root-level onboarding file for the next developer or agent
- [MASTER-PLAN.md](MASTER-PLAN.md): central architecture plan with module tree, internal versions, and parallel development lanes
- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md): current developer handoff, architecture snapshot, and latest development state
- [README.md](README.md): current project overview
- [AUTOSTOPCRM_FULL_INSTRUCTION.txt](AUTOSTOPCRM_FULL_INSTRUCTION.txt): server and deployment operations
- [API_GUIDE.md](API_GUIDE.md): local API contract
- [MCP_GUIDE.md](MCP_GUIDE.md): MCP architecture and runtime behavior
- [README_SETTINGS.md](README_SETTINGS.md): integration settings model

Active agent and operations docs kept in the root:

- `GPT_AGENT_04_SERVER_AGENT_API_AND_COMMANDS.txt`
- `GPT_AGENT_09_MCP_COMMAND_CATALOG.md`
- `GPT_AGENT_10_MCP_OPERATION_FLOWS.md`
- `GPT_AGENT_11_AGENT_AUTOFILL_ORCHESTRATION.md`
- `CHATGPT_CONNECTOR_SETUP.md` remains in the root because the runtime and tests reference that exact path

Removed from the root during the April 2026 cleanup:

- obsolete portable-install docs
- frozen historical test reports
- superseded MCP / GPT setup notes
- duplicated documentation bundles that only mirrored the root files

## Current Branch Policy

- working branch: `autostopCRM`
- `autostopCRM-V2` is a separate line and is not the active production branch for this repository state
