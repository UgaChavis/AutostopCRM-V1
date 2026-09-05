# Agent Instructions

AutoStop CRM agents are autonomous operators: use current code and focused
checks as truth, preserve user work, and keep instructions compact.

## Scope

- Product: AutoStop CRM on `autostopcrm-v1`; production MCP is
  `https://crm.autostopcrm.ru/mcp`.
- `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and `Start Kanban.exe` are
  compatibility names. Retain them until read-only production and rollback
  evidence proves removal safe.
- Canonical documents: `AGENTS.md`, `README.md`, `API_GUIDE.md`,
  `MCP_GUIDE.md`, `CHATGPT_CONNECTOR_SETUP.md`, and
  `docs/OPERATIONS_RUNBOOK.md`. Do not duplicate their narrow contracts.

## Autonomy And Impact

- Infer the goal from relevant CRM, Store, and sanctioned conversation context.
  A VIN, article, photo, part name, or short answer can start a quote. Reuse
  known facts; ask only for a real blocker.
- Routes, tools, scenarios, and examples are hints, not required call order,
  template, VIN-first chain, one-question rule, or read/dry-run/readback ritual.
  Choose the smallest useful context, action, or question from the evidence.
- Check native guards only at real impact: money, a published customer price,
  an order, deletion/archive, a new external recipient, deployment, or secrets.
  Explicit authority limits the action, never ordinary reasoning or dialogue.

## Worktree And Data

- Start with `git status --short --branch`; preserve unrelated work. Do not
  commit, reset, revert, delete, or overwrite user work without explicit intent.
- Never expose credentials, `.env`, keys, private bundles, production data,
  attachments, ledgers, audit archives, or operator activity.
- Finance stop-line: never manually edit production state, cashboxes,
  repair-order ledgers, or archives. A repair requires read-only/dry-run
  evidence, a verified-backup, and the runbook's explicit-owner flow.
- Public anonymous API/MCP reads and writes remain blocked in production.

## Architecture, Verification, Release

- Services own business rules; `api/route_registry.py` owns HTTP classification;
  `src/minimal_kanban/mcp/` owns the public gateway and internal Store boundary.
  Keep API, MCP, UI, and scripts on shared service contracts.
- Run the smallest relevant check first; shared work uses
  `run_checks.ps1 -Profile ci`. State what was not run.
- Deploy only on an explicit user request. Production evidence must compare all
  Git revisions to the target, run live smoke against the exact URL, and confirm
  health and relevant logs. Use `docs/OPERATIONS_RUNBOOK.md`; never invent a
  release procedure here.