# CRM maintenance sweep

One complete subsystem pass, started on 2026-09-30 from GitHub revision
`d14fea683ead4c4f7c9e502f1d4f65bd006030b4`. Publish verified slices to
`autostopcrm-v1`; production deployment is outside this work. Public contracts,
business rules and persisted formats stay compatible. Proposals that change
them require a separate owner decision.

## Baseline

The unmodified baseline passed `scripts/run_checks.ps1 -Profile ci`: 2855
runtime tests in 565.071 seconds, 31 release tests, coverage ratchets, mandatory
core browser smoke and performance gates. Runtime line-plus-branch coverage was
81.74%. Code health classified 571 files, with 33 size and two complexity
ratchets; documentation audit passed. Hosted checks remain required per commit.

## Subsystem progress

| Boundary | State | Evidence and next action |
| --- | --- | --- |
| Snapshot and read models (018) | In progress | Cold snapshot traversed visible events twice; slice 1 reuses counts. Content and event-only reads still construct the unused other GPT-wall section; characterize and isolate those projections next. |
| Shared domain services, orders and inventory (012/010) | Pending | Inspect mutation ownership, repetition and existing integrity coverage. |
| Finance and payroll (019/013) | Pending | Preserve postings, reversals, prices and revision/idempotency guards. |
| Storage and compatibility (017) | Pending | Inventory normalization side effects and recovery consumers before retirement. |
| Browser and asset loading (005/021) | Pending | Inspect request/render ownership and verify assembled assets. |
| Windows host and settings | Pending | Connection checks currently run synchronously in settings callbacks; measure responsiveness before moving work. |
| HTTP transport and authentication | Pending | Inspect route ownership and shared-service delegation. |
| MCP and Gateway (008/009) | Pending | Inspect registrations, workflow/readback ownership and contract parity. |
| Printing (014/021) | Pending | Inspect render/draft ownership and duplicate preparation. |
| Agent and integrations (206/011) | Pending | AST audit found identical numeric-limit normalization in automotive and web tools; inspect callers and edge cases. |
| Checks, tests, packaging and recovery (001/003/020) | Pending | Keep verification meaningful and installations/recovery reproducible. |

## Verified slices

### 1. Reuse visible event counts for snapshot revisions

`SnapshotService` passes the serialization counts to its private revision
builder. Revision-only reads still calculate counts once. Visibility filtering
and snapshot cache keys are unchanged; empty responses skip counting.

Focused snapshot/serialization suites: 20 tests passed. Regression cases cover
empty, active and archive-only boards in compact/full and archive enabled/disabled
modes, checking one event traversal and parity with revision-only reads.
Before/after JSON bytes matched in four compact/archive variants on the same
synthetic fixture and frozen clock using the original baseline implementation.

On one card and 50000 synthetic visible events, cold snapshots with two warmups
and 30 samples measured median 5.307 ms before and 2.933 ms after; p95 5.494 ms
before and 3.559 ms after. This isolates response assembly with the same prepared
bundle; it is not an end-to-end HTTP or production measurement.

The candidate passed the complete local CI profile: 2856 runtime tests in
563.500 seconds, 31 release tests, coverage/code-health/localization/parity
audits, core browser smoke and both performance gates. The focused restricted
employee/cashbox-access suite additionally passed 22 tests in 19.953 seconds.
GitHub publication and hosted CI are pending; record their SHA and outcome in
the next sweep update. Final cross-subsystem verification is pending.
