# 011. Вынести активные manager operations из CardService

Приоритет: P1
Этап: 1
Оценка: 5–8 дней
Риск реализации: средний
Статус: ready; coverage 002 и общий split 003 могут идти параллельно

## Результат

Core board/repair service не содержит manager scans, cleanup plans и
verification helpers. Public compatibility routes остаются доступны через
facade. Условный embedded AI runtime здесь не рефакторится.

## Доказательства

В `card_service.py` находятся:

- manager scans/actions `1672–2567`;
- cleanup/autofill helpers `7880–8312`;
- manager result/filter/verification helpers `9114–9716`.

Standalone AI chat UI retired, но embedded agent runtime активен через
`app.py`/`mcp/main.py`; следовательно, это не dead code.

## Scope

1. Выделить `ManagerBoardService` для реально используемых manager flows.
2. `CardService` предоставляет минимальные delegates для current routes.
3. Передавать минимальный board read/write facade явно.
4. Manager operations используют те же mutation methods, не пишут state
   напрямую.
5. Legacy disabled responses и reason codes сохранить.

## TDD-план

- cleanup dry-run/apply/rollback exact revisions;
- ready-unpaid and missing-data scans;
- board summary generation private-data redaction;
- audit actor/source and no duplicate events;
- no direct state write bypass.

## Подводные камни

- Не смешивать с product decision 203 и условной декомпозицией 206.
- AI enrichment/autofill оставить на месте до подтверждения retention runtime.
- Manager operations могут использовать private helpers CardService; заменить
  на маленький explicit port, не передавать весь service как Any.
- Не копировать validation/update logic в новый service.

## Acceptance criteria

- Manager concern удалён из core CardService; точный ratchet снижен на
  фактически вынесенный объём, без искусственной цели по числу строк.
- Public HTTP/MCP manager behavior exact-equal.
- New services имеют focused tests и dependency interfaces.
- No duplicate mutation/audit/feed implementations.
- CardService ratchet снижен.

## Проверки

`python -m unittest tests.test_manager_operations -v`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/crm_change_feed_producer_parity.py --require-complete`

## Stop condition

Не переносить embedded enrichment «заодно». Его судьба определяется задачами
203 и 206 по usage evidence.
