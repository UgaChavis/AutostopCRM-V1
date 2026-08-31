# 021. Разделить embedded print web module

Приоритет: P1
Этап: 1
Оценка: 3–5 дней, механическими commits
Риск реализации: средний
Статус: ready; может идти параллельно с 014

## Результат

Активный embedded print UI больше не хранится одним 3 367-строчным Python
asset. Draft editor, preview и browser bridge разделены без нового frontend
toolchain; assembled resource и поведение остаются эквивалентными.

## Доказательства

- `printing/web_module.py` — 3 367 физических строк и module exemption.
- В одном asset смешаны markup/style, editor state, backend bridge,
  preview/export interactions и event binding.
- Это иной языковой контур и иной набор проверок, чем backend-сервис 014;
  совместный commit увеличивал бы blast radius без пользы.

## Scope

1. Зафиксировать assembled asset ordering, required symbols и bridge calls.
2. Выделить deterministic fragments: shell/styles, draft editor, preview,
   bridge/bootstrap.
3. Оставить один assembler/facade, сохраняющий текущий public resource.
4. Не менять DOM, CSS и browser-to-Python message schema при переносе.
5. После каждого fragment move запускать syntax, focused asset tests и core
   smoke; full completion-act smoke — после editor/preview.
6. Снять module exemption или оставить facade ≤ 500 строк с точным cap.

## TDD-план

- assembled fragment order и unique top-level bindings;
- draft load/edit/save/reset roundtrip;
- stale version/source fingerprint conflict;
- preview refresh и export/print race;
- bridge unavailable/error response;
- Escape/focus/cleanup и отсутствие duplicate listeners;
- CSP/resource path и offline desktop loading.

## Подводные камни

- Python string escaping может изменить JavaScript без видимого Python diff.
- Не проверять duplicates regex по телам всех функций; только top-level
  declarations/registry.
- Не дублировать VAT/calculation logic из backend 014.
- Physical PDF checks требуют текущего toolchain preflight; missing Poppler не является UI
  regression.
- Не вводить bundler, ES modules или UI redesign.

## Acceptance criteria

- Каждый fragment ≤ 1 000 строк; assembler/facade ≤ 500.
- Assembled resource, bridge schema и required symbol inventory сохранены.
- JS syntax, focused asset tests, core и full completion-act smoke проходят.
- Нет console/page/request errors и duplicate event handlers.
- `printing/web_module.py` exemption удалён.

## Проверки

`python scripts/check_web_assets_js.py`
`python -m unittest tests.test_web_assets tests.test_printing_service tests.test_completion_act_backend -v`
`python scripts/browser_smoke.py --profile core`
`python scripts/browser_smoke.py --profile full`
`python scripts/code_health_audit.py --format text`

## Stop condition

Если extraction требует менять backend calculation, bridge DTO или visual
design, остановить mechanical move и оформить отдельную defect/decision task.
