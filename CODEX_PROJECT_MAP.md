# AutoStopCRM Codex Project Map

## Canonical Repo

- GitHub: `UgaChavis/AutostopCRM-V1`
- Branch: `autostopcrm-v1`
- Local workspace for this checkout: `C:\Users\User\Desktop\AutostopCRM-V1`
- Server workspace: `/opt/autostopcrm`
- Public endpoints: `https://crm.autostopcrm.ru` and `https://crm.autostopcrm.ru/mcp`

## Read First

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `docs/OPERATIONS_RUNBOOK.md`
5. `MCP_GUIDE.md` or `API_GUIDE.md` only when the task needs integration details
6. For manager-agent knowledge, also use the AutoStop Obsidian cloud vault at
   `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM`

## Main Work Areas

- `src/minimal_kanban/api/server.py`
- `src/minimal_kanban/services/card_service.py`
- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/telegram_ai/`
- `src/minimal_kanban/web_assets.py` - public browser HTML facade/export
- `src/minimal_kanban/web_app_assets/` - assembled browser UI, modal stack and cash journal UI
- `scripts/`
- `tests/`
- `deploy.sh`
- `docker-compose.yml`

## Runtime Flow

```text
UI -> local API -> CardService -> JsonStore
MCP -> local API -> CardService
Telegram AI -> CRM tool registry -> local API -> verify/audit
```

## Current UI Rules

- Modal flows use a stack: child windows close back to the parent context.
- Operator login gate hides the board until a valid operator session exists.
- Cashboxes are journal-first. Finance audit/reconciliation remains internal
  backend/API/CLI diagnostics, not an operator UI section.
- Cash journal rows should be compact: batch-rendered, no visible `нет пары`
  diagnostic chips, and transfer pairs shown as one `from -> to` operation.
- Local production-data QA uses dated snapshots outside the repo, not live sync.

## MCP Surface Rule

Не фиксируйте в docs количество tools. Проверяйте live `tools/list`, `scripts/check_live_connector.py`, `src/minimal_kanban/mcp/server.py` и MCP tests.

`cleanup_card_content`, `autofill_vehicle_data`, and `autofill_repair_order` are compatibility/API/UI paths, not normal MCP runtime tools.

## Manager Knowledge Layer

For CRM/MCP/connector operating knowledge, use AutostopManager and the
Obsidian vault as the manager-readable layer. Start from
`C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM\Home.md` when the cloud path
exists; fall back to the desktop mirror only if needed.

## Verification

- `git status --short --branch`
- `python scripts\audit_localization.py`
- `.\scripts\run_checks.ps1`
- `python -m unittest discover -s tests -v`
- `python scripts\check_web_assets_js.py`
- `python scripts\browser_smoke.py` for browser UI/modal flow changes
- `python scripts\perf_probe.py --local-temp-server ...` for local performance budgets
- live smoke commands from `docs/OPERATIONS_RUNBOOK.md`

## Deployment Notes

- Keep local, GitHub, and production branch heads aligned before release work.
- Не коммитьте runtime data, board snapshots, local JSON storage, SQLite databases, secrets или credentials.
- Не удаляйте server-local env files вроде `telegram-ai.env` during sync.
