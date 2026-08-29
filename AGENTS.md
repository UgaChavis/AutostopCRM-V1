# Agent Instructions

Repository rules for coding agents. Code, tests, and the canonical documents
below are the source of truth; keep this file compact.

## Scope And Canonical Sources

- Product: AutoStop CRM. Production branch: `autostopcrm-v1`; production URL:
  `https://crm.autostopcrm.ru`; MCP: `https://crm.autostopcrm.ru/mcp`.
- `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and `Start Kanban.exe` are
  compatibility names for the same product. Treat them as active until
  read-only production and rollback evidence proves removal is safe.
- Start with the task's code and tests, then route questions to the narrow
  canonical owner:
  - `README.md` — project map and supported entry points;
  - `docs/OPERATIONS_RUNBOOK.md` — operations, maintenance, release, rollback,
    credentials, and production verification;
  - `API_GUIDE.md` — HTTP contracts;
  - `MCP_GUIDE.md` — MCP tools, workflows, and write safety;
  - `CHATGPT_CONNECTOR_SETUP.md` — client/auth compatibility only;
  - `AUTOSTOPCRM_FULL_INSTRUCTION.txt` — short server note.
- The canonical set is `AGENTS.md` plus those six documents. Do not recreate
  removed historical plans or handoff files.

## Worktree, Data, And Ownership

- Begin with `git status --short --branch`; preserve unrelated user changes.
- Never reset, revert, delete, or overwrite user work without an explicit
  request. Keep each change focused and use `rg`, existing models, structured
  parsers, and `apply_patch` where appropriate.
- Do not commit unless the user asks. A request to publish changes does not
  authorize a production deploy.
- Never commit or expose credentials, `.env`, SSH keys, private access bundles,
  secret-bearing logs, runtime state, production snapshots, `data/`,
  attachments, financial ledgers, operator activity, or audit archives.
- Finance stop-line: do not manually edit production `state.json`, cashboxes,
  repair-order ledgers, `audit-archive`, or `operator-activity`. Finance safe
  fixes and historical cleanup require the runbook's read-only/dry-run,
  verified-backup, explicit-owner flow. Repair-order numbers have no supported
  correction flow; `/api/correct_repair_order_number` must reject with
  `repair_order_number_immutable`.
- Public anonymous API/MCP reads and every write must remain blocked in
  production.
- Legacy or migration code is not dead-code proof. Delete it only after exact
  runtime/data/deploy evidence, replacement and rollback analysis, and owner
  approval; uncertain compatibility is not removable.

## Architecture Boundaries

- Business logic: `src/minimal_kanban/services/`; persistence and normalization:
  `src/minimal_kanban/storage/json_store.py`.
- HTTP: `src/minimal_kanban/api/server.py` and `route_registry.py`; update the
  immutable `RouteSpec` for registry-owned routes.
- MCP: `src/minimal_kanban/mcp/`; `server.py` orchestrates registrars,
  `tool_registry.py` owns raw registration, and `agent_gateway_v2.py` owns the
  production surface.
- Browser source: `src/minimal_kanban/web_app_assets/source/`; preserve output
  assembled by `web_app_assets/assembler.py`.
- API, MCP, UI, smoke scripts, and compatibility routes must share backend
  contracts instead of duplicating behavior.

## MCP And Live CRM

- Begin normal work with `agent_bootstrap`; locate exact targets with
  `agent_board_digest`, `agent_search`, and `agent_entity_context`.
- For a write, build `prepare_action_contract`, run the named workflow in
  `dry_run`, then `apply` with a unique idempotency key, and reread the exact
  target. Follow `MCP_GUIDE.md` for guarded raw fallback.
- Read live context before every write. Do not move, archive, delete, or change
  money, client, file, order, or inventory data without explicit owner intent.
- Production clients use owner-approved OAuth 2.1 with PKCE and refresh-token
  rotation. The rotating bearer is internal compatibility only.

## Verification

Run focused checks first, then broaden for shared behavior:

- docs: `.\.venv\Scripts\python.exe scripts/docs_audit.py --format text`;
- Python/service/API/MCP: focused `unittest`, then `.\scripts\run_checks.ps1`;
- formatting/lint: Ruff format-check and lint;
- UI: `scripts/check_web_assets_js.py` plus relevant browser smoke;
- Gateway/runtime: `scripts/check_agent_gateway_v2.py`; use `--exhaustive` for a
  release check;
- production-impacting work: the runbook release checklist.

State which relevant checks were not run and why.

## Release Boundary

- `origin/autostopcrm-v1` is the production source of truth.
- Deploy only on an explicit user request. Commit or push approval alone is not
  deploy approval.
- Use `docs/OPERATIONS_RUNBOOK.md`; never improvise server access or release
  commands from this file.
- Production evidence: compare all Git revisions to the target SHA, run live
  smoke against the exact live URL, confirm service/restart health, and inspect
  relevant logs.
- Never delete server-local `.env`, data, backups, active volumes,
  nginx/systemd/VPN files, or dirty parallel checkouts.

## Documentation Changes

Keep agent rules here, the project map in `README.md`, operations in the
runbook, and API/MCP/client details in their narrow contracts. When an active
document is added, deleted, or renamed, update `scripts/docs_audit.py`,
`README.md`, the runbook, and `.dockerignore` together.
