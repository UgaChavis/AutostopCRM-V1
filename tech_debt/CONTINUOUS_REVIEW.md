# Continuous code review

Owner request, 2026-10-15: review small coherent portions deeply, improve them,
publish each accepted portion, and continue until an explicit stop. Starting
revision: `adf0ed49d3e4a97158604bc733bd7e392d3d1e57` on `autostopcrm-v1`.

Second pass, explicitly started by the owner on 2026-10-16: baseline
`e93b102e41bd515de1891fa5633db6479b83334f`, freshly matched to GitHub with
[all five quality jobs passing](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38866686592).
Use a separate clean worktree and portion IDs `R2-NNN`; preserve the old worktree
and handoff archives. Accepted IR-001 through IR-007 remain accepted. IR-008 was
not accepted and was reverted; IR-009/010 remain research, not delivered fixes.

Existing size/complexity ownership remains in
[code_health_audit.py](../scripts/code_health_audit.py). The earlier
[UI sweep](SWEEP.md) is historical evidence, not acceptance of unreviewed code.

Each portion records before/after evidence, focused checks, full local CI and
the exact published SHA's hosted quality result. Detailed synthetic diagnostics
and publication receipts live under ignored `output/continuous-review/IR-NNN/`.
A clean portion needs no artificial source change. Production deployment and
historical-data repair require their own owner request.

For the second pass, freeze source/test hashes, run focused and neighboring
checks, then the complete `scripts/run_checks.ps1 -Profile ci`. Publish each
candidate as `codex/crm-review-v2-NNN`; require `unit`, `static`,
`browser-performance`, `docker-runtime-assets` and `quality` for its exact SHA.
Only then fast-forward `autostopcrm-v1` and require the same five main-branch
jobs. A changed base requires validation of the integrated candidate. Do not
force-push. Diagnose failures before at most one justified unchanged-candidate
retry; a second failure becomes a separate blocker investigation.

Detailed second-pass evidence lives under ignored
`output/continuous-review-v2/R2-NNN/`. Its `index.json` inventories every tracked
file by Git blob and records individual responsibilities in large modules.
Broad audits do not mark a file deeply reviewed; changed blobs require renewed
review. Generated files belong with their generators and build checks. Record
clean reviews without artificial commits. The run continues until the owner's
explicit stop; deployment, historical financial data and the Manager pin are
outside this run.

## Portions

| ID | Scope and evidence | State |
| --- | --- | --- |
| IR-001 | Manager structure fingerprints: service-lock/detached-read ownership; canonical JSON, unknown fields and status-only scope. Remove nested copies from hashing; retain detached projection behavior. Baseline: 29 service/Gateway tests. Six added contract tests pass before changes. Measurements and 1,000 differential cases in the local receipt. | Accepted at `456ceaf`; [quality run 38711453626](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38711453626), attempt 2 |
| IR-002 | Mutation operation validation: arrays/objects returned HTTP 500 from set membership instead of domain 422. Check the string type before the allow-list. Assert saved bytes, untouched request keys, existing Unicode 400 errors, and the legacy UTF-8 receipt digest across restart. | Accepted at `7280cf5`, including guard `47d9cb0` and canvas verification repair; [quality run 38725883478](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38725883478), attempt 1 |
| IR-003 | Automation control transport capability: missing `socket.AF_UNIX` raises `AttributeError` and HTTP 500 on Windows. Preserve the existing unavailable-controller 503 contract before socket creation; exercise direct client and real HTTP status/control with unchanged saved state. | Accepted at `6d6d140`; [quality run 38736120465](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38736120465), attempt 1 |
| IR-004 | Description durability, debt owner 012. Combined-field characterization exposed `Card.from_dict` stripping authored whitespace while JsonStore clones cards for save/load. Preserve string text with the existing cap and legacy non-string coercion; verify disk, readback, restart, no-op and complete audit values. | Accepted at `cd28408`; [quality run 38747033335](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38747033335), attempt 1 |
| IR-005 | `CardService.update_card` ordered simple-field dispatch, debt owner 012. Method lines 235 to 201; branch complexity 27 to 15. Preserve vehicle/profile and deadline/timer ordering, audit order, no-op behavior, defaults and late-validation isolation. | Accepted at `a22aabf`; [quality run 38755458358](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38755458358), attempt 1 |
| IR-006 | Prepared payment identity guard, debt owner 012. Replaying duplicate IDs adds a third cash movement: 200 to 300 while order paid total stays 200. Include stored-ID collisions after length truncation and a later whitespace trim; preserve correction guards before reconciliation. | Accepted at `eb20a9a`; [quality run 38764443140](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38764443140), attempt 2 |
| IR-007 | Cash cancellation payment links, owners 019/012. Match the exact cash movement, retain other payments/legacy IDs, and reject one movement referenced by multiple payments. Share order unlinking while keeping selected-reversal and last-delete journal contracts. | Accepted at `f9fbb6c31ef383c1241baa6a94af78a599ffbd4d`; [quality run 38776397620](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38776397620), attempt 2 |
| IR-008 | Legacy duplicate payment-ID reconciliation. Draft `6af768fde33feaca92de80e1c520edb0920a91ae` was not accepted; Linux SQLite WAL invalidated startup byte-snapshot assertions. Follow-up local runs exposed separate PDF/browser runner failures. | Cancelled and reverted by `1e4f418`; preserved handoff and worktree; reproduce again as R2-005 |
| IR-009 | Existing payment cash-link ownership, reuse and consistency; separate audit safe-fix concern. | Research only; reproduce on the new base as R2-006/007 |
| IR-010 | Combined deadline exceeds the one-year limit although individual fields pass. | Research only; reproduce on the new base as R2-008 |

## Second-pass queue

| ID | Bounded responsibility and required evidence | State |
| --- | --- | --- |
| R2-001 | Worker PDF routing and explicit fallback contracts, separated from isolated real Qt integration. Reproduce the old 10-second false failure, preserve the production 45-second subprocess limit, PDF limits and readable Cyrillic. | Accepted at `af7c539bd06df5a32ca4c30fd8da17dd06ba9dc3`; [candidate CI 38885558451](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38885558451) and [main CI 38886939483](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38886939483), both attempt 1 |
| R2-002 | External test lifecycle: core 30 minutes, each browser group five minutes; nonzero timeout, diagnostics and owned-descendant cleanup; same test IDs/order/coverage, no retries. | Accepted at `31ff1780ccabaf48dd6ec610392fdab140d909cc`; [candidate CI 38899866646](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38899866646) and [main CI 38902265141](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38902265141), both attempt 1 |
| R2-003 | Linux settings-wizard Qt exit 139: window/event teardown and test interaction; fix only a reproduced cause. | Investigated on `31ff178`; native Linux cause unresolved, no speculative change or commit |
| R2-004 | Browser/context/driver cleanup on errors and cancellation; investigate driver disappearance and `ERR_NO_BUFFER_SPACE` separately. | Accepted at `0ff6687f91603c05d7de45fd15b30133977b592d`; [candidate CI 38911489869](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38911489869) and [main CI 38913268055](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38913268055), both attempt 1; historical driver/buffer causes remain unproven |
| R2-005 | Reproduce IR-008 on the new base; minimal payment reconciliation fix, logical SQLite WAL restart checks and byte-stable warm replay. | Fresh reproduction on accepted `0ff6687`; full local CI passed on the only permitted unchanged-candidate retry; exact-SHA candidate/main acceptance pending |
| R2-006 | Reproduce IR-009: cash-link owner, amount, direction, cashbox, cancellation, transfers and reuse, including archives, concurrency and rollback. Preserve existing ambiguity/cancellation errors; use `repair_order_payment_cash_link_invalid` HTTP 409 for inconsistent links. | WIP frozen and externally archived; pre-existing Manager readback race reproduced during full CI; pending accepted shared baseline, no historical-data repair |
| R2-007 | Finance audit safe-fix must not reclassify cancelled or transfer movements as customer payments; preview, apply and repeat. | Pending; separate from normal payment linking |
| R2-008 | Reproduce IR-010: total deadline at most 31,536,000 seconds before mutation; existing `validation_error` HTTP 400, exact boundary across create/update/timer/restart. | Pending |
| R2-009 | Cashbox revision after payment removal: on accepted `0ff6687`, four synthetic cases keep the old cashbox timestamp after removing a ledger row and accept a request with that stale marker. Review revision meaning and all native callers before an independent correction. | New research, separate from payment identity/link validation; no historical repair |
| R2-010 | Manager manual-status readback ownership: block Save and duplicate Read during fresh-state fetch, preserve draft and recover on failure. | Both `6bd85e8` and integrated `0908fdc1f0e180a3dfc20721afd259585010c899` remain unaccepted; [new CI 38966599748](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38966599748) passes unit/static/Docker and all 20 Manager cases, then fails unchanged print-hydration assertion and teardown; no main promotion |
| R2-011 | PowerShell completion-probe timeout evidence: retain partial stdout/stderr and initialization phase in the original failure, preserving the 15-second timeout and native exit guards. | Accepted at `480e5537195438148bddc1e41ca94bba2ff0f24a`; [candidate CI 38957449774](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38957449774) and [main CI 38959188893](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38959188893), both attempt 1; original intermittent timeout remains unresolved |
| R2-012 | Print-hydration test capture readiness: visible loading can precede the Python route capture; preserve real overlap, original assertions/deadlines and production behavior. | Controlled original assertion reproduced on exact rejected `0908fdc1` and accepted `480e553`; stronger existing waits pass native and delayed-capture cases; full local CI passes on the sole justified retry after an unrelated performance refusal; exact-SHA hosted acceptance pending |

R2-010 reproduces a pre-existing readback/save race on both accepted `6ceed3d`
and the financial WIP: while GET is held, another Save sends the stale revision
and receives another native 409. Its local full CI passed only on the sole
justified retry after an unrelated Inventory initial-load timeout; that
Inventory cause remains unproven. Candidate hosted CI then failed in the first
unchanged PowerShell process-exit probe after 15 seconds (3,126 cases, one error,
43 existing skips). Static, browser-performance and Docker asset checks passed;
unit and aggregate quality failed. Main remained at R2-005 at that rejection;
R2-011 is now accepted separately on both branches.
The candidate, its failures and R2-006 financial WIP remain preserved separately.

R2-011 investigates that failure without rerunning full R2-010 CI. Fresh Ubuntu
24.04 runners with the same image, Python 3.12.15 and PowerShell 7.6.6 pass all
seven unchanged tests. Phase evidence places the longest observed stage in
first-use Add-Type compilation, followed by fast process exit and pipe closure;
it does not reproduce or prove the cause of the original 15-second failure.
Fixed alternating reference-list comparisons show only a small warm difference
and do not justify changing the native guard. Original partial output was not
retained by the failing unittest traceback and cannot be reconstructed.

A real 15-second sleeping PowerShell probe reproduces that lost-output defect
on the unchanged accepted helper. Keep the original TimeoutExpired and deadline,
add its captured streams as exception notes, and mark probe initialization in
stderr. A new native regression requires both streams and readiness in the
formatted failure report and rejects a false pass marker. The seven existing
test bodies, all assertions/skips, the production completion guard and
run_checks.ps1 remain unchanged. All eight focused cases pass on Windows (two
existing POSIX skips) and isolated Linux (zero skips); 22 neighboring lifecycle,
partition and release-path cases pass on Windows (three existing POSIX skips).
An initial new assertion assumed byte output; Windows returned text, so the
regression checks the shared formatted report. Both failed fixture evidence and
the final before/after regression are retained. This improves failure evidence;
the original intermittent timeout remains an open investigation.

R2-011 full local CI passed on attempt 1: 3,025 reported core cases (83
existing environment skips), all 39 isolated browser groups, 36 release cases
(four existing environment skips), 82.10% runtime coverage and all existing
smoke/performance gates. All 42 controllers finished successfully with no owned
live descendants. Five real PowerShell process-exit cases, including the
controlled 15-second timeout, pass; existing native PDF, Chromium cleanup and
payment reconciliation cases remain unskipped. Both frozen files matched after
validation; only this result metadata was added afterward. Hosted acceptance
passed on both branches at `480e5537195438148bddc1e41ca94bba2ff0f24a`: all
five jobs on each branch, 3,127 Linux cases (43 existing skips), all native
PowerShell cases unskipped and eight clean controllers per run. Its original
rare timeout remains unresolved.

The integrated R2-010 candidate `0908fdc1` passes its fresh full local CI on
accepted `480e553`: 3,025 core cases, 39 browser groups, 36 release cases and
82.10% runtime coverage; 42 controllers leave no owned descendants. Hosted
unit/static/Docker and all 11 original Manager plus nine concurrency cases pass.
The unchanged print-hydration test then fails at route count zero instead of
one; teardown separately raises Directory not empty. The complete rejected
candidate and diagnostics have a verified 126-member archive. Main remains at
accepted R2-011; financial R2-006 WIP stays frozen. No workflow rerun is used.

R2-012 preserves that failing run and compares the unchanged single scenario
on exact accepted `480e553` and candidate `0908fdc1`. Both native isolated cases
pass. A 200-ms delay before the Python print-route capture reproduces the same
original assertion on both; on the accepted base, a post-failure observation
then records the count moving from zero to one while the modal stays open and
loading. The board has no Manager readback function. This demonstrates the
missing route-readiness contract; the original uninstrumented schedule remains
unknown. The later Directory not empty error is not reproduced and remains a
separate open lifecycle investigation, not a proven cause of the assertion.

Keep one helper inside the existing test method to signal actual route capture.
Strengthen the existing UI-bound/loading waits with that signal before asserting
the original route counts. All original assertions, timeout values, sleeps,
wait count, setup, skips and cleanup are AST-checked unchanged. Production
sources and request/response shapes are unchanged. The fixed native case and
the same delayed-capture counterexample pass; all 49 neighboring print/lab
cases pass without skips. No compiler, runtime cleanup, financial data, source
pin or performance threshold is changed; full exact-candidate gates follow.

R2-012 full local CI1 passes all test groups but refuses the final performance
gate: change_feed.read_page p95 is 70.6 ms against the unchanged 50-ms limit.
All 42 controllers exit zero and leave no descendants. The exact 20-iteration,
two-warmup standalone benchmark then passes on accepted `480e553` (p95 6.0 ms)
and the unchanged candidate (6.1 ms). All 219 runtime sources normalize equally;
15 raw file differences are CRLF versus LF, and the benchmark harness is
identical. Global background resource activity was not sampled, so the original
performance refusal's cause remains unproven and no noise/indirect-regression
claim is made. The original report and both comparisons are retained.

The sole justified unchanged-candidate full retry passes: 3,025 core cases
(83 existing skips), all 39 browser groups, 36 release cases (four existing
skips), 82.10% coverage and every existing smoke/performance gate. Feed-read p95
is 5.1 ms with the same 50-ms limit. The real print-overlap scenario passes
without a skip, all 42 controllers are clean and both frozen files match.
Only this result metadata follows validation. No further full unchanged retry
is allowed across local/hosted validation. The separate historical teardown
Directory not empty remains unproven and is not declared fixed.

R2-001 replaces the old fixture-heavy worker smoke with deterministic tests for
thread routing, native failure/timeout, explicit fallback permission, Unicode,
invalid/oversized output and packaged-worker behavior. Its native integration
runs in an isolated process, disables plain-text fallback, and requires a real
Qt PDF with both source strings extracted through QtPdf. The 60-second outer
probe bound includes the unchanged 45-second production render and process/PDF
inspection overhead. Poppler rendering was visually inspected: both Cyrillic
strings and the red CSS styling are present. The production PDF source is
unchanged; no runtime speed claim is made.

R2-001 full local CI passed on attempt 1: 3,003 reported core cases (80 existing
environment skips; 3,004 planned IDs include a class-level skip), all 38 isolated
browser groups, 36 release cases (four existing environment skips), 82.09%
runtime coverage, mandatory core browser smoke and all existing performance
gates. All five frozen files, including the unchanged production PDF module,
matched their pre-run hashes. Only these result records were updated after
validation. All 128 focused/neighboring checks also pass, including seven
deterministic contracts and the real native Qt integration.

R2-001 hosted acceptance passed on both branches at
`af7c539bd06df5a32ca4c30fd8da17dd06ba9dc3`: all five jobs on each branch,
3,096 Linux unit cases (38 existing environment skips), 82.21% runtime coverage
and 36 release cases. The real Qt PDF integration runs without a skip on both
platforms. The clean primary local checkout was fast-forwarded to the accepted
SHA. All 28 evidence files, the Git bundle and complete blob inventory are
preserved in a checksum-verified external handoff archive.

R2-002 reproduces the old lifecycle failure with an unchanged runner and a
controlled Python child/grandchild, without starting a native browser: the
group remains alive at 305.1 seconds, then returns zero after explicit release.
The candidate wraps the existing commands rather than repartitioning them:
core/release commands have 1,800 seconds, existing browser command groups and
smoke have 300 seconds, with no retry. Timeout returns 124 and preserves command,
elapsed time, owned processes, cleanup result and an advance Python traceback.
Existing nonzero/signal results remain failures, including Linux native exit 139.

Windows ownership uses atomic process creation inside a Job Object, including
the venv launcher and its children. Only standard handles are inherited; closing
the controller's job handle terminates its members. The dedicated Linux
controller adopts orphaned descendants as a subreaper and uses process handles
plus renewed ancestry/start-time checks for cleanup, including detached children.
Successful commands with persistent live descendants are rejected and cleaned.
Fault tests verify that unrelated processes survive. Diagnostics live under
ignored `output/runtime-process/` and are uploaded separately for each hosted
suite/attempt. The previous test selection, order, coverage environment and
quality limits remain required; acceptance results are recorded below.

The real 300-second Windows probe initially exposed an early kernel wait result
at 299.997 seconds. A controlled early-wakeup regression fails before the fix;
waiting on the same handle against a fixed monotonic deadline passes it. The
final Windows implementation rejects the same group at 300.009
seconds with exit 124, a saved traceback and five owned processes reduced to
zero. Both real five-minute results remain in the receipt. The actual
PowerShell entrypoint retains success 0, child failure 7 and timeout 124.
All original 3,351 selected IDs and their order remain present; all 38 browser
groups are unchanged. Eleven real card-save browser cases and all eight PDF
worker cases pass under the controller. Linux signal/orphan behavior remains
an explicit hosted gate, including inherited ignored SIGCHLD and native 139.

The first full local R2-002 run was deliberately stopped, without acceptance,
after a separate controlled probe reproduced delayed Windows cancellation:
an interrupt requested at 0.2 seconds was delivered only after a five-second
native wait. Retain the same handle/deadline and poll it in at most 250-ms
intervals so Python can dispatch cancellation. The probe now exits 130 at
0.288 seconds and leaves no owned processes. A real cancellation regression
waits for child readiness before signalling. The aborted run's logs, frozen
hashes, original process identities and verified cleanup remain in the receipt;
the changed candidate requires a new complete local run, rather than a retry
of the unchanged candidate.

R2-002 full local CI passed for the revised candidate on attempt 2: 3,015
reported core cases (83 environment skips, including three new Linux-specific
contracts), all 38 unchanged browser groups, 36 release cases (four existing
environment skips), 82.09% runtime coverage and all existing smoke/performance
gates. All 41 command controllers finished successfully with no owned live
descendants left. All ten frozen files matched after validation; only these
result records were updated afterward. All original 3,351 selected IDs and
relative order are preserved, with 12 added lifecycle/binding regressions.
Hosted acceptance passed on both branches at
`31ff1780ccabaf48dd6ec610392fdab140d909cc`: all five jobs on each branch,
3,108 Linux unit cases (40 environment skips), 82.22% candidate / 82.21% main
runtime coverage and 36 release cases. Native Linux signal, orphan, cancellation
and unrelated-process contracts pass. All eight uploaded controllers on each
branch finish with exit zero and no owned descendants. The clean primary local
checkout matches the accepted SHA; 198 evidence members are preserved in an
externally stored archive with every member checksum verified after compression.

R2-003 retains the two original Linux crash logs. Both terminate inside the same
wizard-link test, without a saved native/Python stack. On the current base,
53 neighboring Qt lifecycle tests pass in Windows, both in their original order
and with forced collection before 534 status-style updates. Closed native dialog
objects remain after collection/deferred-event draining; this observation does
not establish the Linux crash's cause. No source change or artificial commit is
made. Native Linux reproduction or a new fault stack remains necessary; R2-002
now preserves faulthandler diagnostics for future native failures. Windows
success and the two passing current Linux gates do not close this finding.

R2-004 reproduces nine failing cases, including two real Chromium context leaks:
page/event setup can fail before the context's cleanup guard; acquiring a second
context can fail before the first is protected. A failed/cancelled viewer close
also prevents the admin close. Protect setup immediately after acquisition and
register the two contexts with AsyncExitStack in reverse cleanup order. Errors
and cancellation remain failures; subsequent callbacks run even when one close
fails. All nine frozen regression tests now pass, including real Chromium
cancellation, an unrelated live context and a usable driver after cleanup.
All 95 neighboring smoke, performance and runner checks pass. The native
lifecycle checks are added to the existing mandatory hosted browser command.
The business scenarios, their assertions and order stay unchanged. This bounded
review does not prove a cause for the historical missing driver, buffer error or
Qt crash; other browser helpers remain separate review responsibilities. No
runtime speed claim is made. Full local CI passed on attempt 1: 3,015 core cases
(83 environment skips), all 39 browser groups, 36 release cases (four environment
skips), 82.10% runtime coverage and all mandatory smoke/performance gates. All
42 controllers finish with exit zero and no owned descendants. All four frozen
files match after validation; only these result records are added afterward.
The prior 3,363 selected cases, core order and all 38 original browser groups
remain present. Exact-SHA hosted acceptance passed on both branches at
`0ff6687f91603c05d7de45fd15b30133977b592d`: all five jobs on each branch,
3,117 Linux unit cases (43 environment skips), 82.21% runtime coverage and
36 release cases. All three new native Chromium checks run without skips in the
mandatory browser job. All eight uploaded controllers per branch finish with
exit zero and no owned descendants. The clean primary checkout is synchronized;
179 evidence members, including the complete file/responsibility inventory,
are preserved in a checksum-verified external archive. A controlled owned driver
termination returns a connection-closed error and leaves no processes; it does
not reproduce the historical busy loop or establish its cause.

R2-005 reproduces the reverted IR-008 defect on accepted `0ff6687`: eight tests
produce 48 expected failing subcases. Retaining one of several old payment rows
with the same ID can preserve unrelated ledger movements or overwrite the
selected cash link. Within the single reconciliation function, require one
explicit original cash reference when that old ID is repeated, then match the
retained row by that reference. Ambiguity remains
`repair_order_payment_id_ambiguous` HTTP 409, before cash/audit draft changes.
Unique old IDs retain their original cash-link precedence; explicit replacement
with new IDs retains its prior behavior. Native cancellation still bypasses
bulk reconciliation and retains its original guards. No historical data is
repaired, and no request/response format is replaced.

All eight selected archived tests pass with unchanged assertions after the
18-line runtime change. The adopted fixture explicitly closes all its stores.
A ninth test races two operators selecting different duplicate-ID rows: one
save wins, the other and its stale repeat receive the existing
`card_update_conflict` 409 without writes; replaying the winner is byte-stable.
Running that test with the original function compiled from accepted Git source
produces four additional expected failures. All nine final tests cover 100
parameterized cases, and 82 neighboring tests pass. Thirty-two additional real
SQLite scenarios under the module-local Linux idle-WAL policy pass on Windows:
restart compares the full persisted bundle, state bytes and logical SQL dump;
warm replay still compares every file byte, including live WAL/SHM. SQLite
connections are not mocked. Native Linux remains a required hosted gate.
An AST comparison confines the runtime change to one existing function body;
no complexity/size limit is raised and no speed claim is made. Full local and
exact-SHA candidate/main acceptance follow.

The first full R2-005 local run failed in an unchanged Manager browser test:
`.node[data-id="E1"]` did not become visible within the existing 30-second wait.
All 3,024 core cases and 26 browser groups had passed. The failed group exited
nonzero; its controller cleaned its remaining owned child. Frozen hashes,
original commands and all 28 controller reports are preserved. The same seven
cases pass in a diagnostic run and in the exact original coverage command.
A separate clean `0ff6687` worktree passes those seven cases too. Candidate and
baseline lab seeding take the same 22 payment paths with no repeated old IDs;
Manager read/status routes call no payment reconciliation and return identical
200/200/503 responses. The 503 is the established local
`automation_control_unavailable` boundary, not an unexplained page exception.
The original visibility timeout's precise cause remains unproven; no Manager
source, assertion, timeout, skip or immutable pin is changed.

One justified full retry of the unchanged candidate passed: 3,024 core cases
(83 environment skips), all 39 browser groups, 36 release cases (four environment
skips), 82.10% runtime coverage and every mandatory smoke/performance gate.
The previously failed seven-case group passes in that complete run. All 42
controllers exit zero without owned descendants, and all three frozen files
match their original pre-run hashes. Only these result records are added after
validation. No further unchanged-candidate full retry is permitted. The original
failure and separate comparative diagnosis archives remain preserved.

IR-001 measured on Python 3.13.12 with the same harness, three series of 60
calls and no concurrent tests. Status-scope hash p95 on the public template:
2.4744/3.0583/2.7096 ms before, 1.2751/1.1544/1.2607 ms after. With 2,000
synthetic statuses: 13.0896/21.4292/23.0122 ms before,
5.5962/5.3928/5.2852 ms after. Peak traced allocations for that larger scope
fall from 2,412,587 to 1,978,771 bytes. Baseline timing variability is retained;
these are isolated hash costs, not an end-to-end application speed claim.
All 35 focused service/Gateway/digest tests pass; public JSON and storage formats
are unchanged. Full local CI passed: 2,969 reported core cases (81 environment
skips, including the POSIX-only class), all 38 isolated browser groups, 36 release
cases (four environment skips), 81.98% runtime coverage, core browser smoke and
all existing performance gates. Hosted acceptance: all five jobs pass for
`456ceaf86d67f194499a07ba3b7d5889cb2b5b17`; 3,062 unit cases (38 environment
skips), 82.10% runtime coverage and 36 release cases. Attempt 1 had two
15-second timeouts in unchanged PowerShell completion probes; failed-job retry
passed on the same SHA. Both attempts remain in the local receipt.

IR-002 before evidence: four direct-service type errors and two real-HTTP 500
responses reproduce the operation-type bug. The exploratory Unicode hypothesis
was narrowed after wire testing: invalid surrogate text already returns native
HTTP 400 `validation_error`, so that behavior and valid UTF-8 receipt hashes are
preserved. No financial, authorization or persistence-format rule changes.
Full local CI passed: 2,972 reported core cases (81 environment skips), all 38
isolated browser groups, 36 release cases (four environment skips), 81.98%
runtime coverage, core smoke and existing performance gates. The saved patch
was unchanged throughout validation; hosted acceptance follows publication.

IR-002 hosted verification exposed a pre-existing canvas test setup race:
`test_zoom_keys_do_not_intercept_editor_inputs` captured a selected view before
the mode toggle's queued animation-frame `fit()` ran. Holding and releasing
that callback reproduces the same 6.6-unit translation with no keypress.
Wait for the scheduled frame before selecting the node; retain the exact
transform assertion and additionally verify typed text and unchanged saved
graph. All 11 canvas tests pass locally, including mobile controls. Browser
plugin not available; the repository Playwright flow uses disposable state.
Diagnostics and screenshots remain outside Git. Repeated full local CI passed:
2,972 core cases (81 environment skips), all 38 browser groups, 36 release cases
(four environment skips), 81.98% runtime coverage and all existing gates.
The backend guard and verification patch stayed unchanged during that run.
Runtime UI is unchanged. Exact-SHA hosted acceptance passed at
`7280cf57f2a3ab17627d341cd5835ae177109e11`: all five jobs, 3,065 unit cases
(38 environment skips), 82.10% runtime coverage and 36 release cases.

IR-003 reproduces the observed Windows availability issue with two direct-client
errors and HTTP 500 from both status and control routes. Check the socket family
before opening a connection and reuse the existing OSError-to-503 translation.
Only the two positive Unix-socket tests retain a platform skip; availability
regressions now run on Windows too. Both routes preserve state-file bytes and
open no socket when the platform lacks support. Focused local checks: 14 cases,
six environment skips (two Unix round trips and four POSIX release checks).
Manager still owns execution and schedules; absence of its controller remains
unavailable, with no replacement transport or simulated healthy status.
Full local CI passed: 2,974 core cases (80 environment skips), all 38 isolated
browser groups, 36 release cases (four environment skips), 82.01% runtime
coverage and all existing smoke/performance gates. Earlier attempts exposed a
Playwright driver exit followed by a sync-dispatch loop, and a PDF worker still
alive after its 10-second test wait. The same covered card-save group (11 cases)
and printing module (80 cases) pass independently; both pass in the final full
run. Original logs and live stacks are retained. Source, assertions, timeouts
and quality thresholds stayed unchanged during that investigation.
Hosted acceptance passed at `6d6d14016ea43fccc503f8c3b5b124ebf4dff32c`:
all five jobs, 3,067 unit cases (38 environment skips), 82.12% runtime coverage
and 36 release cases. The primary local checkout is fast-forwarded to this
accepted revision.

IR-004 before evidence: 11 failing subcases cover description loss at create,
update, raw state, model reconstruction, restart, the length boundary and newline
normalization. `Card.from_dict` applied generic `normalize_text().strip()` to a
field whose service/editor/agent contract preserves authored text. A field-only
normalizer preserves strings and keeps the existing 20,000-character cap and
legacy non-string conversion. All 52 focused and 104 neighboring tests pass,
including both writer modes, draft rollback and full audit hydration. The audit
test now requires the complete original value instead of `original.strip()`.
Across 1,000 deterministic model comparisons, only description text differs;
all other stored fields, schema and caller payloads match. No runtime speed
claim is made. The original ordered-dispatch cleanup remains IR-005.
The real-service probe also confirms that an identical repeated description
previously reported a change and rewrote state bytes; the candidate preserves
the text and returns a no-op. Full local CI passed on attempt 1: 2,982 core cases
(80 environment skips), all 38 isolated browser groups, 36 release cases (four
environment skips), 82.06% runtime coverage and all existing gates. All five
candidate files matched their pre-run hashes after validation.
Hosted acceptance passed at `cd284086c9ecf18d331c50b7a94057602c695520`:
all five jobs, 3,075 unit cases (38 environment skips), 82.17% runtime coverage
and 36 release cases. Primary local checkout advanced to the accepted revision.

IR-005 replaces seven repeated field-update blocks with one ordered loop over
the same bound methods and default values. The method is 34 lines shorter;
the existing owner-012 branch-complexity ratchet retains its historical baseline
27 and now enforces maximum 15. Fifty-five focused checks pass, followed by all
22 ratchet checks after tightening. The original method from the accepted Git
revision and the candidate produce identical responses, domain errors, stored
JSON, write decisions and untouched caller payloads across all 128 field subsets
plus 47 edge cases in both writer modes (350 scenario pairs). This is a measured
maintainability change; no runtime speed claim is made.
Full local CI passed on attempt 1: 2,982 core cases (80 environment skips), all
38 isolated browser groups, 36 release cases (four environment skips), 82.03%
runtime coverage and all existing smoke/performance gates. All three candidate
files and the full Git patch match their pre-run hashes. Only this result
metadata is added after validation.

Hosted acceptance passed at `a22aabf981c0690a0d90af775ed581f9cfc65b2e`:
all five jobs, 3,075 unit cases (38 environment skips), 82.15% runtime coverage
and 36 release cases. Primary local checkout advanced to the accepted revision.

IR-006 synthetic reconnaissance also reproduces a collision missed by checking
only the first normalized ID: an 80-character ID ending in a boundary space
becomes the same stored 79-character ID as another payment. Across 1,000 model
vectors, trimming the first-pass ID matches the stable next-round-trip ID.
No historical records or deployed financial data are changed by this review.
The final seven-test suite produces 52 expected failing subcases against the
accepted baseline. All pass with a nine-line guard on prepared payment IDs in
`_update_repair_order`, after existing correction/number guards and before cash
or payroll reconciliation. The protected correction error remains
`repair_order_payment_locked`. Both update methods, field aliases and writer
modes preserve files, retained cache and caller payloads on rejection. Unique
IDs keep their cash links and no-op replay; IDs remain local to each order.
Malformed stored IDs remain readable, while unrelated order/row edits stop
before an ambiguous reconciliation. Fifty-two neighboring payment, correction,
cash-revision, closing, payroll and MCP checks pass. The standalone HTTP fixture
keeps existing test-module budgets unchanged. No runtime speed claim is made.
Full local CI passed on attempt 1: 2,989 core cases (80 environment skips), all
38 isolated browser groups, 36 release cases (four environment skips), 82.04%
runtime coverage and all existing smoke/performance gates. All three candidate
files and the full Git patch match their pre-run hashes. Only this result
metadata is added after validation.

Hosted IR-006 acceptance passed at `eb20a9a8ad5c4fbb99addba89e01d6c49328dc06`:
all five jobs, 3,082 unit cases (38 environment skips), 82.16% runtime coverage
and 36 release cases. Attempt 1 terminated with native Qt exit 139 during an
unchanged settings-wizard test, after all seven payment regressions passed.
The isolated test and 32-case settings UI module pass locally under coverage;
failed-job retry on the same SHA passed. Source, tests, timeouts and thresholds
stayed unchanged. The native crash's root cause remains unproven. Primary local
checkout advanced to the accepted revision.

IR-007 before evidence: 24 failing subcases on the accepted baseline cover
excess payment deletion and accepted ambiguous cash links. Seven tests now pass
without changing their frozen assertions. Shared unlinking selects by the exact
cash-transaction reference and leaves other ledger rows intact; a single cash
cancellation no longer invokes bulk payment reconciliation. Both journal forms,
audit order/correlation, closed-order rules, stale revisions, repeated-request
rejection, both writer modes and late-failure rollback are covered. Native
cancellation retains untouched legacy IDs, including three-row duplicates;
ordinary updates keep IR-006 uniqueness validation and cannot select the internal
cancellation mode through request data. Ambiguous links within/across orders
return 409 without saved/cache/request changes. HTTP tests retain the 403 access
boundary and exercise 409 with a separately authorized cash operator. All 70
neighboring checks pass, including IR-006, reversals, transfers and bundle
isolation. Existing size/complexity budgets pass; no runtime speed claim is made.
Full local CI passed on attempt 1: 2,996 core cases (80 environment skips), all
38 isolated browser groups, 36 release cases (four environment skips), 82.07%
runtime coverage and all existing smoke/performance gates. All five candidate
files and the full Git patch match the pre-run hashes. Only this result
metadata is added after validation; hosted acceptance follows publication.

IR-007 hosted acceptance passed at
`f9fbb6c31ef383c1241baa6a94af78a599ffbd4d`: all five jobs,
3,089 unit cases (38 environment skips), 82.18% runtime coverage and 36 release
cases. Attempt 1 had the same native Qt exit 139 in the settings-wizard test as
IR-006; unchanged attempt 2 passed. Its cause remains unproven and belongs to
R2-003. The later e93b102 baseline retains all accepted IR-001 through IR-007
runtime changes. The IR-008 handoff, verification patch and excluded IR-009/010
research are preserved outside Git; their existence does not imply acceptance.

## Coverage queue

| Area | Next bounded review boundary | State |
| --- | --- | --- |
| Manager structure | One graph mutation after accepted status validation and error side effects | IR-001 and IR-002 accepted |
| Read models and domain services | Legacy bulk-payment reconciliation after cash cancellation | IR-004 through IR-007 accepted; R2-005 pending |
| HTTP API and MCP | One transport/protocol or route/tool family | IR-003 accepted; other boundaries pending |
| Browser and Windows UI | One action's request ownership, draft and recovery | Pending |
| Printing and files | Worker PDF route/fallback and real native rendering | R2-001 accepted; other boundaries pending |
| Inventory, finance and payroll | Payment cash-link ownership and financial audit safe-fix | IR-007 accepted; R2-006/007 pending |
| JsonStore and change feed | One persistence or transaction boundary | Pending |
| Agent, tests and packaging | External runtime test deadlines and owned-process cleanup | R2-002 accepted; other boundaries pending |
