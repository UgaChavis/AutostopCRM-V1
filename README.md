# AutoStop CRM

AutoStop CRM is the current working product on branch `autostopcrm-v1`.

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
- Telegram-first AI Board Manager worker for owner-controlled CRM operations
- background card enrichment action available from the card indicator
- vehicle profiles and card autofill
- repair orders with works, materials, payments, status flow, and text export
- print module for repair-order documents and inspection sheets
- operator authentication and admin user management
- cashboxes, cash transactions, employees, and payroll reports
- client-to-vehicle linking: cards can reference both `client_id` and a concrete `client_vehicle_id`
- client vehicle management: the Clients module can add, edit, and delete a client's saved cars by model, VIN, and license plate
- hidden AI-managed board summaries: cards can show a compact five-line operational summary on the board without exposing a manual user field
- shared Files workspace for workshop documents with a 500 MB server-side limit

## Runtime Modes

- Desktop application: [main.py](main.py)
- MCP server only: [main_mcp.py](main_mcp.py)
- Telegram AI worker: [main_telegram_ai.py](main_telegram_ai.py)
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

Telegram owner
        ->
Telegram AI worker
        ->
OpenAI + explicit CRM tool registry
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

Telegram AI layer:

- [main_telegram_ai.py](main_telegram_ai.py): long-running Telegram worker entrypoint
- [src/minimal_kanban/telegram_ai](src/minimal_kanban/telegram_ai): Telegram gateway, OpenAI client, tool registry, audit, verifier and orchestrator
- [docs/TELEGRAM_AI_BOARD_MANAGER.md](docs/TELEGRAM_AI_BOARD_MANAGER.md): technical runtime map and operator notes

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
- shared files: list, upload, download/open, rename, delete, copy/paste, icon positions
- operator auth and user management
- manual autofill and enrichment endpoints

The main API implementation lives in [src/minimal_kanban/api/server.py](src/minimal_kanban/api/server.py). Detailed payload docs are in [API_GUIDE.md](API_GUIDE.md).

## MCP Capability Groups

The MCP server exposes the current AutoStop CRM board and services as tools over the local API. In addition to board operations, the current tool surface includes:

- bootstrap and runtime diagnostics
- board review and GPT wall
- cashbox access
- repair-order access and updates
- client directory search, profile, vehicle, requisites, client-vehicle upsert and card-link tools
- shared file list/info/upload/download/delete tools
- agent-only card board summary updates through `set_card_board_summary`
- bounded board/card reads; automatic cleanup is intentionally not exposed as an MCP tool

The exact runtime inventory is documented in [MCP_GUIDE.md](MCP_GUIDE.md). The user-facing autofill endpoints remain available in the HTTP API and UI, but they are not MCP tools.

See [MCP_GUIDE.md](MCP_GUIDE.md) and [src/minimal_kanban/mcp/server.py](src/minimal_kanban/mcp/server.py).

## Telegram AI Board Manager

The active server-side AI expansion is now Telegram-first:

- owner sends text, voice, or photo to the Telegram bot
- worker authorizes by Telegram user ID
- worker asks OpenAI for a structured decision
- explicit `найди в интернете` / `загугли` commands and the model-planned `internet_search` tool use OpenAI `web_search_preview`
- CRM writes go through the explicit tool registry and local HTTP API
- every write is verified by read-back
- the worker can update the hidden card board summary through the same API path as MCP
- every run is written to `telegram_ai/audit.jsonl`
- compact per-chat memory is stored for follow-up commands

The worker runs as a separate Docker service named `autostopcrm-telegram-ai` and opens no public port.

Current production note:

- complex CRM planning can escalate from `gpt-5.4-mini` to `gpt-5.4`
- direct internet search intentionally stays on `gpt-5.4-mini` with low search context for stability
- verify the current Telegram AI checkpoint with `git rev-parse --short HEAD` and `scripts/check_live_connector.py`; fixed commit notes are kept only as historical context
- current clients-module release includes the inline client picker, phone matching fixes, and Chrome autofill suppression in the card and client forms

## Legacy Card Cleanup

The older card-indicator cleanup/enrichment behavior remains compatibility-only and separate from the Telegram AI manager:

- only the card indicator remains
- the compatibility path stays inside the existing CRM/card-service contour
- new AI product work should go through the Telegram AI manager
- do not extend the old VIN/green-button experiments as the primary AI runtime

Current cleanup contract:

- `read -> evidence -> patch -> write -> verify`
- normalize description without deleting meaningful user text
- fill only obvious empty local fields
- use patch-only writes
- refresh the card after write

## Local Development

Recommended local Python target:

- Python 3.12
- `PowerShell 7` is preferred for day-to-day scripting, although the repository still supports Windows PowerShell 5.1

Developer bootstrap:

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
```

Environment check:

```powershell
.\scripts\doctor.ps1
```

Lint and format checks:

```powershell
.\scripts\run_checks.ps1
```

`run_checks.ps1` also extracts generated inline JavaScript from `BOARD_WEB_APP_HTML` and validates it with `node --check`. When touching `src/minimal_kanban/web_assets.py`, the same check can be run directly:

```powershell
python scripts\check_web_assets_js.py
```

Desktop app:

```powershell
.\scripts\run_dev.ps1
```

MCP server only:

```powershell
.\scripts\run_mcp_server.ps1
```

Large client directory import:

```powershell
py -3.12 scripts/import_clients_from_markdown.py C:\path\to\clients.md --apply
```

The importer reads the cleaned Markdown JSONL format, creates a state backup before writing, preserves legal requisites, phones and saved client vehicles, and supports dry-run by omitting `--apply`. For production-scale directories, client search is designed to use saved profile vehicles before the heavier related-card fallback.

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

Server deployment is centered around branch `autostopcrm-v1`.

Main files:

- [deploy.sh](deploy.sh)
- [docker-compose.yml](docker-compose.yml)
- [Dockerfile](Dockerfile)
- [AUTOSTOPCRM_FULL_INSTRUCTION.txt](AUTOSTOPCRM_FULL_INSTRUCTION.txt)

The production compose stack currently has these services:

- `autostopcrm`: main app, local API, MCP
- `autostopcrm-telegram-ai`: optional long-polling Telegram AI worker, no public port

## Documentation Map

Read first:

- [00_START_HERE_AUTOSTOP_CRM.md](00_START_HERE_AUTOSTOP_CRM.md): visible root-level onboarding file for the next developer or agent
- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md): current developer handoff, architecture snapshot, and latest development state
- [README.md](README.md): current project overview
- [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md): sync, deploy, and verification workflow
- [API_GUIDE.md](API_GUIDE.md): local API contract
- [MCP_GUIDE.md](MCP_GUIDE.md): MCP architecture and runtime behavior

Keep when relevant to the touched workflow:

- [MASTER-PLAN.md](MASTER-PLAN.md): product/module plan; still referenced by scripts and curated AI knowledge
- [AUTOSTOPCRM_FULL_INSTRUCTION.txt](AUTOSTOPCRM_FULL_INSTRUCTION.txt): legacy server/deployment operator notes
- [README_SETTINGS.md](README_SETTINGS.md): integration settings model
- [CHATGPT_CONNECTOR_SETUP.md](CHATGPT_CONNECTOR_SETUP.md): retained under this exact name because runtime/tests copy and reference it
- [docs/PRINT_DOCUMENTS.md](docs/PRINT_DOCUMENTS.md): print module and template verification
- [docs/TELEGRAM_AI_BOARD_MANAGER.md](docs/TELEGRAM_AI_BOARD_MANAGER.md): Telegram AI technical map
- [docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md](docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md): Telegram AI setup guide

Cleanup notes:

- obsolete AI remake notes and GPT-agent docs were removed
- frozen historical test reports were removed
- duplicated workflow, project-memory, module-note and stale MCP command references are merged into the canonical docs above instead of kept as separate files

## Current Branch Policy

- working branch: `autostopcrm-v1`
