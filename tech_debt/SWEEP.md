# UI stability sweep

Started 2026-10-12 at `579a2573cfd8abc5276cbeb4aba17e011b400743` on
`codex/crm-ui-stability-sweep`. Goal: functional, failure/concurrency and
integration passes over every UI family; two consecutive clean complete
browser runs after the final fix; local CI and exact published SHA accepted by
`quality.yml`. Business rules, authorization, API/MCP contracts and storage
formats are preserved. Source only: no production, SSH, installed-client
profile or live data. The primary checkout's 41 staged constructor files stay
untouched.

2026-10-13 owner decision: finish the current inventory editor portion, then
pause the whole-app audit and prepare a server release. Do not start another
UI family. Deployment requires the owner's separate update command. Remaining
families/final acceptance stay explicitly incomplete; this is not goal completion.

2026-10-13 subsequent owner instruction supersedes that pause: resume iterative
code review and technical fixes until the owner stops the goal. Each portion
requires local CI, direct fast-forward publication and exact-SHA hosted CI.
Business rules remain frozen. Production update is outside this ongoing cycle
and requires a separate command; the earlier authorized deployment is complete.

## Baseline and environment

- Baseline CI: 3,023 runtime tests, six Windows failures, 83 environment skips.
  Explicit UTF-8, a pinned asset's LF checkout and isolated Compose plugin
  discovery repaired those failures in ENV-01 without changing runtime rules.
- Baseline full browser: 49/49, first render 549.1 ms, no unexplained console,
  page or request errors. The smoke missed the reproduced faults below.
- Browser plugin skill absent: repository Playwright tests use disposable CRM
  runtimes, synthetic data and independent operator sessions. Diagnostics and
  screenshots stay outside Git. Pinned dependencies are in the ignored venv.
- Canonical M2 private state/journal is absent locally. No SSH is authorized;
  source-side evidence and the remaining queue are recorded here.

## Screen and action register

`pending` means unaccepted even when the existing smoke passes. Accepted
portions still participate in the final whole-application integration pass.

| Family / actions and expected result | Functional and failure evidence | Acceptance |
| --- | --- | --- |
| Desktop cards: create/update, one click, conflict, uncertain reply, client/vehicle stages; close after all confirmed actions, retain draft on errors | 11 real-browser regressions; drafts, ownership, partial completion, stale snapshot and bounded module loading | Portion accepted at `b7a35a3`; final integration pending |
| Columns and badges: stable ID through create/rename/order/delete, personal copies, hover/open, own/other changes, reload/login | Five browser badge regressions with two users and personal copies; full column smoke | Portion accepted at `b7a35a3`; final integration pending |
| Clients/vehicles/search: create/link/edit/select/search; drafts, validation, late replies, pending controls, logout, uncertain writes | 20 new popup/profile/vehicle browser regressions, three portable desktop/mobile search tests; client/ownership contracts | Portion accepted at `a852bcd`; final integration pending |
| Attachments/shared files/editors/preview/print: upload/delete/open/edit, failures, draft and return navigation | UI-17 through UI-26 accepted, seven shared-file regressions, editor/print functional and race smoke | Portion accepted at `0040f75`; final integration pending |
| Inventory/orders UI: load/edit/repeated clicks, conflict/native errors; unchanged financial values/guards | Existing smoke and contracts; deeper failure pass pending | pending |
| Cashboxes/payroll/employees UI: lists/periods/dialogs, repeated clicks/errors; unchanged calculations | 17 cashbox, 12 payroll-dialog, seven profile, four workspace recovery and eight deletion browser cases | Portions accepted through `af24f9b`; final integration pending |
| Settings/roles/manager maps/dashboard: save/cancel, stale replies, loading/navigation | Existing smoke and contracts; deeper pass pending | pending |
| Mobile: all panels, navigation/cards, small viewport, failures/drafts | Three portable client-search browser regressions and existing mobile smoke; other panels pending | pending |
| Windows: source startup, settings/dialogs/native workflows on temporary data | Existing runtime/release tests; deeper source UI pass pending | pending |
| Shared mechanisms: requests/cache/polling/modals/logout/reconnect | Card/client ownership and failure regressions; remaining families pending | pending |

## Confirmed findings and portions

| ID | Reproduction / consequence | Result / regression | Publication |
| --- | --- | --- | --- |
| ENV-01 | Six baseline Windows failures from locale, CRLF and isolated Compose discovery | UTF-8/LF/plugin path fixes; full local and hosted CI passed | `b03a5d4`, accepted with card portion |
| UI-01 | Fail link after confirmed card create, then retry: second create request | Keep confirmed card ID; retry only unfinished stage | `b7a35a3` |
| UI-02 | Edit during pending card save: later input lost on close | Freeze conflicting controls and restore previous disabled states | `b7a35a3` |
| UI-03 | Fail mark-seen: NEW disappears despite failed acknowledgement | Read back/restore markers and suppression by user/version | `b7a35a3` |
| UI-04 | Late seen reply replaces a newer saved title | Merge current-version markers without replacing content | `b7a35a3` |
| UI-05 | Open NEW and reload: acknowledgement was suppressed | Separate pending optimism and confirmed read | `b7a35a3` |
| UI-06 | Release snapshot sampled before save: previous title restored | Advance mutation generation on confirmed writes | `b7a35a3` |
| UI-07 | Lost no-op update, readback succeeds, NEW returns on reload | Preserve change metadata and confirm personal read | `b7a35a3` |
| UI-08 | Create client, fail saved-card link, retry: create sent again | Keep client ID and retry only the link | `aa1b697`; accepted at `a852bcd` |
| UI-09 | Create client for unsaved card, fail vehicle stage, retry: create sent again | Keep client checkpoint and retry unfinished vehicle stage | `aa1b697`; accepted at `a852bcd` |
| UI-10 | Link response replaces unsaved card title/description | Update confirmed association while retaining draft and dirty state | `aa1b697`; accepted at `a852bcd` |
| UI-11 | Popup fields editable during pending client creation | Freeze popup controls, prevent repeat submit, restore on errors | `aa1b697`; accepted at `a852bcd` |
| UI-12 | Close creation popup, open another, release old reply: new popup affected | Bind result to card, viewer and popup generation | `aa1b697`; accepted at `a852bcd` |
| UI-13 | Client profile fields editable during save/refresh and overwritten | Lock owned controls until confirmed rendering, restore on errors/logout | `aa1b697`; accepted at `a852bcd` |
| UI-14 | Lose profile-create reply after commit, retry: create sent again | Read list, retain draft, block blind repeat without a known receipt ID | `aa1b697`; accepted at `a852bcd` |
| UI-15 | Vehicle refresh fills profile and erases unsaved comment | Refresh vehicles, restore edited profile fields and dirty baseline | `aa1b697`; accepted at `a852bcd` |
| UI-16 | Lose new-vehicle reply after commit, retry: upsert sent again | Read profile, stop blind create; known-ID update/delete reconcile by reading | `aa1b697`; accepted at `a852bcd` |

Card portion: 16 browser regressions, 294 web contracts, full browser 49/49
(first render 573.8 ms), local CI 3,039 runtime tests/83 environment skips and
36 release tests/4 environment skips; generated JS, parity, ratchets and all
performance gates passed. Exact-SHA
[quality.yml run 38263040268](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38263040268)
passed unit, static, browser-performance, docker-runtime-assets and quality for
`b7a35a39881feff1dcce276c2b453c0bc78e2d81`. Both commits were fast-forward
published. Startup JavaScript: 898,403 baseline -> 899,537 bytes, below the
unchanged 901,000-byte gate; no speedup is claimed.

Client portion: five popup defects and two profile defects failed browser
regressions before fixes; vehicle draft loss and repeated upsert also failed
before fixes. Repeated creation changes from two create POSTs to one confirmed
client ID plus only the unfinished stage. Native duplicate detection, vehicle
normalization/synchronization, deletion and authorization rules remain in
unchanged services. All 39 card/client/search browser regressions pass; the
final full browser passed 49/49, first render 530.3 ms, no unexplained console,
page or request errors. Generated JS and 294 web contracts pass.
Full local CI passed: 3,062 runtime tests (81 environment skips), 36 release
tests (four environment skips), coverage, parity, core browser and performance
gates. An obsolete textual dirty-button expectation was updated to require
the new in-flight guard; the real browser test verifies that guard too.
Startup JS: 899,537 -> 897,393 bytes, below the unchanged 901,000-byte gate.
No speedup is claimed. The portion is accepted by the exact-SHA hosted run for `a852bcd` below.

## Concrete limits and next actions

- Create-card/client/vehicle APIs provide no durable request receipt lookup.
  A lost create reply without a known ID cannot be safely attributed using
  title, name, phone or VIN. The guarded UI reads data, retains the draft and
  stops blind repeat. Automatic recovery needs a separate compatible API
  proposal: durable request key/receipt, replay semantics and rollback to the
  guarded UI. No contract change is included here.
- Cashbox fault reproduced on disposable data: a lost committed transaction
  reply followed by retry produces two distinct native receipts. Review UI
  ownership/readback and uncertain-result barriers; do not change finance
  services or repair production records.
- Finish each remaining family, then repeat transitions on fresh data. The
  two final clean complete browser runs have not yet been performed.

ENV-02: exact-SHA quality run 38276836661 for
`aa1b6972c84e2ab1be48cc89de55e3d25a04efde` passed static, browser-performance
and Docker assets, but its unit job exposed an existing PowerShell interruption
race: the native child received Ctrl+C while the outer host returned zero.
A native process-exit completion guard covers a skipped finally command; four
portable subprocess regressions verify success, original failure, incomplete
zero exit and resetting the guard. The original strict POSIX Ctrl+C regression
now repeats three times. Local Linux reproduction is unavailable because the
Docker Desktop WSL backend failed to start; Linux PTY acceptance must come from
GitHub CI. No Docker reset or data cleanup was performed.

Hosted unit jobs omit Chromium, so optional browser classes skip there.
The browser-performance job now requires all 36 card/client draft regressions
in addition to its existing three client-search scenarios. Missing browser
dependencies fail that required step rather than silently skipping it.
ENV-02 local CI passed: 3,066 runtime tests (80 environment skips), 36 release
tests (four environment skips), all parity, browser and performance gates.
Its required 36 browser regressions also passed separately in 70.115 seconds.
Exact-SHA [quality run 38288275298](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38288275298)
for `a852bcd7fb32a6340b0f2473d9479679aeeedb2d` passed all five jobs.
Hosted evidence: 3,027 runtime tests, 36 release tests, 36 required card/client
browser regressions (42.431 seconds), three existing client-search scenarios,
real Linux Ctrl+C repeated three times, and all performance/Docker gates.
ENV-02 and UI-08 through UI-16 are accepted at this SHA. The earlier failed
run remains recorded as the reason for the repair; no checks were weakened.
Local and remote SHA match; the primary checkout still has 41 staged files.

Next portion: cashbox controls, then payroll/employee dialogs and inventory.
The remaining UI families and both final clean whole-application runs are pending.


## Attachment/shared-file/preview/print candidate (from a852bcd)

Confirmed UI-17: close a loading preview; its late success reopens it.
UI-18: select a second preview; the old success replaces the new image.
UI-19: an old preview error erases the newer ready preview.
Those three real-browser cases failed before request/viewer/card ownership
was added; stale replies now allocate no object URL or mutate current state.
Binary preview/download reads have a 90-second deadline through the body.

UI-20: upload two files, fail the second, reselect the same pair: first file
is duplicated (three stored files / four POSTs). Checkpoint confirmed receipt
IDs; retry verifies the known file's bytes by that ID and only writes the
unfinished file (two stored files / three POSTs). An unknown write receipt
reads current attachments and stops blind repeat. File upload/delete and shared
writes have bounded transport; existing API read deadlines remain unchanged.

UI-21: hold passive attachment-status read, start timer, release old read:
active editor revision/time state reverts. Passive reads now reject a changed
local version; mutation readback still accepts its confirmed server revision.
UI-22: start shared-file opening, open a card before its read returns: the
old read steals focus by opening the file workspace. Bind read application to
its original modal navigation.
UI-23: lose a committed shared-file paste reply, repeat: two copies. Preserve
known completion and stop repeat while an unidentifiable paste is pending.
Viewer reset clears that private checkpoint. No ID is guessed from a name.

Local evidence so far: eight attachment browser cases + seven existing file
ownership tests + four printing browser cases passed (19 tests / 35.174s);
three shared-file browser cases passed (10.661s). All 294 web asset contracts
passed with actual bounded-request dependencies; no assertions or thresholds
were removed. Generated JS, full smoke and full local CI pass; publication
and exact-SHA GitHub acceptance are pending. Business services,
public contracts and formats are unchanged. Source diagnostics stay in TEMP.

Other UI families and both final whole-application runs are still pending.

File candidate full smoke passed 49/49 (first render 536.0 ms), no console/page
errors or failed requests. Startup JS is 900,927 bytes under the unchanged
901,000-byte limit. Full local CI passed: 3,077 runtime tests (80 environment
skips), 36 release tests (four environment skips), coverage, parity, generated
JavaScript, browser and all unchanged performance gates. Final diff is scoped
to file UI, its module context, regressions, required hosted browser coverage
and this register. The primary checkout still contains its 41 staged files.
Fast-forward published as `869e503d8890b6636b4531c5dde0d5bfb9e9b01a`.
Exact-SHA [quality run 38297845071](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38297845071)
passed all five jobs, including the required attachment/shared-file/print
browser regressions. UI-17 through UI-23 are accepted at this SHA.

Shared-file follow-up from `869e503`: three additional faults failed real
browser tests: UI-24 partial upload of two files retries the confirmed first
file; UI-25 lost committed upload reply allows another upload; UI-26 lost
native clipboard paste reply allows another paste. Tests inject only temporary
synthetic paths into the disposable API's clipboard provider.
Keep confirmed native file IDs, verify their bytes on batch retry, and repeat
only unfinished writes. A confirmed write followed by failed list refresh
retries reads; an unknown receipt reads the current list and stops blind repeat.
Private checkpoints reset with the viewer. No service or public contract changes.
Seven shared-file browser cases and related source/module contracts pass
(166 targeted tests / 13.667s). Generated JavaScript and code-health limits
pass. Full browser passed 49/49, first render 532.4 ms, no console/page/request
errors. Full local CI passed: 3,081 runtime tests (80 environment skips),
36 release tests (four environment skips), coverage/parity/browser/performance
and unchanged code-health limits. Publication and exact-SHA hosted acceptance
of this follow-up are pending.

Published follow-up `468de56b79040f8dae0026039b21b1a06170f715` matched the
remote ref. Hosted [quality run 38301310370](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38301310370)
failed the existing manager-map browser test. ENV-03: its login helper waited
only for visible map nodes; a startup technical status read scheduled for the
next animation frame could arrive after interaction recording began. A delayed
first animation frame reproduces the same failure locally. Wait for the initial
status response to finish before recording map interactions; the assertion
for zero business API calls remains unchanged. The production UI is unchanged.
Next publication is paused until the exact-SHA repair passes hosted CI.
ENV-03 local acceptance: 19 map source/browser tests (23.742s), including
delayed startup and all 85 interactive map elements, passed. Full local CI
passed: 3,081 runtime tests (80 environment skips), 36 release tests (four
environment skips), coverage/parity/core browser and performance gates.
Only test readiness and this register changed; runtime assets are identical.
Repair `0040f750e3969e0c9b4f1494112270956d7caf0c` matches the remote ref.
Exact-SHA [quality run 38303683314](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38303683314)
passed all five jobs. ENV-03 and UI-24 through UI-26 are accepted at this SHA.

Reproduced cashbox UI failures are queued: duplicate income/transfer after a
lost reply, lost draft on failed refresh, editable pending fields/preview,
parent close during a write, mobile repeat and missing existing version guard.
Their test source and a candidate patch are held
in TEMP until that portion begins, so the file portion's CI stays scoped.

Windows settings write failure was reproduced with a real Qt Save click on a
temporary profile: an uncaught PermissionError instead of an error message.
Queue a UI-only storage-error handler preserving the draft and saved settings;
the pending regression and evidence remain in TEMP until that portion.

Further isolated failure audit confirmed two advance-dialog faults (duplicate
native cash receipt after a lost reply; amount/cashbox/comment remain editable)
and two inventory faults (native 409 clears the price/name draft; re-entering
an uncertain new item creates a second native ID). These regressions remain
in TEMP for the corresponding financial and inventory portions. Cashbox cold
module loading also leaves the amount editable before the action starts;
the prepared UI patch covers loading as well as HTTP execution. No queued
candidate was applied to the checkout during the ENV-03 CI run. The cashbox
candidate below was applied only after ENV-03's exact-SHA hosted acceptance.

## Cashbox UI candidate from 0040f75

UI-27: lost committed income reply allows a second receipt. UI-28: confirmed
income followed by failed refresh clears the draft. UI-29: pending income
allows input edits and a rerender re-enables buttons. UI-30: lost transfer reply
allows another pair. UI-31: pending transfer fields/preview re-enable submit.
UI-32: closing the parent during a write invalidates its UI. UI-33: the mobile
income dialog repeats an unknown write. UI-34: the desktop ignores the existing
native cashbox version guard. UI-35: cold module loading leaves money fields
editable. All nine defects failed isolated browser checks before changes.

One owned UI operation now covers loading, native write, readback and rendering.
Conflicting fields/navigation remain locked; errors preserve drafts and restore
controls, while logout invalidates late results. A known receipt with failed
readback retries only reading. An unknown receipt reads current cashboxes; the
same intent can retry only with its original native version guards after an
unchanged readback. A late original write then fails that same CAS, proven by
a real HTTP 409 and one stored receipt. Changed revisions retain an uncertainty
barrier; no receipt is guessed from amount/note. Known cancellation IDs reconcile
by reading their native cancelled state. Native business services, amounts,
directions, cancellation reasons and public/storage formats are unchanged.

Seventeen real browser cases pass (20.951s), including loading/write deadlines,
partial success, double clicks, native conflict, logout, cancellation and mobile
recovery. Twenty-eight native cancellation/access tests and related UI checks
pass (43 tests / 40.623s); 208 candidate browser/source/context tests also passed.
Generated JS and unchanged code-health gates pass. Full browser passed 49/49
(first render 535.7 ms), with no console/page/request errors. Startup JS is
900,721 bytes under the unchanged 901,000-byte cap; no speedup is claimed.
Final local CI passed: 3,098 runtime tests (80 environment skips, 831.252s),
36 release tests (four skips, 3.288s), coverage/parity and browser/performance
gates. Final diff passed; publication and exact-SHA hosted acceptance are pending. Payroll/employee,
inventory and Windows findings above remain queued; final whole-app acceptance
has not been performed.

Cashbox candidate's first full CI did not pass: after attachment timeout tests,
an unfinished mocked route produced a cancelled callback; the following card
class lost its driver connection and the run stalled for 15 minutes with test
errors. The confirmed failed test subprocess was stopped after verifying its
workspace executable and CI-shell parent, retaining the diagnostic log.
Timeout tests now explicitly abort their held mock routes after assertions
and before context closure, including failure cleanup. No assertions are
removed and no route errors are suppressed. Three browser classes pass under
coverage in one process (36 tests / 62.877s), with no cancelled callbacks or
driver-close errors. Full CI must pass again before publication.

The retry then lost a driver connection in the badge class; its failed stalled
subprocess was also stopped after ownership checks. The same two badge cases
pass alone (12.004s), and all 598 preceding cases in discovery order pass under
coverage (167.422s, 25 environment skips). No deterministic UI fault was
established by those failures. Full diagnostic CI subsequently passed: 3,098
runtime tests (80 environment skips, 830.126s), 36 release tests (four skips),
all coverage/parity/browser/performance gates.

Diagnostic output identified a separate Playwright 1.60 lifecycle leak in the
map helper: Response.finished() leaves a target-close task after returning.
Read the response body instead to keep the same startup readiness boundary;
all 19 map checks pass (27.154s) with no target-close/task warnings. Assertions
and performance thresholds stay unchanged. One final full CI is required for
this corrected harness before the cashbox portion is published.

Prepared Windows UI storage-error candidate passes a real Qt Save/retry test
on a temporary profile (0.073s). Payroll candidate assembles below the same
startup limit (900,958 bytes) and preserves native calculation/operation
payloads. Both remain outside the checkout until their portion starts.

Final normal-environment CI completed without driver-close/task warnings.
Native services, API and storage are unchanged in this cashbox portion. The
source worktree remains isolated; all 41 prepared manager files in the main
checkout are preserved. Ten payroll recovery cases are now prepared in TEMP
for the next portion; five have passed the isolated candidate so far.

Cashbox publication `c977bd9266e7d9c04e98ebfe2b00b2fcddb87a49` matches
the remote ref. Exact-SHA [quality run 38313998656](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38313998656)
passed all five jobs. UI-27 through UI-35 are accepted at this SHA.

## Payroll dialogs from c977bd9

UI-36 lost advance reply duplicates the native cash receipt; UI-37 lost payout
reply repeats the payout; UI-38 lost shift-accrual reply appends a second native
accrual; UI-39 pending amount/cashbox/comment remain editable; UI-40 confirmed
advance followed by failed ledger refresh closes the draft. These five faults
failed before changes on disposable data. The candidate also reproduced a
native cashbox-conflict retry stuck on its original revision (UI-41); refresh
references on confirmed 409 while preserving the draft and native guard.

Private owned receipt checkpoints keep confirmed writes across read failures.
Unknown salary writes read back and can conditionally retry only with the
original native cashbox guard; unknown shift accruals remain held because an
employee version does not guard appended accruals. Amounts, payload kinds,
notes, calculations, balance reset idempotency and services are unchanged.
Eleven isolated browser cases (16.110s) and twelve asynchronous ownership
checks (0.532s) passed before application. Actual assembled checks, full local
CI, scoped publication and hosted acceptance remain pending. Employee profile
edit/delete and deeper period/report/mobile review follow in this same family.

Payroll local acceptance: 12 real browser recovery cases, three existing
reset UI contract checks (15 tests / 17.115s), 165 source/module/context checks,
and existing native finance/access/reset/formula contracts pass. Full browser
passed 49/49, first render 518.3 ms, no console/page/request errors. Startup
JavaScript is 900,958 bytes under the unchanged 901,000-byte cap. No speedup
is claimed. Generated JS and all unchanged code-health gates pass.

First full CI failed/stalled in a manager concurrency browser test, with three
reported errors before a traceback summary could finish. Its verified owned
test subprocess was stopped, keeping the log. The same test passes alone
(8.781s); the root cause remains unestablished. Full diagnostic CI then passed
with identical assertions and additional TEMP-only error/browser logging:
3,110 runtime tests (80 environment skips, 830.410s), 36 release tests (four
skips, 3.334s), coverage 81.86%, parity, core browser and performance gates.
No diagnostic helper is committed. Hosted exact-SHA acceptance is pending.

Two next employee-profile faults are reproduced outside the checkout: editable
pending profile fields and repeated confirmed save after failed report refresh
(two failures / 9.008s). A separate profile candidate and four recovery cases
remain in TEMP. Candidate JS syntax passes and startup is 900,970 bytes;
browser and full acceptance remain pending.

Payroll publication `9d43f2b7733eec67907c9ed5f036eea37213b260` matches
the remote ref. Exact-SHA [quality run 38317957794](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38317957794)
passed all five jobs. UI-36 through UI-41 are accepted at this SHA.

## Employee profile candidate from 9d43f2b

UI-42 profile fields remain editable while save is pending; UI-43 confirmed
save followed by report failure sends another save on retry; UI-44 lost new
profile reply creates two distinct native employee IDs. The first two failed
before changes (9.008s) and the third also failed (8.535s) on disposable data.
Reuse known receipts through read failure, refresh authoritative references
on receipt reuse, freeze fields/navigation until completion and retain
uncertain writes as private viewer/record checkpoints. No profile or payroll
validation/calculation rules change. Unknown create receipt lookup remains
a compatible API proposal; names are not used to infer a receipt.

Seven isolated browser cases pass (12.226s) and twelve existing ownership
checks pass. They cover known validation, confirmed partial save, uncertain
create, frozen fields/parent, deadline, logout and native confirmed deletion.
Deletion rendering waits for owned control release, so a field snapshot cannot
restore the removed profile. Generated candidate sources pass syntax; startup
is 900,970 bytes under the unchanged 901,000-byte cap. Actual checkout, full
local CI and publication remain pending. Period/report/reset/deletion fault
review remains in this family before moving to inventory.

Profile actual-checkout acceptance: all seven browser cases and twelve
payroll recovery cases passed inside the 187-test candidate run. Its sole
source assertion failure expected the former private close-handler variable
name; normalization and cascade assertions now require the renamed key,
with browser closure behavior unchanged. All 168 source/module/context/reset
checks pass (1.985s). Full browser passed 49/49, first render 518.4 ms, no
console/page/request errors. Generated JS (13 scripts), formatting/lint and
unchanged code-health gates pass. Full local CI is pending.

Profile first full CI completed 3,117 tests (842.810s, 80 skips) with one
fixture failure: a reference-race test extracts saveEmployee alone and lacked
its private checkpoint map/form snapshot. Add those fixture dependencies,
keep both stale-reference assertions, and run 38 related tests (1.907s): all
pass. Runtime assets did not change after the successful full browser. Full
CI must pass again before publication.

Remaining employee workspace audit outside the checkout confirmed three
faults (four cases / 12.788s): reset POST has no deadline, a lost committed
reset reply leaves the old displayed ledger until another operation, and a
late warm employee load covers a newer clients window. Logout error ownership
already passes. Their test source remains in TEMP; no additional runtime
changes are included in this profile portion.

Profile final local CI passed: 3,117 runtime tests (80 environment skips,
829.481s), 36 release tests (four skips, 3.166s), coverage/parity/core browser
and unchanged performance/code-health gates. The immediate-error reporter
was TEMP-only; no browser debug mode or test assertions changed. Final diff
is scoped to profile UI, regressions, module reset, workflow and this register.
Publication and exact-SHA hosted acceptance are pending. The separate workspace
recovery candidate compiles at the same startup size; its browser verification
is still pending and it has not been applied to this checkout.

Profile publication `17ca23459311821fc7abb8bd451d3eaa7dde8393` matches
the remote ref. Exact-SHA [quality run 38322175368](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38322175368)
passed all five jobs. UI-42 through UI-44 are accepted at this SHA.

## Remaining employee workspace from 17ca234

UI-45 reset POST without a deadline leaves controls locked indefinitely;
UI-46 lost committed reset reply leaves the old displayed ledger; UI-47 late
warm employee load covers a newer clients window. Three failures were
reproduced before changes (four cases / 12.788s). Bound reset waiting, retain
the existing native snapshot and idempotency key, read the current ledger and
reports after an unknown result, and bind window opening/error rendering to
its original parent. No reset amount, calculation, guard or public/storage
contract changes. Logout error ownership already passes.

Four isolated browser cases pass (10.057s). Existing payload/double-click,
retained-key/conflict, parallel-month and viewer-ownership checks also pass:
the unknown-response fixture now returns its authoritative unchanged ledger
before retry; original key assertions remain. Startup is still 900,970 bytes.
Actual checkout checks, full local CI and publication remain pending.

Workspace recovery actual checks pass: 64 browser/context/reference/reset
checks (40.628s), generated JS (13 scripts), formatting/lint and unchanged
code-health limits. Full browser passed 49/49, first render 521.3 ms, no
console/page/request errors. Startup remains 900,970 bytes; no speedup is
claimed. Full local CI is pending. Known-ID deletion timeout/lost-reply
checks are prepared separately in TEMP, but not yet executed or accepted.

Workspace recovery final local CI passed: 3,121 runtime tests (80 environment
skips, 826.368s), 36 release tests (four skips, 3.147s), coverage/parity/core
browser and unchanged code-health/performance gates. Only one UI source file,
regressions, workflow and this register changed; no service/API/storage change.
Final diff passes. Publication and exact-SHA hosted acceptance are pending.

Workspace publication `6e1e99fa93655e7f807b770b0424d79ff9a8c0fa` matches
the remote ref. First [quality run 38324885634](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38324885634)
passed unit/static/Docker and all added browser regressions, but mandatory core
smoke timed out waiting for a response. Its artifact has no console/page/request
errors; only the preceding timer screenshot was saved. A TEMP-only diagnostic
core run passed every stage. One failed-job rerun for the same immutable SHA
passed all five jobs. UI-45 through UI-47 are accepted at this SHA; the initial
hosted timeout cause remains unestablished and is a verification limitation.
Thresholds and assertions were not changed. Final two fresh full passes remain
required; no claim of complete whole-app acceptance is made.

## Employee deletion from 6e1e99f

UI-48 delete POST has no deadline; UI-49 lost committed reply leaves the deleted
known ID in the UI (two failures / 10.897s). UI-50 unknown profile save permits
a conflicting delete before that save is settled (reproduced against the first
isolated candidate). Bound waiting, reconcile the known ID from a fresh native
list, use the existing native employee version guard, and hold conflicting
deletion when this viewer has an uncertain profile write. Keep native linked
record/finance guards, confirmation text and API/storage formats unchanged.

Eight isolated browser cases pass (12.175s), including guarded retry and late
original HTTP 404, native revision conflict, linked-accrual rejection, partial
report failure, logout and the unresolved-save barrier. Twelve existing
ownership checks pass. Actual checkout, full local CI and publication remain
pending. Business services and deletion eligibility rules are unchanged.

Deletion actual-checkout checks pass: 91 browser/context/reference/access
checks (71.868s), generated JS (13 scripts), formatting/lint and unchanged
code-health gates. Full browser passed 49/49, first render 515.6 ms, no
console/page/request errors. Startup remains 900,970 bytes under the unchanged
901,000-byte cap. No speedup is claimed. Full local CI is pending; only the
private UI deletion handler changed at runtime. Services/API/storage and
financial/linked-record eligibility rules are untouched.

Deletion full CI failed before the second cashbox test's page loaded:
APIRequestContext.post lost the Playwright driver during fixture login, then
new-context/cleanup calls lost that same connection. The diagnostic reporter
captured the first exception; no Node driver child remained at the ownership
check. The verified failed core was stopped, keeping the trace. No application
UI or business operation was running in that failing setup. Root cause of the
external driver exit is not established.

ENV-04 candidate: Windows runtime verification now isolates browser groups in
separate Python/coverage processes and combines all fragments with the existing
coverage step. No retry, skip, reduced threshold or weakened assertion is added;
child failure returns its nonzero code and stops later workers. Discovery plan
contains all 3,134 current test IDs: 2,939 core and 24 browser groups, with zero
duplicate IDs. Four orchestration regressions plus existing completion/interrupt
checks pass (11 tests / 2.445s, two POSIX environment skips). Formatting/lint and
unchanged code-health gates pass. Ubuntu CI retains its existing discovery path.
Full isolated Windows CI is pending; this is a harness mitigation, not a claim
that the Playwright process-exit cause has been fixed.

ENV-04 first isolated core run finished 2,938 reported tests (562.556s,
80 environment skips) with one structural gate failure: the parity test found
two steps named Runtime branch coverage. Name the Windows path separately;
the unchanged hosted discovery step remains checked exactly. Five related
parity/orchestration tests pass. Parent and child discovery core IDs match
(2,939 selected each, no missing or extra IDs); the one report-count difference
is the existing POSIX-only MobileClientSearchDraftTests setUpClass skip.
Full isolated CI must pass before either ENV-04 or deletion is published.

Deletion/ENV-04 final local CI passed. All 3,134 selected IDs remain in the
plan: the core reports 2,938 tests (80 environment skips, 559.176s), and all
24 isolated groups report 195 tests; total reported runtime is 3,133 tests
in 913.232s, with the existing class-level skip accounting for the difference.
Release coverage passed 36 tests (four environment skips, 3.243s). Combined
coverage, parity, generated JS, core smoke, code-health and unchanged
performance limits pass. The prior separate full smoke remains 49/49
(515.6 ms); no runtime source changed since that smoke. Fresh remote ref equals
the candidate base `6e1e99f`; scoped diff and primary checkout preservation
pass. Publication/exact-SHA hosted acceptance remain pending. Next: inventory
draft loss, uncertain create replies, pending fields and partial-refresh recovery.

Deletion/ENV-04 publication `af24f9b8a978ef715a22f4b1a7abfab15471517b`
matches the remote ref. Exact-SHA [quality run 38331747010](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38331747010)
passed all five jobs without a rerun. UI-48 through UI-50 and ENV-04 are
accepted. All remaining families and the final two fresh full passes remain open.

## Inventory and dashboard failure queue from af24f9b

Inventory baseline failures are reproduced on real synthetic runtimes:
native revision conflict replaces the typed name/price draft; a committed
create with a lost reply sends a second create when retried (two IDs);
pending save leaves fields/new-selection controls enabled; an unbounded
write never releases controls; a failed list refresh after a known create is
overwritten by a success message. The first two were confirmed separately;
the other three fail together (11.516s). Five candidate regressions remain
in TEMP; no inventory fix or acceptance is claimed yet.

Dashboard editor baseline also fails two real-browser cases (8.527s): the
contenteditable text stays editable during save, and retry after an image
upload followed by rejected settings save uploads the image twice. These
findings are queued after inventory; implementation/acceptance are pending.

## Inventory editor and release preparation candidate

UI-51 through UI-55 fix the five inventory failures above: retain raw draft,
freeze owned desktop/mobile controls, bound writes, preserve confirmed IDs,
retry only failed reads after a known receipt and stop uncertain creates.
UI-56 retains an unsaved draft during search; UI-57 retains its stable selected
ID through an empty filter or a rename that leaves the filter. Two filter cases
failed before that fix (9.039s). Replenishment reuses the original native
version only after an unchanged authoritative read; a late original then gets
native 409. An unknown committed replenishment never doubles stock. Candidate
review also repaired stale read-only quantity display and an overly broad
uncertain-create barrier: independent known-ID edits remain usable.

Fourteen actual desktop/mobile browser regressions now cover these cases,
logout, pending close/navigation and native conflicts. Expanded checks passed
203 tests (21.198s); the final changed uncertainty scope passed 66 relevant
tests (22.857s). Fixture dependencies were updated without dropping assertions.
Generated JS/format/lint and unchanged code-health pass. Final UI full smoke
passed 49/49 (518.2 ms), with no console/page/request errors. Startup is
900,974 bytes under the unchanged 901,000-byte limit; no speedup is claimed.
Second fresh full smoke, full local CI, publication and hosted acceptance are pending.

REL-01 release preparation: extend the existing CRM-only deploy's exact path
allowlist for the reviewed UI sweep; keep rejection of unknown paths and
service/storage/API/MCP/server configuration. Its real predicate has four
portable regressions. Native Unix locking/rollback tests remain required in
GitHub; direct Windows import cannot run those Unix fixtures. Portable release
checks pass. The reviewed candidate diff from source baseline 579a2573 has
62 paths, none outside this exact allowlist and none in business/API/storage.
This proves source eligibility only: installed revision, clean server worktree,
Manager/Telegram target, mounts, health and backup/rollback remain unverified.

Release candidate repeat full browser passed 49/49 (539.2 ms), following the
fresh 518.2 ms pass of the same final UI. Both have no console/page/request
errors. These are two clean release-candidate runs, not proof that the still
pending whole-app failure registry is complete. Release portable checks passed
60 tests (13 existing POSIX environment skips, 0.140s), including all four
new exact-path rejection regressions. Code-health classifies 630 files with
unchanged limits; the primary checkout still has 41 staged files. Full local
CI, fast-forward publication and exact-SHA GitHub acceptance are pending.

First inventory/release full CI finished its core (2,942 reported tests,
608.320s, 80 environment skips) with three fixture failures: isolated modal
tests lacked the now-referenced unrelated close callbacks, and the module
export expectation omitted inventory resetViewer. Add dependencies that throw
if an unrelated close is invoked; preserve all modal/finance assertions and
require the inventory logout reset explicitly. Addressed fixture/module/release
checks pass. Runtime UI assets did not change after the two clean full passes.
The exact release path list includes the additional reviewed operator fixture;
current diff has 63 reviewed paths and none outside it. Full CI must pass again.
The owner reaffirmed preparation only: no server connection or update now.

Inventory/release final local CI passed: all 3,152 selected IDs retained;
3,151 reported runtime tests (946.604s, 80 environment skips), with the existing
POSIX-only class skip accounting for the difference. Release coverage passed
36 tests (3.312s, four environment skips). All 25 isolated browser groups,
combined coverage, parity, generated JS, core browser and unchanged performance
limits pass. Final diff is scoped to inventory UI, its regressions and the
exact-path CRM-only release preparation. Business services, storage, public
API/MCP, financial rules and installed profiles are unchanged. Publication and
exact-SHA hosted acceptance are pending.

## Server update handoff (preparation only)

- Finish publication and require all five quality.yml jobs for the exact SHA;
  use that immutable GitHub revision as the source candidate.
- On the owner's later update command, obtain the installed CRM revision,
  current image and clean isolated source; compare the actual installed-to-target
  diff with the exact reviewed path list. The 579a2573 source comparison is
  not evidence of the installed server revision.
- Run the runbook's read-only CRM-only preflight with the exact guarded Manager
  controller source. Check current health, Compose/mounts, available space,
  Manager/catalog compatibility and the active Telegram target. Record the
  private preflight result without credentials or customer data.
- The update procedure includes a temporary CRM/Telegram duty pause. Use the
  existing lock, verified snapshot and rollback image through deploy_crm_only.py;
  require the preflight-pinned Telegram target and approved SHA. Do not replace
  this with an ad hoc restart or restore live data after new writes are allowed.
- After update, prove image revision, health, HTTPS/Gateway/authentication,
  unchanged neighboring services and affected UI behavior under the runbook.
  These live checks, backup and update have not been executed during preparation.
- Historical preparation scope: pause this audit after candidate acceptance. Dashboard draft/image faults,
  deeper order/settings/mobile/Windows failure passes and the remaining whole-app
  register stay open; do not describe the full original audit as complete.

## Accepted inventory release and resumed maintenance

Inventory/REL-01 was published as `863df550ee3277747c2bc63bc23d1d67b824aa46`.
All five jobs of quality.yml run `38338037668` passed for that SHA. The owner's
later explicit deployment command was fulfilled on 2026-10-13: native CRM-only
release took 92.33s; deployed revision, health, live asset identity, browser
startup and absence of new errors were verified. The native backup/rollback and
unchanged neighboring services were confirmed. Private receipts remain in the
ignored local release output and on the server; no live data is included here.
These observations close the earlier preparation handoff, not the whole UI audit.

The current clean maintenance branch starts from that exact published revision.
The primary checkout's 41 staged constructor files remain untouched. Reused
autostopcrm-maintain/workflow instructions and repository Playwright with
disposable synthetic state; the Browser plugin skill remains unavailable.

### Dashboard editor: UI-58 through UI-64

Before the fix, two actual browser cases fail on `863df550`: pending Save still
allows text editing, and a rejected message stage uploads a confirmed image
again on retry. UI-58 holds text, formatting, image and parent/child close
controls until completion; late image rendering inherits the hold. UI-59 retains
each confirmed image ID, including partial multi-image upload and removing a
pending photo. UI-60 reuses the existing bounded 90s POST helper. UI-61 reads
back uncertain message writes before retry, retaining the original native
expected_revision when unchanged. UI-62 holds uncertain image uploads instead
of sending a duplicate; the existing API has no receipt lookup for an unknown
upload ID, so the UI asks the operator to reconcile the shared-file list.
UI-63 releases holds/private checkpoints at logout and rejects old callbacks.
UI-64 permits an own successful response to close the editor and preserves a
newer draft after a previous uncertain save is later confirmed.

Existing business sanitization, image validation/limits, message schema,
revision conflict, authorization and calculations are unchanged. No server,
API/service, storage, release path allowlist or installed profile changes.
Confirmed-image retry uses one upload per photo instead of two; partial upload
retries only the failed photo. Confirmed message readback emits no second write.
Add 13 real-browser fault/ownership cases and mandatory hosted execution.
Related checks pass (201 tests in 29.809s plus the late-preview browser case);
all 13 generated scripts parse and existing code-health/size limits pass.
Full browser, local CI and exact-SHA publication acceptance are pending.

Full browser passed 49/49, first render 508.6ms, no console/page/request errors.
Startup JS is 900,948 bytes under the unchanged 901,000 limit. The first full CI
core ran 2,942 tests (558.003s, 80 environment skips) and failed one isolated
parent-modal fixture missing the newly referenced dashboard close callback.
Add a callback that throws if an unrelated workspace closes, preserving the
existing busy repair-order/payment assertions. Recheck that fixture and rerun
the complete CI; do not publish the incomplete initial result.

The second core passed all 2,942 tests (560.906s, 80 skips). A later badge
browser group exposed a pre-existing 150ms wait for a fetched seen response;
under coverage the callback had not finished, and missing route cleanup leaked
the failure into the next test. ENV-05 waits for the actual bounded response,
releases pending routes on failure and waits for the owned seen operation to
finish after replying. It preserves the assertion that an old seen reply cannot
replace the newer saved title. All five badge and 13 dashboard browser cases
pass together (28.090s). Rerun complete CI; no skips, retries or limits weakened.

Final local CI passed: all 3,165 selected IDs retained; 3,164 reported runtime
tests (the existing POSIX-only class skip accounts for one), 26 isolated browser
groups and 80 core environment skips. Core: 2,942 tests in 567.802s; the 13 new
dashboard cases pass under coverage in 19.045s. Release coverage: 36 tests,
3.152s, four environment skips. Combined coverage, documentation, parity,
generated JS, core browser and all unchanged performance thresholds pass;
CI browser first render is 529.0ms. Diff review confirms only dashboard UI,
its regressions, badge test synchronization and this register. Fresh remote
remains `863df550`; exact-SHA hosted acceptance follows publication.

Next independent portion: Windows settings Save/Apply/readiness actions.
Actual Qt buttons reproduce uncaught disk/size failures on synthetic files.
A temporary candidate catches storage failures at the existing save boundary,
retains the form/file, emits no success signal and prevents connection checks
from starting before a successful save. Three additional Qt cases pass on
that isolated candidate (0.181s); the source change is not in this portion.

Dashboard/ENV-05 was published as `d293ec48d605b00603fdea66c38c3b9b76ac5fd0`.
All five jobs of quality.yml run `38376338958` passed for that exact SHA, with
no reruns. The original checkout's 41 prepared files remain untouched.

## Windows settings: UI-65 through UI-68

Continue from accepted `d293ec4`. Actual Save/Apply/diagnostic buttons previously
emit uncaught storage exceptions through Qt, without an actionable form status.
The synthetic native size-guard case also reproduces an uncaught ValueError.
UI-65 handles write errors at the existing form-save boundary; UI-66 retains
the draft, visible dialog, original file and enabled actions; UI-67 emits a
success signal and applies/closes only after the existing save succeeds;
UI-68 prevents full/single connection checks from starting when saving fails.
The status omits private exception text. Field validation, normalization,
atomic write/size limit, settings schema, runtime gates and business logic are
unchanged. The native size limit is shortened only inside a synthetic test write,
leaving reads of the valid original test file unaffected.

All four new real Qt cases pass (0.220s), including successful retry. All 123
related settings/storage/runtime/config/entrypoint checks pass (6.116s); Ruff
and existing code-health limits pass. The first targeted command also requested
the nonexistent test_launcher module; corrected to discovered modules above,
with no source/test failure hidden. Browser assets are unchanged. The source
change is eight lines in SettingsWindow; full local CI and hosted acceptance
are pending before publishing this separate portion. No production access.

Windows final local CI passed on the first full run: all 3,169 selected IDs
retained, 3,168 reported runtime tests (one existing POSIX class skip), 26
isolated browser groups, 80 core environment skips. Core: 2,946 tests in
565.257s; release: 36 tests in 3.196s, four environment skips. Coverage, docs,
parity, generated JS, core browser and unchanged performance thresholds pass;
first render 527.5ms. Fresh remote remains accepted `d293ec4`. Diff is limited
to SettingsWindow's eight-line error handler, four Qt regressions and this
register. Exact-SHA hosted acceptance follows publication.

Windows was published as `e43234e8ff333a89f8221cbe4ef019ea61af6b6a`.
All five jobs of quality.yml run `38380574289` passed for that exact SHA,
with no reruns. The initial push ended with a Schannel TLS disconnect; an
independent GitHub ref read confirmed `d293ec4`, and an ordinary retry published
the same checked commit fast-forward. Local and remote SHA agree.

## Sticky interface: UI-69 through UI-76

Two real-browser cases fail on `e43234e` (8.054s): pending creation leaves the
text editable; after a create commits but its reply is lost, a second Save
creates another sticky (two POSTs/two records). The synthetic reproductions
are kept outside Git. Inspect the editor's pending controls, close ownership,
known receipts, uncertain create/update/delete outcomes and bounded requests.
Preserve native text/deadline/position rules and the public contracts. Unknown
creation has no native receipt key or client-selected ID; do not infer identity
from matching text or bypass this limitation with an invented API field.
Reproduce before changing; acceptance and publication for this portion remain
pending. The iterative goal remains active until the owner stops it.

UI-69 captures the click's payload and holds every editor control before the
optional write module loads; close/replacement is refused while saving.
UI-70 retains native confirmed receipts/IDs through a failed screen update;
unchanged retries read/render, edited drafts update the same ID. UI-71 stops
an uncertain creation across editor reopening without matching by text.
UI-72 reconciles uncertain updates, moves and deletes by stable ID; update
confirmation checks the exact native duration, avoiding display-hour rounding.
UI-73 bounds all sticky POSTs using the existing 90s helper and handles the
15s module-load deadline without a late write. UI-74 releases controls and
private receipts at viewer reset, rejecting old callbacks. UI-75 restores a
missing post-login board through the existing refresh before closing. UI-76
requires a real receipt and a valid sticky array, preserving a known ID if
rendering fails and reading fresh data before replaying a known result.

The eager wrappers retain mutation ownership before cold loading, including
delete and drag. Private persistence/reconciliation is in the auxiliary module;
its explicit bindings and reset are checked. Reuse the already normalized
stickyPayload deadline instead of reading/normalizing it twice. API request
fields, native text/duration/position validation, expiry, storage and business
services are unchanged. The startup bundle is 900,875 bytes (73 fewer than
e43234e), under the unchanged 901,000 cap. This is request/draft correctness;
the small size/render measurements are not a claimed speed improvement.

All 14 new browser cases pass, including missing receipt, cold/stalled module,
partial UI failure, fresh peer data, changed draft, exact 3,700s versus 7,200s
readback, lost update/delete/move replies, 90s deadline and logout. The related
runtime/dashboard/asset run passed everything except a static assertion that
required the removed unsafe empty-array fallback; replace it with valid-array
and absence-of-fallback requirements, keeping all dock/modal assertions.
Final 183 checks pass (17.490s). Full browser: 49/49, first render 484.9ms,
no console/page/request errors; all 13 generated scripts parse. Full local CI
and exact-SHA hosted acceptance are pending before publication.

Compatibility limit: unknown create IDs have no native receipt lookup or
client-selected ID, so creation is held in the current viewer session until
manual reconciliation. It is not inferred from text; a complete recovery
protocol across reload/login needs a separately agreed API/storage proposal.

Final local CI passed: all 3,183 selected IDs retained; 3,182 reported runtime
tests (one existing POSIX-only class skip), 27 isolated browser groups and 80
core environment skips. Core: 2,946 tests in 563.404s; sticky browser: 14 tests
under coverage in 18.751s. Release: 36 tests in 3.057s, four environment skips.
Combined coverage, docs, parity, generated JS, core browser and all unchanged
performance thresholds pass; CI first render 525.4ms. An initial CI start was
stopped after the diff check caught an extra EOF blank line; fix it and run the
complete final candidate above, rather than treating that stopped run as passed.
Final diff is scoped to sticky UI, its regressions/module bindings and this
register; no service/API/MCP/storage/finance or release-controller changes.
Fresh remote remains accepted `e43234e`; hosted acceptance follows publication.

Sticky UI was published as `5cdf94ef217043332e4a8db2a446053b31aab380`.
All five quality.yml jobs of run `38388352469` passed for that exact SHA,
without reruns. Local/remote SHA match. The original checkout retains its
41 prepared constructor files; no deployment occurred in this goal.

## Inventory material interface: UI-77 through UI-84

Two actual browser cases fail on `5cdf94e` (8.789s): a pending native issue
allows quantity editing, and after an issue commits but its reply is lost,
the native item quantity is 9 while the UI still shows 10. One unit was issued
from synthetic stock of 10. The first probe used a private module helper in
page.evaluate; correct it to the visible state lookup and reproduce both
assertion failures above. A third actual browser failure proves a successful
issue removes an unsaved work row while native saved works remain empty.
Additional failing probes reproduce loss of unsaved tags and return-row
quantity, plus inaccessible read recovery after a confirmed return followed
by a failed inventory refresh (three failures in 10.255s).

UI-77 locks the quantity, selection, order/card controls and closes while a
material operation is pending, stopping duplicate and overlapping order writes.
UI-78 retains raw work/material nodes, scalar fields and draft tags separately
from canonical card updates; returned movement IDs locate affected return rows.
UI-79 reads card, movements and item after uncertain issue/return responses;
it applies confirmed state without replaying the irreversible operation.
UI-80 keeps known receipts through failed refreshes, allowing read recovery
even with zero stock or a cleared return link. UI-81 retries an unconfirmed
write only after unchanged native revisions, retaining the original CAS body;
another intent is held until its predecessor is reconciled. UI-82 bounds POSTs
at 90s and stock refresh at 20s, invalidating late reads. UI-83 clears holds
and private receipts at viewer reset; old callbacks cannot replace a new card.
UI-84 validates receipt card/item/link identity and reflects the existing
active-movement guard. Rules for quantities, prices, ledgers, row limits and
orders, finance services, public request fields and storage remain unchanged.

All 18 real browser regressions pass: fractional issue/return and exact ledger
links; lost replies; zero-stock and return read recovery; raw drafts; item/card
conflicts; unchanged-revision retry; unresolved changed intent; deadlines;
logout; mismatched receipt. The native out-of-range guard for an additional
unsaved material row remains enforced, with draft and stock preserved; this
does not silently save an unrelated order draft. Early new-suite failures
were an undefined local variable and fixture calls to private helpers/hidden
fields; fix the variable and exercise actual visible controls, keeping checks.
Final focused run: 40 tests in 23.451s. The preceding broader inventory/order,
payment, module/asset regression run: 236 tests in 44.853s. Full browser: 49/49,
first render 513.6ms, no page/console/request errors. All 13 generated scripts
parse; unchanged code-health ratchets remain size 33/33, complexity 2/2.
Startup JavaScript: 900,997 bytes, below the unchanged 901,000 cap; recovery
helpers are lazy. These fixes claim request/draft correctness, not acceleration.

Compatibility limit: private receipts live in the current viewer session.
If native reads cannot establish the result, repeat writes are held; recovery
across reload/login or independently identifiable lost transactions requires
a separately agreed API/storage protocol. No new protocol is introduced.
Final full local CI passes: all 3,201 selected IDs retained, 3,200 reported
runtime tests (one existing POSIX-only class skip), 28 isolated browser groups.
Core: 2,946 tests in 563.723s with the same 80 environment skips; new material
browser: 18 tests in 26.124s under coverage. Release: 36 tests in 3.046s, four
environment skips. Combined coverage, docs, parity, generated JS, core browser
and unchanged performance gates pass; CI first render 495.4ms. Fresh remote is
accepted `5cdf94e`; publication and exact-SHA GitHub acceptance follow.
The original checkout retains 41 prepared files; no server access or deploy.
The goal remains active; next audit candidate is operator admin mutation UI,
with a synthetic pending-delete probe prepared outside Git, not yet executed.

Published material UI as `8f7ca878e7403e95790fa95534f8aced91ee6c78`.
All five quality.yml jobs of run `38398157242` passed for that exact SHA,
without reruns. Unit: 8m29s; browser/performance: 4m54s. Native watch exit is
zero and local/remote SHA match. This acceptance record accompanies the next
portion; only this progress file is modified after the accepted commit.
No deployment occurred; the ongoing goal remains active.

## Operator admin deletion and bounded writes: UI-85 through UI-92

The pending-delete browser probe fails on accepted `8f7ca87` (8.078s): its
button stays enabled. A lost native delete reply leaves a deleted user's row
visible; both checks fail in the combined before run (11.378s). The initial
deadline probe incorrectly relied on an undefined old owner field; do not
count it as evidence. Its replacement requires an actual failed/aborted HTTP
request, ready controls and no repeated POST. A separate native peer deletion
also leaves a stale row after 404 (one failure, 8.275s).

UI-85 holds admin controls and close/navigation before cold loading, enforcing
one delete and stopping overlapping user/binding writes. UI-86 preserves another
editor's raw login/password draft and the captured target through list rendering.
UI-87 reads actual absence after a lost/malformed delete receipt or peer 404.
UI-88 retains known receipts through failed reads; repeated actions only read.
UI-89 holds unknown writes without a native revision guard and preserves a
recreated account with the same username. An early candidate missed recreation
before the first read (two deletes instead of one); the failing probe (9.345s)
is fixed by requiring fresh absence even after a confirmed receipt. UI-90 uses
the existing 90s POST helper for deletion, user save and employee binding;
the 15s module deadline restores controls with no late write. UI-91 releases
owned controls on completion/reset and rejects old-viewer callbacks. UI-92
keeps password-only saves separate from explicit permissions and retains native
expected_permissions CAS, current-admin protection and employee assignment.

Request preparation/ownership remain eager. Existing user-save/binding response
handling moves to the auxiliary module, retaining original native payloads,
intent checks and caller error/finally behavior; reject a changed viewer before
starting a cold-loaded write. Private delete checkpoints reset with the viewer.
Local userKey/owner names are internal only. Startup JavaScript is 900,986 bytes,
below the unchanged 901,000 cap (11 fewer than 8f7ca87); no speedup is claimed.
No auth service, business calculation, API/MCP or storage format changes.

All 21 real browser cases pass, including native password login, exact permission
and binding payloads, timeouts, active-admin rejection, lost replies, partial
reads, peer deletion/recreation, cold/stalled module and logout. Early Node
fixture failures lacked the newly extracted helpers; include their actual
shipped code/90s helper, retaining the assertions. Update only variable/indent
assertions for the mechanical move. The stalled-module probe now removes its
route hold/restores the real timer before a separately approved fresh click;
its late-load/no-POST assertions remain. Final targeted run: 222 tests in 29.213s.
Full smoke before the last race fix: 49/49, first render 513.5ms; final fresh
full smoke: 49/49, 500.8ms, no page/console/request errors. All 13 generated
scripts parse; code health passes for 637 files with unchanged ratchets 33/33,
2/2. Final local CI passes with REL-02 included; hosted acceptance follows for
the exact published commit and is recorded in the local release manifest.

Compatibility limit: delete checkpoints cover the current viewer session.
Username-only deletion cannot safely replay an unknown write or distinguish a
recreated account by durable ID; no new CAS/receipt field is invented. Follow-up
within this same admin subsystem: unknown password-save receipts and lost binding
replies, keeping passwords out of read probes and preserving native protections.
The original 41 prepared constructor files and production remain untouched.
The owner's next request gives release preparation priority over new audit portions.

## Release preparation after UI-92: REL-02

The owner requested server-update preparation; no server update is included in
this step. Last verified deployed revision is historical `863df550`; native
preflight must read the actual installed revision again before a later release.
The candidate changes 27 paths since that historical baseline, including the
pending admin portion. Twelve reviewed paths were missing from the CRM-only
release allowlist. Add only those exact dashboard, Windows settings, sticky,
material and admin source/test paths. Keep business/auth/storage/API/MCP/server
configuration and neighbouring unreviewed UI/test files rejected. The real
native preflight still validates its actual installed-to-candidate Git diff.

The first full admin CI passed 2,946 core cases and eight browser groups before
the Playwright driver connection closed in the existing client-profile group.
Its cleanup stalled without a driver; terminate only the identity-verified
unittest leaf and retain the failed run (native exit 1), rather than accepting
partial coverage. Check that group independently, then run the final full CI
with REL-02 included. No automatic retry or test/limit weakening is introduced.
Local package, exact-SHA hosted acceptance and release handoff follow the final
candidate verification. Live host preflight, backup and deployment remain pending.

The isolated client-profile diagnostic passes all six tests in 11.596s; the
driver loss is not reproduced and its root cause remains unconfirmed. All five
real release-policy tests pass (0.007s), including the new exact-path cohort
and rejection of adjacent auth/settings/UI/test files. Changed-file Ruff and
diff checks pass. With the release guard/tests included, the historical
863df550-to-candidate diff has 29 paths, all accepted by the real gate; business
service, auth, storage, API and MCP sources have no changes.

Final `run_checks.ps1 -Profile ci` exits zero: 3,223 selected runtime IDs,
3,222 reported cases (2,947 core plus 275 cases in 29 isolated browser groups),
and 36 release cases in 3.299s. The existing POSIX-only class accounts for the
selected/reported difference; 80 runtime and four release environment skips
remain unchanged. Core time: 559.795s; the 21 admin browser cases take 29.371s.
All browser groups, including clients, pass without retries in this final run.
Generated JS (13 scripts), docs, coverage, 637-file code health and performance
gates pass; core smoke first render is 527.3ms. CI report SHA-256:
`c5dfa45e4493218acc339c19a7e70c8b8b70e01e5e5885c99905a25c524a3074`.
The ignored `output/release-candidate-<published SHA prefix>/manifest.json`
records exact publication/hosted CI and the verified raw-Git-blob source ZIP,
alongside checksums and a short server-agent handoff. Actual host preflight is
still required before a separately authorized server update.

Two consecutive final full browser runs pass 49/49 scenarios on the same UI
source: first render 500.8ms and 525.9ms. Both have zero console/page errors
and failed requests and succeed on their first attempt; no speedup is claimed.
The second native smoke exit is zero. Final progress-doc audit also passes.

## Employees goal, October 2026: EMP-01 (in progress)

The owner authorized a complete Employees review, repair and measured speed pass.
Start from GitHub `71a3d038` in the existing clean attached worktree, branch
`codex/crm-employees-stability-performance`; preserve the original 41 staged
constructor files. The goal is active. No server/client rollout is authorized.
Use the existing temporary runtime and synthetic fixtures only. Browser plugin
is not available; use repository Playwright tests. No formula, rounding, payment,
historical snapshot, permission, native money guard, API/MCP or storage change.

| Area/action | Finding and current work | Evidence / next check |
| --- | --- | --- |
| Desktop list, reopen, month change | Reproduced cached balance `0` after a peer's saved `17.35` shift; duplicate report reads. Always obtain one fresh full response, reuse its embedded report and coalesce concurrent reads. | Real failing-before/passing-after browser cases; current-viewer/session/access and month races. |
| List server preparation | Full cards and unrelated settings copied under the shared lock. Detach only consumed payroll inputs; preserve complete orders and all transaction amounts, use a balance-only transaction projection. | Original response oracle, detached nested/aliased inputs, concurrent write and lock-release tests. Calculators remain unchanged. |
| Loading and recovery | Window waited for all data. Show a closable loading shell, make unavailable fields inert, show read errors and an explicit read-only retry. | Five real browser cases including the native GET deadline, double month events, peer write, close and failed read. |
| Profiles and deletion | Keep known IDs, uncertain-write checkpoints, drafts, expected employee versions and caller-owned controls. | Existing real profile/delete cases pass; final CI remains required. |
| Advances, payouts, shift accruals, reset | Preserve native guards and confirmed receipts; a failed refresh retries reads only. | Existing real money/reset cases pass. Failure injection follows the full response now carrying the report. |
| Periods, formulas, history, reports and reconciliation | Exact response comparison on frozen inputs, month boundaries, policy dates and Friday before/after 20:00. | 164 original/candidate responses identical; larger fixture, UI print races and final integration remain pending. |
| Mobile and Windows | Shared-service and existing UI behavior remain compatible. | Next: fresh mobile entry, navigation races, native Windows launcher/settings, final full smoke. |

Baseline full browser smoke passes on the original source. Baseline local CI
stops at the unrelated client-popup draft test; an isolated one-case diagnostic
passes its assertion but has Windows SQLite cleanup failure. A separate full
seven-case client-creation diagnostic passes. Retain these failures; no test,
retry policy or gate is weakened and the final full CI must succeed.

Preliminary measurements found selector retry backoff adding 100-500ms after
an early loading shell. The final shared harness uses the same frame-based
data-ready milestones on both revisions, frozen time and identical serialized
synthetic fixtures (standard and exactly three times the base collections).
Do not present earlier instrument-dependent numbers as final acceptance.
Run three series of twenty without concurrent test load, then record medians,
p95, request counts and server-lock hold. Full CI, exact-SHA GitHub acceptance,
publication receipt and the two final clean browser passes are still pending.

Follow-up in this same goal: reproduce and guard late salary-window opening
after its parent closes, report/print replies after logout or period-dialog
replacement, and mobile cached re-entry. The unchanged payroll calculators and
native write services remain the behavioral oracle. This read-service change
requires a separately reviewed future release; retain the CRM-only UI deploy
allowlist and do not widen it. The commit containing EMP-01 identifies this
portion; its exact SHA/CI receipt is stored in the ignored local run artifacts.

EMP-01 local acceptance: final CI exits zero, 2,951 core cases in 648.788s and
all 30 isolated browser groups, release coverage, docs, parity, JS and existing
performance gates. Standard fixture: 164 financial service responses are byte
identical to the Git baseline, SHA-256
`cf1f9f7ccf139ceee0ddea55f9043a473d14812332e8aefe7fec1bcbe5ad2e03`.
Same final measurement harness/fixture/time, three series of twenty: server
list p95 215.1/221.8/220.4ms -> 157.8/161.3/160.8ms; month change
396.7/429.1/445.7ms -> 244.4/245.4/195.8ms. All events zero. Standard UI
budgets pass. Reopening is now a fresh balance read instead of the former
cached-balance/report-only path: p95 131.8/133.2/139.6ms ->
250.2/268.7/285.6ms. The goal's no-regression criterion is NOT met; investigate
remaining read preparation in the next portion, without reverting freshness or
caching financial results. Earlier selector-backoff measurements are discarded.
Exact x3 measurements and the raw artifacts remain in the ignored local run.
This published portion fixes confirmed freshness/recovery defects and reduces
server work; it does not mark the complete Employees goal or release ready.

Real follow-up probes confirm mobile cached re-entry (`0` after the saved
`17.35` shift) and an old print response closing a newer seven-day period
dialog. Salary-parent close and print-after-logout probes pass the existing
guards; retain them. All source work remains local until ordinary GitHub
publication; production and the original constructor work remain unchanged.
