# AutoStop CRM: приоритизированный backlog технического долга

Дата аудита: 2026-08-23
Базовый commit: `e0cb9544588e48c1b730a44e815d36465d727ea7`
Ветка: `autostopcrm-v1`

## Что проверено на исходном commit

- 319 tracked-файлов классифицированы штатным `code_health_audit.py`.
- Полный suite: 1 908 тестов, 34 skip, 333.256 s, результат `OK`.
- Capability parity: 175 действий, 170 покрыты исполнимым контрактом, 5 имеют
  зафиксированную human-session границу, необъяснённых gaps нет.
- Change-feed producer parity: 100/100 write actions, gaps нет.
- Docs, localization, generated JavaScript syntax и Ruff проходят.
- В коде около 186 тыс. строк Python, 20.8 тыс. строк JavaScript и 1 911
  статически найденных test-методов.
- Строгий диагностический профиль Ruff
  `C901, PLR0911, PLR0912, PLR0913, PLR0915` находит 433 сигнала:
  92 complex functions, 191 long parameter lists, 57 long functions,
  49 excessive returns и 44 excessive branches.
- Текущий health-gate содержит 17 безусловных исключений для больших модулей,
  10 для больших классов и 12 для больших функций. Они не дают новым
  неизвестным монолитам появиться, но не запрещают уже известным расти.
- Самые крупные runtime hotspots (физические строки, как в health-gate):
  `app_main_before_printing.js` — 20 186 строк / около 1 221 функций;
  `CardService` — 11 180 строк / 407 методов;
  `AgentRunner` — 4 865 строк / 146 методов;
  `CardServicePayrollMixin` — 4 608 строк / 86 методов;
  `create_mcp_server` — 3 623 строки;
  `register_agent_gateway_v2` — 3 288 строк.
- Самые крупные тестовые hotspots (физические строки):
  `test_service.py` — 13 514 строк;
  `test_api.py` — 7 692;
  `test_web_assets.py` — 5 963;
  `test_agent_gateway_v2.py` — 4 447;
  `test_mcp.py` — 3 264.
- `coverage.py` отсутствует; browser smoke в GitHub Actions запускается только
  вручную; единый unit-suite даёт позднюю обратную связь.
- Локальный полный browser smoke воспроизводимо прошёл 39/40 сценариев, но
  четыре попытки одного completion-act сценария завершились false. Причина
  baseline-инфраструктуры: `pdfinfo` и `pdftotext` отсутствуют, а smoke не
  делает preflight и повторяет заведомо невозможную проверку.
- Stage-1 production-sized performance gate прошёл без violations:
  `update_card p95=170.7 ms`, `storage write p95=195.9 ms`,
  `change-feed read p95=14.0 ms`.
- Старые явные мусорные семейства `AI_REMODEL_*`, `GPT_AGENT_*`,
  `main_agent.py` и example tool JSON в текущем tracked tree отсутствуют.
  Оставшиеся compatibility/migration пути нельзя удалять без runtime/data
  доказательств.

## P0 выполнен и опубликован; hosted CI подтверждён 2026-08-26

- 000: deterministic browser/PDF preflight; после установки Poppler полный
  smoke прошёл 44/44 с первой попытки.
- 001: после закрытия 007 и MCP test-slice 003 текущие 34/34 size и 2/2
  complexity ratchets имеют exact caps, причины и одного owner; browser,
  `_make_handler` и module-level `test_mcp.py` exemptions закрыты и удалены.
- 002: branch-inclusive baseline 78.82%, floor 78.50%, текущий integrated
  результат 79.36%; 13/13 global/critical floors проходят, covered suite
  заменяет plain unit run в CI.
- 004: обязательный core smoke покрывает 11 critical temp-data flows и локально
  проходит примерно за 12 секунд; full остаётся отдельным release gate.
- 006: handler/auth/maintenance/mutation/response/feed/readback policy сведена
  в immutable `RouteSpec`, а старые sets оставлены derived compatibility views.
- Финальная интеграция: covered suite 1 946 тестов / 34 skip / `OK`; coverage
  audit 13/13; capability parity gaps=0; change-feed parity 100/100; Ruff,
  docs, code-health, localization, JavaScript и Stage-1 performance gates
  проходят без violations.
- Опубликованный commit `6615143a` прошёл hosted Ubuntu workflow
  `32935776774`: covered suite, coverage/code-health ratchets, core browser
  smoke и Stage-1 performance gates зелёные. Production этим publish не
  обновлялся.

## Общие правила выполнения

1. Один логичный дефект или один механический перенос — один небольшой commit.
2. Сначала characterization test, затем перенос, затем удаление старого пути.
3. Публичные HTTP/MCP DTO, error codes, audit/feed события, ordering,
   idempotency и optimistic-concurrency не менять в cleanup-коммитах.
4. Compatibility-названия `Minimal Kanban`, `%APPDATA%\Minimal Kanban` и
   `Start Kanban.exe` сохранять, пока отдельная задача не докажет безопасную
   миграцию.
5. Не совмещать структурный перенос с улучшением бизнес-правил.
6. Для каждого extracted-модуля удалять соответствующее исключение из
   `code_health_audit.py` или снижать его числовой потолок.
7. Не гнаться за 100% coverage и нулём всех complexity-warning. Цель —
   уменьшить blast radius наиболее часто меняемых и финансово опасных путей.
8. Любая задача о migration/legacy сначала выполняет read-only inventory.
   Production state и сервер в рамках этих задач не изменяются.
9. Базовый gate для Python-задач:
   focused unittest → Ruff по затронутым файлам → полный unittest →
   code health → capability/change-feed parity, если менялись контракты.
10. Для UI: JS syntax → focused web asset tests → core browser smoke →
    полный browser smoke перед release.

## Этап 1: минимально достаточные изменения с максимальным ROI

| № | Приоритет | Задача | Зачем сейчас | Зависит от |
|---|---|---|---|---|
| 000 | P0 | [Починить preflight browser/PDF smoke](000-browser-smoke-preflight.md) | **Выполнено 2026-08-23** | — |
| 001 | P0 | [Зафиксировать числовой maintainability ratchet](001-maintainability-ratchet.md) | **Выполнено 2026-08-23** | — |
| 002 | P0 | [Добавить измеряемый coverage baseline](002-coverage-baseline.md) | **Выполнено; hosted CI подтверждён 2026-08-26** | 001 |
| 004 | P0 | [Сделать малый browser smoke обязательным](004-mandatory-core-browser-smoke.md) | **Выполнено; hosted CI подтверждён 2026-08-26** | 000, 001 |
| 006 | P0 | [Свести HTTP route metadata в один registry](006-unify-api-route-contracts.md) | **Выполнено 2026-08-23** | 001 |
| 003 | P1 | [Разрезать монолитные test modules и fixtures](003-split-test-suites.md) | **MCP registration/payload slice опубликован; остальные срезы — по production-задачам** | 001 |
| 005 | P1 | [Разрезать web asset source по доменам](005-split-board-web-assets.md) | Убрать 20.2k-строчный god-script без смены frontend stack | 004; свой test-slice из 003 |
| 007 | P1 | [Разделить HTTP request handler](007-split-api-request-handler.md) | **Выполнено 2026-08-25** | 006 |
| 008 | P1 | [Разрезать MCP tool registration по доменам](008-split-mcp-tool-registration.md) | **Payload models вынесены; следующий срез — read-only registrar** | 001; свой test-slice из 003 |
| 009 | P1 | [Разделить Gateway workflow executor](009-split-gateway-workflow-executor.md) | Изолировать security/idempotency/readback правила hot path | 001, 008; coverage 002 параллельно |
| 010 | P1 | [Вынести attachment/file I/O из CardService](010-extract-card-attachments.md) | Самый безопасный крупный срез god-service | 001; свой test-slice из 003 |
| 011 | P1 | [Вынести manager operations из CardService](011-extract-manager-service.md) | Отделить активные manager flows от core CRM | 001; свой test-slice из 003 |
| 012 | P1 | [Выделить repair-order lifecycle boundary](012-extract-repair-order-lifecycle.md) | Снизить риск в заказ-нарядах, payroll, inventory и payments | 001, 006; coverage 002 параллельно |
| 013 | P1 | [Вынести payroll calculators и reconciliation](013-payroll-calculators.md) | Снизить риск сложных зарплатных вычислений | 001, 012 |
| 014 | P1 | [Разделить backend PrintModuleService](014-split-print-module-service.md) | Изолировать drafts, templates, расчёты и render/export | 000, 001; свой test-slice из 003 |
| 017 | P1 | [Закрыть lifecycle one-off migrations и compatibility shims](017-retire-migrations-and-shims.md) | Удалять legacy только по доказательствам, не по названию | 001, 002 |
| 018 | P1 | [Разделить SnapshotService по read models](018-split-snapshot-service.md) | Уменьшить 2.7k-строчный read-side god-class | 001; свой test-slice из 003 |
| 019 | P1 | [Вынести finance audit и safe-fix planner](019-finance-audit-planner.md) | Изолировать денежные планы без смешения с payroll | 001; coverage 002 параллельно |
| 020 | P1 | [Разделить backup/verify/restore release state](020-split-release-backup-restore.md) | Защитить критичный rollback seam | 001 |
| 021 | P1 | [Разделить embedded print web module](021-split-print-embedded-web-module.md) | Изолировать editor/preview/bridge без нового toolchain | 000, 004 |

## Рекомендуемые волны

- Волна A — 000–002, 004, 006 — выполнена локально 2026-08-23. Hosted gates
  активируются после публикации изменений.
- Волна B — доменные срезы 003, 005, 007–008, 010–011, 017–021. В основном механические разрезы с
  сохранением facade и DTO.
- Волна C — 009, 012–014, 019–020. Здесь выше бизнес- и security-риск; каждую задачу
  выполнять отдельной серией маленьких commits после characterization.

## Этап 2: сначала обсудить, потом решать

| № | Приоритет | Задача | Почему не делать автоматически |
|---|---|---|---|
| 201 | P2 | [Typed commands между HTTP/MCP и services](201-typed-boundary-commands.md) | Широкая миграция API; выгодна только после стабилизации seams |
| 202 | P2 | [Пересмотреть JSON state и cross-store transactions](202-state-transaction-boundaries.md) | Архитектурное решение с migration/rollback риском |
| 203 | P2 | [Решить судьбу desktop и embedded agent runtime](203-runtime-lifecycle-decision.md) | Нужна продуктовая информация об активных пользователях |
| 205 | P2 | [Оценить декомпозицию deploy.sh](205-deploy-orchestration-decision.md) | Release path безопасен сейчас; rewrite легко ухудшит rollback |
| 206 | P2 | [Разделить AgentRunner, только если runtime сохраняется](206-split-agent-runner-if-retained.md) | Сначала требуется usage inventory и решение 203 |
| 207 | P2 | [Разделить attestation runner только при доказанной боли](207-split-attestation-runner-if-needed.md) | Исправный release guard не трогать только ради размера |
| 204 | P3 | [Оценить frontend build tooling](204-frontend-tooling-decision.md) | Bundler/framework может стоить дороже текущей проблемы |

## Явно не ставим задачами

- Переписать CRM целиком на новый framework.
- Заменить unittest только ради pytest.
- Перевести JSON state в PostgreSQL без измеренной проблемы и migration plan.
- Удалить compatibility names или scripts по одному поиску ссылок.
- Довести все функции до cyclomatic complexity ≤ 10.
- Добиться 100% line coverage.

Эти действия выглядят «современно», но не дают гарантированного ближайшего
результата и увеличивают риск регрессий.

## Независимый review backlog

- Первый проход саб-агента: 7,8/10. Были найдены цикл 000↔004,
  перегруженный P0, длинный dependency graph и смешанные runtime/finance/UI
  scopes.
- После одного alignment-pass: 9,1/10, blockers нет.
- Финальные major-замечания также учтены без третьего прохода: один конечный
  owner на exemption, честная per-artifact restore atomicity, отдельный print
  web task 021 и conditional attestation task 207.
