# 001. Maintainability budgets

The live owner map, sizes and complexity budgets are in
`scripts/code_health_audit.py`; do not duplicate changing numbers here.
This owner also covers the bounded demo-data and builtin-template factories.

A useful cleanup reduces duplication, responsibility or measured cost. Merely
moving lines is not progress. Update an affected budget to its smaller measured
size; preserve remaining coverage and dependency checks.

Report complete baseline and candidate source totals separately from deleted
dead or duplicate paths. Removing real duplication is useful, but it is not a
net codebase reduction when new correctness code leaves the full tracked runtime
larger; never infer shrinkage from file moves or gross deleted-line counts.

Validation: code-health JSON/text, including missing or duplicate owners,
invalid AST and unexpected growth. General checks are in the operations runbook.
