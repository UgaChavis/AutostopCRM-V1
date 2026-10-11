# Исправление замечаний документации Manager и CRM — 2026-10-11

Это датированный исторический отчёт о проверке и публикации исходного кода.
Действующие инструкции находятся в [AGENTS.md](../../AGENTS.md),
[runbook](../OPERATIONS_RUNBOOK.md) и
[контракте конструктора](../agent/module_operations/manager_structure.md).
Отчёт не разрешает deployment, business actions, сообщения или изменение
сохранённого рабочего графа.

## Исходное состояние и охват

CRM проверяется в отдельном чистом worktree от опубликованного
`30c6cb7c3e890e84593e34b9ac9fbf3df6961729`. Исходный checkout и работающая
служба не обновляются. Manager-основа перед исправлениями —
`53ea64c2149d10039037475da00e251fa26bb9f1`.

Каждый полный цикл включает все 24 исходных Markdown: шесть основных
документов, пять карточек операций, 11 карточек владения техническим долгом,
проектный скилл и предыдущий исторический отчёт. Этот отчёт становится 25-м
Markdown и тоже проверяется на ссылки. Датированные отчёты исключены из
runtime image; они не заменяют текущие инструкции.

Связанный охват: CommonMark audit и renderer, API/Gateway схемы конструктора,
immutable bundle, portable template и infrastructure blueprint. Корпус
конструктора содержит 233 инструкции: 15 модулей, 122 карточки операций и по
48 инструкций template и blueprint. Каталог проверяется по точной ревизии и
через воспроизведение из sealed Manager, а не по mutable checkout.

## Подтверждённые исправления

Manager устраняет скрытую инициализацию в чтении checkpoint, исправляет шесть
описаний сетевых/составных операций, фактический формат VIN-фото и тип SHA
журнала M2. Каталоги и генерируемые карточки обновляются через источники.
CRM получает экспорт точного опубликованного Manager-коммита после его CI.

Полное перечитывание CRM дополнительно выявило противоречие в
[README](../../README.md#manager-structure-constructor) и
[API guide](../../API_GUIDE.md#manager-structure-constructor): тексты утверждали,
что сохранённые SVG paths всегда orthogonal. Текущая реализация поддерживает
manual quadratic/cubic curves. Оба текста теперь различают автоматически
построенные orthogonal routes и сохранённые manual paths, включая перенос
концов и соседних control points при перемещении модуля. Runtime/API не меняется.
Проверка использует существующий browser сценарий
`test_manual_curve_controls_canvas_and_api_screen_readback`: он сохраняет cubic
path, сверяет API и DOM, затем двигает узел и проверяет перенос control point.

Весь исходный Gateway-каталог сверяется с SDK: 24/24 tool names и каждый
required/optional property set совпадают. Базовый name/inputSchema fingerprint:
`b10378098f31e0416c5a0a2159957befcd1a6c8ecc56002d32cf388421e44913`.
Схемы регистрируются с synthetic client и disposable DB target без вызова tools;
БД не создаётся. Исторический fingerprint 2026-09-25 в карточке явно датирован
и не выдаётся за текущий установленный контракт.

## Итерации и проверки

| Цикл | Полный охват | Результат |
| --- | --- | --- |
| 1 | Все 24 исходных Markdown, новый отчёт, bundle/template/blueprint и browser contract | Исправление manual-path описаний; Manager [0df106d](https://github.com/UgaChavis/AutostopManager/commit/0df106dae6779657f2c01bf200f1cdbf9dab468e), его [CI](https://github.com/UgaChavis/AutostopManager/actions/runs/38118760767) прошёл; CRM получает exact pin в коммите с этим состоянием отчёта |
| 2 | Повторный полный реестр и сверка с реализацией | Дополнительные замечания открыты; результат фиксируется после следующего полного цикла |
| 3 | Независимый review точной исправленной ревизии | Приёмка фиксируется после последнего исправления |

До экспорта нового Manager начальный CommonMark audit всех 24 исходных
документов проверил 133 видимые ссылки без ошибок. Повторный audit с новым
историческим отчётом тоже прошёл. Baseline bundle воспроизведён из sealed
Manager; 10 focused asset/release/Gateway tests прошли. Полный browser corpus
233/233 инструкции и 1 432 перехода совпал с независимым CommonMark oracle,
page errors и внешних запросов нет. Эти результаты относятся к baseline pin;
проверки каталога повторяются на новом точном опубликованном Manager.
После изменений повторяются весь актуальный реестр, exact producer parity,
generated JS и обязательный
`pwsh -NoLogo -NoProfile -File scripts/run_checks.ps1 -Profile ci`.
Отдельные checks не заменяют exact-SHA GitHub CI.

Browser plugin отсутствует; используется проектный Playwright Chromium.
Первый preflight обнаружил отсутствующий Chromium cache и остановился до
запуска сценариев. Browser версии, требуемой уже установленным проектным
Playwright, восстановлен; dependency pins не менялись. 12/12 targeted browser
сценариев прошли без пропусков за 68,573 s; desktop/mobile и viewer screenshots
проверены визуально. Browser проверки используют
отдельную локальную CRM с synthetic data: exact revision links, CommonMark
literals, unsafe URL blocking, owner/viewer, keyboard/focus, desktop/mobile,
catalog/session/version races и manual curve readback. Live providers, CRM
business records и реальные сообщения в этих сценариях не используются.

## Полный реестр исходных CRM Markdown

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

## Публикация и последующий выпуск

Порядок каждого цикла: проверенный Manager-коммит → обычный push в
`AutostopManager` → exact-SHA CI → immutable export → CRM проверки → отдельный
CRM-коммит → обычный push в `autostopcrm-v1` → exact-SHA CI и независимое чтение.
Source publication не устанавливает новую версию работающей службы.

Каталог первого цикла привязан к
`0df106dae6779657f2c01bf200f1cdbf9dab468e`, content hash
`5b0cde9ef1e5ba382749bfb5ed758742dbcab3ad3731eb37d0a104ff40cc2bea`.
Portable template/blueprint обновлены из sealed Manager через штатный генератор
по свежему read-only graph version 164: 48 nodes, 37 relations. Независимое
сравнение каждого поля, кроме instruction, подтвердило неизменную геометрию,
IDs, paths и связи. Portable artifacts не содержат commissioning records.
Offline patch первого цикла содержит A4/A5/E9/E15; live apply не выполняется.
Число и набор будущих изменений определяются заново после окончательных
исправлений, этот patch не является итоговым выпуском.

Следующий review уже выявил дополнительные вопросы: контекст sourcing offer,
выбор источника evidence, старые обозначения модулей, неверный UTF-8 в checkpoint
и служебные записи automation registry при чтении статуса. Они остаются открыты
для второго цикла; первый цикл не утверждает чистую итоговую приёмку.

Комплект согласованного выпуска содержит точные Manager/CRM SHA, content hash,
sealed producer, свежий технический graph snapshot, instruction-only patch и
инструкцию отката. Preview/apply requests готовятся offline; применение требует
нового полного чтения, текущей version, owner/CAS/idempotency и независимого
readback. Canvas, IDs, ручные paths, связи, права и commissioning records
сохраняются. Старую подготовку графа нельзя повторно применять после изменения
Manager или live version. Выпуск следует [runbook](../OPERATIONS_RUNBOOK.md).
