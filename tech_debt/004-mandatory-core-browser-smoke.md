# 004. Сделать малый browser smoke обязательным

Приоритет: P0
Этап: 1
Оценка: 2–4 дня
Риск реализации: средний
Статус: completed locally 2026-08-23; hosted workflow starts after publish

## Реализация и доказательства (2026-08-23)

- Добавлен единый tagged scenario registry: 44 full и 11 core scenarios;
  имена уникальны, core — строгий subset full.
- Core делает реальные temp-data roundtrips для login/anonymous rejection,
  create/edit/move card, timer, client link с exact readback, repair preview,
  inventory и files; любой console/page/request error валит результат.
- Локально core прошёл 11/11 примерно за 12 секунд, full — 44/44 примерно за
  37 секунд, оба с первой попытки и без browser errors.
- `quality.yml` устанавливает Chromium и запускает core на каждом push/PR;
  screenshot/JSON загружаются только при failure. Full остаётся явным
  `workflow_dispatch` release gate.
- Monolith разделён на profile/runtime/core/PDF-support modules. Остаточный
  CLI — 2 497 строк, completion-act editor — 424, desktop orchestrator — 12;
  все три прежних exemptions удалены.
- Parser/preflight/retry/cleanup/contracts: 28/28 focused tests `OK`;
  browser/API/web regression slice: 321/321 `OK`.

Перед этой задачей закрыть 000, чтобы full/PDF smoke отличал product failure
от отсутствующей локальной зависимости.

## Результат

Каждый PR автоматически проверяет короткий набор критических UI flow. Полный
35+ scenario smoke остаётся release/manual gate и не замедляет каждый commit.

## Доказательства

- `scripts/browser_smoke.py` — 3 563 физических строки и объединяет desktop/mobile,
  finance, payroll, files, repair orders, completion act и dashboard.
- GitHub Actions устанавливает Chromium и запускает browser smoke только при
  ручном `workflow_dispatch(browser_smoke=true)`.
- Большой JS source планируется механически разделять; substring/unit tests не
  доказывают реальную binding/navigation работу.

## Core smoke scope

Обязательный короткий набор должен покрывать:

1. operator login и anonymous write rejection;
2. board load/create-edit-move exact readback на temp data;
3. card modal open/save/close и timer start/stop;
4. client search/link без создания production-like duplicates;
5. repair-order open/edit/close preview path без реальных finance records;
6. один inventory roundtrip на synthetic state;
7. открытие основного files/modal route;
8. отсутствие console/page/request errors.

Не включать в core: физическую PDF regression, длинную payroll chain,
cross-card race, все mobile panels, dashboard 1920x1080 screenshot.

## Реализация

1. Вынести scenario registry с tags `core`, `full`, `mobile`, `pdf`,
   `finance`.
2. Добавить CLI `--profile core|full`.
3. Core обязан владеть отдельным temp state и очищать его при любом исходе.
4. Сохранить текущий `full` behavior и JSON schema результата.
5. Добавить CI step с установленным Chromium.
6. Публиковать screenshot/log artifact только при failure.
7. Вынести scenario registry и completion-act exercise из двух oversized
   функций в небольшие profile/scenario modules; снять module и оба function
   exemptions либо заменить их точными caps на остаточный CLI facade.

## TDD-план

- Parser tests для profile/tags и unknown profile.
- Contract test: core registry не пуст, subset full, имена уникальны.
- Temp-state cleanup на success, assertion failure и browser crash.
- Test, что любое console/page/request error делает result `ok=false`.
- Test, что skipped core scenario не считается passed.

## Подводные камни

- Не направлять smoke на production URL.
- Не использовать реальные credentials; temp runtime создаёт local operator.
- Chromium startup variability: budget должен иметь небольшой platform margin.
- Не скрывать flaky retry. Допустим один retry только для browser launch, не
  для business assertion.
- Не менять full scenario semantics при выделении core.

## Acceptance criteria

- `browser_smoke.py --profile core` стабильно укладывается в согласованный CI
  budget (цель ≤ 120 s на Linux runner).
- Core запускается на каждом PR/push.
- Full profile остаётся доступен и проходит перед release.
- Любая поломка event binding или API roundtrip ловится core smoke.
- Browser artifacts не попадают в tracked tree.

## Проверки

`python -m unittest tests.test_browser_smoke -v`
`python scripts/browser_smoke.py --profile core`
`python scripts/browser_smoke.py --profile full`
`python scripts/check_web_assets_js.py`

## Stop condition

Если core нельзя уложить в 120 s, уменьшать число fixture setup, а не удалять
auth/board/card assertions. Разрешается отдельный nightly full job.
