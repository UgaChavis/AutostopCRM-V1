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
| IR-003 | Automation control transport capability: missing `socket.AF_UNIX` raises `AttributeError` and HTTP 500 on Windows. Preserve the existing unavailable-controller 503 contract before socket creation; exercise direct client and real HTTP status/control with unchanged saved state. | Focused checks and full local CI passed; publication and exact-SHA hosted acceptance pending |

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

## Coverage queue

| Area | Next bounded review boundary | State |
| --- | --- | --- |
| Manager structure | One graph mutation after accepted status validation and error side effects | IR-001 and IR-002 accepted |
| Read models and domain services | One snapshot projection, then one card/client/repair operation | Pending |
| HTTP API and MCP | Windows automation client without AF_UNIX, then route/tool families | IR-003 in progress |
| Browser and Windows UI | One action's request ownership, draft and recovery | Pending |
| Printing and files | One field/download/print or file lifetime | Pending |
| Inventory, finance and payroll | One operation/calculation with unchanged financial rules | Pending |
| JsonStore and change feed | One persistence or transaction boundary | Pending |
| Agent, tests and packaging | One runtime/tool/test/build responsibility | Pending |
