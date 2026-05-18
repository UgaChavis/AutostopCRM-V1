# AutoStop CRM Project Handoff

Primary handoff for branch `autostopcrm-v1`.

Read after `00_START_HERE_AUTOSTOP_CRM.md`. Use `docs/OPERATIONS_RUNBOOK.md` for commands and production checks.

## Current Product

AutoStop CRM is a production-oriented workshop CRM with:

- kanban board, cards, columns, archive, deadlines, tags, attachments and stickies;
- clients module with phones, requisites, saved vehicles and optional card links;
- repair orders with works, materials, payments, status flow and PDF export;
- cashboxes, cash journal, employees and payroll;
- shared Files workspace;
- local HTTP API;
- MCP endpoint for ChatGPT/OpenAI-compatible clients;
- Telegram AI Board Manager for owner commands through text, voice and photo.

Legacy names are expected:

- package: `minimal_kanban`;
- app data: `%APPDATA%\Minimal Kanban`;
- portable executable: `Start Kanban.exe`.

## Environments

- branch: `autostopcrm-v1`
- local workspace: `C:\Users\9860606\Desktop\AutostopCRM\autostopcrm`
- production repo: `/opt/autostopcrm`
- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- manager knowledge vault: `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM`
  with local desktop mirror `C:\Users\User\Desktop\Obsidian CRM\AutostopCRM`

Не доверяйте pinned commit notes в документации. Перед release work проверяйте local, `origin/autostopcrm-v1` и production по command output.

Production still has a known credential-hygiene risk around the default admin account. Rotate it only as a controlled separate pass.

## Runtime Shape

```text
Desktop/browser UI
  -> local HTTP API
  -> CardService + services
  -> JsonStore

MCP client
  -> MCP server
  -> local HTTP API
  -> same business core

Telegram owner
  -> Telegram AI worker
  -> OpenAI + CRM tool registry
  -> local HTTP API
  -> verify + audit
```

## Code Map

Entrypoints:

- `main.py`
- `main_mcp.py`
- `main_telegram_ai.py`

Core:

- `src/minimal_kanban/app.py`
- `src/minimal_kanban/config.py`
- `src/minimal_kanban/models.py`
- `src/minimal_kanban/storage/json_store.py`

Business services:

- `src/minimal_kanban/services/card_service.py`
- `src/minimal_kanban/services/column_service.py`
- `src/minimal_kanban/services/snapshot_service.py`
- `src/minimal_kanban/services/vehicle_profile_service.py`

API/auth:

- `src/minimal_kanban/api/server.py`
- `src/minimal_kanban/operator_auth.py`

MCP:

- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/mcp/runtime.py`
- `src/minimal_kanban/mcp/oauth_provider.py`

Telegram AI:

- `src/minimal_kanban/telegram_ai/`
- `docs/TELEGRAM_AI_BOARD_MANAGER.md`

UI and printing:

- `src/minimal_kanban/web_assets.py`
- `src/minimal_kanban/web_app_assets/assembler.py`
- `src/minimal_kanban/ui/main_window.py`
- `src/minimal_kanban/ui/settings_window.py`
- `src/minimal_kanban/printing/service.py`

## Current Development Focus

Stable enough for iterative production work:

- board core, columns, cards and archive;
- local API and operator auth;
- MCP transport and current tool registry;
- clients, client vehicles, repair orders, cashboxes and files;
- browser UI modal ladder for card, repair-order, client, employee, cashbox and file workflows;
- Telegram AI foundation with audit, conversation memory, text/voice/photo intake and explicit internet-search route;
- generated browser JavaScript validation.

Areas that still need care:

- production credential rotation;
- production/local/GitHub drift checks before deploy;
- Telegram AI composed workflows that mix CRM context, internet search and writeback;
- large `web_assets.py` / `web_app_assets` changes should remain measured and well-tested;
- modal navigation must preserve parent context when a child window closes, especially employees, clients, repair orders, cashboxes and card payments;
- docs should stay short and canonical.

## AI And Cleanup Rules

- New AI product work should go through Telegram AI and MCP/local API paths.
- The old card indicator/enrichment path is compatibility behavior.
- For manager-agent CRM/MCP/connector work, use AutostopManager plus the
  AutoStop Obsidian vault as the human-readable knowledge layer. Prefer
  `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM\Home.md` when available.
- Obsidian may hold safe manager snapshots and quality signals only. Raw
  client databases, phone rows, VIN/license tables, cashbox ledgers, and full
  repair-order text remain in live CRM unless the owner explicitly approves the
  exact cloud export.
- Agent cleanup follows `read -> evidence -> patch -> write -> verify`.
- Не move/archive cards и не меняйте payments, works, materials, files, clients или repair orders без явной команды owner.
- Keep full card details in `description`.
- Keep board preview in `board_summary`, no more than 4-5 operator-facing lines.
- Refresh `board_summary` after content/profile/tag changes and verify `board_summary_stale=false`.
- VIN/profile enrichment may fill aggregate vehicle fields only from source-backed evidence and must preserve manual fields.

## Verification Baseline

Use the current commands, not old counts:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch origin autostopcrm-v1 --prune
git rev-parse --short origin/autostopcrm-v1
python scripts\audit_localization.py
```

For code/UI changes:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

For deployment and live smoke, use `docs/OPERATIONS_RUNBOOK.md`.

## Documentation Rule

Canonical docs:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `MCP_GUIDE.md`
- `API_GUIDE.md`

Workflow docs остаются только пока они обслуживают active code или operator flows:

- `CHATGPT_CONNECTOR_SETUP.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `docs/TELEGRAM_AI_BOARD_MANAGER.md`

Не возвращайте historical commit lists, frozen smoke reports и one-off plan files в active documentation.
