from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.auth import StaticBearerTokenVerifier
from minimal_kanban.mcp.server import create_mcp_server

GATEWAY_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
}


class FakeBoardApi:
    base_url = "http://127.0.0.1:41731"

    def __init__(self) -> None:
        self.raw_requests: list[dict] = []
        self.card_updated_at = "2026-07-11T00:00:00+00:00"
        self.repair_order_payments: list[dict] = []
        self.cash_transactions: list[dict] = []

    def get_board_context(self) -> dict:
        return {
            "ok": True,
            "data": {
                "context": {
                    "columns_total": 2,
                    "active_cards_total": 3,
                    "archived_cards_total": 1,
                    "stickies_total": 0,
                }
            },
        }

    def get_cards(self, *, include_archived: bool = False, compact: bool = True) -> dict:
        cards = [
            {
                "id": f"card-{index}",
                "short_id": f"C-{index}",
                "vehicle": "Vehicle",
                "title": f"Task {index}",
                "column": "inbox",
                "column_label": "Inbox",
                "tags": [],
                "status": "ok",
                "indicator": "green",
                "remaining_seconds": 200_000,
                "deadline_timestamp": "2026-07-13T00:00:00+00:00",
                "client_id": "",
                "board_summary": "",
                "updated_at": "2026-07-11T00:00:00+00:00",
                "deadline_heat_glow_color": "large-ui-only-value",
            }
            for index in range(4 if include_archived else 3)
        ]
        return {"ok": True, "data": {"cards": cards}, "meta": {"compact": compact}}

    def search_cards(self, **_: object) -> dict:
        return self.get_cards()

    def get_card(self, card_id: str) -> dict:
        return {"ok": True, "data": {"card": {"id": card_id, "title": "Task"}}}

    def get_repair_order(self, card_id: str) -> dict:
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": {
                    "number": "42",
                    "payments": [dict(item) for item in self.repair_order_payments],
                    "payment_summary": {"cash_due": "1000", "noncash_due": "1176.47"},
                },
            },
        }

    def get_cashbox(
        self,
        cashbox_id: str,
        *,
        transaction_limit: int | None = None,
        transaction_offset: int | None = None,
    ) -> dict:
        del transaction_limit, transaction_offset
        return {
            "ok": True,
            "data": {
                "cashbox": {
                    "id": cashbox_id,
                    "name": "Наличный",
                    "transactions": [dict(item) for item in self.cash_transactions],
                }
            },
        }

    def update_repair_order(
        self,
        *,
        card_id: str,
        repair_order: dict,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        del actor_name
        if expected_updated_at != self.card_updated_at:
            return {"ok": False, "error": {"code": "card_update_conflict"}}
        self.repair_order_payments = [dict(item) for item in repair_order.get("payments", [])]
        payment = self.repair_order_payments[-1]
        transaction_id = "cash-transaction-payment-1"
        payment["cash_transaction_id"] = transaction_id
        self.cash_transactions.append(
            {
                "id": transaction_id,
                "cashbox_id": payment["cashbox_id"],
                "amount": payment["amount"],
            }
        )
        self.card_updated_at = "2026-07-11T00:01:00+00:00"
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": {"payments": [dict(item) for item in self.repair_order_payments]},
            },
        }

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        self.raw_requests.append(
            {
                "path": path,
                "payload": dict(payload or {}),
                "method": method,
                "extra_headers": dict(extra_headers or {}),
            }
        )
        return {
            "ok": True,
            "data": {"path": path, "accepted": True},
            "meta": {"request_id": "fake-request"},
        }


class AgentGatewayV2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()
        self.board_api = FakeBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41831,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    async def _call(self, name: str, arguments: dict | None = None):
        tool = self.server._tool_manager.get_tool(name)
        self.assertIsNotNone(tool)
        return await tool.run(arguments or {}, convert_result=False)

    async def test_v2_replaces_full_surface_with_compact_tools(self) -> None:
        names = {tool.name for tool in self.server._tool_manager.list_tools()}
        self.assertLessEqual(len(names), 25)
        self.assertIn("agent_bootstrap", names)
        self.assertIn("agent_board_digest", names)
        self.assertIn("call_raw_capability", names)
        self.assertIn("ping_connector", names)
        self.assertNotIn("get_cards", names)
        self.assertNotIn("create_cash_transaction", names)

    async def test_board_digest_is_paginated_and_omits_ui_fields(self) -> None:
        result = await self._call("agent_board_digest", {"limit": 2})
        payload = result.structuredContent
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]["cards"]), 2)
        self.assertTrue(payload["page"]["has_more"])
        self.assertEqual(payload["page"]["next_cursor"], "2")
        self.assertNotIn("deadline_heat_glow_color", payload["data"]["cards"][0])
        self.assertLessEqual(len(result.content[0].text), 1000)

    async def test_raw_capability_requires_live_schema_hash(self) -> None:
        discovered = await self._call("discover_raw_capabilities", {"query": "get_cards"})
        items = discovered.structuredContent["data"]["capabilities"]
        capability = next(item for item in items if item["name"] == "get_cards")

        rejected = await self._call(
            "call_raw_capability",
            {"name": "get_cards", "arguments": {}, "schema_hash": "stale"},
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "schema_hash_mismatch_rediscover_capability",
            rejected.structuredContent["warnings"],
        )

        accepted = await self._call(
            "call_raw_capability",
            {
                "name": "get_cards",
                "arguments": {"include_archived": False, "compact": True},
                "schema_hash": capability["schema_hash"],
            },
        )
        self.assertTrue(accepted.structuredContent["ok"])
        self.assertTrue(accepted.structuredContent["verification"]["schema_hash_verified"])

    async def test_raw_write_requires_idempotency_key(self) -> None:
        discovered = await self._call("discover_raw_capabilities", {"query": "create_sticky"})
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "create_sticky"
        )
        rejected = await self._call(
            "call_raw_capability",
            {
                "name": "create_sticky",
                "arguments": {
                    "text": "test",
                    "x": 0,
                    "y": 0,
                    "deadline": {"total_seconds": 60},
                },
                "schema_hash": capability["schema_hash"],
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "idempotency_key_required_for_raw_write",
            rejected.structuredContent["warnings"],
        )

    async def test_raw_write_fails_closed_when_durable_manager_ledger_is_missing(self) -> None:
        discovered = await self._call(
            "discover_raw_capabilities", {"query": "api:/api/create_cashbox_transfer"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_cashbox_transfer"
        )
        rejected = await self._call(
            "call_raw_capability",
            {
                "name": capability["name"],
                "arguments": {
                    "from_cashbox_id": "cash-1",
                    "to_cashbox_id": "cash-2",
                    "amount": "1000",
                },
                "schema_hash": capability["schema_hash"],
                "idempotency_key": "transfer-cash-1-cash-2-v1",
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertEqual(self.board_api.raw_requests, [])
        self.assertIn("durable_workflow_ledger_unavailable", rejected.structuredContent["warnings"])

    async def test_finance_update_requires_optimistic_revision_before_ledger(self) -> None:
        rejected = await self._call(
            "agent_finance_workflow",
            {
                "operation": "update_repair_order",
                "payload": {"card_id": "card-1", "repair_order": {"comment": "x"}},
                "idempotency_key": "repair-order-card-1-v1",
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "expected_updated_at_required_reread_exact_card_first",
            rejected.structuredContent["warnings"],
        )

    async def test_virtual_raw_capability_covers_hidden_internal_crm_writes(self) -> None:
        discovered = await self._call(
            "discover_raw_capabilities", {"query": "create_employee_salary_transaction"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_employee_salary_transaction"
        )
        schema = await self._call("get_raw_capability_schema", {"name": capability["name"]})
        self.assertTrue(schema.structuredContent["ok"])
        self.assertEqual(schema.structuredContent["summary"]["risk"], "write")
        self.assertTrue(schema.structuredContent["data"]["input_schema"]["additionalProperties"])

    async def test_virtual_raw_schema_hash_is_bound_to_exact_route(self) -> None:
        first = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/create_cashbox_transfer"},
        )
        second = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/update_repair_order"},
        )

        self.assertNotEqual(
            first.structuredContent["summary"]["schema_hash"],
            second.structuredContent["summary"]["schema_hash"],
        )
        self.assertEqual(
            second.structuredContent["data"]["input_schema"]["title"],
            "/api/update_repair_order",
        )

    async def test_virtual_operator_admin_read_is_available_without_write_ledger(self) -> None:
        schema = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/list_operator_users"},
        )
        self.assertEqual(schema.structuredContent["summary"]["risk"], "read")

        result = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/list_operator_users",
                "arguments": {},
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            },
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(self.board_api.raw_requests[-1]["path"], "/api/list_operator_users")

    async def test_finance_switch_blocks_virtual_repair_order_and_payroll_bypasses(self) -> None:
        with patch.dict(
            "os.environ",
            {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"},
            clear=False,
        ):
            server = create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41835,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

        async def assert_blocked(name: str, arguments: dict) -> None:
            schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
                {"name": name}, convert_result=False
            )
            with patch.dict(
                "os.environ",
                {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"},
                clear=False,
            ):
                result = await server._tool_manager.get_tool("call_raw_capability").run(
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                        "idempotency_key": f"blocked-{name}",
                    },
                    convert_result=False,
                )
            self.assertFalse(result.structuredContent["ok"])
            self.assertIn("agent_gateway_finance_disabled", result.structuredContent["warnings"])

        await assert_blocked(
            "api:/api/update_repair_order",
            {"card_id": "card-1", "repair_order": {"payments": []}},
        )
        await assert_blocked(
            "api:/api/save_employee",
            {"employee": {"id": "employee-1", "salary": "1000"}},
        )

    async def test_production_master_switch_fails_closed_to_diagnostics(self) -> None:
        production_env = {
            **GATEWAY_ENV,
            "AUTOSTOP_DEPLOYMENT_ENV": "production",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "0",
            "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED": "0",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
        }
        with patch.dict("os.environ", production_env, clear=False):
            server = create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41836,
                path="/mcp",
                bearer_token="b" * 48,
                public_endpoint_url="https://crm.example/mcp",
            )

        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertEqual(
            names,
            {"ping_connector", "get_connector_identity", "get_runtime_status"},
        )
        self.assertNotIn("create_cash_transaction", names)
        self.assertNotIn("update_repair_order", names)

    async def test_raw_write_uses_ledger_and_deduplicates_only_completed_result(self) -> None:
        state = {"started": False, "status": "planned", "scope": None}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, metadata
                if state["started"] and scope != state["scope"]:
                    return {
                        "ok": False,
                        "status": state["status"],
                        "warnings": ["idempotency_key_conflict"],
                    }
                deduplicated = state["started"]
                state["started"] = True
                state["scope"] = scope
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 77,
                    "status": state["status"],
                    "summary": {
                        "id": 77,
                        "status": state["status"],
                        "deduplicated": deduplicated,
                    },
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 77,
                    "status": status,
                    "summary": {"id": 77, "status": status},
                }

        self.manager_register.side_effect = register_fake_ledger
        board_api = FakeBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41833,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

        async def call(name: str, arguments: dict):
            return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

        discovered = await call(
            "discover_raw_capabilities", {"query": "api:/api/create_cashbox_transfer"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_cashbox_transfer"
        )
        arguments = {
            "name": capability["name"],
            "arguments": {
                "from_cashbox_id": "cash-1",
                "to_cashbox_id": "cash-2",
                "amount": "1000",
                "actor_name": "spoofed-human",
                "source": "ui",
            },
            "schema_hash": capability["schema_hash"],
            "idempotency_key": "transfer-cash-1-cash-2-v1",
        }
        first = await call("call_raw_capability", arguments)
        duplicate = await call("call_raw_capability", arguments)

        self.assertTrue(first.structuredContent["ok"])
        self.assertTrue(first.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(len(board_api.raw_requests), 1)
        self.assertEqual(board_api.raw_requests[0]["payload"]["actor_name"], "codex-owner-agent")
        self.assertEqual(board_api.raw_requests[0]["payload"]["source"], "mcp_agent_gateway_v2")
        self.assertTrue(duplicate.structuredContent["ok"])
        self.assertIn(
            "idempotency_reused_completed_result", duplicate.structuredContent["warnings"]
        )
        self.assertEqual(len(board_api.raw_requests), 1)

    async def test_named_repair_order_payment_reconciles_order_and_cash_journal(self) -> None:
        state = {"started": False, "status": "planned"}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata
                state["started"] = True
                return {
                    "ok": True,
                    "run_id": 88,
                    "status": state["status"],
                    "summary": {"id": 88, "deduplicated": False},
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 88, "status": status, "summary": {}}

        self.manager_register.side_effect = register_fake_ledger
        board_api = FakeBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41834,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        tool = server._tool_manager.get_tool("agent_finance_workflow")
        result = await tool.run(
            {
                "operation": "record_repair_order_payment",
                "payload": {
                    "card_id": "card-1",
                    "cashbox_id": "cashbox-main",
                    "amount": "1000",
                    "payment_method": "cash",
                    "expected_updated_at": "2026-07-11T00:00:00+00:00",
                    "note": "Полная оплата заказ-наряда 42",
                },
                "idempotency_key": "payment-card-1-full-v1",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertTrue(result.structuredContent["verification"]["ledger_closed"])
        evidence = result.structuredContent["verification"]["evidence"]
        self.assertTrue(evidence["cash_journal_entry_present"])
        self.assertEqual(len(board_api.repair_order_payments), 1)
        self.assertEqual(len(board_api.cash_transactions), 1)

    async def test_payment_readback_failure_enters_compensating_after_write(self) -> None:
        state = {"status": "planned"}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata
                return {
                    "ok": True,
                    "run_id": 89,
                    "status": state["status"],
                    "summary": {"id": 89, "deduplicated": False},
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 89, "status": status, "summary": {}}

        class MismatchedPaymentBoardApi(FakeBoardApi):
            def update_repair_order(self, **kwargs) -> dict:
                result = super().update_repair_order(**kwargs)
                self.repair_order_payments[-1]["payment_method"] = "cashless"
                return result

        self.manager_register.side_effect = register_fake_ledger
        board_api = MismatchedPaymentBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41837,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        result = await server._tool_manager.get_tool("agent_finance_workflow").run(
            {
                "operation": "record_repair_order_payment",
                "payload": {
                    "card_id": "card-1",
                    "cashbox_id": "cashbox-main",
                    "amount": "1000",
                    "payment_method": "cash",
                    "expected_updated_at": "2026-07-11T00:00:00+00:00",
                },
                "idempotency_key": "payment-card-1-mismatch-v1",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["status"], "compensating")
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertFalse(
            result.structuredContent["verification"]["evidence"]["payment_method_exact"]
        )
        self.assertIn(
            "verification_failed_compensation_required",
            result.structuredContent["warnings"],
        )
        self.assertEqual(len(board_api.cash_transactions), 1)

    async def test_production_bearer_mode_exposes_no_embedded_oauth_provider(self) -> None:
        with patch.dict(
            "os.environ",
            {
                **GATEWAY_ENV,
                "AUTOSTOP_DEPLOYMENT_ENV": "production",
                "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED": "0",
                "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            },
            clear=False,
        ):
            server = create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41832,
                path="/mcp",
                bearer_token="a" * 48,
                public_endpoint_url="https://crm.example/mcp",
            )
        self.assertIsNone(server._auth_server_provider)
        self.assertIsInstance(server._token_verifier, StaticBearerTokenVerifier)
        self.assertIsNotNone(await server._token_verifier.verify_token("a" * 48))
        self.assertIsNone(await server._token_verifier.verify_token("wrong"))


if __name__ == "__main__":
    unittest.main()
