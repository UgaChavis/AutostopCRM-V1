# AutoStop CRM

AutoStop CRM is the active workshop CRM on branch `autostopcrm-v1`. It covers
the board, clients and vehicles, repair orders and printing, warehouse,
cashboxes, payroll, shared files, and API/MCP integrations.

`minimal_kanban`, `%APPDATA%\Minimal Kanban`, and `Start Kanban.exe` are
compatibility names for this product, not separate applications.

## Source Of Truth

Use current code and tests first. The maintained documentation is:

- `README.md` — this project map and contributor entrypoint;
- [AGENTS.md](AGENTS.md) — short rules for coding agents;
- [operations runbook](docs/OPERATIONS_RUNBOOK.md) — local checks, production
  layout, deploy, rollback, and maintenance;
- [API guide](API_GUIDE.md) — HTTP transport and safety-critical contracts;
- [MCP guide](MCP_GUIDE.md) — Gateway v2 surface and write rules;
- [ChatGPT/Responses compatibility note](CHATGPT_CONNECTOR_SETUP.md) —
  supported OAuth/Codex/ChatGPT/Responses authentication;
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt` — short server-side operator note.

For a deployed system, verify the local, remote, and server Git revisions plus
live health as described in the runbook. Generated builds, release copies,
screenshots, private access bundles, and old plans are not sources of truth.

## Product And Architecture

```text
Browser UI / MCP / API clients
  -> local HTTP API
  -> CardService and domain services
  -> JsonStore

Owner MCP client -> 24-tool Gateway v2 -> mounted AutostopManager adapter
  -> isolated internal Docker network -> AutoStop App agent API
```

- Board: columns, cards, deadlines, tags, notes, archive, attachments, and
  audit history. Each operator can also enable a personal final virtual column
  filtered by an exact tag label and color; its cards stay in their original
  shared columns.
- CRM: clients, companies, contacts, vehicles, and card links.
- Repair: immutable order numbers, works, materials, payments, standard print
  templates, and PDF export.
- Operations: inventory movements, cashboxes, transfers, payroll, reports,
  shared files, and operator activity.
- Shared display: a read-only TV dashboard opened from board scale settings,
  with a shared mechanics message board and four weekly repair-order revenue
  buckets. Its compact response does not expose employee or payroll data.
- Integrations: local HTTP API, streamable HTTP MCP, Agent Gateway v2,
  Responses API clients, AutoStop App store context, and automotive/web
  research helpers.

Production MCP exposes exactly 24 Gateway v2 tools. OAuth, call order, Store
boundaries, raw-capability escape, and exact write/readback rules are in the
[MCP guide](MCP_GUIDE.md); never invoke hidden low-level tools directly.

Business rules belong in `src/minimal_kanban/services/`. API, MCP, UI, smoke
scripts, and compatibility routes must call the same services instead of
reimplementing those rules.

## Code Map

- `main.py` — desktop entrypoint.
- `main_mcp.py` — API/MCP runtime entrypoint.
- `src/minimal_kanban/api/server.py` and `api/route_registry.py` — HTTP
  transport and immutable `RouteSpec` policy registry; auth, maintenance,
  mutation, response, and readback classifications are derived from its specs.
- `src/minimal_kanban/services/` — business services.
- `src/minimal_kanban/storage/json_store.py` — state normalization and
  persistence.
- `src/minimal_kanban/mcp/` — raw orchestration, focused registrars, contracts,
  and Gateway v2; see the [MCP guide](MCP_GUIDE.md) for its source map.
- [`scripts/crm_capability_parity.py`](scripts/crm_capability_parity.py) and its
  JSON manifest — machine-verifiable UI/backend/Gateway capability matrix,
  readback classes, test evidence, reviewed gaps, and human-session exemptions.
- [`scripts/crm_change_feed_producer_parity.py`](scripts/crm_change_feed_producer_parity.py)
  and its JSON manifest — every discovered write route mapped to a commit-bound
  feed producer or a fixed, evidence-backed privacy/infrastructure exemption;
  generic executor routes additionally require an executable temporary-state
  route-to-feed readback contract or an exact reviewed boundary rationale.
- `src/minimal_kanban/deployment_security.py` — production auth, maintenance,
  identity, and kill-switch validation.
- `src/minimal_kanban/web_app_assets/source/` and
  `web_app_assets/assembler.py` — browser UI sources and assembly.
- `docker-compose.yml`, `Dockerfile`, and `deploy.sh` — production runtime and
  bounded release flow.

## Local Development

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\scripts\run_checks.ps1 -Profile ci
```

The default profile is the fast changed-file gate. `-Profile ci` is the
canonical serial local mirror of mandatory non-container quality gates,
including branch coverage, parity, core browser smoke, and bounded performance
checks. Hosted CI remains required for Ubuntu/Python 3.12, production Compose
validation, and `docker-runtime-assets`. Use browser `--profile full` only for
release verification after `scripts\toolchain_doctor.ps1` confirms the PDF
toolchain.

Run the desktop application or the headless API/MCP runtime:

```powershell
.\scripts\run_dev.ps1
.\scripts\run_mcp_server.ps1
```

Default local endpoints:

- API/UI: `http://127.0.0.1:41731`
- Module map: `http://127.0.0.1:41731/module-map` (or use the `КАРТА`
  link in the CRM top bar); select a module or open its stable `#MODULE_ID` link
  to highlight direct dependencies while the rest of the map stays visible;
  click any visible connection for its direction and purpose; use the mouse
  wheel to zoom and drag the empty canvas to pan. The full infrastructure view
  loads only for an authenticated operator session.
- TV dashboard: `http://127.0.0.1:41731/dashboard` (open it from board
  settings so it reuses the current operator session)
- MCP: `http://127.0.0.1:41831/mcp`

Production endpoint, Compose topology, and deploy/rollback procedure are in
the [operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Safety

Never commit secrets or runtime/production data, or edit production state and
financial/order history manually. Use the relevant guides and
[operations runbook](docs/OPERATIONS_RUNBOOK.md); public anonymous API/MCP
reads and writes remain blocked in production.
