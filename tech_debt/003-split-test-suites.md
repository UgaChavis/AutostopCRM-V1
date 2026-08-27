# 003. Разрезать монолитные test modules и общие fixtures

Приоритет: P1
Этап: 1
Оценка: 4–6 дней суммарно, независимыми доменными срезами
Риск реализации: низкий
Статус: in progress — опубликованный MCP read baseline дополнен локально
проверенными board-column, sticky и card-timer write slices 2026-08-27

## Результат

Тесты запускаются по доменным срезам, fixtures не копируются, а падение
локализуется в конкретном контракте. Поведение тестов не меняется.

## Доказательства

- `tests/test_service.py` — 13 514 физических строк, один `CardServiceTests`.
- `tests/test_api.py` — 7 692 строки плюс отдельный auth class.
- `tests/test_web_assets.py` — 5 963 строки, один `WebAssetsTests`.
- `tests/test_agent_gateway_v2.py` — 4 447 строк, один большой async class.
- `tests/test_mcp.py` — 3 264 строки до первого среза; сейчас 2 913 строк,
  module exemption удалён, а лимит оставшегося end-to-end backend test снижен
  с 1 229 до текущих 1 169 строк без запаса.
- На исходном baseline полный suite занимал 333 s; health audit бессрочно
  исключал перечисленные files/classes из size budget.

## Выполненный MCP registration/payload/diagnostics/board-read slice (2026-08-26)

- До переноса `tests.test_mcp` выполнял 37 тестов без skip за 62.307 s.
- Семь существующих test methods механически перенесены в
  `test_mcp_registration_contracts.py` и `test_mcp_payload_contracts.py`;
  payload data, aliases, assertions и production API не менялись. Для двух
  read-only registrars добавлены по два focused contract/execution test в
  `test_mcp_connector_diagnostics.py` и `test_mcp_board_reads.py`.
- Добавлен один временный characterization test полного builtin/raw MCP
  registry до public whitelist: 98 tool names, 98 уникальных попыток
  регистрации и точный canonical hash
  `c7c68b2b73880c7a8d958b6596b7e2d61e37ebd11570ec782ee684355de2fa5d`
  по input/output schemas и annotations. Отдельная проверка попыток нужна,
  потому что FastMCP при повторном имени оставляет первый tool без ошибки.
- Этот snapshot страхует только механический рефакторинг 008 и не создаёт
  второй production manifest: внешний Gateway surface остаётся ровно 24 tools,
  а raw name set по-прежнему берётся из `PUBLIC_MCP_TOOL_NAMES`.
- Registry snapshot изолирован от optional manager tools и feature flags:
  development environment, OAuth и шесть Gateway switches явно выключены;
  active Manager dependency set и annotations проверяются отдельным контрактом
  с fake registrar. Server fixtures также подменяют optional sibling hook,
  поэтому результат не зависит от версии соседнего AutostopManager checkout.
- Registration/payload/diagnostics/board-read modules выполняют 13/13 тестов
  без skip. Discovery по `test_mcp*.py` находит и выполняет 141 тест без skip
  за 60.615 s: потерь и скрытых duplicate names нет.
- Полный suite после core board-read extraction: 1 965 тестов, 34 штатных
  Windows skip, 369.278 s, `OK`. От diagnostics slice 1 963 добавлены ровно два
  focused board-read test; ранее payload/diagnostics дали ещё три контракта.
  Предшествующее снижение с 1 966 до 1 959 объяснялось upstream cleanup: 12
  устаревших Gateway и deploy contract methods заменили пять актуальных, а не
  потеряли при механическом переносе.
- Большой end-to-end `test_mcp_tools_reach_backend` сохранён: он по-прежнему
  проверяет protocol `list_tools` и реальные backend calls. Его function
  ratchet остаётся активным до следующих backend/transport/runtime срезов.
- `code_health_audit.py --include-untracked --format text` проходит по 368
  файлам: size 34/34, complexity 2/2.
- Core board-read slice опубликован коммитом `6c5c127`; GitHub Actions quality
  run `32979510635` полностью прошёл на неизменённом SHA.

Focused-команды для этого среза:

`python -m unittest tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts tests.test_mcp_connector_diagnostics tests.test_mcp_board_reads -v`

`python -m unittest discover -s tests -p "test_mcp*.py" -v`

## Board write slices (2026-08-27)

- Для `create_column`, `rename_column` и `delete_column` добавлен отдельный
  двухтестовый contract/execution module `test_mcp_board_column_writes.py`.
- Он фиксирует exact tool set, descriptions, annotations, required/default
  schema и legacy `name` alias, а также точные backend arguments.
- Совместный focused suite с registration, payload и board reads: 44/44 `OK`;
  большой end-to-end backend test сохранён и проходит отдельно.

`python -m unittest tests.test_mcp_board_column_writes tests.test_mcp_registration_contracts tests.test_mcp_board_reads tests.test_mcp_payload_contracts tests.test_docs_audit -q`

- Для четырёх sticky write tools добавлены два focused contract/execution
  tests и отдельная проверка legacy relative order. Она защищает две исходные
  точки регистрации, которые общий sorted snapshot не различает.
- Проверяются полный `deadline.model_dump()`, отличие отсутствующего deadline
  от нулевого, строгие `int` координаты move, destructive delete и exact
  backend arguments. Совместный focused suite: 47/47 `OK`.

`python -m unittest tests.test_mcp_board_sticky_writes tests.test_mcp_board_column_writes tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts tests.test_mcp_board_reads tests.test_docs_audit -q`

- Для четырёх card deadline/timer/indicator tools добавлены два focused
  contract/execution tests и relative-order assertion. Они отдельно фиксируют
  optional deadline, optimistic revision только для start/stop, Literal enums,
  response modes и полный deadline dump.
- Совместный registrar/registration/payload/docs suite: 50/50 `OK`; большой
  end-to-end backend test с реальными raw tool calls сохранён и проходит.

`python -m unittest tests.test_mcp_board_card_timer_writes tests.test_mcp_board_sticky_writes tests.test_mcp_board_column_writes tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts tests.test_mcp_board_reads tests.test_docs_audit -q`

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
6. MCP slice — registration/payload, tests двух read-only registrars и трёх
   малых board-write registrars выполнены; далее разносить оставшийся
   `test_mcp.py` по backend/transport/runtime только по мере нужды
   production-задач.
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
