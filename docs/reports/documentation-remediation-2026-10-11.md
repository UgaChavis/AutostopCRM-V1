# Исправление замечаний документации Manager и CRM — 2026-10-11

Это датированный отчёт о проверке и публикации исходников. Действующие
инструкции находятся в [AGENTS.md](../../AGENTS.md),
[runbook](../OPERATIONS_RUNBOOK.md) и
[контракте конструктора](../agent/module_operations/manager_structure.md).
Установленные службы и сохранённый рабочий граф остаются на своей ревизии;
согласованный выпуск подготовлен отдельно.

## Основа и полный охват

Работа выполнена в отдельных worktree от опубликованных CRM
`30c6cb7c3e890e84593e34b9ac9fbf3df6961729` и Manager
`53ea64c2149d10039037475da00e251fa26bb9f1`. Исходные незакоммиченные изменения
сохранены. Каждый цикл охватывает весь реестр, а после последнего исправления
требует полного независимого перечитывания точной опубликованной ревизии.

Реестр Manager содержит 222 записи: инструкции модулей, скиллы, справочники,
карточки инструментов, JSON-каталоги и исторические отчёты; tracked Markdown —
204, действующих документов — 212. Проверены все 122 automotive-карточки и
70 зарегистрированных native tool schemas/annotations. Настоящие SDK models
приняли 118 валидных примеров и отвергли 118 невалидных; четыре planned
extensions не вызываются. Проверка внешних установленных skill entrypoints
отделена от переносимого CI; каталог фиксирует выбранные имена, descriptions,
frontmatter и hashes, а не произвольные сторонние skill bodies.

В CRM каждый цикл включает все 24 исходных Markdown и настоящий 25-й отчёт:
шесть основных документов, пять карточек операций, 11 карточек технического
долга, проектный скилл и два исторических отчёта. Датированные отчёты исключены
из runtime image и не заменяют текущие инструкции.

Связанный охват: генераторы, общий CommonMark parser и renderer, URL/anchor/
line checks, API/Gateway схемы, immutable bundle, portable template и
infrastructure blueprint. Browser corpus содержит 233 инструкции: 15 модулей,
122 карточки операций и по 48 инструкций template и blueprint. Каталог
воспроизводится из sealed Manager точной ревизии.

## Три полных цикла

| Цикл | Полный review и исправления | Публикация и проверки |
| --- | --- | --- |
| 1 | Полный реестр Manager/CRM, навигация, генерация, эффекты инструментов; CRM README/API guide исправляют описание manual curves | Manager [0df106d](https://github.com/UgaChavis/AutostopManager/commit/0df106dae6779657f2c01bf200f1cdbf9dab468e), [exact CI](https://github.com/UgaChavis/AutostopManager/actions/runs/38118760767); CRM [d70b187](https://github.com/UgaChavis/AutostopCRM-V1/commit/d70b187cb72fca18c13c1835500938be66d34cc7), [exact CI](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38122827247); полный локальный набор и независимое чтение завершены |
| 2 | Весь реестр повторно сопоставлен с реализацией; Manager S5–S19, CRM legacy labels S7 и registry metadata effect S9; S20–S23 переданы третьему циклу | Manager [775fdc8](https://github.com/UgaChavis/AutostopManager/commit/775fdc8926202e8dd7cd87262d0e26b98d1dc380), [exact CI](https://github.com/UgaChavis/AutostopManager/actions/runs/38125740732); CRM [c5f2b59](https://github.com/UgaChavis/AutostopCRM-V1/commit/c5f2b592ab3d09f29ad84b3ed4bc2f4fc48dc99a), [exact CI](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38128211412); все пять CRM jobs и независимое чтение прошли |
| 3 | Независимый полный review после последних исправлений; Manager S20/S21 и release kit, CRM selected-source preflight S22 и readonly catalog capability S23 | Manager [ba61cfa](https://github.com/UgaChavis/AutostopManager/commit/ba61cfa17690f496fae9ab7ce88e09ee2971b040), [exact CI](https://github.com/UgaChavis/AutostopManager/actions/runs/38128812372): прошёл. CRM публикует эту версию отчёта вместе с точным pin после обязательного полного `Profile ci`; exact-SHA CI и итоговая независимая приёмка привязываются к её коммиту после публикации |

Manager-исправления и их source counterparts подробно описаны в
[отчёте Manager этой ревизии](https://github.com/UgaChavis/AutostopManager/blob/ba61cfa17690f496fae9ab7ce88e09ee2971b040/docs/reports/documentation-remediation-2026-10-11.md).
Первый цикл исправил checkpoint read initialization, описания составных и
сетевых операций, формат VIN-фото, тип SHA и генерируемые каталоги. Второй
уточнил sourcing offer, выбор evidence source, module labels, повреждённый
UTF-8, lifecycle/cleanup VIN и записи технического automation registry.
Третий согласует SQL enqueue с отдельным network worker и учитывает lifecycle
ошибки/housekeeping до disabled error в верхнем VIN reference.

В CRM [README](../../README.md#manager-structure-constructor) и
[API guide](../../API_GUIDE.md#manager-structure-constructor) различают
автоматические orthogonal routes и сохранённые manual quadratic/cubic paths.
Существующий browser сценарий сохраняет cubic path, сверяет API/DOM, двигает
узел и проверяет перенос конца и соседнего control point.

S7 отличает legacy CRM E8 contract от текущего Manager E15, а прежний Store E7
от текущих F1/F2. S9 объясняет: help rendering не посылает control command,
status может инициализировать/обновлять Manager technical registry metadata.
Capability names, inputSchema, credential keys, timeouts и полномочия сохраняются.
110 существующих Gateway/web-safety/credential tests, Ruff и CommonMark audit
прошли до pin второго цикла; code AST меняет только описательные строки.

S22 исправляет release prerequisite: `AUTOSTOP_MANAGER_SOURCE_DIR` выбирает
проверяемый clean Manager checkout на основной ветке с exact fetched remote
SHA. Существующий dirty checkout сохраняется; можно подготовить отдельный
clean clone и его `.venv`. `AUTOSTOP_MANAGER_RELEASE_PYTHON` выбирает preflight
interpreter, CRM `.env` может переопределять process settings. Fixed native
interpreter, `.env`, credential file и постоянная БД проверяются отдельно.
Legacy Store readiness использует permanent DB в read-only режиме. Согласованы
runbook, CLI card и synthetic schema examples; deploy code не меняется.

S23 добавляет пропущенное `api:/api/manager_structure/tool_catalog`:
authenticated readonly capability со строгим пустым `arguments: {}`.
[MCP guide](../../MCP_GUIDE.md#manager-structure-through-raw-capabilities)
перечисляет graph read, catalog read и owner apply; dialogue sequence явно
называет graph-read/apply. Owner permission, CAS/version, outer idempotency key
и независимый readback сохраняются. SDK публично регистрирует прежние 24 tools.

## Доказательства проверок

Первый CRM полный локальный профиль завершился с direct exit 0:
3 065 runtime tests, один пропуск optional Docker packaging, 36 backup/restore
checks, coverage 82,11%. Первый запуск превысил performance-порог; источники и
пороги сохранены, повторные измерения и полный профиль прошли. GitHub CI exact
commit выполнил Docker packaging и все release jobs. Независимо перечитаны
25 Markdown, 3 155 строк, 165 видимых ссылок и все 601 source hashes; sealed
producer/template parity и browser corpus 233/233, 1 432 links прошли.

Второй Manager полный release gate: 5 818 tests за 597,46 s, coverage 87%,
E2 branch coverage 87,87%, все 531 source hashes совпали с frozen manifest.
Второй CRM полный `Profile ci` завершился с direct exit 0 и final marker:
3 065 runtime tests за 1 559,073 s (один optional Docker skip), 36 backup tests
за 6,450 s, coverage 82,10%, critical backup coverage 80,57%.
Stage1 использовал прежние 20 samples / 2 warmups и все прежние thresholds;
p95 feed read 4,4 ms, replay 0,7 ms, backend write 194,1 ms, storage 232,7 ms.
Все 601 source hashes до/после и опубликованного archive совпали. Exact CI
выполнил `docker-runtime-assets`, `unit`, `browser-performance`, `static`,
`quality` успешно. Новый non-author reviewer перечитал все 25 Markdown,
3 215 строк, 172 links и 233 rendered instructions / 1 437 links; кроме
принятых для третьего цикла S22/S23 новых замечаний не нашёл.

Третий Manager полный release gate повторён после последних исправлений:
5 818 tests за 655,87 s, coverage 87%, E2 branches 87,87%; все 531 source hashes
сохранены. Новый независимый полный review охватил 222/222 записей, все 122
карточки, 70 native schemas/annotations и SDK примеры 118 accepted / 118
rejected. Источники выполнения побайтно совпадают со вторым циклом;
исправлены четыре Markdown. Синтетический J1 start проверил queued / one query /
zero documents без network search; отдельно worker выполнил mock search.

Первый локальный CRM профиль третьего цикла остановлен после ошибки
существующего deployment test: проверка требовала две точные корректные
формулировки runbook. Они восстановлены, selected-source prerequisites
сохранены; runtime, tests и thresholds не менялись. Незавершённый запуск
не засчитывается: focused RED/GREEN и повтор полного профиля после последней
правки обязательны.

CRM public SDK name/inputSchema fingerprint остаётся
`b10378098f31e0416c5a0a2159957befcd1a6c8ecc56002d32cf388421e44913`;
annotations проверяются отдельно. 24/24 required/optional property sets в
карточке Gateway совпадают с SDK. Регистрация использует synthetic client,
tools не вызываются, disposable DB не создаётся. Исторические fingerprints
явно датированы и не выдаются за текущий installed contract.

Дополнительный независимый raw/API/rights review проверил 94 pure cases:
три constructor routes, 11 operations, CAS/key/status limits, 12 synthetic
readbacks и 46 CRM workflow operations. Никакие сообщения, live providers,
бизнес-записи или служебные journals не затрагиваются.

Browser plugin отсутствует; используется проектный Playwright Chromium.
Сценарии используют отдельную локальную CRM с synthetic state: exact revision
links и переходы, CommonMark literals, опасные URL, owner/viewer, keyboard/focus,
desktop/mobile, catalog/session/version races, manual curves. Весь corpus
сверяется с независимым CommonMark oracle; внешние запросы блокируются.
Desktop/mobile/viewer screenshots проверены визуально. Dependency pins не
менялись. Генерация, проверки ссылок/anchors/line numbers, Ruff, code health,
coverage, capability/feed parity, browsercore и performance входят в полный CI.

## Полный реестр CRM Markdown

| Документ | Назначение |
| --- | --- |
| [AGENTS.md](../../AGENTS.md) | действующая инструкция |
| [API_GUIDE.md](../../API_GUIDE.md) | действующий HTTP контракт |
| [CHATGPT_CONNECTOR_SETUP.md](../../CHATGPT_CONNECTOR_SETUP.md) | настройка клиента и OAuth |
| [MCP_GUIDE.md](../../MCP_GUIDE.md) | действующий MCP контракт |
| [README.md](../../README.md) | архитектура и навигация |
| [OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md) | проверки, выпуск и восстановление |
| [module_operations/README.md](../agent/module_operations/README.md) | каталог операций |
| [crm_commands.md](../agent/module_operations/crm_commands.md) | CLI entrypoints и эффекты |
| [crm_gateway.md](../agent/module_operations/crm_gateway.md) | Gateway, schemas и полномочия |
| [crm_runtime.md](../agent/module_operations/crm_runtime.md) | runtime и зависимости |
| [manager_structure.md](../agent/module_operations/manager_structure.md) | конструктор и документация |
| [Предыдущий review](documentation-review-2026-10-10.md) | исторический отчёт |
| [001-maintainability-ratchet.md](../../tech_debt/001-maintainability-ratchet.md) | карточка обслуживания кода |
| [003-split-test-suites.md](../../tech_debt/003-split-test-suites.md) | карточка обслуживания кода |
| [008-split-mcp-tool-registration.md](../../tech_debt/008-split-mcp-tool-registration.md) | карточка обслуживания кода |
| [009-split-gateway-workflow-executor.md](../../tech_debt/009-split-gateway-workflow-executor.md) | карточка обслуживания кода |
| [012-extract-repair-order-lifecycle.md](../../tech_debt/012-extract-repair-order-lifecycle.md) | карточка обслуживания кода |
| [013-payroll-calculators.md](../../tech_debt/013-payroll-calculators.md) | карточка обслуживания кода |
| [014-split-print-module-service.md](../../tech_debt/014-split-print-module-service.md) | карточка обслуживания кода |
| [018-split-snapshot-service.md](../../tech_debt/018-split-snapshot-service.md) | карточка обслуживания кода |
| [019-finance-audit-planner.md](../../tech_debt/019-finance-audit-planner.md) | карточка обслуживания кода |
| [021-split-print-embedded-web-module.md](../../tech_debt/021-split-print-embedded-web-module.md) | карточка обслуживания кода |
| [206-split-agent-runner-if-retained.md](../../tech_debt/206-split-agent-runner-if-retained.md) | карточка обслуживания кода |
| [CRM skill](../../tools/codex/skills/autostopcrm-maintain/SKILL.md) | проектный скилл |
| [Настоящий отчёт](documentation-remediation-2026-10-11.md) | исторический отчёт третьего цикла |

## Каталог и комплект согласованного выпуска

Каталоги первых двух циклов связаны соответственно с Manager
`0df106dae6779657f2c01bf200f1cdbf9dab468e` / content hash
`5b0cde9ef1e5ba382749bfb5ed758742dbcab3ad3731eb37d0a104ff40cc2bea` и
`775fdc8926202e8dd7cd87262d0e26b98d1dc380` / content hash
`8f3188a1e292e1b62ea46e6c0a356b6ef887227ed1fe6b98dd7a9162a8f734ca`.
Итоговый каталог привязан к `ba61cfa17690f496fae9ab7ce88e09ee2971b040`,
content hash `8f3188a1e292e1b62ea46e6c0a356b6ef887227ed1fe6b98dd7a9162a8f734ca`. Producer parity требует точный sealed
source и независимо воспроизводит export; relative links разрешаются от
исходного Markdown и связываются с ревизией этого bundle.

Portable template/blueprint обновляются через штатный генератор по source-typed
technical graph version 164: 48 nodes, 37 relations. Каждое поле, кроме
instruction, независимо сравнивается: IDs, canvas, geometry, paths, styles и
relations сохраняются. Tool statuses, commissioning records и raw private graph
не публикуются. Instruction-only offline preview не выполняет live apply.
Набор окончательных изменений определяется после последнего pin; перед
реальным выпуском нужны новый полный read и текущая graph version.

[Комплект выпуска Manager](https://github.com/UgaChavis/AutostopManager/blob/ba61cfa17690f496fae9ab7ce88e09ee2971b040/docs/agent/references/deployment.md#комплект-для-выпуска-документации)
содержит порядок выбора clean sources, exact Manager/CRM SHA и CI, immutable
bundle/content hash, persistent/native dependency checks, не менее 30 GiB плюс
release reserve, свежий технический graph snapshot, instruction-only patch и
rollback ограничения. Apply использует owner/CAS/idempotency и независимый
readback. Старый preview нельзя повторно применять после изменения Manager или
live version. Выпуск выполняется по [CRM runbook](../OPERATIONS_RUNBOOK.md).

## Завершение третьего цикла

Порядок каждого цикла: Manager gates → отдельный commit → обычный main push →
exact-SHA CI → sealed export → полный CRM registry/corpus/gates → отдельный
CRM commit → обычный main push → exact-SHA CI и независимое перечитывание.
При изменении remote ref требуется согласование и повтор затронутых проверок.

CRM коммит с этой версией отчёта допускается только после direct exit 0 полного
`pwsh -NoLogo -NoProfile -File scripts/run_checks.ps1 -Profile ci` на неизменном
source manifest. После публикации точный SHA, все обязательные GitHub jobs и
последняя независимая проверка фиксируются во внешних immutable acceptance
proofs и итоговом отчёте задачи. Отчёт не выдумывает собственный commit SHA или
результат ещё не завершённого CI. Любая непроверенная обязательная часть
остаётся открытой; новые замечания требуют следующего полного цикла.
