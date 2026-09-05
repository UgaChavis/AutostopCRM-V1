# 005. Browser maintainability

The browser source combines board/cards, clients, orders, stock, payroll,
files, mobile navigation, polling and modal state. The existing assembler
produces a fingerprinted asset used by the browser and Windows package.

Reduce duplicate requests/rendering and unrelated responsibilities when current
measurements justify it. Existing chunks can support a smaller change without
introducing another frontend toolchain; their number and extraction order are
implementation choices.

Preserve initialization order, shared state, one-time event binding, printing
boundaries, asset fingerprints, session reset and offline desktop loading.
Check modal focus/Escape, revisions, background freshness, timer cleanup and
object URL lifetime. Use generated JS checks and relevant browser scenarios;
compare real polling and simultaneous views as well as isolated timings.
