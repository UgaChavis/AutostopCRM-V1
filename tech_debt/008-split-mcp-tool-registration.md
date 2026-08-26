# 008. Разрезать MCP tool registration по доменам

Приоритет: P1
Этап: 1
Оценка: 5–8 дней
Риск реализации: средний
Статус: in progress — payload slice выполнен 2026-08-26; registrar slices впереди

## Результат

`create_mcp_server` собирает tool families из небольших registrars. Payload
models и backend relay не живут внутри одной 3.6k-строчной функции.
Production surface по-прежнему ровно 24 Gateway tools.

## Доказательства

- До первого среза `mcp/server.py` содержал 4 322 строки и 151 function;
  после синхронизации с upstream и payload extraction — 4 046 строк и 148
  functions.
- `create_mcp_server` — 3 623 строки, complexity 211.
- `tool_registry.py` уже группирует raw tools по 8 доменам / десяткам names,
  но регистрации всё ещё находятся в одном lexical scope.
- Файл входит в top churn hotspots.

## Выполненный payload slice (2026-08-26)

- 13 Pydantic payload/envelope models, `McpInt` и pure deadline normalization
  механически перенесены в `mcp/payloads.py` без изменения полей, defaults,
  limits или validation.
- Старые imports из `mcp.server` сохранены как compatibility re-exports и
  проверяются отдельным тестом; новый модуль является каноническим владельцем.
- Characterization snapshot по 98 builtin/raw tools сохранил тот же canonical
  hash схем и annotations; production Gateway surface остаётся ровно 24 tools.
- Module ratchet `mcp/server.py` снижен с 4 322 до текущих 4 046 строк без
  headroom. `create_mcp_server` пока не уменьшился: его следующий безопасный
  срез — один read-only registrar.
- Focused payload/registration/hardening tests: 22/22 `OK`; полный MCP-family:
  137/137 `OK`; полный repository suite: 1 960 тестов, 34 штатных Windows skip,
  388.759 s, `OK`.

## Минимальная архитектура

- `McpRegistrationContext`: client, server, logger, limits, shared helpers.
- Registrars: diagnostics, board, clients, repair orders, finance/payroll,
  inventory, files, manager compatibility.
- Payload models вынести в `mcp/payloads.py` по доменам.
- Каждая registrar возвращает exact set зарегистрированных raw names.
- Финальный aggregator сверяет registry equality до Gateway filtering.

## Порядок

1. **Выполнено:** test exact raw tool names/schemas/annotations.
2. **Выполнено:** вынести payload models.
3. Вынести read-only family.
4. Вынести один write family с exact backend tests.
5. Повторить по доменам.
6. Оставить transport/OAuth bootstrap в `server.py`.
7. Удалить large-function/module exemptions, когда budgets достигнуты.

## TDD-план

- exact raw name set equals `PUBLIC_MCP_TOOL_NAMES`;
- no duplicate registrations;
- input schema/required/defaults unchanged;
- annotations readonly/destructive unchanged;
- errors and media results unchanged;
- hidden Store tools captured then absent from raw escape;
- final public Gateway surface exactly 24.

## Подводные камни

- Decorators register function object at definition time; registrar lifecycle
  и closure context должны жить достаточно долго.
- Function names могут попадать в schema/title/docs tests.
- Default values и Optional typing влияют на generated JSON schema.
- Gateway удаляет/hides tools через внутренний tool manager; порядок важен.
- Не объединять этот move с изменением tool names или schemas.

## Acceptance criteria

- Ни один registrar > 800 строк; target ≤ 500.
- `create_mcp_server` ≤ 500 строк orchestration.
- Exact raw/public tool inventories и schemas совпадают.
- MCP backend, transport, OAuth, parity и exhaustive local smoke проходят.
- Code-health exemptions сокращены.

## Проверки

`python -m unittest tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts -v`
`python -m unittest tests.test_mcp tests.test_mcp_main tests.test_mcp_server_hardening -v`
`python scripts/check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive`
`python scripts/crm_capability_parity.py --require-complete`

## Stop condition

Если registrar начинает копировать relay/auth/idempotency logic, сначала
выделить один shared helper. Не создавать базовый class hierarchy ради восьми
functions.
