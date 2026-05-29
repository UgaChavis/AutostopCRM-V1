# AutoStop CRM

AutoStop CRM is the active workshop CRM on branch `autostopcrm-v1`. It includes
the board, clients, vehicles, repair orders, cashboxes, employee payroll,
shared files, MCP access, and Telegram AI owner workflows.

Historical names such as `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
`Start Kanban.exe` are compatibility names, not a separate product.

## Source Of Truth

Use this order when checking project facts:

1. Code and tests in this repository.
2. Live server checkout `/opt/autostopcrm` on branch `autostopcrm-v1`.
3. Canonical docs listed below.
4. Local Codex skill/access notes and secret bundle for private access details.

Do not treat `release/`, `build/`, `dist/`, `.venv/`, local screenshots, old
plans, or copied secret-bundle docs as source of truth unless the runbook says
so.

## Product Map

- Board: columns, cards, archive, tags, deadlines, attachments, notes, compact
  snapshots, and audit log.
- Clients: people, companies, phones, requisites, vehicles, and card links.
- Repair orders: immutable numbers, works, materials, statuses, payments,
  print templates, and PDF export.
- Cashboxes/payroll: money movements, transfers, journal, employee salary
  ledger, reports, and reconciliation print.
- Integrations: local HTTP API, MCP endpoint, ChatGPT/Responses API clients,
  and Telegram AI worker.

## Runtime Architecture

```text
UI / MCP / Telegram AI
  -> local HTTP API
  -> CardService and domain services
  -> JsonStore
```

Business logic belongs in services. UI, MCP, Telegram AI, and compatibility
routes call the same backend API and storage.

## Code Map

- `main.py` - desktop runtime.
- `main_mcp.py` - API + MCP production/runtime entrypoint.
- `main_telegram_ai.py` - Telegram AI worker.
- `src/minimal_kanban/api/server.py` - HTTP API routes.
- `src/minimal_kanban/services/card_service.py` - main business service.
- `src/minimal_kanban/storage/json_store.py` - JSON storage.
- `src/minimal_kanban/mcp/server.py` - MCP tools and optional manager mount.
- `src/minimal_kanban/web_app_assets/assembler.py` - browser UI assembly.
- `deploy.sh`, `docker-compose.yml`, `Dockerfile` - production deployment.

## Documentation Map

- `README.md` - short project map and contributor entrypoint.
- `docs/OPERATIONS_RUNBOOK.md` - release gates, GitHub/server sync, deploy,
  production smoke, performance checks, watchdog, and maintenance safety.
- `API_GUIDE.md` - HTTP API route groups and safety-critical contracts.
- `MCP_GUIDE.md` - MCP runtime, ChatGPT connector flow, tool groups, optional
  manager layer, and write rules.
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt` - short server/operator note copied by
  `deploy.sh`.

`requirements.txt` and `requirements-dev.txt` are dependency manifests, not
operator documentation.

## Local Development

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\scripts\run_dev.ps1
```

Headless API/MCP:

```powershell
.\scripts\run_mcp_server.ps1
```

Minimum local gate before release:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

Use [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) for deploy,
production verification, performance baselines, and maintenance procedures.

## Current Endpoints

- Local API: `http://127.0.0.1:41731`
- Local MCP: `http://127.0.0.1:41831/mcp`
- Production CRM: `https://crm.autostopcrm.ru`
- Production MCP: `https://crm.autostopcrm.ru/mcp`

Route and tool lists are dynamic. Verify with code, `tools/list`, tests, and
`scripts/check_live_connector.py`, not stale copied docs.

## Safety

- Never commit runtime state, production snapshots, attachments, cashbox data,
  logs, tokens, `.env`, `telegram-ai.env`, or secret-bundle contents.
- Do not edit production `state.json`, `audit-archive`, operator activity, or
  cashbox ledgers manually.
- Finance audit and repair-order number correction are maintenance flows, not
  normal UI/MCP actions.
- Historical plans and one-off reports stay outside active docs.
