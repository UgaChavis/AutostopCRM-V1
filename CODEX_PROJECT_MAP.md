# AutoStop CRM Codex Project Map

## Scope

This repository is the active AutoStop CRM v1 codebase for the single production board exposed at:

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- server path: `/opt/autostopcrm`
- deployment branch: `autostopcrm-v1`

## Main Entry Points

- `main.py` - desktop CRM application.
- `main_mcp.py` - MCP server entrypoint.
- `main_telegram_ai.py` - Telegram AI worker entrypoint.
- `src/minimal_kanban/api/server.py` - HTTP API routes.
- `src/minimal_kanban/services/card_service.py` - main business facade.
- `src/minimal_kanban/services/snapshot_service.py` - board snapshots, board context, search, archived/overdue views.
- `src/minimal_kanban/mcp/server.py` - MCP tool surface.
- `src/minimal_kanban/mcp/client.py` - MCP-to-API client.
- `src/minimal_kanban/web_assets.py` - browser board UI.

## Verification

Use the project virtual environment when available:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\scripts\run_checks.ps1
python scripts\check_live_connector.py --strict --expect-https --site-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp
```

## Operational Rules

- CRM is the source of truth for cards, clients, repair orders, cashboxes, deadlines and attachments.
- Do not use automatic card cleanup as a routine MCP/agent action.
- Prefer read-only MCP checks first: `ping_connector`, `get_runtime_status`, `get_board_context`, `review_board`, `search_cards`, `get_card_context`.
- Use compact snapshots for broad scans: `get_board_snapshot(compact=true, include_archive=false)`.
- For writes, identify the exact `card_id`, preserve existing phone/VIN/plate/description data, then verify with a read-back.
