# 000. Починить preflight browser/PDF smoke

Приоритет: P0
Этап: 1
Оценка: 0.5–1 день
Риск реализации: низкий
Статус: completed locally 2026-08-23

## Реализация и доказательства (2026-08-23)

- `browser_smoke_profiles.py` проверяет Playwright, Chromium, Qt PDF,
  `pdfinfo` и `pdftotext` до создания `TempRuntime`.
- Ошибка аддитивно возвращается как JSON с `error=missing_dependency`, точным
  списком зависимостей, пустыми scenarios/events и exit code 2.
- Missing dependency и любой business/scenario failure не повторяются;
  разрешён только один повтор ошибки запуска Chromium.
- `toolchain_doctor.ps1` проверяет обе Poppler-команды и Qt PDF backend.
- Characterization: без Poppler full-профиль завершился ровно с
  `pdfinfo,pdftotext`, без runtime и retry. После установки Poppler 25.07.0
  полный профиль прошёл 44/44 с первой попытки и без browser errors.
- Общие focused browser tests: 28/28 `OK`.

## Результат

Полный browser smoke до запуска сценариев проверяет обязательные внешние
инструменты и либо запускается, либо немедленно завершается с точным
machine-readable кодом. Отсутствие `pdfinfo`/`pdftotext` не маскируется как
регрессия completion-act UI и не вызывает четыре одинаковых retry.

## Фактическое воспроизведение

На clean HEAD `e0cb954`:

- unit suite: 1 908 tests, `OK`;
- 39/40 browser scenarios true;
- console/page/request errors отсутствуют;
- `completion_act_editor_draft_roundtrip=false` четыре попытки подряд;
- failed checks: `editor_export_race`, `editor_print_race`,
  `main_print_regression`, `physical_pdf_regression`;
- `Get-Command pdfinfo,pdftotext` ничего не возвращает;
- helper `_completion_act_pdf_contract` в этом случае получает page count 0 и
  возвращает только false без причины.

Это не доказанный product defect. Это красный, но недиагностичный test
environment baseline.

## Scope

1. Добавить startup preflight текущего полного smoke для Chromium, Qt PDF
   backend, `pdfinfo` и `pdftotext`.
2. При отсутствии обязательного инструмента завершаться до TempRuntime.
3. Сохранить тип существующего JSON-поля: `ok=false`,
   `error="missing_dependency"`; добавить `missing_dependencies` и ноль
   business scenarios. Профиль появится аддитивно только в задаче 004.
4. Retry разрешён для transient browser launch, но не для missing dependency.
5. Добавить проверку в `doctor/toolchain_doctor` либо documented browser
   prerequisites; не печатать PATH целиком.
6. CI сохраняет установленный `poppler-utils` и выполняет PDF checks на Linux.

## TDD-план

- mocked missing both tools;
- missing only one tool;
- tool present but non-zero probe;
- текущий smoke с полным набором tools;
- missing dependency не создаёт temp state и не увеличивает attempt count;
- error JSON deterministic и не содержит environment dump.

## Подводные камни

- `QPdfDocument` доказывает parseability, но не извлекает page text/footer;
  нельзя молча считать его полной заменой Poppler.
- Не превращать full smoke в skip-success: release gate должен быть красным с
  понятной причиной.
- Не добавлять тяжёлый PDF dependency в runtime requirements; это dev/CI tool.
- Windows install path может различаться. Проверять команду через PATH, не
  hard-code Program Files.

## Acceptance criteria

- Без Poppler текущий smoke завершается один раз с `missing_dependency`.
- С Poppler completion-act PDF checks реально выполняются.
- CI PDF checks остаются зелёными.
- Runbook содержит короткую prerequisite/check команду.

## Проверки

`python -m unittest tests.test_browser_smoke -v`
`python scripts/browser_smoke.py`
`python scripts/toolchain_doctor.py --help` или соответствующий PowerShell
doctor test.

## Stop condition

Не заменять Poppler новым PDF stack в этой маленькой задаче. Если Windows
поддержка требует bundling, оформить отдельный evaluated follow-up.
