# AutoStop CRM: Technical-Debt Backlog

Current on 2026-09-05, branch `autostopcrm-v1`. This directory is a compact map
of active ratchet owners, not a prescribed work sequence. Use current code,
tests, and audit scripts; choose the smallest useful evidence-backed slice.
Completed work belongs in gates, not historical narratives.

## Current Contracts

- `scripts/code_health_audit.py` classifies tracked files. Its current caps are
  module 2500, test module 3000, class 2500, and function 450 lines.
- MCP surface, capability, and change-feed matrices remain audit-owned; take
  exact values from their current checks.
- Compatibility names `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
  `Start Kanban.exe` stay until an independently proven migration.
- Task 017 is read-only inventory only. Generated/ignored output cleanup stays
  separate and recoverable; releases, `.venv`, production data, and rollback
  assets are never generic cleanup targets.

## Active Owners

| IDs | Focus |
|---|---|
| 001 | Maintainability caps and ownership |
| 003, 005 | Test seams and small domain extraction |
| 008, 009 | MCP registrar, executor, and verifier slices |
| 010–014 | Attachments, manager compatibility, repair, payroll, printing |
| 017 | Migration and compatibility inventory |
| 018–021 | Read models, finance, release boundaries, print web chunks |
| 206 | Autonomous agent runtime boundary |

Each ratchet has one owner. Pick by current evidence rather than list order.
Before deletion or migration, check runtime, imports, tests, docs, and rollback.
Keep DTO/schema/error/audit/feed ordering, idempotency, and revision contracts
stable unless the task explicitly changes them. Start focused; broaden checks
only for the shared boundary involved.
