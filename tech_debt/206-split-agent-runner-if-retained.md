# 206. Conditional split of AgentRunner and attestation runner

Приоритет: P2. Этап: 2. Статус: blocked до owner-approved ADR.

## Владелец рачетов

- `agent/runner.py` — 5 093 строк; `AgentRunner` — 4 865.
- `scripts/attest_agent_gateway_v2.py` — 9 498;
  `_finance_apply_audit_safe_fixes_case` — 457.
- Caps запрещают рост, но не доказывают необходимость рефакторинга.

## Gate

- ADR подтверждает сохранить или retire embedded runtime.
- Для attestation нужны churn, defect history или измеренная стоимость изменения
  после 009. Без этого scope закрывается `not planned`.

## Правила одобренной работы

1. Зафиксировать task/log DTO, CLI/case IDs и порядок, report schema/hashes и
   mode-0600 artifacts.
2. Выносить один чистый boundary за раз, оставляя runner/CLI thin.
3. Characterize timeout, invalid JSON, cancellation/retry/restart, exact
   readback, redaction, target-bounded fixture cleanup и Linux/Docker imports.
4. Не запускать attestation `--apply-synthetic` против production.

## Приёмка

- Нет drift DTO/schema/log-level, case IDs, порядка или report hashes.
- Каждый commit уменьшает или удаляет свой cap.
- Проходят unit/local-temp и Linux CI.

## Проверки

`python -m unittest tests.test_agent_scenarios tests.test_agent_payload_hardening tests.test_agent_runner_output tests.test_agent_runner_serialization tests.test_agent_runtime_check tests.test_agent_gateway_v2_attestation_script tests.test_agent_gateway_v2_attestation_unittest -v`
`python scripts/check_agent_runtime.py --help`
`python scripts/attest_agent_gateway_v2.py --help`
`python scripts/check_agent_gateway_v2.py --help`
