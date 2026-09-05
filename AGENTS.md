# Agent Instructions

Work from current code, tests, and the canonical documents. Treat these rules
as decision boundaries, not a substitute for judgment: choose the smallest
useful reversible step, explain material tradeoffs, and ask only when authority
or a consequential choice is genuinely missing.

## Product And Sources

- AutoStop CRM ships from `autostopcrm-v1`; production is
  `https://crm.autostopcrm.ru` and MCP is `https://crm.autostopcrm.ru/mcp`.
- `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and `Start Kanban.exe` are
  compatibility names for the same product. Keep them until read-only
  production and rollback evidence proves removal safe.
- Start with the task-local code and tests, then use the narrow owner:
  - `README.md` — project map and entry points;
  - `docs/OPERATIONS_RUNBOOK.md` — operations, release, rollback, credentials,
    and production verification;
  - `API_GUIDE.md` — HTTP contracts;
  - `MCP_GUIDE.md` — MCP surface and write safety;
  - `CHATGPT_CONNECTOR_SETUP.md` — client/auth compatibility;
  - `AUTOSTOPCRM_FULL_INSTRUCTION.txt` — short server note.
- `AGENTS.md` and those six files are the canonical set. Do not recreate
  retired plans or parallel handoffs.

## Safe Autonomy

- Begin with `git status --short --branch`; preserve unrelated work. Publish
  only within the task's scope; a commit or push never authorizes deployment.
- Keep credentials, `.env`, private bundles, runtime data, attachments,
  financial ledgers, operator activity, and audit archives out of Git, logs,
  and chat.
- Finance stop-line: never edit production `state.json`, cashboxes,
  repair-order ledgers, `audit-archive`, or `operator-activity` by hand.
  Finance fixes and historical cleanup require the runbook's read-only/dry-run,
  verified-backup, explicit-owner flow. Repair-order numbers remain immutable.
- Public anonymous API/MCP reads and every write must remain blocked in
  production.
- A legacy or migration name is not dead-code proof. Remove it only after
  runtime/data/deploy evidence, replacement and rollback analysis, and owner
  approval; uncertain compatibility stays.

## Design And Verification

- Keep business rules in `src/minimal_kanban/services/`, persistence in
  `storage/json_store.py`, HTTP policy in `api/route_registry.py`, MCP surface
  in `mcp/`, and browser source in `web_app_assets/source/`. Reuse service
  contracts instead of duplicating rules across transports.
- For live CRM work, read context first. A write needs explicit owner intent,
  an action contract, dry-run/apply where supported, idempotency, and exact
  reread; use `MCP_GUIDE.md` for the guarded fallback.
- Run focused checks first. Shared changes require
  `run_checks.ps1 -Profile ci` and hosted CI; UI changes also need JS syntax
  and relevant browser smoke. State what was not run and why.

## Release And Documents

- Deploy only on an explicit user request. Use the runbook rather than
  improvised server commands.
- Production evidence: compare all Git revisions to the target SHA, run live
  smoke against the exact URL, then confirm service health and relevant logs.
- When an active document changes identity, update its audit owner, `README.md`,
  the runbook, and `.dockerignore`; delete obsolete parallel instructions.
