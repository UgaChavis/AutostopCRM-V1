# 019. Finance audit and repair planning

Audit, cancellation, transfers and safe-fix planning should share deterministic
calculation without duplicating payment, order or feed writes. Keep dry-run and
apply consistent for the same revision and revalidate facts before effects.

Preserve cash/cashless and 85/15 behavior, partial payments, repeat cancellation,
legacy journal references, minor-unit rounding, issue order, actor attribution
and exactly-once posting. Plan hashes must not contain private actor data.

Check stale revisions, boundary amounts and failure before/after possible effects.
Current DTO, idempotency and audit/feed contracts remain stable. Financial repairs
require their own scoped evidence and native confirmation; the cleanup does not
automatically apply proposed fixes to production.
