# UI stability sweep

Started 2026-10-12 at `579a2573cfd8abc5276cbeb4aba17e011b400743` on
`codex/crm-ui-stability-sweep`. Goal: three passes over every UI family,
confirmed technical fixes, two consecutive clean full browser runs and exact
published SHA accepted by `quality.yml`. Business rules, authorization,
API/MCP contracts and stored formats are preserved. Source only; no production,
SSH, installed-client profile or live data. The primary checkout's 41 staged
constructor files are untouched.

## Baseline and environment

- Isolated managed checkout updated to the exact remote SHA; pinned dev
  dependencies restored in its ignored virtual environment.
- Baseline CI: 3,023 runtime tests, 2 failures and 4 errors, 83 skips;
  703.699 seconds. New Windows failures concern UTF-8, a pinned vendor asset's
  CRLF checkout, and isolated Docker Compose plugin discovery. Later CI steps
  were not reached. They must pass before publication.
- Baseline full browser: all 49 scenarios passed, first render 549.1 ms;
  no console errors, page errors or failed requests. This smoke alone missed
  the four reproduced card defects below.
- Browser plugin skill absent; use existing Playwright with temporary synthetic
  CRM runtimes. Screenshots and diagnostics stay outside Git.
- Canonical M2 private state/journal is absent locally. No SSH or deployment
  is authorized; source-side progress is recorded here.

## Screen and action register

Each family requires functional, failure/concurrency and integration evidence.
`pending` means unaccepted even where existing smoke currently passes.

| Screen / element | Required actions and expected result | Passes / evidence |
| --- | --- | --- |
| Desktop cards and save | Existing/new, one click, duplicates, conflict, uncertain reply, client/vehicle stage; close only after full success, retain draft on error | 10 new browser regressions pass; full CI/publication pending |
| Columns and card badges | Create/rename/order/delete by stable ID; personal clones, hover/open, own/other update, reload/login; identical badges across copies | 5 new browser regressions pass, two users/roles and personal copies; existing column smoke; full CI/publication pending |
| Clients, vehicles, search, references | Create/edit/select/search, empty and long lists, late replies, navigation, permissions | pending |
| Attachments, shared files, editors, preview, print | Upload/delete/list/open/edit, draft, failures, close during loading and return navigation | pending |
| Inventory and orders UI | Loads, edits, repeat clicks, concurrent updates, native errors; unchanged guards and values | pending |
| Cashboxes, payroll and employee UI | Lists, filters, periods, dialogs, repeat clicks/errors; identical financial results | pending |
| Board/operator settings, roles, manager maps, dashboard | Save/cancel, roles, stale replies, module loading, popup/navigation | pending |
| Mobile | All panels, cards, navigation, small viewport, failures and draft recovery | pending |
| Windows | Source startup, dialogs/settings and relevant native workflows on disposable data | pending |
| Shared mechanisms | Requests, cache, polling, modals, event ownership, logout/login, reconnect | pending |

## Confirmed findings and portions

| ID | Reproduction / consequence | Fix and regression | Publication |
| --- | --- | --- | --- |
| ENV-01 | Baseline Windows CI: six failures from locale/CRLF/plugin discovery | Explicit UTF-8, pinned LF checkout, temp Compose config; 14 focused tests and docs/Ruff passed; full CI pending | pending |
| UI-01 | Create card; fail client-link response; click Save again: two created cards, one linked | Retain confirmed ID and completed stage; retry only unfinished action | pending |
| UI-02 | Delay save; title remains editable; type again; release response: modal closes and later text is lost | Freeze conflicting controls for the owned save; preserve pre-existing disabled state | pending |
| UI-03 | Fail `mark_card_seen`: NEW disappears despite unconfirmed read, suppression remains | Reconcile/restore badges after failure; scope suppression to user and version | pending |
| UI-04 | Hold old seen reply; save new title; release old reply: board shows old title, storage has new title | Reject obsolete reply without replacing current card data | pending |
| UI-05 | Open NEW, wait for hydration, reload: acknowledgement was never sent and NEW returns | Separate pending optimism from confirmed read; browser reproduction failed before fix | pending |
| UI-06 | Hold snapshot sampled before save; release after successful save: board restores the previous title | Advance local mutation generation on confirmed card writes; browser reproduction failed before fix | pending |
| UI-07 | Lose an unchanged update before delivery; readback confirms the data but NEW returns after reload | Preserve no-op change metadata and acknowledge any still-unconfirmed personal read | pending |

ENV-01 is local commit `b03a5d4`; publication waits for the full shared candidate's
CI. Card save and seen reconciliation load as one optional module; controls and
ownership stay eager. Startup JavaScript remains under the unchanged 901,000-byte
limit. The creation API supplies no idempotency key or receipt lookup: after a
lost create reply, the UI reads the board and retains its draft, but deliberately
does not guess the created ID or repeat creation. Automatic recovery for that
case requires a separate compatible API proposal (receipt/key retention,
replay semantics and rollback to the current guarded UI).

Card section local evidence: 16 real-browser regressions pass in 30.554 seconds.
Full browser before the final no-op fix: 49/49, first render
530.3 ms, no errors/failed requests. Existing web contract suite: 294 tests passed.
First shared CI candidate: 3,038 tests, seven extraction-fixture failures and
83 skips. Fixtures now read expanded sources with the actual save dependencies;
their 49 focused tests pass, as do all 294 web contract tests. Full CI must be
repeated before publication. Seven card defects have been reproduced and fixed;
the confirmed client-creation retry defect is queued for the next section.
Business-service source is unchanged. Before/after startup JavaScript measured
on the same checkout assembler: 898,403 -> 899,479 bytes; gzip 169,405 -> 169,894.
The 901,000-byte gate is unchanged. The duplicate reproduction changes from
two create requests/cards to one, followed only by the unfinished client link.
No speedup is claimed from these byte or timing figures.

Final card candidate: full browser 49/49, first render 573.8 ms, no errors or
failed requests. Local CI passed: 3,039 runtime tests (83 environment skips),
36 release tests (4 environment skips), generated JS, parity, coverage ratchets,
core browser and all performance gates. The remaining local client-search
browser skip is queued for portable Windows coverage. No limits were weakened.
Card startup asset: 899,537 bytes, below 901,000; the optional module owns save
and seen reconciliation. ENV-01 and UI-01 through UI-07 are locally verified.

Next: fast-forward publication and exact-SHA hosted CI. Other families remain
unaccepted until their deeper review and failure/integration evidence.
