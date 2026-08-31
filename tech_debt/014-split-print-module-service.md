# 014. Разделить backend PrintModuleService

Приоритет: P1
Этап: 1
Оценка: 6–10 дней
Риск реализации: средний/высокий
Статус: ready после 001; print test-slice 003 может идти параллельно

## Результат

Template storage, completion-act drafts, document context calculations,
HTML rendering и PDF/physical output отделены. Один backend calculator остаётся
source of truth для invoice, invoice-factura, UPD и completion act.

## Доказательства

- `printing/service.py` — 4 223 строк.
- `PrintModuleService` — 2 831 строк / 87 methods.
- `_build_document_context` — 344 строки.
- В том же class legacy draft migration, secure filesystem, templates,
  calculations и export.
- Embedded UI имеет другой язык и regression gates; он вынесен в 021.

## Seams

- `PrintTemplateRepository`;
- `CompletionActDraftStore`;
- `DocumentContextBuilder`;
- `RegulatedDocumentCalculator`;
- `DocumentRenderer/Exporter` facade.

Существующие `printing/pdf.py` и `printers.py` не переписывать.

## Порядок

1. Golden contexts/HTML/PDF metadata.
2. Вынести pure regulated calculations.
3. Вынести draft store/migration.
4. Вынести template repository.
5. Вынести context builder по document family.
6. Оставить PrintModuleService orchestration facade.

## TDD-план

- VAT included/excluded/none, cent balancing;
- invoice/factura/UPD equality invariants;
- manual document without card;
- completion act 0/1/300 rows and 40-page bound;
- stale version/source fingerprint/idempotency;
- reset tombstone and legacy migration;
- malformed/oversized/symlinked draft store fail closed;
- template default/delete/duplicate;
- PDF renderer failure and cleanup.

## Подводные камни

- HTML exact-string tests хрупки, но accounting context должен проверяться
  структурно до render.
- Не дублировать calculation в JS preview.
- Draft backup/rollback schema используется deploy.
- Qt PDF behavior platform-specific; Linux CI и Windows local оба нужны.
- Legacy migration удалять только через 017.

## Acceptance criteria

- Facade ≤ 800 строк; каждый component имеет focused tests.
- VAT/totals/document contexts exact-equal baseline.
- Backup/restore и completion act tests проходят.
- Print service exemptions удалены/снижены.
- Browser completion-act full scenario проходит.

## Проверки

`python -m unittest tests.test_printing_service tests.test_completion_act_backend tests.test_agent_release_backup -v`
`python scripts/browser_smoke.py --profile full`
`python scripts/post_build_verification.py --help`

## Stop condition

Не заменять Qt renderer или template language в этой задаче. Любая такая смена
требует отдельного ADR и visual/PDF parity plan.
