# 012. Выделить repair-order lifecycle boundary

Приоритет: P1
Этап: 1
Оценка: 8–12 дней
Риск реализации: высокий
Статус: ready после 001 и 006; coverage 002 параллельно

## Результат

Создание, редактирование, reopen/reclose, numbering, payments/inventory guards
и posting cycles управляются одним доменным coordinator. CardService остаётся
facade, а finance/payroll/inventory вызываются через явные ports.

## Доказательства

- Repair-order public methods и helpers занимают несколько несмежных блоков
  `card_service.py`: `1536+`, `2681–4407`, `7142–7880` и `8313–10108`.
- `update_card` отдельно может обновлять вложенный repair-order.
- Closed correction связана с payroll reversals, inventory movement guards,
  payment immutability, revenue recognition и immutable posting cycles.
- Эти контракты уже хорошо покрыты, но разнесены по god-service.

## Минимальная boundary

`RepairOrderLifecycleService`:

- query/list/get;
- validate/prepare patch;
- create/number uniqueness;
- status transition state machine;
- preview/reopen/reclose;
- posting cycle coordination;
- payment signature/lock guards;
- inventory material guard;
- event/result construction.

Rendering/export остаётся в PrintModuleService; cash/payroll mutations — в
своих services через ports.

Coordinator содержит только transition/posting coordination и имеет cap
≤ 800 строк; validators/query/serialization components — ≤ 500 строк каждый.
Query и serialization не должны зависеть от mutation coordinator.

## Порядок

1. Golden state-transition table.
2. Вынести pure validators/sort/serialization.
3. Вынести query/list.
4. Вынести create/update for open orders.
5. Вынести close.
6. Вынести reopen/reclose и posting coordination последними.
7. Переключить CardService delegates и route registry.

## TDD-план

Матрица:

- absent/open/ready/closed/correction-active/archived;
- paid/unpaid/overpaid, cash/cashless;
- payroll snapshot absent/current/legacy;
- inventory-linked/unlinked material;
- stale/current revision;
- first/repeated/different idempotency key;
- migration legacy cycle/current cycles;
- close period/revenue recognition invariants.

Обязательные invariants:

- order number immutable/unique;
- payment/cash/inventory IDs не переписываются при reopen;
- payroll reverses/reposts ровно один раз;
- feed/audit sequence deterministic;
- state write atomic at current JsonStore boundary.

## Подводные камни

- Не создавать второй aggregate, который копирует `RepairOrder` model.
- `get_repair_order(create_if_missing)` имеет compatibility semantics.
- Update via `update_card` нельзя оставить обходным путём.
- Archived-card restore — отдельный lifecycle; сохранить current guards.
- Timestamp/business timezone влияет на recognized period.
- Migration methods нужны до production evidence задачи 017.

## Acceptance criteria

- Все repair-order mutations проходят через один coordinator.
- В CardService остаются thin delegates, не duplicate business logic.
- Golden transition matrix и branch coverage floor добавлены.
- API/MCP DTOs, codes, numbers, totals, audit/feed unchanged.
- Repair-order, finance, payroll, inventory и printing focused suites проходят.
- CardService ratchet существенно снижен.

## Проверки

`python -m unittest tests.test_repair_order_reopen tests.test_service tests.test_api tests.test_mcp tests.test_printing_service -v`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/crm_change_feed_producer_parity.py --require-complete`
`python scripts/perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20 --max-list-cashboxes-ms 50 --max-feed-read-ms 50 --max-feed-replay-ms 20`

## Stop condition

Любое изменение cent totals, IDs, event order или payroll reconciliation
останавливает refactor. Не «исправлять заодно» без отдельного defect.
