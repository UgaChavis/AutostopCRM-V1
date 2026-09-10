# 005. Browser maintainability

The startup bundle retains board/cards, clients, navigation, polling and modal
state. Payroll, stock, printing and cash-journal panels have versioned lazy
bundles. The assembler/loader and Windows package share that asset contract.
On a full-access desktop employees open, one fresh `list_employees` response
supplies the embedded month summary and detail rows. A references-only, malformed
or wrong-month response uses a guarded `get_payroll_report` fallback; mobile and
explicit month changes retain their separate payroll read. Inventory and saved-card
printing start their read-only preparation while the lazy module loads. Unsaved-card
printing still saves first and does not start an early workspace request. Prepared
results wait for exact viewer, session, request, card, editor and hydration
ownership; concurrent opens reuse the pending invocation. No heavy panel is
prefetched at startup. Keep first-open latency and failure/retry coverage with this
boundary.
The cold inventory shell opens immediately with its workspace inert until ready;
Close/Escape remain available. Prepared reads own the exact modal entry, so a
closed or replaced shell cannot be reopened by an old response. Script failure
keeps uninitialized controls inert and permits an explicit close/open retry.
Review asynchronous work at its operator/request/editor boundary, including
success, errors, cleanup and debounce/loading invalidation. Multi-request writes
must check ownership before each follow-up request; an obsolete response is not
a reason to repeat a write using the current operator.

`openCardWorkspace` captures viewer, hydration and editor generations after its
initial cached open. Stale success and errors stop before changing UI or scheduling
side effects; `tests/test_card_workspace_context.py` covers these races and the
normal cached/uncached opening paths.
Direct repair-order opening shares hydration ownership and also checks the
originating modal entry. Closing and reopening that parent cannot revive an old
response; an already submitted creation request is never retried for this reason.

Global mobile-more navigation owns one intent generation: opening Clients,
returning Back, changing the active view, or choosing another destination
invalidates any older lazy navigation before it can reopen Archive, Files, or
Employees. Card Back and repair-order parent closure remain blocked while their
workspace owns a save, file, payment, or order mutation, so an ambiguously
completed request cannot be hidden and then repeated from a new editor context.

Continue separating board/session responsibilities only when it simplifies
behavior; startup bytes and first-panel latency must be measured together.

Reduce duplicate requests/rendering and unrelated responsibilities when current
measurements justify it. Existing chunks can support a smaller change without
introducing another frontend toolchain; their number and extraction order are
implementation choices.

Board reconciliation renders only new or changed card bodies. Root-level position
controls ordering, not presentation; other fields and virtual-column preferences
remain part of the render signature. Keep node identity through ordering changes
and test empty/nonempty column transitions alongside counts and controls.

Preserve initialization order, shared state, one-time event binding, printing
boundaries, asset fingerprints, session reset and offline desktop loading.
Check modal focus/Escape, revisions, background freshness, timer cleanup and
object URL lifetime. Use generated JS checks and relevant browser scenarios;
compare real polling and simultaneous views as well as isolated timings.
