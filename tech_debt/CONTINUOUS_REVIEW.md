# Continuous code review

Owner request, 2026-10-15: review small coherent portions deeply, improve them,
publish each accepted portion, and continue until an explicit stop. Starting
revision: `adf0ed49d3e4a97158604bc733bd7e392d3d1e57` on `autostopcrm-v1`.

Existing size/complexity ownership remains in
[code_health_audit.py](../scripts/code_health_audit.py). The earlier
[UI sweep](SWEEP.md) is historical evidence, not acceptance of unreviewed code.

Each portion records before/after evidence, focused checks, full local CI and
the exact published SHA's hosted quality result. Detailed synthetic diagnostics
and publication receipts live under ignored `output/continuous-review/IR-NNN/`.
A clean portion needs no artificial source change. Production deployment and
historical-data repair require their own owner request.

## Portions

| ID | Scope and evidence | State |
| --- | --- | --- |
| IR-001 | Manager structure fingerprints: service-lock/detached-read ownership; canonical JSON, unknown fields and status-only scope. Remove nested copies from hashing; retain detached projection behavior. Baseline: 29 service/Gateway tests. Six added contract tests pass before changes. Measurements and 1,000 differential cases in the local receipt. | Accepted at `456ceaf`; [quality run 38711453626](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38711453626), attempt 2 |
| IR-002 | Mutation operation validation: arrays/objects returned HTTP 500 from set membership instead of domain 422. Check the string type before the allow-list. Assert saved bytes, untouched request keys, existing Unicode 400 errors, and the legacy UTF-8 receipt digest across restart. | Accepted at `7280cf5`, including guard `47d9cb0` and canvas verification repair; [quality run 38725883478](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38725883478), attempt 1 |
| IR-003 | Automation control transport capability: missing `socket.AF_UNIX` raises `AttributeError` and HTTP 500 on Windows. Preserve the existing unavailable-controller 503 contract before socket creation; exercise direct client and real HTTP status/control with unchanged saved state. | Accepted at `6d6d140`; [quality run 38736120465](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38736120465), attempt 1 |
| IR-004 | Description durability, debt owner 012. Combined-field characterization exposed `Card.from_dict` stripping authored whitespace while JsonStore clones cards for save/load. Preserve string text with the existing cap and legacy non-string coercion; verify disk, readback, restart, no-op and complete audit values. | Accepted at `cd28408`; [quality run 38747033335](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38747033335), attempt 1 |
| IR-005 | `CardService.update_card` ordered simple-field dispatch, debt owner 012. Method lines 235 to 201; branch complexity 27 to 15. Preserve vehicle/profile and deadline/timer ordering, audit order, no-op behavior, defaults and late-validation isolation. | Accepted at `a22aabf`; [quality run 38755458358](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38755458358), attempt 1 |
| IR-006 | Prepared payment identity guard, debt owner 012. Replaying duplicate IDs adds a third cash movement: 200 to 300 while order paid total stays 200. Include stored-ID collisions after length truncation and a later whitespace trim; preserve correction guards before reconciliation. | Accepted at `eb20a9a`; [quality run 38764443140](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38764443140), attempt 2 |
| IR-007 | Cash cancellation payment links, owners 019/012. Match the exact cash movement, retain other payments/legacy IDs, and reject one movement referenced by multiple payments. Share order unlinking while keeping selected-reversal and last-delete journal contracts. | Full local CI passed on attempt 1; exact-SHA hosted acceptance pending publication |

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

## Coverage queue

| Area | Next bounded review boundary | State |
| --- | --- | --- |
| Manager structure | One graph mutation after accepted status validation and error side effects | IR-001 and IR-002 accepted |
| Read models and domain services | Legacy bulk-payment reconciliation after cash cancellation | IR-004 through IR-006 accepted; IR-007 under validation |
| HTTP API and MCP | One transport/protocol or route/tool family | IR-003 accepted; other boundaries pending |
| Browser and Windows UI | One action's request ownership, draft and recovery | Pending |
| Printing and files | One field/download/print or file lifetime | Pending |
| Inventory, finance and payroll | One cash cancellation with original journal/guard contracts | IR-007 under validation; other boundaries pending |
| JsonStore and change feed | One persistence or transaction boundary | Pending |
| Agent, tests and packaging | One runtime/tool/test/build responsibility | Pending |
