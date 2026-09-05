# 012. Repair-order lifecycle

CardService coordinates creation, editing, close/reopen, posting, payments and
inventory. Shared helpers are useful when they remove duplication; preserve the
same domain behavior through API, MCP and nested update_card writes.

Keep immutable unique numbers, payment/cash/inventory identities, deterministic
feed/audit order, current JsonStore atomicity and exactly-once payroll
reversal/reposting. Preserve create_if_missing, archived restoration, business
timezone and period-recognition semantics.

Exercise open/closed/correction/archived orders, paid and unpaid cases, legacy
snapshots, linked materials, stale revisions and repeated keys. Existing
repair-order, finance, payroll, inventory and printing suites plus the runbook's
performance gates define acceptance. Data migration retirement belongs to 017.
