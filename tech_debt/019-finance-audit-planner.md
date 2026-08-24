# 019. Вынести finance audit и safe-fix planner

Приоритет: P1
Этап: 1
Оценка: 5–8 дней, независимыми commits
Риск реализации: высокий
Статус: ready; coverage 002 может идти параллельно

## Результат

Finance audit, cancellation/transfer checks и safe-fix dry-run строят один
детерминированный внутренний plan без state I/O. Apply повторно валидирует
revision и применяет ровно этот plan одним контролируемым commit.

## Доказательства

- `CardServiceFinanceMixin` остаётся oversized module/class exemption.
- Finance audit builder: 399 строк, complexity 40.
- Safe-fix planner: 237 строк, complexity 23.
- Cancellation и transfer paths связывают payment journal, repair order и
  change feed, поэтому смешивать их перенос с payroll-задачей рискованно.

## Scope

1. Зафиксировать golden audit issues и proposed operations.
2. Вынести pure `FinanceAuditPlanner` и typed internal result.
3. Dry-run и apply используют один внутренний plan hash; публичный HTTP/MCP
   DTO и существующие idempotency keys не меняются.
4. Отдельный applier проверяет expected revision, permissions и current facts.
5. Сохранить явное operator confirmation: safe fixes не запускаются сами.
6. После каждого среза снижать module/class ratchet; снять оба exemptions к
   закрытию задачи либо документировать остаточный facade cap.

## TDD-план

- cash/cashless, partial payment и 85/15 invariant;
- transfer, cancellation и repeated cancellation;
- stale revision между dry-run и apply;
- issue ordering и стабильность внутреннего plan hash;
- legacy missing fields и unknown journal reference;
- zero/negative/boundary cents, `Decimal` и `ROUND_HALF_UP`;
- apply failure до/после возможного side effect;
- audit actor, change-feed event и no duplicate posting.

## Подводные камни

- Не включать actor/PII в hash.
- Не менять presentation rounding одновременно с ledger formula.
- Не превращать planner в общий rules engine.
- Ошибка текущих totals оформляется отдельным correctness-fix с owner review;
  golden expectation не обновлять внутри механического переноса.

## Acceptance criteria

- Planner не читает store и не использует implicit current time.
- Dry-run/apply plans совпадают для одной revision.
- Existing finance DTO, issues, ordering, audit/feed semantics exact-equal.
- Finance mixin существенно сокращён; его exemptions удалены или заменены
  точным малым facade-cap.
- Finance audit, reopen и stage-1 performance gates проходят.

## Проверки

`python -m unittest tests.test_finance_audit_report tests.test_repair_order_reopen tests.test_change_feed_gateway -v`
`python scripts/finance_audit_report.py --help`
`python scripts/perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20 --max-list-cashboxes-ms 50 --max-feed-read-ms 50 --max-feed-replay-ms 20`

## Stop condition

Любое расхождение ledger totals или ordering сначала классифицировать как
existing defect либо refactor regression. Не применять safe fixes к production
state в рамках задачи.
