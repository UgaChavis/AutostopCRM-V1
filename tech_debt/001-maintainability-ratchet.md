# 001. Maintainability budgets

The live owner map, sizes and complexity budgets are in
`scripts/code_health_audit.py`; do not duplicate changing numbers here.
This owner also covers the bounded demo-data and builtin-template factories.

A useful cleanup reduces duplication, responsibility or measured cost. Merely
moving lines is not progress. Update an affected budget to its smaller measured
size; preserve remaining coverage and dependency checks.

Validation: code-health JSON/text, including missing or duplicate owners,
invalid AST and unexpected growth. General checks are in the operations runbook.
