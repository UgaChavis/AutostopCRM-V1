# Working On AutoStop CRM

Own the user's outcome. Choose the approach, tools, and order from current
code and evidence; examples and debt tasks are optional aids. Reuse known
context, ask only for a real blocker, and keep changes and instructions small.
Authorization already given for this task remains valid through completion.

Use [README.md](README.md) for architecture and the relevant contract or check;
read deeper documentation only when it helps the task. Business rules belong
in shared services, used by browser, Windows client, API, and MCP.

- Inspect the worktree and preserve unrelated user work. Keep secrets,
  production data, attachments, ledgers, and private context out of Git/output.
- Preserve needed behavior, data durability, authentication, and native guards
  for money, customer prices, orders, deletion, and external recipients.
  Production finance or historical-data repair follows the
  [operations runbook](docs/OPERATIONS_RUNBOOK.md).
- Compatibility names `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
  `Start Kanban.exe` still serve deployed clients and data. Retire them only
  with migration and rollback evidence.
- Match verification to the changed behavior; use `run_checks.ps1 -Profile ci`
  for the final shared change. Report measured effects and verification gaps.
- When deployment is in the user's scope, finish the runbook release and
  prove target Git revision, live endpoint behavior, health, and logs.
  Repository publication alone does not imply a production rollout.
