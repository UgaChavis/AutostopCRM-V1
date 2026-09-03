# 001. Maintainability ratchets

Приоритет: P0 owner. Статус: реализовано; файл остаётся владельцем двух
data-only caps.

`code_health_audit.py` — единственный источник значений. Общие пределы:
production module 2500, test module 3000, class 2500, function 450 строк.
Все 34 size и 2 complexity exemptions имеют reason, baseline, `max_allowed`
без headroom и ровно один существующий `owner_task`.

## Exact owner map и caps

- 001: `_demo_specs` 957; `builtin_template_records` 1164.
- 003: modules `test_service.py` 13512, `test_api.py` 7684,
  `test_agent_gateway_v2.py` 4445, `test_web_assets.py` 5954; classes
  `CardServiceTests` 13294, `ApiServerTests` 7259, `AgentGatewayV2Tests` 3024,
  `WebAssetsTests` 5896; `test_mcp_tools_reach_backend` 1169.
- 008: `mcp/server.py` 3514; `create_mcp_server` 3104;
  `register_agent_gateway_v2` 3086.
- 009: modules `mcp/agent_gateway_v2.py` 3376, `mcp/raw_gateway.py` 1464;
  `_execute_workflow` 610 and complexity 56, `call_raw_capability` 707,
  `verify_virtual_api_write_readback` 966.
- 012: `card_service.py` 11627, `CardService` 11122,
  `CardService.update_card` complexity 29.
- 013: `card_service_payroll.py` 4467, `CardServicePayrollMixin` 4266.
- 014: `printing/service.py` 4223, `PrintModuleService` 2831.
- 018: `snapshot_service.py` 2879, `SnapshotService` 2574.
- 019: `card_service_finance.py` 3048, `CardServiceFinanceMixin` 3002.
- 021: `printing/web_module.py` 3367.
- 206: `agent/runner.py` 5093, `AgentRunner` 4865;
  `scripts/attest_agent_gateway_v2.py` 9498,
  `_finance_apply_audit_safe_fixes_case` 457.

Срез сразу уменьшает cap или удаляет exemption; cap не повышается ради нового
кода. Фабрики 001 остаются bounded data-only функциями без искусственной
абстракции. Приёмка: text/json audit зелёный, owners точны, ambiguous target,
AST error, duplicate/missing owner и рост выше cap fail closed.
