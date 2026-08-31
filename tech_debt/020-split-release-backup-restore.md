# 020. Разделить backup, verify и restore release state

Приоритет: P1
Этап: 1
Оценка: 3–5 дней, малыми commits
Риск реализации: высокий
Статус: ready после 001

## Результат

`agent_release_backup.py` имеет отдельно тестируемые manifest parsing,
backup, verification и restore boundaries. Полная preflight-валидация
проходит до mutation; каждый отдельный artifact заменяется атомарно.

## Доказательства

- Скрипт имеет 10 strict complexity/length signals без учёта длинных
  parameter lists.
- `_load_manifest` имеет complexity 25; restore path — complexity 20.
- Этот код участвует в release rollback, поэтому ошибка опаснее обычного
  maintenance script и не должна ждать отдельной архитектурной инициативы.

## Scope

1. Characterization tests для текущего manifest schema и exit codes.
2. Вынести pure manifest parser/validator без filesystem mutations.
3. Вынести backup inventory/checksum builder.
4. Вынести restore plan: source, destination, expected checksum, action.
5. Apply restore только после полной валидации всего плана; временные sibling
   files и atomic replace сохраняют current per-artifact platform semantics.
6. Сузить broad exceptions до typed failure categories, сохранив CLI output.
7. Удалить temporary artifacts и не печатать секреты/содержимое state.

## TDD-план

- valid current and oldest supported manifests;
- missing/duplicate/unknown entries;
- path traversal, absolute path, symlink/reparse target;
- checksum mismatch и truncated backup;
- destination changed between plan and apply;
- disk/write/replace failure with original state intact;
- repeated restore/idempotent verification;
- Windows/Linux path normalization;
- exact exit codes и redacted diagnostic output.

## Подводные камни

- Не тестировать restore на реальном production directory.
- Не ослаблять manifest validation ради старого повреждённого backup.
- TOCTOU между verify/apply минимизировать, но не обещать cross-artifact
  transaction: state, feed, drafts и Manager восстанавливаются разными sinks.
- Cross-artifact rollback/transaction требует отдельного discovery.
- Не менять deploy orchestration и retention policy в этой задаче.

## Acceptance criteria

- Parser и planner pure и покрыты malformed/adversarial fixtures.
- До первого mutation проверены все paths, schema и checksums.
- При simulated failure текущий заменяемый artifact остаётся читаемым; уже
  успешно заменённые предыдущие artifacts могут требовать reconciliation.
- CLI arguments, manifest schema и success/failure exit codes сохранены.
- Complexity ключевых функций ниже blocking budgets либо имеют сниженный
  числовой ratchet с объяснением.

## Проверки

`python -m unittest tests.test_agent_release_backup -v`
`python scripts/agent_release_backup.py --help`
`python -m ruff check scripts/agent_release_backup.py tests/test_agent_release_backup.py`

## Stop condition

Если для atomic restore требуется менять deploy.sh или backup schema,
остановиться и вынести совместимый migration/rollback plan отдельно.
