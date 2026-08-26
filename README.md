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

Production MCP exposes exactly 24 Gateway v2 tools. Codex and ChatGPT Apps use
owner-approved OAuth 2.1 with PKCE S256, rotating refresh tokens, exact
audience/scope checks, and encrypted persistent authorization state. The
deployment-rotated bearer remains only for internal release checks and
Responses API calls that explicitly supply it; it is not Codex/App state.

The only supported agent sequence is `agent_bootstrap` ->
`agent_board_digest` -> `agent_search`/`agent_entity_context` ->
`prepare_action_contract` -> named workflow `dry_run`/`apply` -> exact-target
reread and verification. Guarded raw capability discovery is an escape hatch
only when no named workflow exists; hidden low-level tools are never called
directly. It exposes the read-only `search_web_multi`, `fetch_page_excerpt`,
`fetch_page_browser`, and `research_drive2_cases` capabilities without adding a
25th public tool. Drive2 research is bounded, public-only, does not use an
account or retain raw journal pages, and returns forum case evidence rather
than procedure authority. Their schemas are hash-bound, arguments are bounded,
and page fetches retain the public-HTTP(S)/SSRF guard.

AutoStop CRM owns workshop and financial state; AutoStop App owns Store state;
AutostopManager owns routing, compact refs, contracts, and checkpoints. Gateway
never reads the App database. Bootstrap is CRM-only and reports Store as
`not_loaded`; explicit Store digests use opaque cursor/ACK delivery. Store
writes require dry-run/apply with distinct keys and stable correlation, exact
DTO readback, terminal notification state, and same-key receipt reconciliation
after an uncertain result.

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
- `src/minimal_kanban/mcp/server.py`, `mcp/payloads.py`,
  `mcp/tool_registry.py`, and `mcp/agent_gateway_v2.py` — raw MCP
  implementation, payload contracts, registry, and the production Gateway v2
  surface.
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
.\.venv\Scripts\python.exe scripts\crm_capability_parity.py --require-complete
.\.venv\Scripts\python.exe scripts\crm_change_feed_producer_parity.py --require-complete
.\.venv\Scripts\python.exe scripts\browser_smoke.py --profile core --attempts 1
```

The core browser profile is the short mandatory temp-data gate. Use
`--profile full` for the release profile after `scripts\toolchain_doctor.ps1`
confirms Chromium, Qt PDF, `pdfinfo`, and `pdftotext`.

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

Production endpoints:

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`

The production Compose project contains `autostopcrm`, `searxng`, and
`crawl4ai`. The CRM container also joins the precreated internal-only
`autostop-store-agent` network with `autostop-app`; the PostgreSQL container is
never attached. Only the CRM service is replaced during a normal deploy.

## Safety

- Never commit `.env`, credentials, runtime state, production snapshots,
  attachments, shared files, logs, audit archives, or financial ledgers.
- Never edit production `state.json`, `audit-archive`, `operator-activity`, or
  cashbox data manually.
- Repair-order numbers are immutable. The compatibility route
  `/api/correct_repair_order_number` is deliberately blocked.
- Closed repair orders are immutable. Corrections use the audited
  preview/reopen/reclose flow; payroll is reversed and reposted, while payment,
  cashbox, and inventory histories remain unchanged.
- Finance safe fixes and destructive historical cleanup are explicit
  owner-approved maintenance procedures, never routine UI/MCP work.
- Public anonymous API/MCP reads and all writes must remain blocked in
  production.
