# 018. Snapshot and read models

SnapshotService combines compact/full board payloads, revisions, search, reviews
and audit-log presentation. Simplify repeated formatting and reads while keeping
cache ownership explicit. Components and their extraction order are choices,
not a prescribed architecture.

Preserve viewer/archive/compact cache boundaries, unseen markers, notification
timestamps, ordering, search transliteration/ranking and private-data redaction.
Avoid audit-archive hydration in the high-frequency compact path. Share serializers
only when actual repetition warrants it.

Check cache hits/invalidation for changed write families, pagination, archived
details and reads during commit. Compare identical-fixture request/payload costs
and p95; existing HTTP/MCP parity and performance gates remain the reference.
