# 018. Snapshot and read models

SnapshotService coordinates compact/full board payloads, revisions, search,
reviews and journal reads. Simplify repeated reads and projection work while
keeping cache ownership explicit.

`services/card_log_projection.py` owns journal entries, compact presentation,
groups, totals and Markdown. `SnapshotService.get_card_log` owns storage reads,
event visibility, limits, permitted detail hydration and the `card_journal.v2`
response assembly. Only events that passed these visibility and limit rules reach
the projector. Preserve public compact/full responses and event order when
changing either side; direct formatter checks belong to the projection module,
while public journal and limited-permission checks exercise the service boundary.

Preserve viewer/archive/compact cache boundaries, unseen markers, notification
timestamps, ordering, search transliteration/ranking and private-data redaction.
Avoid audit-archive hydration in the high-frequency compact path. Share serializers
only when actual repetition warrants it.

Check cache hits/invalidation for changed write families, pagination, archived
details and reads during commit. Compare identical-fixture request/payload costs
and p95; existing HTTP/MCP parity and performance gates remain the reference.
