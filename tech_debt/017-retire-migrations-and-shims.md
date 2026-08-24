# 017. Закрыть lifecycle one-off migrations и compatibility shims

Приоритет: P1
Этап: 1
Оценка: 3–6 дней на inventory; удаления отдельными approvals
Риск реализации: средний/высокий
Статус: ready for read-only inventory

## Результат

Каждый legacy/migration path имеет статус: active compatibility, rollback
asset, production-complete removable или uncertain. Удаляется только то, для
чего есть runtime/data/deploy доказательство.

## Кандидаты

- `scripts/apply_payroll_policy_2026_07_13.py`;
- `migrate_payroll_policy_2026_07_13` в payroll service;
- `scripts/normalize_cashboxes_after_safe_fix.py`;
- `scripts/migrate_repair_order_cycles.py` и service method;
- legacy completion-act draft migration;
- settings-model legacy field mappings;
- legacy repair-order prepayment normalization;
- blocked `correct_repair_order_number` compatibility route;
- legacy backup schemas;
- retired embedded-agent responses.

Это кандидаты на классификацию, не список удаления.

## Evidence register для каждого кандидата

1. Runtime references/imports/dynamic registry.
2. Tests и public contract/docs.
3. Production data prevalence: read-only counts/schema versions, без PII.
4. Deploy/backup/rollback/recovery references.
5. Last known use/release date.
6. Replacement path.
7. Rollback consequence после удаления.
8. Решение и owner approval.

## Порядок

1. Добавить machine-readable inventory в code health или отдельный маленький
   audit, без production connection по умолчанию.
2. Локально классифицировать tracked candidates.
3. Для data migrations подготовить read-only production probe отдельно.
4. После подтверждения нулевого legacy prevalence выдержать минимум один
   release window.
5. Удалять по одному family: code + tests + docs + audit allowlist.

## TDD-план

- Current schema loads without legacy mapper.
- Representative oldest supported fixture migration.
- Backup restore compatibility before/after.
- Blocked routes remain fail-closed, если public compatibility ещё нужна.
- Mechanical wording tests сохраняют legacy→current mapping.
- Removed script path отсутствует в docs/deploy/tests.

## Подводные камни

- Missing direct import не означает dead: route/MCP registries и ops docs.
- Production rollback image может требовать старую schema.
- Settings в `%APPDATA%\Minimal Kanban` — compatibility boundary.
- Удаление mapper до нормализации всех state files делает rollback/startup
  невозможным.
- Не хранить production samples в Git.

## Acceptance criteria

- Inventory покрывает все найденные legacy/migration paths.
- У каждого delete есть exact evidence и replacement/rollback statement.
- Нет uncertain deletion.
- После каждого удаления full suite, docs, backup/restore и deployment tests
  проходят.
- Canonical docs не разрастаются historical notes; evidence остаётся в task
  completion section или release record.

## Проверки

`rg -n -i 'legacy|migrat|compat|retired|obsolete' src scripts tests docs`
`python scripts/docs_audit.py --format text`
`python scripts/code_health_audit.py --format text`
focused migration/backup tests.

## Stop condition

Нет read-only production evidence или rollback всё ещё требует path — статус
`active compatibility`/`uncertain`, удаления нет.
