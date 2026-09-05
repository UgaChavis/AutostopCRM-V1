# 014. Backend printing

PrintModuleService joins templates, drafts, document contexts, calculation and
export. Reduce repeated calculation and isolate responsibilities where useful;
invoice, invoice-factura, UPD and completion act share the backend's values.

Preserve VAT modes, cent balancing, manual documents, draft versions/source
fingerprints, idempotency, reset tombstones and legacy draft recovery. Validate
filesystem limits and renderer failure cleanup. Backup/restore consumes drafts.

Check structured contexts before rendered output, large completion acts and
full browser PDF scenarios. Qt rendering needs both Linux and Windows evidence.
The print browser boundary is 021; migration retirement is 017. There is no
required component list or mechanical extraction order.
