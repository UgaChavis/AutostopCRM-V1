from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class GatewayPaymentOverpaymentContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        register = self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.state: dict = {}
        register.side_effect = lambda server, logger: register_fake_store_manager_tools(
            server, logger, self.state
        )
        self.board_api = FakeBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            logging.getLogger(self._testMethodName),
            host="127.0.0.1",
            port=41854,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def _payment(
        self,
        flag: object,
        *,
        mode: str | None = None,
        payload_overrides: dict | None = None,
        **extra_fields: object,
    ):
        arguments = {
            "operation": "record_repair_order_payment",
            "payload": {
                "card_id": "card-1",
                "cashbox_id": "cashbox-main",
                "amount": "1001",
                "payment_method": "cash",
                "expected_updated_at": self.board_api.card_updated_at,
                "expected_cashbox_updated_at": self.board_api.cashbox_updated_at,
                "allow_overpayment": flag,
            },
            "idempotency_key": f"synthetic-overpayment-{mode}-{type(flag).__name__}-{flag}",
        }
        if mode is not None:
            arguments["mode"] = mode
        arguments["payload"].update(payload_overrides or {})
        arguments.update(extra_fields)
        return await self.server._tool_manager.get_tool("agent_finance_workflow").run(
            arguments, convert_result=False
        )

    async def test_legacy_false_string_cannot_authorize_overpayment(self) -> None:
        result = await self._payment("false")

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)
        self.assertFalse(bool(self.state.get("calls")))

    async def test_preview_requires_boolean_overpayment_flag(self) -> None:
        for flag in ("false", "true", 0, 1, None, [], {}):
            with self.subTest(flag=flag):
                result = await self._payment(flag, mode="dry_run")
                self.assertFalse(result.structuredContent["ok"])
                self.assertIn(
                    "finance_payload_schema_validation_failed",
                    result.structuredContent["warnings"],
                )
                self.assertIn(
                    "arguments.allow_overpayment:bool_type",
                    result.structuredContent["summary"]["validation_errors"],
                )
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)
        self.assertFalse(bool(self.state.get("calls")))

    async def test_boolean_false_preserves_debt_guard(self) -> None:
        result = await self._payment(False)

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)

    async def test_other_truthy_values_cannot_authorize_legacy_overpayment(self) -> None:
        for flag in ("true", 0, 1, None, [], [True], {}, {"approved": True}):
            with self.subTest(flag=flag):
                result = await self._payment(flag)
                self.assertFalse(result.structuredContent["ok"])
                self.assertEqual([], self.board_api.repair_order_payments)
                self.assertEqual([], self.board_api.cash_transactions)
        self.assertFalse(bool(self.state.get("calls")))

    async def test_invalid_apply_flag_is_rejected_before_preview_binding(self) -> None:
        result = await self._payment("false", mode="apply")

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn(
            "finance_payload_schema_validation_failed", result.structuredContent["warnings"]
        )
        self.assertFalse(bool(self.state.get("calls")))
        self.assertEqual([], self.board_api.repair_order_payments)

    async def test_boolean_true_cannot_bypass_finance_permission(self) -> None:
        with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"}):
            result = await self._payment(True)

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("agent_gateway_finance_disabled", result.structuredContent["warnings"])
        self.assertFalse(bool(self.state.get("calls")))
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)

    async def test_explicit_true_apply_retains_proof_and_idempotency_contract(self) -> None:
        preview = await self._payment(True, mode="dry_run")
        proof_fields = {
            "dry_run_proof": preview.structuredContent["data"]["dry_run_proof"],
            "dry_run_idempotency_key": preview.structuredContent["data"]["dry_run_idempotency_key"],
        }
        applied = await self._payment(True, mode="apply", **proof_fields)
        self.assertTrue(applied.structuredContent["ok"])
        self.assertTrue(applied.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(1, len(self.board_api.repair_order_payments))
        self.assertEqual(1, len(self.board_api.cash_transactions))

        # Reuse the original revision and exact payload, as a true retry must.
        replay = await self._payment(
            True,
            mode="apply",
            payload_overrides={
                "expected_updated_at": "2026-07-11T00:00:00+00:00",
                "expected_cashbox_updated_at": "2026-07-11T00:00:00+00:00",
            },
            **proof_fields,
        )
        self.assertTrue(replay.structuredContent["ok"])
        self.assertTrue(replay.structuredContent["summary"]["deduplicated"])
        self.assertEqual(1, len(self.board_api.repair_order_payments))
        self.assertEqual(1, len(self.board_api.cash_transactions))

    async def test_boolean_true_requires_apply_preview_proof(self) -> None:
        result = await self._payment(True, mode="apply")

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("finance_dry_run_proof_required", result.structuredContent["warnings"])
        self.assertFalse(bool(self.state.get("calls")))
        self.assertEqual([], self.board_api.repair_order_payments)

    async def test_boolean_true_preserves_revision_guard(self) -> None:
        original = self.board_api.get_repair_order

        def stale_order(*args, **kwargs):
            response = original(*args, **kwargs)
            response["data"]["card"]["updated_at"] = "2025-01-01T00:00:00+00:00"
            return response

        with patch.object(self.board_api, "get_repair_order", side_effect=stale_order):
            result = await self._payment(True)

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)

    async def test_boolean_true_cannot_bypass_write_permission(self) -> None:
        with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0"}):
            result = await self._payment(True)

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("agent_gateway_writes_disabled", result.structuredContent["warnings"])
        self.assertFalse(bool(self.state.get("calls")))
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)

    async def test_preview_accepts_boolean_flags_without_business_mutation(self) -> None:
        for flag in (False, True):
            with self.subTest(flag=flag):
                result = await self._payment(flag, mode="dry_run")
                self.assertTrue(result.structuredContent["ok"])
        self.assertEqual([], self.board_api.repair_order_payments)
        self.assertEqual([], self.board_api.cash_transactions)

    async def test_boolean_true_keeps_explicit_override_compatible(self) -> None:
        result = await self._payment(True)

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(1, len(self.board_api.repair_order_payments))
        self.assertEqual(1, len(self.board_api.cash_transactions))
        self.assertEqual("1001", self.board_api.repair_order_payments[0]["amount"])


if __name__ == "__main__":
    unittest.main()
