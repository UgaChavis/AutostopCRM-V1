# AutoStop CRM: Technical-Debt Backlog

This directory maps remaining technical debt and maintainability ownership.
Use current code, tests, and audit scripts to choose the smallest useful
evidence-backed slice. Documents describe current boundaries and remaining
work; completed behavior is protected by its checks.

The [iteration map](ITERATION_MAP.md) orders the next small, locally verifiable
slices and separates them from larger contract and data work.

## Current Contracts

- `scripts/code_health_audit.py` classifies tracked files and owns module,
  class, function and complexity limits, including each ratchet's owner.
- MCP surface, capability, and change-feed matrices remain audit-owned; take
  exact values from their current checks.
- Compatibility names `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
  `Start Kanban.exe` stay until an independently proven migration.
- Task 017 uses read-only production inventory to decide which unused code can
  be retired. Generated/ignored output cleanup stays separate and recoverable;
  releases, `.venv`, production data, and rollback assets are never generic
  cleanup targets.

## Active Owners

| IDs | Focus |
|---|---|
| 001 | Maintainability caps and ownership |
| 003 | Test suite boundaries and shared fixtures |
| 005 | Browser rendering, loading and request ownership |
| 008, 009 | MCP registrar, executor, and verifier slices |
| 010–014 | Attachments, manager compatibility, repair, payroll, printing |
| 017 | Migration and compatibility inventory |
| 018–021 | Read models, finance, release boundaries, print web chunks |
| 206 | Autonomous agent runtime boundary |

Each ratchet has one owner; other tasks cover remaining debt without a size
exception. Pick by current evidence rather than list order.
Before deletion or migration, check runtime, imports, tests, docs, and rollback.
Keep DTO/schema/error/audit/feed ordering, idempotency, and revision contracts
stable unless the task explicitly changes them. Start focused; broaden checks
only for the shared boundary involved.
