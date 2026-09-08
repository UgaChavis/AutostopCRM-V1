# 012. Repair-order lifecycle

CardService coordinates creation, editing, close/reopen, posting, payments and
inventory. Shared helpers are useful when they remove duplication; preserve the
same domain behavior through API, MCP and nested update_card writes.

Mutation isolation lives in `services/bundle_draft.py`; derived order-file
publication lives in `services/repair_order_artifacts.py`. Lifecycle rules still
belong to CardService and its shared domain helpers. Preserve these boundaries
when extracting further behavior: rejected changes must not alter retained read
models, audit archives or files, and post-commit derivative failure is not a
failed financial transaction.

The shared `_read_card_bundle_for_update` defines the repeated four-domain
mutation-copy policy once; read-only order text/list paths use narrower drafts.
Verified text files are reused only while document inputs and file metadata
match. Snapshot serializers detach nested history; change-feed selection covers
cycles and payroll postings with exact case-sensitive parent prefixes.

Keep immutable unique numbers, payment/cash/inventory identities, deterministic
feed/audit order, current JsonStore atomicity and exactly-once payroll
reversal/reposting. Preserve create_if_missing, archived restoration, business
timezone and period-recognition semantics.

Exercise open/closed/correction/archived orders, paid and unpaid cases, legacy
snapshots, linked materials, stale revisions and repeated keys. Existing
repair-order, finance, payroll, inventory and printing suites plus the runbook's
performance gates define acceptance. Data migration retirement belongs to 017.
