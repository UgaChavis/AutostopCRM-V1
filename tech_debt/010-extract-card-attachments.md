# 010. Card attachments

Attachment CRUD, extraction and filesystem handling belong to
`services/card_attachments.py`; CardService remains the public facade. Future
work is narrower parser/IO boundaries, not another copy of the attachment API.

Preserve file/state failure ordering, valid filenames, Content-Disposition,
size/type/content limits, symlink and traversal checks, archive extraction limits,
truncation markers and audit/feed events. Agent and Gateway media are consumers.

Validate add/list/read/remove and a file-success/state-failure case, malformed
OpenXML/PDF/image inputs and Windows/Linux paths. A transaction redesign is a
separate compatibility change, not an incidental consequence of cleanup.
