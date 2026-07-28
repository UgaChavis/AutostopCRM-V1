from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.server import create_mcp_server


class FakeBoardApi:
    base_url = "http://api.example"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.wall_response: Any = {"ok": True, "data": {"text": "wall"}}
        self.context_response: Any = {"ok": True, "data": {"context": {}}}
        self.events_response: Any = {"ok": True, "data": {"text": "events"}}
        self.content_response: Any = {"ok": True, "data": {"text": "content"}}
        self.card_log_response: Any = {"ok": True, "data": {"events": []}}

    def health(self) -> dict[str, Any]:
        return {"ok": True, "data": {"status": "ok"}}

    def get_board_context(self) -> Any:
        return self.context_response

    def get_gpt_wall(self, **kwargs: Any) -> Any:
        self.calls.append(("get_gpt_wall", kwargs))
        return self.wall_response

    def get_board_events(self, **kwargs: Any) -> Any:
        self.calls.append(("get_board_events", kwargs))
        return self.events_response

    def get_board_content(self, **kwargs: Any) -> Any:
        self.calls.append(("get_board_content", kwargs))
        return self.content_response

    def get_card_log(self, card_id: str, **kwargs: Any) -> Any:
        self.calls.append(("get_card_log", {"card_id": card_id, **kwargs}))
        return self.card_log_response

    def __getattr__(self, name: str) -> Any:
        def _method(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = dict(kwargs)
            if args:
                payload["args"] = args
            self.calls.append((name, payload))
            return {"ok": True, "data": {"meta": dict(kwargs)}}

        return _method


class McpServerHardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            logging.getLogger(f"test.mcp_hardening.{self._testMethodName}"),
            host="127.0.0.1",
            port=41891,
            path="/mcp",
            bearer_token=None,
        )

    async def _call(self, tool_name: str, args: dict[str, Any] | None = None) -> Any:
        return await self.server._tool_manager.call_tool(tool_name, args or {})

    def _last_call(self, name: str) -> dict[str, Any]:
        for call_name, payload in reversed(self.board_api.calls):
            if call_name == name:
                return payload
        self.fail(f"Expected board API call {name!r}")

    async def test_board_tool_returns_structured_error_for_non_mapping_envelope(self) -> None:
        self.board_api.wall_response = ["not", "an", "envelope"]

        result = await self._call("get_gpt_wall", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "board_api_malformed_response")
        self.assertEqual(result.error["response_type"], "list")

    async def test_bootstrap_handles_non_object_wall_data(self) -> None:
        self.board_api.wall_response = {"ok": True, "data": ["not", "object"]}

        result = await self._call("bootstrap_context", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "board_api_malformed_response")
        self.assertEqual(result.data["identity"]["board_scope"], "single_local_board_instance")

    async def test_board_tool_returns_structured_error_for_non_object_data(self) -> None:
        self.board_api.content_response = {"ok": True, "data": ["bad"]}

        result = await self._call("get_board_content", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "board_api_malformed_response")
        self.assertEqual(result.error["data_type"], "list")

    async def test_board_tool_sanitizes_bad_meta_containers(self) -> None:
        self.board_api.events_response = {
            "ok": True,
            "data": {"text": "events", "meta": ["bad"]},
            "meta": ["bad"],
        }

        result = await self._call("get_board_events", {"event_limit": 15})

        self.assertTrue(result.ok)
        self.assertEqual(result.data["meta"]["event_limit"], 15)
        self.assertEqual(result.meta["event_limit"], 15)

    async def test_malformed_public_endpoint_does_not_break_connector_identity(self) -> None:
        server = create_mcp_server(
            self.board_api,
            logging.getLogger(f"test.mcp_hardening.{self._testMethodName}.malformed"),
            host="127.0.0.1",
            port=41892,
            path="/mcp",
            bearer_token=None,
            public_endpoint_url="http://[bad",
        )

        result = await server._tool_manager.call_tool("get_connector_identity", {})

        self.assertTrue(result.ok)
        self.assertEqual(result.data.identity.connector_name, "autostopcrm-this-board-only-local")
        self.assertEqual(result.data.identity.server_base_url, "http://127.0.0.1:41892")

    async def test_malformed_public_endpoint_with_bearer_falls_back_before_auth(self) -> None:
        with patch.dict(os.environ, {"MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": ""}):
            server = create_mcp_server(
                self.board_api,
                logging.getLogger(f"test.mcp_hardening.{self._testMethodName}.malformed_auth"),
                host="127.0.0.1",
                port=41893,
                path="/mcp",
                bearer_token="mcp-secret",
                public_endpoint_url="http://[bad",
            )

        result = await server._tool_manager.call_tool("get_connector_identity", {})

        self.assertTrue(result.ok)
        self.assertEqual(result.data.identity.connector_name, "autostopcrm-this-board-only-local")
        self.assertEqual(result.data.identity.server_base_url, "http://127.0.0.1:41893")
        self.assertEqual(result.data.identity.resource_url, "http://127.0.0.1:41893/mcp")
        self.assertEqual(result.data.identity.auth_mode, "oauth_2_1_pkce")

    async def test_invalid_port_public_endpoint_falls_back_to_local_identity(self) -> None:
        server = create_mcp_server(
            self.board_api,
            logging.getLogger(f"test.mcp_hardening.{self._testMethodName}.invalid_port"),
            host="127.0.0.1",
            port=41894,
            path="/mcp",
            bearer_token=None,
            public_endpoint_url="https://agent.example:bad/mcp",
        )

        result = await server._tool_manager.call_tool("get_connector_identity", {})

        self.assertTrue(result.ok)
        self.assertEqual(result.data.identity.server_base_url, "http://127.0.0.1:41894")
        self.assertEqual(result.data.identity.resource_url, "http://127.0.0.1:41894/mcp")

    async def test_mcp_integer_arguments_reject_bool_before_backend_call(self) -> None:
        with self.assertRaises(ToolError):
            await self._call("manager_board_scan", {"limit": True})
        with self.assertRaises(ToolError):
            await self._call("create_card", {"title": "Новая заявка", "deadline": {"days": True}})

        self.assertNotIn("manager_board_scan", [name for name, _payload in self.board_api.calls])

    async def test_heavy_wall_and_event_limits_are_clamped_before_backend_call(self) -> None:
        await self._call("get_gpt_wall", {"event_limit": 999999, "view_mode": "full"})
        await self._call("get_board_events", {"event_limit": 999999})

        self.assertEqual(
            self.board_api.calls[0],
            (
                "get_gpt_wall",
                {
                    "include_archived": True,
                    "event_limit": 5000,
                    "compact": False,
                },
            ),
        )
        self.assertEqual(
            self.board_api.calls[1],
            (
                "get_board_events",
                {
                    "event_limit": 5000,
                    "include_archived": True,
                    "view_mode": "audit",
                },
            ),
        )

    async def test_compact_card_log_limit_is_clamped_before_backend_call(self) -> None:
        await self._call("get_card_log", {"card_id": "card-1", "compact": True, "limit": 999999})

        self.assertEqual(
            self.board_api.calls[-1],
            (
                "get_card_log",
                {
                    "card_id": "card-1",
                    "limit": 1000,
                    "compact": True,
                    "include_full_details": False,
                },
            ),
        )

    async def test_read_context_and_manager_limits_are_clamped_before_backend_call(self) -> None:
        await self._call("get_card_context", {"card_id": "card-1", "event_limit": 999999})
        await self._call("get_board_snapshot", {"archive_limit": 999999})
        await self._call(
            "review_board",
            {
                "stale_hours": 999999,
                "overload_threshold": 999999,
                "priority_limit": 999999,
                "recent_event_limit": 999999,
            },
        )
        await self._call("manager_board_scan", {"limit": 999999})
        await self._call("audit_client_links", {"limit": 999999, "candidate_limit": 999999})

        self.assertEqual(self._last_call("get_card_context")["event_limit"], 200)
        self.assertEqual(self._last_call("get_board_snapshot")["archive_limit"], 50)
        self.assertEqual(
            self._last_call("review_board"),
            {
                "stale_hours": 720,
                "overload_threshold": 100,
                "priority_limit": 20,
                "recent_event_limit": 50,
            },
        )
        self.assertEqual(self._last_call("manager_board_scan")["limit"], 200)
        self.assertEqual(self._last_call("audit_client_links")["limit"], 200)
        self.assertEqual(self._last_call("audit_client_links")["candidate_limit"], 10)

    async def test_finance_inventory_client_and_search_limits_are_clamped(self) -> None:
        await self._call("get_cash_journal", {"months": 1e308, "limit": 1e308})
        await self._call(
            "get_cashbox",
            {
                "cashbox_id": "cashbox-1",
                "transaction_limit": 1e308,
                "transaction_offset": 1e308,
            },
        )
        await self._call("list_inventory_items", {"limit": 999999})
        await self._call("list_clients", {"limit": 999999})
        await self._call("search_clients", {"query": "x", "limit": 999999})
        await self._call("get_client", {"client_id": "client-1", "order_limit": 999999})
        await self._call("suggest_clients_for_card", {"card_id": "card-1", "limit": 999999})
        await self._call("list_repair_orders", {"limit": 999999})
        await self._call("list_archived_cards", {"limit": 999999})
        await self._call("search_cards", {"query": "x", "limit": 999999})

        self.assertEqual(self._last_call("get_cash_journal"), {"months": 12, "limit": 10000})
        self.assertEqual(self._last_call("get_cashbox")["transaction_limit"], 5000)
        self.assertEqual(self._last_call("get_cashbox")["transaction_offset"], 1_000_000)
        self.assertEqual(self._last_call("list_inventory_items")["limit"], 500)
        self.assertEqual(self._last_call("list_clients")["limit"], 1000)
        self.assertEqual(self._last_call("search_clients")["limit"], 100)
        self.assertEqual(self._last_call("get_client")["order_limit"], 200)
        self.assertEqual(self._last_call("suggest_clients_for_card")["limit"], 30)
        self.assertEqual(self._last_call("list_repair_orders")["limit"], 300)
        self.assertEqual(self._last_call("list_archived_cards")["limit"], 100)
        self.assertEqual(self._last_call("search_cards")["limit"], 100)

    async def test_bulk_manager_operation_numbers_are_normalized_before_backend_call(self) -> None:
        await self._call(
            "bulk_set_deadline_if_below",
            {
                "min_total_seconds": 999999999,
                "target_total_seconds": 1,
                "limit": 999999,
                "card_ids": ["card-1"],
                "expected_updated_at_by_card_id": {"card-1": "revision-1"},
            },
        )
        await self._call("bulk_refresh_board_summaries", {"limit": 999999})
        await self._call(
            "apply_ready_unpaid_followups",
            {
                "target_total_seconds": 999999999,
                "limit": 999999,
                "card_ids": ["card-1"],
                "expected_updated_at_by_card_id": {"card-1": "revision-1"},
            },
        )
        await self._call(
            "run_manager_operation",
            {
                "operation": "manager_board_scan",
                "payload": {"limit": True},
                "limit": 999999,
            },
        )

        self.assertEqual(
            self._last_call("bulk_set_deadline_if_below"),
            {
                "mode": "dry_run",
                "min_total_seconds": 31_536_000,
                "target_total_seconds": 31_536_000,
                "limit": 1000,
                "include_archived": False,
                "card_ids": ["card-1"],
                "expected_updated_at_by_card_id": {"card-1": "revision-1"},
                "actor_name": None,
            },
        )
        self.assertEqual(self._last_call("bulk_refresh_board_summaries")["limit"], 500)
        self.assertEqual(
            self._last_call("apply_ready_unpaid_followups")["target_total_seconds"],
            31_536_000,
        )
        self.assertEqual(self._last_call("apply_ready_unpaid_followups")["limit"], 200)
        self.assertEqual(
            self._last_call("apply_ready_unpaid_followups")["card_ids"],
            ["card-1"],
        )
        self.assertEqual(
            self._last_call("apply_ready_unpaid_followups")["expected_updated_at_by_card_id"],
            {"card-1": "revision-1"},
        )
        manager_call = self._last_call("run_manager_operation")
        self.assertEqual(manager_call["payload"]["limit"], 50)
        self.assertEqual(manager_call["limit"], 200)


if __name__ == "__main__":
    unittest.main()
