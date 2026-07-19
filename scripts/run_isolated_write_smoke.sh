#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
TEMP_ROOT="$(mktemp -d /tmp/autostop-crm-write-smoke.XXXXXX)"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT

export PYTHONPATH="$PROJECT_ROOT/src:$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

"$PYTHON" -m unittest \
  tests.test_api.ApiServerTests.test_health_and_create_card \
  tests.test_api.ApiServerTests.test_inventory_routes_save_search_write_off_and_return_fractional_item \
  tests.test_api.ApiServerTests.test_archive_card_route_allows_open_empty_repair_order_without_money \
  tests.test_agent_gateway_v2.AgentGatewayV2Tests.test_raw_write_requires_idempotency_key \
  tests.test_agent_gateway_v2.AgentGatewayV2Tests.test_raw_write_fails_closed_when_durable_manager_ledger_is_missing
