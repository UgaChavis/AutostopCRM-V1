# 018. Разделить SnapshotService по read models

Приоритет: P1
Этап: 1
Оценка: 4–7 дней
Риск реализации: средний
Статус: ready после 001; focused test-slice 003 идёт параллельно

## Результат

Board snapshot/revision, search/review и audit-log projection строятся
отдельными read-model components. Cache/revision semantics и HTTP/MCP payloads
остаются неизменными.

## Доказательства

- `snapshot_service.py` — 2 879 строк.
- `SnapshotService` — 2 574 строки / 91 method.
- Module и class находятся в unconditional code-health allowlist.
- Class одновременно строит compact/full snapshots, board revision,
  manager/review text, search results, event pages и card-log descriptions.
- `_get_board_snapshot`, `review_board`, `get_gpt_wall`, card-log formatting и
  search имеют разные change cadence и consumers.

## Минимальные seams

- `BoardSnapshotBuilder`: compact/full board DTO.
- `BoardRevisionReader`: cached revision and invalidation inputs.
- `CardSearchReadModel`.
- `BoardReviewReadModel`.
- `CardAuditLogPresenter`.
- `SnapshotService` остаётся facade и владеет shared cache/dependencies.

Не вводить CQRS framework.

## Порядок

1. Golden payloads для compact/full/revision/search/log/review.
2. Вынести pure formatting/presentation helpers.
3. Вынести card audit log.
4. Вынести search/review.
5. Вынести snapshot builder.
6. Revision/cache ownership переносить последним после hit/miss tests.
7. Снизить/remove allowlist thresholds.

## TDD-план

- compact/full with/without archive;
- deterministic ordering and payload byte budgets;
- viewer-relative unseen markers and notification timestamps;
- cache hit/invalidation after every write family;
- event pagination and archived detail hydration;
- PII redaction in board summary/review/GPT wall;
- search transliteration/ranking/exact filters;
- concurrent read during state commit.

## Подводные камни

- Не копировать serializers из CardService; вынести shared pure serializer
  только при фактическом duplicate proof.
- Cache key должен включать viewer/archive/compact boundaries.
- `notification_updated_at` отличается от обычного `updated_at`.
- Audit archive hydration может делать filesystem I/O; не включать её в
  high-frequency compact path.
- Payload ordering/size является performance contract.

## Acceptance criteria

- Facade ≤ 600 строк; component ≤ 700.
- Snapshot/revision/search/log payloads exact-equal golden baseline.
- Cache and performance gates не ухудшились более чем на agreed noise margin.
- Snapshot module/class exemptions удалены.
- API/MCP/parity и stage-1 performance проходят.

## Проверки

`python -m unittest tests.test_snapshot_service tests.test_service tests.test_api tests.test_mcp -v`
`python scripts/perf_probe.py --local-temp-server --warmup-iterations 2 --iterations 5 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-revision-server-ms 20 --max-get-card-ms 800`
[канонический Stage-1 performance gate](../docs/OPERATIONS_RUNBOOK.md#performance-smoke)
`python scripts/crm_capability_parity.py --require-complete`

## Stop condition

Если extraction ухудшает compact snapshot/revision p95 сверх noise budget,
откатить только cache ownership move, оставив уже вынесенные pure presenters.
