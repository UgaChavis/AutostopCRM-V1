# 010. Card attachments

Attachment CRUD, extraction and filesystem handling are a separable concern
inside CardService. Reuse shared validation or extract a focused boundary when
that reduces maintenance cost; keep the public facade and storage layout.

Preserve file/state failure ordering, valid filenames, Content-Disposition,
size/type/content limits, symlink and traversal checks, archive extraction limits,
truncation markers and audit/feed events. Agent and Gateway media are consumers.

Validate add/list/read/remove and a file-success/state-failure case, malformed
OpenXML/PDF/image inputs and Windows/Linux paths. A transaction redesign is a
separate compatibility change, not an incidental consequence of cleanup.
