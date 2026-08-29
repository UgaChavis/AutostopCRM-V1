# AutoStop CRM: приоритизированный backlog технического долга

Дата последней большой проверки: 2026-08-29
Базовый commit: `e7f112e73427e2e017cc797040d47ff218e0ea85`
Ветка: `autostopcrm-v1`

## Актуальная исходная точка

- До подготовки этого плана локальный `HEAD`, `origin/autostopcrm-v1` и GitHub
  совпадали, а worktree был чистым. Workflow `33206487245` на этом exact SHA
  завершён успешно; текущие незакоммиченные изменения — только документы плана.
- `code_health_audit.py`: 394/394 tracked-файла классифицированы, size-ratchets
  34/34 и complexity-ratchets 2/2 проходят.
- Полный current-HEAD suite под branch coverage: 2 035 тестов, 34 ожидаемых
  skip, 519.734 s, `OK`. Текущая coverage — 79.61% при floor 78.50%; все 13
  global/critical thresholds проходят. Release backup/restore — 78.40% при
  floor 78.00%.
- Три pytest-style функции в двух `test_*.py` не собираются `unittest
  discover`; реальный обязательный baseline после исправления должен стать
  2 038 тестов без нового skip.
- Core browser smoke прошёл 12/12, full — 45/45, console/page/request errors
  отсутствуют. Stage-1 synthetic production-scale gate и локальный HTTP perf
  probe проходят без violations.
- Два заявленных локальных perf-инструмента неработоспособны: `perf_mcp.py`
  использует устаревший raw surface, а browser-path `perf_workflows.py` зависает
  в smoke-only отрицательном login helper. CI эти пути не запускает.
- Capability parity: 176 действий, gaps=0. Change-feed producer parity:
  101/101. Gateway публикует ровно 24 инструмента; attestation-контракт содержит
  46 CRM workflow operations. Числа 175/100/43 ниже — только исторические.
- Tracked tree: 394 файла, 10.72 MiB; 313 Python-файлов / 212 841 строк,
  runtime `src/` — 98 833 строки, tests — 81 311, scripts — 32 671, JavaScript
  — 22 632. Локальные ignored/generated artifacts занимают около 2.53 GiB.
- Самые крупные hotspots: `app_main_before_printing.js` — 20 639 строк;
  `CardService` — 11 122 строки / 404 метода; `attest_agent_gateway_v2.py` —
  9 498 строк; `AgentRunner` — 4 865; `create_mcp_server` — 3 104;
  `register_agent_gateway_v2` — 3 086.
- Strict Ruff inventory `C901,PLR0911,PLR0912,PLR0913,PLR0915` вырос с 433
  исторических до 532 сигналов. Это diagnostic inventory, не список из 532
  задач: блокируем рост в затрагиваемых hotspots и уменьшаем их по одному seam.
- Статический внутренний import graph: 137 модулей / 387 edges / 0 cycles.
  Основной долг — концентрация обязанностей и неявный mixin API, а не циклы.
- Явного tracked-мусора нет. До read-only production inventory потенциальными
  delete-candidates остаются только две one-off migration scripts из задачи
  017. Generated build outputs очищаются отдельно и никогда не смешиваются с
  Git-изменением.

## Нулевая волна перед структурными рефакторами

1. [Закрыть пробел collection и локального quality gate](016-close-test-collection-gap.md).
2. [Починить реальные MCP/browser performance smoke](015-repair-performance-smoke-contracts.md).
3. [Защитить runtime PNG в Docker build context](022-protect-docker-runtime-assets.md).
4. [Синхронизировать canonical repo docs и их audit](023-reconcile-docs-and-crm-skills.md).
5. [Отдельно актуализировать локальные CRM skills](024-reconcile-local-crm-skills.md).
6. После каждого независимого repo-исправления: focused regression -> полный
   relevant gate -> один небольшой commit. Публиковать green commit в GitHub и
   ждать hosted CI. Local-skill slice проверять и отчётно фиксировать отдельно;
   production не менять без отдельной команды владельца.

## Завершённая база

| № | Устойчивый результат | Проверяемое доказательство |
|---|---|---|
| 000 | Browser/PDF preflight проверяет Playwright, Chromium, Qt PDF, `pdfinfo` и `pdftotext` до `TempRuntime`; missing dependency детерминированно даёт exit 2, retry разрешён только для browser launch. | `browser_smoke_profiles.py`, `test_browser_smoke.py`, `f75d90f`; full 44/44 при закрытии. |
| 002 | Branch coverage разделяет runtime и release reports; 13 global/critical floors блокируют регресс, covered suite заменяет plain unit run в CI. | `.coveragerc`, `coverage_baseline.json`, `coverage_audit.py`, `d5eb246`; baseline/floor 78.82/78.50%. |
| 004 | Core — обязательный strict subset full на push/PR: synthetic temp-data readback и любой browser error fail closed; full остаётся ручным release gate. | `quality.yml`, `browser_smoke_profiles.py`, `f75d90f`, `d5eb246`; current core/full 12/12 и 45/45. |
| 006 | Immutable `RouteSpec` — единый HTTP policy source; compatibility views derived, unknown/incomplete/duplicate policy fail closed, public contract сохранён. | `route_registry.py`, `test_contracts.py`, `2324c92`; parity зелёные. |
| 007 | `_make_handler` сокращён с 1 352 до 136 строк на baseline 2026-08-29 и разделён на transport/auth/static/dispatch seams без нового framework/global state; truncated body fail closed, exception logs не раскрывают payload. | `server.py`, `test_api_transport_contracts.py`, `858bd8a`; потолок ≤150 соблюдён, exemption удалён. |

Задача 001 остаётся каноническим контрактом maintainability ratchets: 34/34
size и 2/2 complexity mappings имеют exact caps, причины и существующих
владельцев. Общая интеграция `6615143a` прошла hosted workflow `32935776774`;
production этим publish не обновлялся.

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
11. После включения режима движения к цели каждый законченный repo green-срез
    получает один логический commit и push в `origin/autostopcrm-v1`; следующий
    срез начинается после успешного hosted CI. Deploy этим не разрешается.
    Изменения user-level skills вне репозитория сначала получают recoverable
    backup и отдельный local audit; hosted CI их не видит и не подтверждает.
12. После каждой волны обновлять current metrics и статусы, а завершённые
    журналы уплотнять. Исторические цифры всегда помечать датой/commit.
13. Для structural/cleanup wave требовать измеряемое снижение хотя бы одного
    production/doc/skill hotspot или его ratchet. Добавленные regression tests
    не считать отрицательным ростом продукта.
14. Stop-the-line: unexplained test/coverage/parity failure, изменение public
    DTO/error/audit/feed contract, отсутствие production evidence для удаления
    migration/compatibility или необходимость работы с финансовыми данными.

## Этап 1: минимально достаточные изменения с максимальным ROI

Завершённые prerequisite IDs в столбце «Зависит от» относятся к таблице выше;
отдельные task-файлы сохранены только для активных задач и ratchet owners.

| № | Приоритет | Задача | Зачем сейчас | Зависит от |
|---|---|---|---|---|
| 001 | P0 | [Зафиксировать числовой maintainability ratchet](001-maintainability-ratchet.md) | **Выполнено 2026-08-23** | — |
| 015 | P0 | [Восстановить MCP/browser performance smoke](015-repair-performance-smoke-contracts.md) | Два документированных gate сейчас падают и не входят в CI | 001, 004 |
| 016 | P0 | [Закрыть test collection/local quality gaps](016-close-test-collection-gap.md) | **Collection и guard опубликованы (`33248712521`); F841 и единый local profile ещё открыты** | 001, 002 |
| 022 | P0 | [Защитить Docker runtime PNG](022-protect-docker-runtime-assets.md) | **Выполнено; hosted CI `33255765251` зелёный** | 001 |
| 023 | P0 | [Синхронизировать canonical repo docs](023-reconcile-docs-and-crm-skills.md) | **Срезы 1–3 опубликованы; финальный consolidation в этом срезе** | — |
| 024 | P0 | [Актуализировать локальные CRM skills](024-reconcile-local-crm-skills.md) | User-level skills содержат stale и небезопасные инструкции | 023 |
| 003 | P1 | [Разрезать монолитные test modules и fixtures](003-split-test-suites.md) | **Registrar test slices опубликованы; следующий split только вместе с production seam** | 001 |
| 005 | P1 | [Разрезать web asset source по доменам](005-split-board-web-assets.md) | Уменьшить 20.6k-строчный god-script без смены frontend stack | 004; свой test-slice из 003 |
| 008 | P1 | [Разрезать MCP tool registration по доменам](008-split-mcp-tool-registration.md) | **Attachment/shared-file registrars опубликованы; 98 raw / 24 public; server 3 551 / factory 3 104 строк** | 001; свой test-slice из 003 |
| 009 | P1 | [Разделить Gateway workflow executor](009-split-gateway-workflow-executor.md) | `workflow_guards.py` уже выделен; raw executor/verifier остаются hotspots | 001, 008; coverage 002 параллельно |
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

- Завершённая база 000/002/004/006/007 сведена в таблицу выше; отдельные журналы
  удалены после проверки всех 36 активных ratchet mappings и прямых ссылок.
- Волна 0 — 016, 015, 022, 023 и отдельный local-only slice 024. Сначала
  восстановить доверие к gates, Docker context и инструкциям; затем
  зафиксировать новый baseline.
- Волна 1 — следующий read-only registrar/test slice 008+003, первый JS slice
  005, docs/generated cleanup и read-only inventory 017.
- Волна 2 — 010, 011 и pure read-model slices 018. Каждый перенос уменьшает
  исходный module/class cap и сохраняет facade/DTO.
- Волна 3 — 009, 012–014, 019–021. Здесь выше business/security/release риск;
  каждую задачу выполнять отдельной серией маленьких commits после
  characterization.

## Контроль результата

На старте каждой волны фиксируются exact SHA и текущие значения; в конце
сравнивается один и тот же набор сигналов:

- task 016 добавляет ровно три ранее невидимых теста к baseline того же SHA;
  после неё guard не допускает новых необёрнутых `test_*`, а suite не уменьшается
  без отдельного объяснённого удаления; только объяснённые platform skip;
- runtime coverage не ниже 78.50% и все 13 critical floors зелёные;
- 24 public Gateway tools, capability/change-feed gaps=0 и exact contract
  counts, если задача явно не меняет surface;
- ни одного нового strict Ruff сигнала в затронутых production paths;
- каждый structural slice уменьшает строки/методы/complexity или exact cap
  исходного hotspot; перенос без уменьшения не считается завершением долга;
- по rolling wave объём runtime + active repo docs не растёт. Test LOC может
  временно вырасти только ради characterization/regression;
- tasks 023/024 удаляют все перечисленные stale/duplicate patterns, проходят
  semantic safety checklist и уменьшают каждый затронутый docs/skill total
  относительно зафиксированного pre-slice baseline. После среза exact totals
  становятся новыми caps; полезные guardrails не удаляются ради процента;
- consolidation завершённых debt-журналов должен давать net-сокращение после
  переноса обязательных итогов и проверки owner references; quota в KiB нет;
- локальные `dist/build/htmlcov` очищаются отдельной recoverable операцией
  после подтверждения release artifact и могут освободить около 752 MiB.
  Удаление `release`/`.venv` не является автоматической частью goal.

После каждой волны итоговый отчёт содержит commits/SHA, удалённые и добавленные
строки/файлы, новые caps, все выполненные и пропущенные gates, hosted CI URL,
известные риски и подтверждение, что production не менялся. По завершении всего
backlog формируется deploy handoff; сервер обновляется только после отдельной
команды владельца.

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
- Review плана 2026-08-29 нашёл два блокера: отсутствующий Docker build gate и
  смешение repo/user-level skills. Они закрыты задачей hosted build без publish
  в 022 и отдельным recoverable local-only контуром 024.
