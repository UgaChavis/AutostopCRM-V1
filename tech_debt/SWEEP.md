# CRM maintenance sweep

One complete subsystem pass, started on 2026-09-30 from GitHub revision
`d14fea683ead4c4f7c9e502f1d4f65bd006030b4`. Publish verified slices to
`autostopcrm-v1`; production deployment is outside this work. Public contracts,
business rules and persisted formats stay compatible. Proposals that change
them require a separate owner decision. The owner additionally excluded all
business-rule changes: payroll accruals, payments, prices and order rules remain
as implemented. Only technical behavior-preserving changes are in scope.

## Baseline

The unmodified baseline passed `scripts/run_checks.ps1 -Profile ci`: 2855
runtime tests in 565.071 seconds, 31 release tests, coverage ratchets, mandatory
core browser smoke and performance gates. Runtime line-plus-branch coverage was
81.74%. Code health classified 571 files, with 33 size and two complexity
ratchets; documentation audit passed. Hosted checks remain required per commit.

## Subsystem progress

| Boundary | State | Evidence and next action |
| --- | --- | --- |
| Snapshot and read models (018) | In progress | Both slices published with successful exact-SHA CI; Markdown and requested-section preparation isolated. Cache, search, cursor and journal ownership inspected. |
| Shared domain services, orders and inventory (012/010) | In progress | Detached mutation and post-commit artifact boundaries inspected. Neighbour detachment scans the entire list per card; 2000 detachments measured median 40.439 ms. Optimization left unapplied because this helper participates in order mutation; preserve current behavior. |
| Finance and payroll (019/013) | In progress | Transfer cancellation and snapshot preservation boundaries inspected. Work-row preservation parses 300 next rows twice: 900 total row parses with 300 previous rows, median 9.858 ms. Observation only: payroll optimization excluded by owner instruction; no source changes. |
| Storage and compatibility (017) | In progress | Cache-miss normalization can persist data; process locks, CAS and read-cache invalidation inspected. Keep migration/recovery consumers until supported-data evidence permits retirement. |
| Browser and asset loading (005/021) | In progress | 1460 assembled function definitions have no duplicate names. Three apparent unused functions are called by the lazy loader or injected print bridge; retain them. |
| Windows host and settings | In progress | A simulated 200 ms network check delayed a 10 ms Qt timer to 224 ms. Move full connection diagnostics off the UI thread, preserving stale-settings protection. |
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
Published as `1fc1eeb98c00db3763fb1c1582425c0c20755b41`; all jobs in
[GitHub quality run 36724521691](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/36724521691)
passed for that exact SHA. Final cross-subsystem verification is pending.

### 2. Prepare only requested board read sections

Pure Markdown formatting now belongs to `BoardReadProjection`. SnapshotService
keeps storage reads, visibility, cache and view selection. Content-only reads
skip event projection/formatting; event-only reads skip card/sticky serialization
and board Markdown. Full GPT-wall output and public method signatures stay stable.

Original-baseline versus candidate JSON bytes matched in 48 variants: full wall,
content and events; archive included/excluded; privileged/restricted sessions;
agent/full/audit/invalid view modes. Ten focused GPT-wall tests passed, including
requested-section parity and regression guards against discarded projection work.

On the same prepared synthetic bundle of 200 cards and 2000 events, two warmups
and 30 samples measured content median 20.507 ms before versus 19.259 ms after;
event-only median 53.760 ms before versus 0.748 ms after, p95 65.981 versus
0.959 ms. Event-only reads perform zero card serializations rather than 200.
These are assembly measurements, not HTTP/production latency claims.

The full local CI profile passed: 2859 runtime tests in 575.905 seconds and
31 release tests, with all audit, browser and performance gates. The snapshot
module/class size caps were tightened to 1352/1270 lines. Published as `c70488d353714fc7e0cc3925862bef28adf450a2`;
[GitHub quality run 36728147635](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/36728147635)
passed for that exact SHA.

### 3. Keep full Windows connection diagnostics responsive

Connection probes execute on a daemon worker that retains only service/settings,
with queued results to the GUI thread. Duplicate checks are blocked. Editing
controls are disabled while pending; Cancel stays available. Closing the dialog
discards the result. Probe exceptions restore controls with a generic message;
private exception details are not displayed. Existing settings persistence and
stale-configuration reconciliation remain unchanged. Individual connection tests
and the ChatGPT wizard retain their existing synchronous behavior.

78 focused settings UI/service/write-integrity tests passed. Regressions cover
UI heartbeat while a probe blocks, duplicate suppression, GUI-thread persistence,
closed-dialog result discard, exception recovery and stale-setting warnings. On
the same synthetic 200 ms probe, a 10 ms Qt timer fired at 10.830 ms after the
change versus 224 ms before; total check time was 209.138 ms. This demonstrates
UI responsiveness rather than faster network access. A standalone deleted-dialog
late-result check also passed without persistence or popup. Full local CI passed:
2862 runtime tests in 566.017 seconds, 31 release tests in 2.773 seconds, all
audit, browser and performance gates. Hosted CI remains required for this slice.
