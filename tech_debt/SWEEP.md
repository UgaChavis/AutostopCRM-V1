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
| Clients/vehicles/search: create/link/edit/select/search; drafts, validation, late replies, pending controls, logout, uncertain writes | 20 new popup/profile/vehicle browser regressions, three portable desktop/mobile search tests; client/ownership contracts | Local CI passed; exact-SHA hosted acceptance pending |
| Attachments/shared files/editors/preview/print: upload/delete/open/edit, failures, draft and return navigation | Existing smoke and contracts; deeper failure pass pending | pending |
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
| UI-08 | Create client, fail saved-card link, retry: create sent again | Keep client ID and retry only the link | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-09 | Create client for unsaved card, fail vehicle stage, retry: create sent again | Keep client checkpoint and retry unfinished vehicle stage | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-10 | Link response replaces unsaved card title/description | Update confirmed association while retaining draft and dirty state | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-11 | Popup fields editable during pending client creation | Freeze popup controls, prevent repeat submit, restore on errors | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-12 | Close creation popup, open another, release old reply: new popup affected | Bind result to card, viewer and popup generation | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-13 | Client profile fields editable during save/refresh and overwritten | Lock owned controls until confirmed rendering, restore on errors/logout | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-14 | Lose profile-create reply after commit, retry: create sent again | Read list, retain draft, block blind repeat without a known receipt ID | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-15 | Vehicle refresh fills profile and erases unsaved comment | Refresh vehicles, restore edited profile fields and dirty baseline | `aa1b697`; hosted acceptance pending ENV-02 |
| UI-16 | Lose new-vehicle reply after commit, retry: upsert sent again | Read profile, stop blind create; known-ID update/delete reconcile by reading | `aa1b697`; hosted acceptance pending ENV-02 |

Card portion: 16 browser regressions, 294 web contracts, full browser 49/49
(first render 573.8 ms), local CI 3,039 runtime tests/83 environment skips and
36 release tests/4 environment skips; generated JS, parity, ratchets and all
performance gates passed. Exact-SHA
[quality.yml run 38263040268](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38263040268)
passed unit, static, browser-performance, docker-runtime-assets and quality for
`b7a35a39881feff1dcce276c2b453c0bc78e2d81`. Both commits were fast-forward
published. Startup JavaScript: 898,403 baseline -> 899,537 bytes, below the
unchanged 901,000-byte gate; no speedup is claimed.

Client candidate: five popup defects and two profile defects failed browser
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
No speedup is claimed. Publication and exact-SHA hosted acceptance are pending.

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
Only exact-SHA hosted acceptance, including the repeated real Linux Ctrl+C
scenario, remains pending for this portion.
