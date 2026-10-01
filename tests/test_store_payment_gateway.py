"""The focused Store payment route retains native finance guards and exact reread."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.agent_gateway_support import _is_finance_capability, _policy_error
from minimal_kanban.mcp.store_gateway import (
    inventory_gateway_operations,
    store_action_arguments,
    store_planned_changes,
    validate_store_workflow_request,
    verify_store_readback,
)
from tests import test_agent_gateway_v2 as fixture

OPERATION = "set_order_payment_status"
REVISION = "2026-10-01T00:00:00+00:00"


def payment_payload(paid=True):
    return {
        "target_id": "order-1",
        "expected_updated_at": REVISION,
        "owner_intent": "owner_finance: Synthetic current separate owner instruction",
        "planned_changes": {"paid": paid},
    }


class StorePaymentGatewayTests(unittest.TestCase):
    def test_operation_is_exposed_and_always_classified_as_finance(self) -> None:
        self.assertIn(OPERATION, inventory_gateway_operations(frozenset()))
        self.assertTrue(_is_finance_capability(OPERATION, {"paid": False}))
        with patch.dict(
            "os.environ", {**fixture.GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"}
        ):
            self.assertEqual(
                "agent_gateway_finance_disabled",
                _policy_error(tool_name=OPERATION, risk="write", arguments={"paid": False}),
            )

    def test_payment_boolean_and_current_financial_intent_are_strict(self) -> None:
        for paid in (None, "true", "false", 1, 0, [], {}):
            with self.subTest(paid=paid):
                check = validate_store_workflow_request(
                    OPERATION,
                    payment_payload(paid),
                    idempotency_key="payment-test-0001",
                    mode="dry_run",
                )
                self.assertEqual("store_payment_paid_boolean_required", check["warning"])
        for intent in (
            "owner_finance",
            "owner_finance: ",
            "technical task",
            "owner_finance: " + "a" * 500,
        ):
            with self.subTest(intent=intent):
                check = validate_store_workflow_request(
                    OPERATION,
                    {**payment_payload(), "owner_intent": intent},
                    idempotency_key="payment-test-0001",
                    mode="apply",
                )
                self.assertEqual("store_explicit_owner_finance_intent_required", check["warning"])
        for paid in (True, False):
            self.assertTrue(
                validate_store_workflow_request(
                    OPERATION,
                    payment_payload(paid),
                    idempotency_key="payment-test-0001",
                    mode="dry_run",
                )["passed"]
            )

    def test_native_arguments_preserve_false_and_include_no_invented_proof_field(self) -> None:
        payload = payment_payload(False)
        fields = {
            name: {}
            for name in (
                "domain",
                "action",
                "target_id",
                "expected_updated_at",
                "planned_changes",
                "owner_intent",
                "idempotency_key",
                "mode",
                "correlation_id",
            )
        }
        raw_tools = {"store_management_action": SimpleNamespace(parameters={"properties": fields})}
        arguments = store_action_arguments(
            raw_tools,
            OPERATION,
            payload,
            idempotency_key="payment-test-0001",
            mode="apply",
            correlation_id="payment-test-0001",
        )
        self.assertEqual("store_order", arguments["domain"])
        self.assertEqual({"paid": False}, arguments["planned_changes"])
        self.assertEqual(set(fields), set(arguments))
        self.assertEqual({"paid": False}, store_planned_changes(OPERATION, payload))

    def test_readback_requires_native_verification_exact_payment_and_no_effects(self) -> None:
        for paid in (True, False):
            payload = {**payment_payload(paid), "correlation_id": "payment-test-0001"}
            target = {
                "id": "order-1",
                "updated_at": "2026-10-01T00:01:00+00:00",
                "payment_status": "PAID" if paid else "PAYMENT_REQUIRED",
                "paid_at": "2026-10-01T00:01:00+00:00" if paid else None,
            }
            result = {
                "ok": True,
                "correlation_id": "payment-test-0001",
                "changes": [{"field": "payment_status"}, {"field": "paid_at"}],
                "meta": {"readback_verified": True, "effects": []},
            }

            def verify(updated_result=result, updated_target=target):
                return verify_store_readback(
                    OPERATION,
                    payload,
                    updated_result,
                    mode="apply",
                    preflight={"actual_updated_at": REVISION},
                    readback={"ok": True, "items": [updated_target]},
                )

            self.assertTrue(verify()["passed"])
            self.assertFalse(verify({**result, "meta": {"effects": []}})["passed"])
            self.assertFalse(
                verify(
                    {
                        **result,
                        "meta": {"readback_verified": True, "effects": [{"type": "telegram"}]},
                    }
                )["passed"]
            )
            self.assertFalse(
                verify(updated_target={**target, "payment_status": "incorrect"})["passed"]
            )
            self.assertFalse(verify({**result, "changes": [{"field": "status"}]})["passed"])


class StorePaymentPublicGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        environment = patch.dict("os.environ", fixture.GATEWAY_ENV, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        self.calls = []
        self.state = {}

        def register(server, logger):
            fixture.register_fake_store_manager_tools(server, logger, self.state)
            server._tool_manager._tools.pop("store_management_action")

            @server.tool(name="store_management_action", description="INTERNAL_ONLY Store write")
            def native_action(
                domain: str,
                action: str,
                target_id: str,
                planned_changes: dict,
                owner_intent: str,
                expected_updated_at: str,
                idempotency_key: str,
                correlation_id: str,
                mode: str = "dry_run",
            ) -> dict:
                arguments = dict(locals())
                self.calls.append(arguments)
                target = self.state["entities"][(domain, target_id)]
                if mode == "apply":
                    target.update(
                        payment_status="PAID" if planned_changes["paid"] else "PAYMENT_REQUIRED",
                        paid_at="2026-10-01T00:01:00+00:00" if planned_changes["paid"] else None,
                        updated_at="2026-10-01T00:01:00+00:00",
                    )
                return {
                    "ok": True,
                    "status": "applied" if mode == "apply" else "dry_run",
                    "correlation_id": correlation_id,
                    "changes": [{"field": "payment_status"}, {"field": "paid_at"}],
                    "meta": {"effects": [], "readback_verified": mode == "apply"},
                }

        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools", side_effect=register
        ):
            self.server = fixture.create_mcp_server(
                fixture.FakeBoardApi(),
                logging.getLogger("payment-gateway-synthetic"),
                host="127.0.0.1",
                port=41831,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )
        self.state["entities"][("store_order", "order-1")]["updated_at"] = REVISION
        self.tool = self.server._tool_manager.get_tool("agent_inventory_workflow")

    async def test_invalid_finance_intent_stops_before_executor_and_ledger(self) -> None:
        result = await self.tool.run(
            {
                "operation": OPERATION,
                "payload": {**payment_payload(), "owner_intent": "technical repair"},
                "mode": "dry_run",
                "idempotency_key": "payment-dry-run-0001",
            },
            convert_result=False,
        )
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual([], self.calls)
        self.assertFalse(any(name == "start_workflow" for name, _ in self.state["calls"]))

    async def test_dry_run_then_apply_calls_native_with_the_same_plan_and_verifies_reread(
        self,
    ) -> None:
        for mode in ("dry_run", "apply"):
            result = await self.tool.run(
                {
                    "operation": OPERATION,
                    "payload": payment_payload(False),
                    "mode": mode,
                    "idempotency_key": "payment-gateway-" + mode + "-0001",
                },
                convert_result=False,
            )
            self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        self.assertEqual(2, len(self.calls))
        self.assertEqual({"paid": False}, self.calls[-1]["planned_changes"])
        self.assertEqual(self.calls[0]["correlation_id"], self.calls[1]["correlation_id"])


if __name__ == "__main__":
    unittest.main()
