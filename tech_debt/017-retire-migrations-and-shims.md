# 017. Migration and compatibility inventory

Candidates include payroll-policy and cashbox maintenance scripts, repair-order
cycle migration, completion-act draft migration, legacy settings/prepayments,
blocked repair-order-number correction and older backup schemas.
These remain candidates, not established dead code.

Classify each as active, required for recovery, removable with evidence or
uncertain. Use imports and dynamic registration, current tests, supported Windows
data, read-only production schema/count evidence and backup/rollback consumers.
Record the reason and replacement when removing a path; uncertain paths stay.

Validate current and oldest supported fixtures, recovery and public compatibility
errors. The Minimal Kanban data directory is a live compatibility boundary.
Do not publish real data samples or apply business-data migrations as cleanup.
Approved cleanup needs no repeated approval for each proven unused helper;
actual data corrections follow the operations runbook and task authority.
