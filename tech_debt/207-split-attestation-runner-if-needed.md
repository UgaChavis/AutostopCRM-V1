# 207. Разделить production attestation runner, только при доказанной боли

Приоритет: P2
Этап: 2 — conditional после 009
Оценка: 5–8 дней
Риск реализации: высокий из-за release role
Статус: proposed только при сохраняющемся churn/стоимости изменений

## Результат

Если после 009 release guard остаётся дорогим или часто меняемым,
`attest_agent_gateway_v2.py` становится thin CLI/orchestrator, а case families,
fixtures, cleanup и report store тестируются отдельно. Production semantics и
mode-0600 artifacts не меняются.

## Доказательства

- Script — 9 498 строк, 148 functions.
- 39 strict complexity signals.
- Крупнейшие cases 200–457 строк.
- Script является stop-the-line production guard; сопровождаемость важна, но
  rewrite опасен.

## Seams

- `attestation/models.py`: CaseSpec/result refs;
- `attestation/public.py`;
- `attestation/board.py`;
- `attestation/finance.py`;
- `attestation/inventory.py`;
- `attestation/documents.py`;
- `attestation/raw.py`;
- `attestation/cleanup.py`;
- `attestation/report_store.py`;
- исходный script: CLI + registry + run loop.

## Порядок

1. Freeze exact inventory/case IDs/report schema.
2. Вынести pure models/report store.
3. Вынести read-only/public cases.
4. Вынести cleanup with global verification.
5. Вынести mutating families по одной.
6. Сравнить dry synthetic transcript hashes.

## TDD-план

- inventory deterministic and complete;
- one-case-per-invocation/resume/retry;
- failure blocks later cases;
- mode 0600 and no body/token/PII artifact;
- cleanup idempotent and target-bounded;
- crash before/after fixture creation;
- run-id/path validation;
- exact public/raw operation coverage.

## Подводные камни

- Imports must work inside Docker/Linux with repository root.
- Cleanup ownership нельзя расширять glob-ами.
- Не менять case IDs: это release history.
- Shared helper не должен скрывать operation-specific readback.
- Production run не запускать в cleanup task без explicit owner intent.

## Acceptance criteria

- CLI script ≤ 800 строк.
- Case inventory, ordering, report schema и hashes unchanged.
- Attestation unit tests проходят на Linux CI.
- No secret/business body persisted or printed.
- Code-health script exemption удалён.

## Проверки

`python -m unittest tests.test_agent_gateway_v2_attestation_script tests.test_agent_gateway_v2_attestation_unittest -v`
`python scripts/attest_agent_gateway_v2.py --help`
`python scripts/check_agent_gateway_v2.py --help`

## Stop condition

Если после 009 нет подтверждённого churn, defect history или времени изменения
cases, закрыть `not planned`: один размер исправного guard не оправдывает
refactor. Не запускать `--apply-synthetic` против production. Если equivalence
нельзя доказать unit/local temp tests, остановить release-path refactor.
