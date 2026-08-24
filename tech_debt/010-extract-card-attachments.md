# 010. Вынести attachment и file I/O из CardService

Приоритет: P1
Этап: 1
Оценка: 4–6 дней
Риск реализации: средний
Статус: ready после 001; coverage 002 и focused test-slice 003 параллельно

## Результат

Attachment CRUD, path safety, type detection и text extraction находятся в
отдельном cohesive service/mixin. `CardService` сохраняет публичный facade.

## Доказательства

В `card_service.py` attachment responsibilities разбросаны между:

- public operations примерно `5509–5778`;
- serialization `10228+`;
- DOCX/XLSX/PDF/image extraction `10373–10685`;
- safe path/read/write/delete `10686–10846`;
- validation/type detection `11187–11481`.

Это около 2 тыс. строк с низкой связью с repair-order/finance core.

## Scope

1. Создать `CardAttachmentService` либо существующему facade добавить один
   `CardServiceAttachmentsMixin`.
2. Передавать явные dependencies: base dir, logger, limits, event/save hooks.
3. Сохранить public CardService methods как delegates/унаследованные methods.
4. Сгруппировать pure parsers отдельно от filesystem operations.
5. Не менять storage layout, filenames и public payload.

## TDD-план

- add/list/read/remove exact roundtrip;
- size/extension/MIME/content mismatch;
- traversal, absolute paths, symlink and non-regular files;
- DOCX/XLSX/XML/PDF/image metadata extraction limits;
- truncated text flags;
- cleanup empty/orphan directories without deleting valid data;
- audit and change-feed event equality;
- rollback when file write succeeds but state commit fails.

## Подводные камни

- File write и state write имеют partial-failure window; preserve current
  cleanup/rollback ordering.
- OpenXML ZIP bombs/path traversal limits нельзя ослаблять.
- MIME не доверять caller.
- Windows symlink tests skip без Developer Mode; Linux CI обязателен.
- Filename normalization и Content-Disposition должны остаться совместимыми.
- Attachment may be used by agent/Gateway media paths.

## Acceptance criteria

- Не менее 1 500 строк удалено из `card_service.py` без поведения change.
- Новая boundary имеет focused tests и branch coverage floor.
- Public API/MCP schemas, error codes, audit/feed events unchanged.
- Attachment/security tests проходят на Linux CI.
- CardService module ratchet снижен.

## Проверки

`python -m unittest tests.test_card_attachments tests.test_service tests.test_api tests.test_mcp -v`
`python scripts/crm_change_feed_producer_parity.py --require-complete`
`python scripts/code_health_audit.py --format text`

## Stop condition

Если extraction требует изменить storage transaction model, сохранить delegate
и вынести transaction redesign в задачу 202.
