# Восстановление документации Manager и навигации CRM — 2026-10-10

Это датированный исторический отчёт. Действующие инструкции CRM находятся в
[AGENTS.md](../../AGENTS.md), [runbook](../OPERATIONS_RUNBOOK.md) и
[описании конструктора](../agent/module_operations/manager_structure.md).
Этот отчёт не задаёт клиентский процесс, не разрешает business actions и не является deployment.

## Охват и исходное состояние

Manager: весь исходный реестр 215 материалов и добавленный исторический отчёт,
итого 216: 203 Markdown, 12 JSON и один UI YAML. Все 212 текущих материалов graph,
76 ручных инструкций/справочников, 124 генерируемых Markdown и три historical/draft
Markdown прошли полные повторные проверки. Полный исходный реестр и находки:
[Manager review](https://github.com/UgaChavis/AutostopManager/blob/53ea64c2149d10039037475da00e251fa26bb9f1/docs/reports/documentation-review-2026-10-10.md).

CRM: все 23 исходных Markdown и этот исторический отчёт; связанный renderer,
assembler, Markdown audit, generator, bundle, template и infrastructure blueprint.
Общий корпус UI — 233 записи: 15 модулей и 122 операции каталога, по 48 инструкций
template и blueprint. Все записи сравниваются с общим CommonMark oracle.

Исходный Manager GitHub SHA: `bb1172845036aa28ad32b893e09152d61b853fba`.
Исходный CRM SHA: `d70909d7eb02bbb9b8a7d0cc7a6b763f1d6703cb`.
Работа выполнялась в отдельных worktrees. Dirty Manager checkout на `496ee3d…`
и исходный CRM checkout сохранены. На исходном live-срезе CRM graph version 164:
48 узлов, 37 связей, каталог Manager `bb1172845036…`. Этот срез не обновлялся.

## Итерации и публикация

| Итерация | Manager main `AutostopManager` | CRM main `autostopcrm-v1` | Результат |
| --- | --- | --- | --- |
| 1 | [bb1490d](https://github.com/UgaChavis/AutostopManager/commit/bb1490d35c9bd258c77b44d236634d1d76da848e) | [3ca93a6](https://github.com/UgaChavis/AutostopCRM-V1/commit/3ca93a6bdb4dd83fbc6eb42777312d22f5ddd780) | полный реестр, ссылки, каталоги, safe DOM и revision pin |
| 2 | [da9ac13](https://github.com/UgaChavis/AutostopManager/commit/da9ac13094c14b5e93b138ce6572870fa4074014) | [2065ec5](https://github.com/UgaChavis/AutostopCRM-V1/commit/2065ec533398d4fa1fe8de158cb18efb1d6f4c42) | параметры/контракты, CommonMark audit, A5 pointer и подтверждённые literal edge cases |
| 3 | [53ea64c](https://github.com/UgaChavis/AutostopManager/commit/53ea64c2149d10039037475da00e251fa26bb9f1) | [e852313](https://github.com/UgaChavis/AutostopCRM-V1/commit/e852313d8744ffc1aeffb3768de68fa11227a190) | независимый полный review, общий browser parser и согласованный final bundle; локальный профиль прошёл, Docker CI обнаружил ошибку упаковки |
| 4 | повторный полный review опубликованного `53ea64c…`, без изменений Manager | коммит, содержащий эту версию отчёта | исключение исторических отчётов из runtime image, повторная проверка всего корпуса и Docker packaging |

Каждая публикация использует отдельный commit и обычный push. Remote SHA и CI
сверяются по точному коммиту; independent readback повторяет весь реестр.

В первой CRM итерации появились кликабельные canonical/relative ссылки из всех
модулей, ссылки на карточки и immutable Manager revision. Сохранены права,
редактор, геометрия, commissioning marks и API. Вторая использует общий backend
Markdown parser для ссылок, якорей и CR/LF file:line границ; A5 содержит относительные
ссылки без замороженных counts/SHA.

Новый независимый reviewer после второй итерации подтвердил один P2 класс:
ручной browser regex терял валидные multiline/reference/list links и делал
literal HTML/reference definitions кликабельными. Независимый корпус из 26
случаев воспроизвёл 15 несовпадений count/label. Третья итерация использует
локальный pinned CommonMark parser, safe DOM и прежний URL guard; regression
корпус и полный текущий набор повторно проверяются после последнего исправления.
Независимый review также выявил пропуск link audit во вложенных исторических
отчётах: их классификация принимала вложенные пути, а discovery обходил только
первый уровень. Обход включает все уровни; regression проверяет root-level и два
уровня вложенности, обнаружение битых local/Manager links и исправленный target.
Проверка cross-repository links теперь разрешает исходный путь до чтения и
отклоняет symlink за пределы CRM; отдельный regression подтверждает отсутствие
чтения внешнего файла для общих инструкций, root/nested reports и blueprint JSON.

GitHub Docker CI третьей CRM итерации обнаружил отдельную ошибку: вложенный
исторический отчёт попадал в image и ссылался на проектный скилл, исключённый
из runtime context. Четвёртая итерация явно исключает `docs/reports/` из Docker
context. Исторические GitHub отчёты по-прежнему полностью проверяются в source
audit; canonical runtime документы и их ссылки остаются в образе. Проверка
ссылок не ослаблена. Упаковка проверяется настоящим Docker build и штатным
`scripts/docs_audit.py` внутри отдельного контейнера без production data.

HTML не исполняется; code и image literals не создают переходы. Небезопасные URL
и абсолютные host paths остаются текстом. Относительные пути разрешаются от
исходного Manager документа на exact revision. Line suffix превращается в `#Lline`;
обычный fragment сохраняется. При неизвестном исходном документе относительные ссылки остаются текстом;
явные безопасные HTTP(S) доступны. Права owner/viewer, session/version guards и ключ
кеша `(schema, content_hash, source_revision)` сохранены.

## Проверки

Manager полные локальные gates: 5 597 / 5 651 / 5 684 теста, без пропусков;
coverage 87%, E2 branch coverage 87,87% (1 021/1 162). Ruff, форматирование,
mypy, doctor, automotive registry, 122 карточки и portable generator проходят.
Полный host check отдельно: 37 навыков, 244 входа = 212 project + 32 external,
output byte matches. Итоговые независимые reviewers перечитали опубликованный
`53ea64c…`: 216/216 материалов, 1 490 links, подтверждённых ошибок нет.
Все 70 native inputSchemas, 43 PartsAPI methods и 24 CRM declarations согласованы;
внешний API формат не меняется.

Manager GitHub CI:
[итерация 1](https://github.com/UgaChavis/AutostopManager/actions/runs/38034563738),
[итерация 2](https://github.com/UgaChavis/AutostopManager/actions/runs/38037049324),
[итерация 3](https://github.com/UgaChavis/AutostopManager/actions/runs/38038858508).
Все прошли на exact SHA: 5 513 / 5 567 / 5 600 tests, по 84 skipped.
Пропуски относятся к root/ownership/installer/duty и локальным OCR/PDF возможностям;
весь этот набор выполнен локально. Причины опубликованы через `-ra`.

CRM для каждого изменённого source проходит обязательный
`pwsh -NoLogo -NoProfile -File scripts/run_checks.ps1 -Profile ci` с прямым
readback кода завершения PowerShell и immutable source fingerprint.
Это включает runtime/backup-restore tests, coverage, Ruff, docs audit, JS,
automotive producer parity, capability/readback evidence, code-health и
browser/performance gates. Скриншоты и test data остаются только в tasktemp.
[CI первой CRM итерации](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38038055175)
прошёл. Локальные первые два CRM профиля: 3 047 / 3 057 runtime tests,
по 36 backup/restore tests, coverage 82,11%; все без пропусков.
[CI второй CRM итерации](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38041197802)
прошёл на точном SHA `2065ec5…`. Третий локальный профиль прошёл:
3 064 runtime tests и 36 backup/restore tests без пропусков, coverage 82,11%.
[CI третьей CRM итерации](https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/38043751135)
выявил описанную ошибку packaged documentation и не считается успешным.
Окончательный результат принимается только после полного локального профиля,
успешных всех GitHub CI jobs точного CRM коммита четвёртой итерации и полного
независимого перечитывания опубликованной ревизии после последнего исправления.

Реальный browser тестирует clean/editor view, owner/viewer, focus/popup,
relative revision links, опасные URL, session/cache races и Markdown fixtures.
Использован проектный Playwright Chromium: Browser plugin не был доступен.
Сценарии и записи синтетические; сообщений и реальных business writes нет.
Итоговый exact-SHA independent receipt фиксируется после публикации отчёта.

## Полный реестр CRM документов

Все 23 исходных документа перечитаны в каждой итерации; этот отчёт — 24-й.
Датированные отчёты проверяются на ссылки отдельно от текущих инструкций.
Они публикуются в GitHub и исключены из runtime image.
Карточки `tech_debt` описывают остающуюся работу по обслуживанию кода.

| Документ | Назначение |
| --- | --- |
| [AGENTS.md](../../AGENTS.md) | действующая инструкция |
| [API_GUIDE.md](../../API_GUIDE.md) | действующая инструкция |
| [CHATGPT_CONNECTOR_SETUP.md](../../CHATGPT_CONNECTOR_SETUP.md) | действующая инструкция |
| [MCP_GUIDE.md](../../MCP_GUIDE.md) | действующая инструкция |
| [README.md](../../README.md) | действующая инструкция |
| [docs/OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md) | действующая инструкция |
| [docs/agent/module_operations/README.md](../agent/module_operations/README.md) | действующая инструкция |
| [docs/agent/module_operations/crm_commands.md](../agent/module_operations/crm_commands.md) | действующая инструкция |
| [docs/agent/module_operations/crm_gateway.md](../agent/module_operations/crm_gateway.md) | действующая инструкция |
| [docs/agent/module_operations/crm_runtime.md](../agent/module_operations/crm_runtime.md) | действующая инструкция |
| [docs/agent/module_operations/manager_structure.md](../agent/module_operations/manager_structure.md) | действующая инструкция |
| [tech_debt/001-maintainability-ratchet.md](../../tech_debt/001-maintainability-ratchet.md) | карточка обслуживания кода |
| [tech_debt/003-split-test-suites.md](../../tech_debt/003-split-test-suites.md) | карточка обслуживания кода |
| [tech_debt/008-split-mcp-tool-registration.md](../../tech_debt/008-split-mcp-tool-registration.md) | карточка обслуживания кода |
| [tech_debt/009-split-gateway-workflow-executor.md](../../tech_debt/009-split-gateway-workflow-executor.md) | карточка обслуживания кода |
| [tech_debt/012-extract-repair-order-lifecycle.md](../../tech_debt/012-extract-repair-order-lifecycle.md) | карточка обслуживания кода |
| [tech_debt/013-payroll-calculators.md](../../tech_debt/013-payroll-calculators.md) | карточка обслуживания кода |
| [tech_debt/014-split-print-module-service.md](../../tech_debt/014-split-print-module-service.md) | карточка обслуживания кода |
| [tech_debt/018-split-snapshot-service.md](../../tech_debt/018-split-snapshot-service.md) | карточка обслуживания кода |
| [tech_debt/019-finance-audit-planner.md](../../tech_debt/019-finance-audit-planner.md) | карточка обслуживания кода |
| [tech_debt/021-split-print-embedded-web-module.md](../../tech_debt/021-split-print-embedded-web-module.md) | карточка обслуживания кода |
| [tech_debt/206-split-agent-runner-if-retained.md](../../tech_debt/206-split-agent-runner-if-retained.md) | карточка обслуживания кода |
| [tools/codex/skills/autostopcrm-maintain/SKILL.md](../../tools/codex/skills/autostopcrm-maintain/SKILL.md) | проектный скилл |

## Подготовленный каталог и последующий выпуск

[Bundle](../../src/minimal_kanban/web_app_assets/source/automotive_tool_catalog.json)
привязан к Manager `53ea64c2149d10039037475da00e251fa26bb9f1`,
content hash `8b7ce5d77837a6b38c7af975f470d90e9617cd89fc175ee298ba1b31eb664baa`.
Сверены все 137 instruction hashes и независимая генерация из sealed Git archive.
[Template](../../templates/manager_structure.json) и blueprint воспроизводятся
из канонических источников; ID/canvas/геометрия/связи сохранены, приватные
tool-status records не публикуются в portable template.

Результат этой задачи — GitHub. Работающие службы, installed Manager snapshot
и сохранённые live инструкции/commissioning state остаются на исходной ревизии.
Для последующего согласованного выпуска по [runbook](../OPERATIONS_RUNBOOK.md):

1. Выбрать exact CRM коммит с этим отчётом и exact Manager `53ea64c…`;
   повторить release gates и проверить bundle pin/hash перед deployment.
2. Получить свежий полный private technical graph, сохранить backup вместе
   с текущей согласованной release tuple. Не применять подготовку со старой version.
3. `scripts/sync_manager_structure_instructions.py` только offline готовит
   preview/apply requests из fresh graph, sealed Manager и bundle. Проверить
   unchanged-scope digest; live apply выполнять отдельным owner-authorized выпуском
   с текущими `expected_version` и отдельными idempotency keys.
4. После deployment перечитать exact revision/hash и все сохранённые инструкции;
   `--check --check-saved` проверяет совпадение. ID/геометрия/связи/tool_statuses
   должны совпасть с pre-release projection; browser ссылки проверяются на установленной tuple.

Source/tests/GitHub CI не подтверждают deployment, provider availability, свежие
robots/лицензионные условия, VIN/OEM fitment, stock или live финансовые расчёты.
Такие операции в этой задаче не выполнялись.
