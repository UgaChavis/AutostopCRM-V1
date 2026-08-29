# 005. Разрезать board web asset source по доменам

Приоритет: P1
Этап: 1
Оценка: 6–10 дней, 8–12 механических commits
Риск реализации: средний
Статус: ready после 004; извлечь только свой web test-slice из 003

## Результат

Основной browser UI больше не редактируется в одном 20.6k-строчном script.
Assembler по-прежнему выдаёт один fingerprinted JS asset и не требует нового
frontend framework или Node runtime в production.

## Доказательства

- `app_main_before_printing.js` — 20 639 физических строк, около 1 236 function
  declarations и один общий mutable `state`.
- В одном lexical scope находятся operator/admin, clients, cards, repair
  orders, payroll, cashboxes, inventory, files, mobile UI, polling и modal
  stack.
- `assembler.py` уже последовательно склеивает source chunks и fingerprint'ит
  итог; cashbox chunks показывают рабочий pattern без bundler.
- `test_web_assets.py` — 5 963 строки; core browser smoke уже обязательный,
  full smoke остаётся release gate.

## Минимальная архитектура

Сохранить один `<script defer>` и один lexical scope. Добавить source chunks в
детерминированном порядке:

- `app_core_state.js` — constants, state, DOM refs;
- `app_api_session.js` — api, auth, operator session;
- `app_modal_stack.js`;
- `app_board_cards.js`;
- `app_clients.js`;
- `app_repair_orders.js`;
- `app_inventory.js`;
- `app_employees_payroll.js`;
- `app_shared_files.js`;
- `app_mobile.js`;
- `app_bootstrap.js` — binding/startup only.

Это промежуточная декомпозиция source, не ES modules rewrite.

## Порядок переноса

1. Добавить exact no-growth ratchet для исходного JS, assembler ordering
   contract и scope-aware duplicate-symbol
   detector только для top-level registry/declarations; не искать regex по
   телам всех функций.
2. Перенести pure format/normalize helpers.
3. Перенести modal stack.
4. Перенести один домен с минимальным state footprint: clients/files.
5. Перенести inventory/cashboxes.
6. Перенести repair orders/payroll.
7. Перенести board/cards и polling последними.
8. Сократить исходный файл до bootstrap либо удалить его exemption.

Каждый commit: только move + assembler list + focused tests.

## TDD-план

- Exact hash/path меняется, HTML должен ссылаться на новый hash.
- Assembled JS syntax проходит.
- Symbol defined once; required startup symbols существуют.
- DOM binding вызывается один раз.
- Core browser smoke после каждого domain move.
- Full smoke после board/repair/payroll moves.
- Tests на modal focus/escape, optimistic revisions, polling cleanup,
  object URL revoke и auth reset.

## Подводные камни

- Порядок declaration и initialization важен: некоторые refs заполняются
  после динамического markup injection.
- Нельзя случайно создать несколько `state` либо копии timers/maps.
- Function hoisting меняется при переходе function → const arrow; в
  механическом переносе форму declaration не менять.
- Printing module вставляется между chunks; сохранить marker boundaries.
- Browser asset contract tests часто ищут literal strings. Не ослаблять их до
  бессмысленного contains-anywhere.
- Не вводить dynamic imports: CSP/cache/offline desktop behavior может
  измениться.

## Не входит

- React/Vue/Vite/Webpack.
- TypeScript migration.
- UI redesign.
- Изменение API payloads или localStorage compatibility keys.

## Acceptance criteria

- Нет source JS chunk > 2 500 строк; временные исключения имеют ratchet.
- До первого extraction текущие 20 639 строк являются exact cap; после каждого
  среза cap только снижается.
- Общий assembled UI behavior и публичные asset paths сохранены.
- `app_main_before_printing.js` удалён либо ≤ 1 000 строк bootstrap/core.
- Core и full browser smoke проходят без console/page/request errors.
- `test_web_assets.py` split и его exemption удалён.
- Новая source classification учтена в code health и PyInstaller assets.

## Проверки

`python scripts/check_web_assets_js.py`
`python -m unittest tests.test_web_assets tests.test_web_assets_runtime -v`
`python scripts/browser_smoke.py --profile core`
`python scripts/browser_smoke.py --profile full`
`python scripts/code_health_audit.py --format text`

## Stop condition

Если для переноса домена требуется менять global contract или бизнес-логику,
остановить move и создать отдельную defect-задачу. Не маскировать зависимость
новым global singleton.
