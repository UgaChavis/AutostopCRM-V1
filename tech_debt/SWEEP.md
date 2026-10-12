# UI stability sweep

Started 2026-10-12 at `579a2573cfd8abc5276cbeb4aba17e011b400743` on
`codex/crm-ui-stability-sweep`. Goal: functional, failure/concurrency and
integration passes over every UI family; two consecutive clean complete
browser runs after the final fix; local CI and exact published SHA accepted by
`quality.yml`. Business rules, authorization, API/MCP contracts and storage
formats are preserved. Source only: no production, SSH, installed-client
profile or live data. The primary checkout's 41 staged constructor files stay
untouched.

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
| Cashboxes/payroll/employees UI: lists/periods/dialogs, repeated clicks/errors; unchanged calculations | Cashbox candidate has 17 real desktop/mobile failure cases; five payout/advance/shift/dialog faults queued | Cashbox portion accepted at `c977bd9`; payroll/employees pending |
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
