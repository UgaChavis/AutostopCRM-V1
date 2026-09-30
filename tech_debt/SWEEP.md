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

| Boundary | State | Evidence and disposition |
| --- | --- | --- |
| Snapshot and read models (018) | Reviewed, fixed | Slices 1/2 published with exact-SHA CI. Cache, search, cursor, visibility and journal ownership inspected; permitted Markdown projection isolated. |
| Shared domain services, orders and inventory (012/010) | Reviewed, preserved | Detached mutation and post-commit artifact boundaries inspected. Neighbour detachment scans per card (2000: median 40.439 ms). No change: participates in order mutation and is excluded by owner instruction. |
| Finance and payroll (019/013) | Reviewed, preserved | Transfer-pair cancellation and salary snapshot preservation inspected and covered by unchanged regressions. Repeated row normalization observed (300 next/300 previous: 900 parses, median 9.858 ms); payroll optimization explicitly excluded. |
| Storage and compatibility (017) | Reviewed, preserved | Process locks, CAS, cache invalidation, bounded reads and restore compatibility inspected. Cache-miss normalization can persist state; changing it requires a data compatibility decision. |
| Browser and asset loading (005/021) | Reviewed, preserved | 1460 assembled function definitions have no duplicate names. Three apparent unused functions are called by lazy loading or the print bridge; retained. Assembled JS and core smoke pass. |
| Windows host and settings | Reviewed, fixed | Slice 3 published with exact-SHA CI. Full network diagnostic no longer blocks Qt; existing persistence/stale-result logic remains. Individual checks/wizard are addressed in the authorized Windows diagnostic follow-up below. |
| HTTP transport and authentication | Reviewed, preserved | Route ownership, proxied mutation classification, service delegation and trusted operator-session injection inspected. Side-effecting reads cannot be reclassified as harmless GETs. API/auth regression suites pass. |
| MCP and Gateway (008/009) | Reviewed, preserved | Tool registration, schema/readback parity and bounded executor inspected. Worker retains its slot after caller cancellation; contextvars preserve authenticated ownership. Contract/native-guard suites pass. |
| Printing (014/021) | Reviewed, preserved | Shared render batches, per-document overrides, completion-act locks and stale async workspace ownership inspected. Printing/overlap regressions and browser checks pass. |
| Agent and integrations (206/011) | Reviewed, fixed | Slice 4 reuses identical numeric-limit normalization through the existing web adapter dependency; contracts/defaults retained. Public search/VIN protections and integration tests pass. |
| Runtime, automation and change feed | Reviewed, preserved | Control operation/field allowlists, bounded socket transport, revision/idempotency pass-through and opaque feed cursor/ack boundary inspected. Manager owns orchestration; CRM retains service/readback ownership. Producer parity covers all 106 write actions. |
| Checks, tests, packaging and recovery (001/003/020) | Reviewed, preserved | Full local/hosted gates pass for slices 1–3. Coverage/dependency budgets retained; snapshot size caps tightened. Backup schemas/checksum/path guards and build boundaries inspected; 31 release tests pass. No Windows installer or production release executed. |

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
audit, browser and performance gates. Published as `348424dc1e518cab012a9cc9f465f3025c1fdd55`;
[GitHub quality run 36732195982](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/36732195982)
passed for that exact SHA.

### 4. Share integration limit normalization

The automotive adapter now delegates to the existing web adapter normalizer,
already in its dependency chain. Its private method signature remains stable.
No price parsing, business rule, tool schema or external recipient changes.
The duplicated 13-line algorithm has one owner; no new runtime module was added.

49 focused automotive/web/Gateway tests passed. A regression matrix fixes
fallback, finite integer conversion, overflow and upper/lower-bound semantics.
Original versus candidate results matched in 3174 synthetic cases with three
minimum values, invalid types, random fractions, numeric strings and extremes.
This is a maintenance improvement; no latency improvement is claimed.
The final combined candidate passed the full local CI profile: 2863 runtime
tests in 648.661 seconds, 31 release tests in 2.647 seconds, runtime combined
line/branch coverage 81.78%, all audit/parity/browser/performance gates. The
publication identity of this final slice is the enclosing Git commit (recoverable
with `git log -1 -- tech_debt/SWEEP.md`); its exact-SHA GitHub quality checks are
the hosted acceptance record. Publication is accepted only when all required
jobs succeed, and the final user report records the SHA and run URL.

## Follow-ups outside this pass

- Keep payroll, finance, pricing and order behavior unchanged as instructed.
  The measured payroll/order detachment observations are not applied patches.
- Before changing normalization during reads, inventory supported stored formats,
  side effects and migration/rollback using synthetic fixtures. No live business
  GET was used as a supposedly harmless probe.
- The original pass moved only the full diagnostic button; the subsequent
  authorized Windows follow-up below extends its worker to individual checks
  and sequential ChatGPT preflight. Runtime start/stop and deployments retain
  their existing behavior.
- Retain deployed compatibility names and all backup schemas; retirement requires
  client/data inventory and a separate migration decision.
- Windows installer execution, real printer output, external-provider availability
  and production rollout remain unverified and outside publication scope.

The pass is a subsystem-boundary review backed by existing and added regression
checks, not a proof that every possible defect is absent. Final cross-subsystem
verification uses the complete CI profile on the last combined candidate and
GitHub quality jobs on its exact published SHA. Source totals are 167 -> 169
tracked runtime Python files and 101056 -> 101159 lines (+103); projection and
worker ownership, rather than net deletion, explain the change. Business service
rules, storage, API/MCP, printing and browser source files have no diff from the
baseline (except the documented snapshot read boundary). The original manager
worktree remains separate and untouched. The subsystem review and local final
verification are complete; hosted acceptance belongs to the enclosing commit's
checks, not to a prediction written before that run.

## Windows diagnostic follow-up, 2026-09-30

Authorized after the first pass, from `b3d45d01d5df8230945eaec1724cc88f4cb85f25`.
Remove GUI-thread network waits in four individual checks and ChatGPT preflight,
preserving endpoint eligibility, MCP-before-external order, result tones, form
validation and native stale-settings reconciliation. No business services,
API/MCP schemas, financial/order rules or production deployment changes.

The full/individual checks share `SettingsConnectionCheck`. Only network probes
run in the worker; persistence and widget changes run on the GUI thread. The
wizard uses request identities tied to its QObject lifetime. Closing, deleting
or replacing a wizard discards its pending result and stops the next preflight
stage. A running probe retains the exclusive slot until it finishes; duplicate
checks cannot bypass it. Cancellation status becomes terminal when the pending
probe finishes. Thread-start, probe and persistence errors restore
controls and display generic messages without private exception details.
Superseded wizard widgets are disposed rather than retained as hidden children.

87 focused UI/service/write-integrity tests passed, including nine new regression
tests with matrices over all four targets, both preflight stages, native status
combinations, public URL eligibility, cancellation/deletion/replacement, foreign
responses, changed settings, worker startup and persistence failures. Existing
full-check regressions remain successful.

With the same synthetic 200 ms probe per target, a 10 ms Qt timer fired at
225.945 ms before versus 9.642 ms after for an individual check, and 420.374 ms
before versus 10.409 ms after for the sequential wizard. Completed operations
took 211.480/443.559 ms after the change. These measurements demonstrate GUI
responsiveness, not faster network IO. The final full local CI profile passed:
2872 discovered runtime tests in 589.407 seconds (61 existing platform skips),
31 release tests in 2.668 seconds (three Windows symlink privilege skips),
runtime combined line/branch coverage 81.86%, all audit/parity/browser/performance
gates. The initial candidate run was stopped to add a terminal cancellation
status; the complete run above verifies the final code. No checks were weakened.
Publication identity is the enclosing Git commit; its exact-SHA GitHub quality
checks are the hosted acceptance record, with SHA/run URL in the final report.
A successful Ubuntu harness remains required for the POSIX-only checks. Actual
Windows installer, production endpoints and deployment remain outside scope.
