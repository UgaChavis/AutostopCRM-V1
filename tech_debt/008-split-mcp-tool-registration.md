# 008. Разрезать MCP tool registration по доменам

Приоритет: P1
Этап: 1
Оценка: 5–8 дней
Риск реализации: средний
Статус: in progress — read baseline, три board-write, attachment-read и
shared-file read/write registrars опубликованы 2026-08-28; следующий домен
выбирается отдельным малым срезом

## Результат

`create_mcp_server` собирает tool families из небольших registrars. Payload
models и backend relay не живут внутри одной 3.5k-строчной функции.
Production surface по-прежнему ровно 24 Gateway tools.

## Доказательства

- До первого среза `mcp/server.py` содержал 4 322 строки и 151 function;
  после payload extraction, diagnostics, board reads, трёх board-write
  registrars, attachment и shared-file operations — 3 551 строка и 121
  function.
- `create_mcp_server` сокращён с 3 623 до 3 104 строк; branch complexity
  снизилась с 211 до 184.
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
- На payload-срезе module ratchet `mcp/server.py` был снижен с 4 322 до 4 046
  строк без headroom. `create_mcp_server` тогда ещё не уменьшился: следующий
  срез — один read-only registrar.
- Focused payload/registration/hardening tests: 22/22 `OK`; полный MCP-family:
  137/137 `OK`; полный repository suite: 1 960 тестов, 34 штатных Windows skip,
  388.759 s, `OK`.

## Выполненный connector diagnostics slice (2026-08-26)

- `get_connector_identity`, `ping_connector` и `get_runtime_status` перенесены
  в 123-строчный `mcp/connector_diagnostics.py`; `bootstrap_context`, OAuth,
  transport и общие relay helpers остались в `server.py`.
- Registrar получает frozen/slots context только с используемыми identity,
  formatting и relay dependencies и возвращает canonical
  `DIAGNOSTIC_TOOL_NAMES`; новый focused execution/description contract
  выполняет 2/2 теста.
- Exact snapshot 98 builtin/raw names, schemas и annotations сохранил прежний
  hash; production master-switch по-прежнему оставляет ровно три diagnostics,
  а Gateway runtime wrapper продолжает использовать CRM status tool.
- Exact ratchets без headroom снижены: `mcp/server.py` 4 046 → 3 988,
  `create_mcp_server` 3 623 → 3 561. Следующий registrar не должен расширять
  diagnostics context; при новых shared dependencies сначала выделить общий
  response support.
- Combined focused registration/payload/diagnostics/hardening suite: 24/24
  `OK` за 5.576 s; полный MCP-family: 139/139 `OK` за 65.193 s; repository
  suite: 1 963 теста, 34 штатных Windows skip, 382.076 s, `OK`.

## Выполненный core board-read slice (2026-08-26)

- `list_columns`, `get_cards`, `get_card` и `get_board_snapshot` механически
  перенесены в 128-строчный `mcp/board_reads.py`. Context/event/search,
  attachments, writes и общие relay/auth helpers остались в `server.py`.
- Отдельный frozen/slots `BoardReadContext` содержит только client и шесть
  используемых registration/response helpers; diagnostics context не расширен.
- TDD-red зафиксировал отсутствующий module; затем exact set, descriptions,
  annotations, defaults, backend arguments, meta и snapshot limit прошли 2/2.
  Общий snapshot 98 raw tools и публичная поверхность 24 tools не изменились.
- Exact ratchets снижены без headroom: `mcp/server.py` 3 988 → 3 920,
  `create_mcp_server` 3 561 → 3 492; functions 145 → 141, complexity 208 → 204.
- Combined focused suite: 26/26 `OK`; MCP-family: 141/141 `OK` за 60.615 s;
  repository suite: 1 965 тестов, 34 штатных Windows skip, 369.278 s, `OK`.
- Core board-read slice опубликован коммитом `6c5c127`; GitHub Actions quality
  run `32979510635` полностью прошёл на неизменённом SHA.

## Выполненный board-column write slice (2026-08-27)

- `create_column`, `rename_column` и `delete_column` механически перенесены в
  97-строчный `mcp/board_column_writes.py`; relay, auth и backend не менялись.
- Frozen/slots context содержит только board client, description/annotation
  factories и relay helper. Exact descriptions, schemas, annotations и legacy
  `name` alias защищены двумя focused contract/execution tests.
- Raw snapshot остался 98 tools с прежним canonical hash; Gateway surface —
  24 tools. End-to-end backend test также проходит.
- Exact ratchets снижены без headroom: `mcp/server.py` 3 920 → 3 886,
  `create_mcp_server` 3 492 → 3 454; functions 141 → 138, complexity 204 → 201.
- Совместный focused suite: 44/44 `OK`; docs и code-health audits проходят.

## Выполненный board-sticky write slice (2026-08-27)

- `create_sticky`, `update_sticky`, `move_sticky` и `delete_sticky` перенесены
  в 138-строчный `mcp/board_sticky_writes.py` без изменения payload/backend.
- Один узкий context используется двумя registrar phases: create остаётся
  между column и attachment tools, mutations — между repair-order и timer
  tools. Новый relative-order test фиксирует обе точки явно.
- Exact schemas/defaults, полный deadline dump, annotations и backend arguments
  защищены двумя focused tests; raw hash 98 tools и Gateway 24 не изменились.
- Exact ratchets снижены без headroom: `mcp/server.py` 3 886 → 3 825,
  `create_mcp_server` 3 454 → 3 388; functions 138 → 134, complexity 201 → 197.
- Совместный focused suite: 47/47 `OK`; сохранённые backend/transport sticky
  tests также проходят.

## Выполненный board-card timer write slice (2026-08-27)

- `set_card_deadline`, `start_card_timer`, `stop_card_timer` и
  `set_card_indicator` механически перенесены в 134-строчный
  `mcp/board_card_timer_writes.py` на прежней позиции перед `move_card`.
- Узкий frozen/slots context содержит те же четыре зависимости, что column и
  sticky writes; relay, optimistic revision и backend validation не менялись.
- Focused tests фиксируют exact schemas/descriptions/annotations, полный
  deadline dump, отличие omitted deadline, response modes, indicator enum и
  наличие `expected_updated_at` только у start/stop. Relative order также
  защищён отдельно; raw hash 98 tools и Gateway 24 прежние.
- Exact ratchets снижены без headroom: `mcp/server.py` 3 825 → 3 740,
  `create_mcp_server` 3 388 → 3 299; functions 134 → 130, complexity 197 → 193.
- Совместный focused suite: 50/50 `OK`; end-to-end backend test проходит.
- Три board-write registrars опубликованы коммитами `bc87712`, `e4496bc` и
  `0a700f1`; GitHub Actions quality run `33042892243` полностью прошёл на
  конечном SHA.

## Выполненный card attachment read slice (2026-08-28)

- `list_card_attachments`, `get_card_attachment` и `read_card_attachment`
  механически перенесены в 123-строчный `mcp/card_attachment_reads.py` без
  изменения API client, payload, limits или response meta.
- Узкий frozen/slots context содержит board client, description/annotation
  factories, relay и data-meta helper. TDD-контракт фиксирует exact schemas,
  annotations, backend arguments и исходную позицию перед shared-file tools.
- Raw snapshot остался 98 tools с прежним canonical hash, Gateway surface —
  24 tools; существующие backend attachment и client limit regressions зелёные.
- Exact ratchets снижены без headroom: `mcp/server.py` 3 740 → 3 673,
  `create_mcp_server` 3 299 → 3 228; functions 130 → 127, complexity 193 → 190.
- Focused registrar/payload/hardening suite проходит 37/37; полный MCP-family
  выполняет 153/153 без skip.
- Slice опубликован коммитом `6c6a0a6`; GitHub Actions quality run
  `33147066250` полностью прошёл на неизменённом SHA.

## Выполненный shared-file read slice (2026-08-28)

- `list_shared_files`, `get_shared_file_info` и `download_shared_file`
  механически перенесены в 106-строчный `mcp/shared_file_reads.py` без
  изменения API client, payload, base64 limit или response meta.
- Узкий frozen/slots context повторно использует только board client,
  description/annotation factories, relay и data-meta helper. TDD RED
  зафиксировал отсутствующий module; focused tests защищают обе download
  ветви и исходную позицию перед shared-file writes.
- Независимое ревью опубликованного attachment slice оценило его на 9/10 без
  блокеров. Единственный P2 test gap закрыт отдельным выполнением default
  attachment arguments; production code не менялся.
- Raw snapshot остаётся 98 tools с прежним canonical hash, Gateway surface —
  24 tools. Exact ratchets снижены без headroom: `mcp/server.py` 3 673 → 3 614,
  `create_mcp_server` 3 228 → 3 168; functions 127 → 124, complexity 190 → 187.
- Совместный focused suite проходит 99/99; полный MCP-family выполняет 157/157
  без skip. Существующие backend shared-file roundtrip и client-side base64
  clamp tests сохранены.
- Slice опубликован коммитом `84932d3`; GitHub Actions quality run
  `33148027351` полностью прошёл на неизменённом SHA. Независимый review-pass
  оценил его на 9,5/10 без findings и блокеров.

## Выполненный shared-file write slice (2026-08-28)

- `upload_shared_file`, `delete_shared_file` и
  `update_shared_file_position` механически перенесены в 116-строчный
  `mcp/shared_file_writes.py`; backend, payloads и relay policy не менялись.
- Узкий frozen/slots context содержит только board client,
  description/annotation factories и relay helper. TDD RED зафиксировал
  отсутствующий module; contracts защищают исходную позицию между download и
  `get_card_context`.
- Upload test доказывает передачу default/explicit content в backend и
  отсутствие base64/actor в relay params. Delete сохраняет optimistic
  `expected_updated_at` и destructive annotation; position остаётся
  idempotent с обычными integer coordinates.
- Raw snapshot остаётся 98 tools с прежним canonical hash, Gateway surface —
  24 tools. Exact ratchets снижены без headroom: `mcp/server.py` 3 614 → 3 551,
  `create_mcp_server` 3 168 → 3 104; functions 124 → 121, complexity 187 → 184.
- Focused suite проходит 102/102; backend shared-file roundtrip входит в
  зелёный доменный набор из 36 тестов с двумя штатными platform-skip; полный
  MCP-family выполняет 160/160 без skip.
- Slice опубликован коммитом `ac1d877`; GitHub Actions quality run
  `33149587104` полностью прошёл на неизменённом SHA за 8m09s.
- Финальный локальный integration pass: 1 986 тестов, 34 штатных Windows-skip,
  runtime branch coverage 79,45% при floor 78,50%; все critical-path и release
  coverage ratchets прошли. Capability parity сохранил 175 actions / 0 gaps,
  change-feed producer parity — 100/100.
- Core browser smoke прошёл 11 сценариев без console/page/request errors;
  local-temp performance и Stage 1 synthetic current-production gates
  завершились без violations. Сервер и production data в этой волне не
  затрагивались.

## Минимальная архитектура

- Узкий context каждого registrar: client/state и только используемые shared
  helpers; не создавать общий god-context заранее.
- Registrars: diagnostics, board, clients, repair orders, finance/payroll,
  inventory, files, manager compatibility.
- Payload models вынести в `mcp/payloads.py` по доменам.
- Каждая registrar возвращает exact set зарегистрированных raw names.
- Финальный aggregator сверяет registry equality до Gateway filtering.

## Порядок

1. **Выполнено:** test exact raw tool names/schemas/annotations.
2. **Выполнено:** вынести payload models.
3. **Выполнено:** вынести permanent connector diagnostics read-only family.
4. **Выполнено:** вынести core read-only board family.
5. **Выполнено:** вынести board-column write family с exact backend tests.
6. **Выполнено частично:** вынести sticky и card-timer write families; далее
   повторять по небольшим доменам после отдельного выбора следующего среза.
7. Оставить transport/OAuth bootstrap в `server.py`.
8. Удалить large-function/module exemptions, когда budgets достигнуты.

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

`python -m unittest tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts tests.test_mcp_connector_diagnostics tests.test_mcp_board_reads -v`
`python -m unittest tests.test_mcp tests.test_mcp_main tests.test_mcp_server_hardening -v`
`python scripts/check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive`
`python scripts/crm_capability_parity.py --require-complete`

## Stop condition

Если registrar начинает копировать relay/auth/idempotency logic, сначала
выделить один shared helper. Не создавать базовый class hierarchy ради восьми
functions.
