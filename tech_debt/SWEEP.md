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
| Desktop cards and save | Existing/new, one click, duplicates, conflict, uncertain reply, client/vehicle stage; close only after full success, retain draft on error | Four defects reproduced; fix pending |
| Columns and card badges | Create/rename/order/delete by stable ID; personal clones, hover/open, own/other update, reload/login; identical badges across copies | Existing column smoke passes; races reproduced; pending |
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

Next: finish ENV-01 checks in a separate portion; implement the card/column/badge
section and its actual-browser regressions before moving to other screen families.
