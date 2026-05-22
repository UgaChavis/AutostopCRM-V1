# AutoStop CRM

AutoStop CRM is the active CRM for the AutoStop workshop on branch
`autostopcrm-v1`. The product includes a board, clients, vehicles, repair
orders, cashboxes, employee payroll, shared files, MCP access, and Telegram AI
owner workflows.

Historical technical names such as `minimal_kanban`,
`%APPDATA%\Minimal Kanban`, and `Start Kanban.exe` are compatibility names, not
a separate product line.

## Source Of Truth

Use this order when checking project facts:

1. Code and tests in this repository.
2. Live server checkout `/opt/autostopcrm` on branch `autostopcrm-v1`.
3. Canonical docs listed below.
4. Local Codex skill/access notes.
5. Secret/access bundle for credentials and non-public server access details.

Do not treat `release/`, `build/`, `dist/`, `.venv/`, local screenshots, old
plans, or copied secret-bundle docs as source of truth unless a current runbook
step explicitly says so.

## Product Map

- Board: columns, cards, archive, tags, deadlines, attachments, notes, compact
  snapshots, and audit log.
- Clients: people, individual entrepreneurs, companies, phones, requisites,
  vehicles, and card links.
- Repair orders: immutable order numbers, works, materials, statuses, payments,
  print templates, and PDF export.
- Cashboxes: money movements, transfers, compact journal, internal finance
  audit, employees, salary ledger, and payroll reports.
- Files: card attachments and shared workshop file grid with API/UI/MCP access.
- Integrations: local HTTP API, MCP endpoint, ChatGPT connector, Responses API
  clients, and Telegram AI worker.

## Runtime Architecture

```text
Desktop/browser UI
  -> local HTTP API
  -> CardService and domain services
  -> JsonStore

MCP client / ChatGPT / Responses API
  -> MCP server
  -> local HTTP API
  -> same CardService

Telegram owner
  -> Telegram AI worker
  -> explicit CRM tool registry
  -> local HTTP API
  -> read-back verification and audit
```

UI, MCP, Telegram AI, and compatibility routes must not duplicate business
logic. They call the backend API and share the same storage.

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

- `README.md` - current project map and contributor entrypoint.
- `docs/OPERATIONS_RUNBOOK.md` - GitHub/server sync, SSH, deploy, smoke,
  performance, finance, state maintenance, and production cautions.
- `API_GUIDE.md` - HTTP API route families and safety-critical parameters.
- `MCP_GUIDE.md` - MCP workflow, tool groups, optional manager layer, and
  write rules.
- `CHATGPT_CONNECTOR_SETUP.md` - ChatGPT connector setup and first-call smoke.
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt` - short server/operator note copied by
  `deploy.sh`.

`requirements.txt` and `requirements-dev.txt` are dependency manifests, not
how-to docs.

## Local Development

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
```

Run the desktop application:

```powershell
.\scripts\run_dev.ps1
```

Run MCP/API headless:

```powershell
.\scripts\run_mcp_server.ps1
```

Core release gate:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

Use [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) for production
deploy, smoke credentials, server parity, public checks, performance probes, and
maintenance procedures.

## API, MCP, And Connector

- Local API default: `http://127.0.0.1:41731`.
- Local MCP default: `http://127.0.0.1:41831/mcp`.
- Production CRM: `https://crm.autostopcrm.ru`.
- Production MCP: `https://crm.autostopcrm.ru/mcp`.

Route and tool lists are dynamic. Use `src/minimal_kanban/api/server.py`,
`src/minimal_kanban/mcp/server.py`, `tools/list`, and
`scripts/check_live_connector.py` for final verification.

`AutostopManager` can add optional manager tools to the same MCP endpoint. A
production-only tool such as `estimate_repair_work_cost` is expected when the
manager layer is mounted. Compare tool names, not only total counts.

## Data And State

Local data:

- `%APPDATA%\Minimal Kanban\state.json`
- `%APPDATA%\Minimal Kanban\settings.json`
- `%APPDATA%\Minimal Kanban\attachments`
- `%APPDATA%\Minimal Kanban\repair-orders`
- `%APPDATA%\Minimal Kanban\shared-files`
- `%APPDATA%\Minimal Kanban\audit-archive`
- `%APPDATA%\Minimal Kanban\operator-activity`
- `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`

Docker data:

- host path: `./data`
- container path: `/root/.minimal-kanban`

Never commit runtime state, production snapshots, attachments, cashbox data,
logs, tokens, `.env`, `telegram-ai.env`, or secret-bundle contents.

Heavy audit events keep compact details in active `state.json`; full
`before/after` details live in append-only `audit-archive`. Always run
`scripts/state_size_report.py` and `scripts/compact_audit_events.py --dry-run`
before any live compaction. Apply compaction only with backup and owner review.

Operator activity lives outside `state.json` in `operator-activity/current`,
`operator-activity/details`, and `operator-activity/aggregates`. The admin
journal uses compact current rows for the dense table, keeps detailed rows for
the recent retention window, and preserves older counters in aggregates. Use
`scripts/operator_activity_maintenance.py --dry-run --json` before any cleanup;
apply only with `--apply --backup`.

## AI And Safety Contracts

- `Приберись` is an agent procedure, not a backend command.
- Cleanup does not move or archive cards without a separate explicit request.
- `description` stores full recoverable text.
- `board_summary` stores a short board preview and must be refreshed after
  meaningful card/profile/tag changes.
- VIN/profile enrichment must preserve manual fields and write only
  source-backed confirmed facts.
- Finance audit and repair-order number correction are maintenance flows, not
  normal UI/MCP actions.

## Documentation Hygiene

Run docs checks after changing routes, tools, deploy, auth, performance,
maintenance, or user-facing instructions:

```powershell
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
```

For the local secret/access bundle, run the optional stale-instruction scan
without printing secret values:

```powershell
python scripts\docs_audit.py --format text --secret-bundle "C:\Users\9860606\Desktop\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер"
```

Keep active documentation small and role-based. Historical plans and one-off
reports belong outside active docs or must be clearly marked as historical.
