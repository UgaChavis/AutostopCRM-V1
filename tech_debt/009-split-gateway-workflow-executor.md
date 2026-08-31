# 009. Разделить Gateway workflow executor

Приоритет: P1
Этап: 1
Оценка: 8–12 дней, только малыми commits
Риск реализации: высокий
Статус: ready после 001 и 008; coverage 002 параллельно

## Результат

Named workflows, raw capability execution, Store actions и verification
используют общий небольшой lifecycle kernel, а доменные plan/execute/readback
стратегии изолированы. Security switches и compensating semantics не меняются.

## Доказательства

- `agent_gateway_v2.py` менялся в 41 из последних 150 commits — главный
  hotspot.
- `register_agent_gateway_v2` — 3 086 строк, complexity 280.
- Nested `_execute_workflow` — 610 строк, complexity 56.
- `call_raw_capability` — 707 строк, complexity 58.
- File имеет 30 strict-complexity signals.

## Целевая минимальная схема

- `WorkflowLifecycleKernel`: start/dedupe/transition/complete/compensating.
- `OperationSpec`: policy switch, revision preflight, executor, verifier,
  readback class.
- Domain operation maps: board, finance, inventory, documents, Store.
- `RawCapabilityExecutor` отдельно от named workflows.
- Registration functions только валидируют public DTO и вызывают kernel.

Не делать generic workflow DSL.

## Инварианты, которые нельзя менять

1. Dry-run и apply имеют разные idempotency keys и корректно отражают mode.
2. Applied-but-unverified → `compensating`, не retryable success.
3. Exact repeated Store request/key может reconcile receipt; новый request нет.
4. CAS state_version на lifecycle transitions.
5. OAuth owner audit actor и technical service identity не подменяются payload.
6. Finance/destructive/raw switches fail closed.
7. Maintenance blocking и 24-tool surface сохраняются.
8. Exact target revision/readback до закрытия ledger.

## Порядок

1. Заморозить operation matrix и golden workflow traces.
2. Вынести immutable operation specs без смены executor.
3. Вынести lifecycle transitions.
4. Вынести verification/readback strategies.
5. Вынести Store path.
6. Вынести raw path.
7. Сократить registration и удалить exemptions.

Deliverable A (named lifecycle/Store) и deliverable B (raw execution/readback)
должны быть закрываемыми независимо. В B отдельно разрезать 966-строчный
`raw_gateway.verify_virtual_api_write_readback`; снять module/function
exemptions `raw_gateway.py` после extraction.

## TDD-план

Табличные tests для каждой операции и состояний:

- invalid args before ledger;
- stale revision closes failed preflight;
- executor success + readback success;
- executor success + timeout/uncertain readback;
- executor failure before/after possible side effect;
- repeated same/different key;
- maintenance/policy disabled;
- OAuth owner disabled/deleted;
- external notifier states SENT/NOT_APPLICABLE/CLAIMED/FAILED.

## Подводные камни

- Не менять ordering side effects и ledger writes.
- Не ловить broad Exception в новом kernel без classification.
- Operation map не должен хранить bound closures с stale runtime state.
- Hash/canonicalization input должен оставаться byte-equivalent.
- Не использовать inheritance tree; typed dataclass + callables достаточно.
- Production attestation — release proof, но не замена focused unit traces.

## Acceptance criteria

- Lifecycle kernel ≤ 500 строк; domain strategy module ≤ 800.
- `_execute_workflow` и `call_raw_capability` exemptions удалены.
- 24 public tools и 46 CRM workflow operation contracts exact-equal current
  attestation baseline.
- Capability/change-feed parity gaps 0.
- Exhaustive safe local smoke и attestation unit tests проходят.
- Нет изменения public envelope/schema/hash/idempotency semantics.

## Проверки

`python -m unittest tests.test_agent_gateway_v2 tests.test_change_feed_gateway tests.test_store_owner_gateway tests.test_gateway_release_probes -v`
`python scripts/check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/crm_change_feed_producer_parity.py --require-complete`

## Stop condition

Любое необъяснимое отличие golden trace/schema/hash прекращает refactor.
Сначала доказать причину; не обновлять snapshot автоматически.
