# 009. Gateway execution

Named workflows, raw capability execution, Store actions and verification share
lifecycle responsibilities. Simplify actual duplication; a new common kernel,
operation map or extraction sequence is not a prerequisite.

`tool_execution.py` bounds blocking handlers outside the request loop while
SDK validation, schemas, owner context and result conversion remain intact.
Workflow registration and exact readback still deserve independent slices;
do not reintroduce direct synchronous invocation in async handlers.

Preserve distinct dry-run/apply idempotency, state-version transitions, owner
identity and policy/maintenance checks. Applied-but-unverified work remains
uncertain or compensating. Only an exact repeated Store request/key can
reconcile an existing receipt; preserve required revision/readback evidence.

Exercise invalid arguments, stale revisions, success, uncertain readback,
failures before/after side effects, repeated keys and notifier outcomes.
Current contract/parity tests own inventories and hashes. Investigate a changed
trace before changing its expected result.
