# 003. Разрезать монолитные test modules и общие fixtures

Приоритет: P1
Этап: 1
Оценка: 4–6 дней суммарно, независимыми доменными срезами
Риск реализации: низкий
Статус: ready

## Результат

Тесты запускаются по доменным срезам, fixtures не копируются, а падение
локализуется в конкретном контракте. Поведение тестов не меняется.

## Доказательства

- `tests/test_service.py` — 13 514 физических строк, один `CardServiceTests`.
- `tests/test_api.py` — 7 692 строки плюс отдельный auth class.
- `tests/test_web_assets.py` — 5 963 строки, один `WebAssetsTests`.
- `tests/test_agent_gateway_v2.py` — 4 447 строк, один большой async class.
- `tests/test_mcp.py` — 3 264 строки.
- Полный suite занимает 333 s; текущий health audit бессрочно исключает эти
  files/classes из size budget.

## Scope и независимые deliverables

Каждый пункт можно закрывать независимо в рамках production-задачи, которой
нужны эти characterization tests. Завершать весь 003 до начала refactor не
требуется.

1. Выделять только нужные reusable fixtures/builders без изменения assertions.
2. Service slice — разнести `test_service.py`:
   board/cards, repair orders, attachments, manager/AI compatibility,
   archive/timers.
3. API slice — разнести `test_api.py`:
   transport/static/downloads, operator auth, route authorization,
   domain dispatch, maintenance/errors.
4. Web slice — разнести `test_web_assets.py` по UI-доменам.
5. Gateway slice — разнести Gateway tests:
   public surface, workflows, raw escape, Store, OAuth/audit actor.
6. MCP slice — разнести `test_mcp.py` по payload/schema/backend/transport/runtime.
7. Удалять allowlist entry сразу после каждого файла, не в финальном mega
   commit.

## Правила механического переноса

- Не переименовывать test methods без необходимости.
- Не менять test data, expected payloads и mocks одновременно с переносом.
- Сохранить порядок setup/cleanup и temp directory ownership.
- Общий fixture не должен превращаться в новый god-helper.
- Domain fixture может наследовать только самый маленький shared base.
- Не использовать wildcard imports.

## TDD/verification strategy

Для каждого исходного файла:

1. Снять exact test count командой unittest discovery/list.
2. Перенести один coherent class/group.
3. Запустить старый и новый набор; число executed/skipped совпадает.
4. Проверить, что duplicate test names не скрылись.
5. Удалить перенесённый блок из исходного файла.
6. Полный suite после каждого исходного mega-file.

## Подводные камни

- unittest discovery зависит от имён `test_*.py`.
- Module-level patches/constants могут неявно использовать старый module name.
- `IsolatedAsyncioTestCase` создаёт новый loop; нельзя вынести async resources
  в обычный global singleton.
- Windows skip не подтверждает Linux-only tests; CI должен пройти до закрытия.
- Большие fixtures могут хранить state между tests. Требовать fresh temp state.
- Tests, импортирующие scripts через `sys.path`, чувствительны к расположению.

## Acceptance criteria

- Ни один test module > 3 000 строк, кроме временно отдельно обоснованного.
- Ни один test class > 2 500 строк.
- Число tests/skips не уменьшилось без явного объяснения.
- Полный suite проходит на Windows и Linux CI.
- Старые test-module exemptions удалены из code health.
- Есть documented focused commands для каждого домена.
- Время полного suite не выросло более чем на 10%; focused feedback быстрее.

## Проверки

`python -m unittest discover -s tests -v`
`python scripts/code_health_audit.py --format text`
`python -m ruff format --check tests`
`python -m ruff check tests`

## Stop condition

Если перенос требует менять production API либо assertions, остановить
механическую задачу и вынести функциональный дефект отдельно.
