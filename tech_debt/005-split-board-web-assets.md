# 005. Browser maintainability

The startup bundle retains board/cards, clients, navigation, polling and modal
state. Payroll, stock, printing and cash-journal panels have versioned lazy
bundles. The assembler/loader and Windows package share that asset contract.
On a cold employees or inventory open, read-only data requests overlap module
loading. Prepared results wait for the module and current request/session scope;
concurrent opens reuse the pending invocation. No heavy panel is prefetched at
startup. Keep first-open latency and failure/retry coverage with this boundary.
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
