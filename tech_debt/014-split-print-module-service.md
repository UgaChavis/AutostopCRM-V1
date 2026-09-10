# 014. Backend printing

PrintModuleService joins templates, drafts, document contexts, calculation and
export. Reduce repeated calculation and isolate responsibilities where useful;
invoice, invoice-factura, UPD and completion act share the backend's values.

`document_rendering.py` owns HTML shells and preview projection; preview,
export and print use the same prepared-document batch. Context calculation and
draft persistence still need smaller independently testable boundaries.

Browser request ownership and the cancellable print-frame lifecycle live in
`web_async_context.py`. Each asynchronous continuation retains its operator,
card/workspace and request context, including failures and server-print fallback.
Changing context must never submit a follow-up write using the new operator.

Preserve VAT modes, cent balancing, manual documents, draft versions/source
fingerprints, idempotency, reset tombstones and legacy draft recovery. Validate
filesystem limits and renderer failure cleanup. Backup/restore consumes drafts.

Custom templates, print settings and inspection-sheet drafts are independent
JSON files but form one read-modify-write boundary. Their six public mutators
share a bounded process lock through atomic replacement and change-feed sync, so
two desktop/server instances cannot silently discard each other's changes.
`test_printing_state_lock` owns this contract, including timeout classification.
The full projection during initialization and pending completion-act replay use
the same boundary. Completion-act drafts retain their versioned cycle-key shards,
idempotency and recovery rules.

Check structured contexts before rendered output, large completion acts and
full browser PDF scenarios. Qt rendering needs both Linux and Windows evidence.
The print browser boundary is 021; migration retirement is 017. There is no
required component list or mechanical extraction order.
