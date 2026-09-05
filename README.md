# AutoStop CRM

AutoStop CRM is the active workshop CRM on `autostopcrm-v1`: board, clients,
vehicles, repair orders, warehouse, cashboxes, payroll, files, and API/MCP
integrations. `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
`Start Kanban.exe` are compatibility names for this same product.

## Source Of Truth

Use current code and focused checks first:

- [AGENTS.md](AGENTS.md) — autonomy and durable safety boundaries;
- [operations runbook](docs/OPERATIONS_RUNBOOK.md) — local checks, production,
  deployment, rollback, and maintenance;
- [API guide](API_GUIDE.md) — HTTP contracts;
- [MCP guide](MCP_GUIDE.md) — Gateway v2, Store boundary, and action guards;
- [ChatGPT/Responses compatibility](CHATGPT_CONNECTOR_SETUP.md) — OAuth setup.

The single [CRM development skill](tools/codex/skills/autostopcrm-maintain/SKILL.md)
is versioned here; the runbook describes checking or installing its local copy.

Generated builds, release copies, screenshots, private bundles, and old plans
are not sources of truth. For production, use the runbook to compare local,
remote, and server revisions and verify live health.

## Architecture

```text
Browser UI / MCP / API clients -> HTTP API -> domain services -> JsonStore
Owner MCP client -> 24-tool Gateway v2 -> internal Store adapter -> Store API
```

- `src/minimal_kanban/services/` owns business behavior;
  `storage/json_store.py` owns persistence and normalization.
- `api/server.py` and `api/route_registry.py` own HTTP transport,
  authentication, and mutation classification.
- `src/minimal_kanban/mcp/` owns the public Gateway v2, action guards, and the
  internal Store adapter. API, MCP, UI, and scripts use the same services.
- `src/minimal_kanban/web_app_assets/source/` plus `assembler.py` own browser
  assets. `main.py` and `main_mcp.py` are desktop and API/MCP entrypoints.

The agent builds a customer goal from relevant CRM, Store, and sanctioned
conversation context. A short quote signal can be enough to start. Routes and
tools are suggestions, while native confirmation protects only money, published
prices, orders, deletion, new external recipients, deployment, and secrets.

## Local Development

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\scripts\run_checks.ps1 -Profile ci
.\scripts\run_dev.ps1
.\scripts\run_mcp_server.ps1
```

Local API/UI defaults to `http://127.0.0.1:41731`; MCP defaults to
`http://127.0.0.1:41831/mcp`. Production topology and release recovery are in
the [operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Safety

Do not commit secrets or runtime/production data, or manually alter production
state, finance, or order history. Public anonymous API/MCP reads and writes are
blocked in production. Use the narrow guide or runbook for high-impact work.
