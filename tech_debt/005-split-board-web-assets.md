# 005. Browser maintainability

The startup bundle retains board/cards, clients, navigation, polling and modal
state. Payroll, stock, printing and cash-journal panels have versioned lazy
bundles. The assembler/loader and Windows package share that asset contract.
Continue separating board/session responsibilities only when it simplifies
behavior; startup bytes and first-panel latency must be measured together.

Reduce duplicate requests/rendering and unrelated responsibilities when current
measurements justify it. Existing chunks can support a smaller change without
introducing another frontend toolchain; their number and extraction order are
implementation choices.

Preserve initialization order, shared state, one-time event binding, printing
boundaries, asset fingerprints, session reset and offline desktop loading.
Check modal focus/Escape, revisions, background freshness, timer cleanup and
object URL lifetime. Use generated JS checks and relevant browser scenarios;
compare real polling and simultaneous views as well as isolated timings.
