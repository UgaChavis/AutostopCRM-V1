# Agent Instructions

This file is for AI coding agents working in this repository. It is not an
operator runbook and must stay short. Link to the canonical docs instead of
copying their full content.

## Project Identity

- Product: AutoStop CRM, the active workshop CRM.
- Production branch: `autostopcrm-v1`.
- Production CRM: `https://crm.autostopcrm.ru`.
- Production MCP: `https://crm.autostopcrm.ru/mcp`.
- Production checkout: `/opt/autostopcrm`.
- Historical names such as `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
  `Start Kanban.exe` are compatibility names, not a separate product.

## What The App Does

AutoStop CRM manages an auto-service workflow:

- board columns, cards, deadlines, tags, notes, archive, attachments, and audit
  log;
- clients, companies, phones, requisites, vehicles, and card links;
- repair orders with immutable numbers, works, materials, statuses, payments,
  print templates, and PDF export;
- warehouse positions, fractional oils, replenishment, write-off into repair
  order materials, returns, and technical movement journal;
- cashboxes, transfers, cash journal, employee payroll, salary ledgers, reports,
  and reconciliation print;
- shared files, local HTTP API, MCP endpoint, ChatGPT connector, and Responses
  API integrations.

## First Read

Use this order for current project facts:

1. Code and tests in this repository.
2. `README.md`.
3. `docs/OPERATIONS_RUNBOOK.md`.
4. `API_GUIDE.md` for HTTP contracts.
5. `MCP_GUIDE.md` for MCP contracts and write rules.
6. `docs/SERVER_MAP.md` for production paths and service boundaries.
7. `CHATGPT_CONNECTOR_SETUP.md` when touching connector setup.
8. `AUTOSTOPCRM_FULL_INSTRUCTION.txt` only as the short server/operator note.
9. The smallest module-specific source and test files for the requested change.

If another instruction mentions missing historical files such as
`00_START_HERE_AUTOSTOP_CRM.md`, `docs/CODEX_WORKFLOW.md`, or
`PROJECT_HANDOFF.md`, do not invent them. Use the current first-read path above
and keep this file in sync if the canonical docs change.

## Architecture Rules

Runtime shape:

```text
UI / MCP / API clients
  -> local HTTP API
  -> CardService and domain services
  -> JsonStore
```

- Keep business logic in services, especially `src/minimal_kanban/services/`.
- API, MCP, UI, smoke scripts, and compatibility routes should call the same
  backend contracts instead of duplicating behavior.
- Primary entrypoints are `main.py` for desktop and `main_mcp.py` for API/MCP
  runtime.
- API routes live in `src/minimal_kanban/api/server.py` and
  `src/minimal_kanban/api/route_registry.py`.
- MCP tools live in `src/minimal_kanban/mcp/server.py` and
  `src/minimal_kanban/mcp/tool_registry.py`.
- CRM agent automotive/web tools live in `src/minimal_kanban/agent/`; public
  research should use `search_web_multi` before excerpt/browser tools.
- JSON state normalization and persistence live in
  `src/minimal_kanban/storage/json_store.py`.
- Browser UI chunks live in `src/minimal_kanban/web_app_assets/source/`; keep
  generated asset behavior consistent with `web_app_assets/assembler.py`.

## Worktree Rules

- Start with `git status --short --branch` and identify existing user changes.
- Never reset, revert, delete, or overwrite user changes unless the user asks
  for that exact operation.
- Keep edits narrowly scoped to the request and the local patterns already in
  the repo.
- Prefer `rg`/`rg --files` for search.
- Use structured parsing or existing helpers for structured data; avoid ad hoc
  string manipulation when a local parser/model exists.
- Use `apply_patch` for manual edits.
- Do not commit unless the user explicitly asks.

## Data And Security Boundaries

Never commit or expose:

- `.env`, tokens, bearer secrets, SSH keys, secret-bundle content, logs, or
  settings containing credentials;
- runtime state, production snapshots, `data/`, attachments, shared files,
  cashbox ledgers, operator activity, or audit archives;
- local release/build/dist artifacts, screenshots, Playwright artifacts, or
  temporary browser output.

Do not manually edit production `state.json`, `audit-archive`,
`operator-activity`, cashbox data, or repair-order ledgers. Finance audit safe
fixes, repair-order number correction, and historical financial cleanup are
maintenance flows that require the runbook path, read-only/dry-run checks,
backups, and explicit owner approval.

Public anonymous writes must remain blocked.

## MCP And Live Board Rules

When using the AutoStop CRM MCP connector, scope is exactly one current CRM
board at `https://crm.autostopcrm.ru/mcp`.

- Begin normal work with `agent_bootstrap`; use `agent_board_digest`,
  `agent_search`, and `agent_entity_context` for live scope and exact targets.
- Call `get_runtime_status` when auth, tunnel, or runtime state is unclear.
- Use a named domain workflow before raw discovery. Never call hidden legacy
  tools by name from an external agent.
- Read live context before every write.
- Write only by confirmed ids such as `card_id`, `sticky_id`, `column_id`,
  `client_id`, `repair_order` card id, or cashbox id.
- Read back changed cards, files, clients, repair orders, inventory, or
  cashboxes after writing.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.

## Verification

Choose focused checks first, then broaden when shared behavior changes.

- Documentation changes:
  `python scripts/docs_audit.py --format text`.
- Python/service/API/MCP changes:
  `.\scripts\run_checks.ps1` or focused `python -m unittest ...`.
- Formatting/lint before release:
  `.\.venv\Scripts\python.exe -m ruff format --check .` and
  `.\.venv\Scripts\python.exe -m ruff check .`.
- UI asset changes:
  `python scripts/check_web_assets_js.py` and relevant browser smoke coverage.
- Connector/runtime changes:
  `python scripts/check_agent_gateway_v2.py` with the runbook's local or
  production arguments; add `--exhaustive` for a safe call of all 24 tools.
- Production-impacting changes:
  follow `docs/OPERATIONS_RUNBOOK.md` release checklist.

Do not claim a change is complete until the relevant checks have run, or state
clearly which checks were not run and why.

## Deploy Rules

- GitHub branch `autostopcrm-v1` is the production source of truth.
- Deploy only when the user explicitly asks for it or confirms that the change
  is meant to ship.
- Use the runbook deploy path: commit intended changes, push to
  `origin/autostopcrm-v1`, deploy from `/opt/autostopcrm`, verify local,
  GitHub, and server `HEAD`, then run live smoke checks.
- Never delete server-local `.env`, data, backups, active Docker volumes,
  active nginx/systemd/VPN files, or dirty parallel checkouts.

## Documentation Rules

- Keep `AGENTS.md` agent-only and concise.
- Keep `README.md` as the short project map.
- Keep `docs/OPERATIONS_RUNBOOK.md` as the operational source of truth.
- Keep `API_GUIDE.md`, `MCP_GUIDE.md`, and `CHATGPT_CONNECTOR_SETUP.md` as
  narrow contract references.
- If adding, deleting, or renaming active docs, update `scripts/docs_audit.py`,
  `README.md`, `docs/OPERATIONS_RUNBOOK.md`, and `.dockerignore` together.
