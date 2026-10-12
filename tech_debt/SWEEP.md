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
| Attachments/shared files/editors/preview/print: upload/delete/open/edit, failures, draft and return navigation | UI-17 through UI-23 accepted; shared batch/clipboard follow-up reproduced; editor/print functional and race smoke | Follow-up candidate; final integration pending |
| Inventory/orders UI: load/edit/repeated clicks, conflict/native errors; unchanged financial values/guards | Existing smoke and contracts; deeper failure pass pending | pending |
| Cashboxes/payroll/employees UI: lists/periods/dialogs, repeated clicks/errors; unchanged calculations | Lost committed cashbox reply followed by retry sends two writes; UI fix queued | pending |
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

Next portion: attachments, shared files, editors, preview and print. Review
passive file-status reads overlapping card saves, bounded request recovery,
partial upload/copy failures, preview ownership and print navigation. Other
UI families and both final clean whole-application runs remain pending.


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
candidate has been applied to the checkout during the current CI run.
