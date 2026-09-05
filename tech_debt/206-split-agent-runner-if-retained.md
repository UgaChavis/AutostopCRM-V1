# 206. Autonomous agent runtime boundary

Приоритет: P2 owner. Отдельный split нужен только при измеренной проблеме.

## Ratchets

- `agent/runner.py` — 3 682 строки; `AgentRunner` — 3 462.
- `scripts/attest_agent_gateway_v2.py` — 9 498;
  `_finance_apply_audit_safe_fixes_case` — 457.
- Caps запрещают рост, но не заменяют доказательство необходимости refactor.

## Boundary

- Runtime uses one autonomous execution path; it does not retain a second executor.
- Preserve task/log DTO, report schema/hashes, redaction, cancellation/retry,
  and Linux/Docker imports when extracting a real boundary.
- Do not run attestation `--apply-synthetic` against production.

## When to change it

- Require measured churn, a defect history, or a concrete latency/maintenance
  cost. One change removes or lowers its own cap and keeps the public contract.

## Проверки

`python -m unittest tests.test_agent_payload_hardening tests.test_agent_runner_output tests.test_agent_runner_serialization tests.test_agent_runtime_check tests.test_agent_gateway_v2_attestation_script tests.test_agent_gateway_v2_attestation_unittest -v`
`python scripts/check_agent_runtime.py --help`
`python scripts/attest_agent_gateway_v2.py --help`
`python scripts/check_agent_gateway_v2.py --help`
