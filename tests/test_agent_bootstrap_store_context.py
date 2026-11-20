from __future__ import annotations

# ruff: noqa: E402
import logging
import unittest
from unittest.mock import Mock, patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from mcp.server.fastmcp.exceptions import ToolError

from minimal_kanban.mcp.gateway_store_context import bootstrap_store_context_requests
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class AgentBootstrapStoreContextTests(unittest.IsolatedAsyncioTestCase):
    """Exercise bootstrap boundaries with shared fakes, without the full gateway case."""

    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        environment = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

    def _create_store_server(self, *, board_api: FakeBoardApi | None = None):
        state: dict = {}
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=lambda server, logger: register_fake_store_manager_tools(
                server, logger, state
            ),
        ):
            server = create_mcp_server(
                board_api or FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41839,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )
        return server, state

    async def test_runtime_status_reports_degraded_crm_probes_without_losing_diagnostics(self):
        failure = {"ok": False, "error": {"code": "synthetic_dependency_unavailable"}}
        for condition in (
            "ready",
            "health_failed",
            "context_failed",
            "maintenance",
            "invalid_health",
            "invalid_context",
        ):
            with self.subTest(condition=condition):
                board_api = FakeBoardApi()
                if condition == "health_failed":
                    board_api.health = Mock(return_value=failure)
                elif condition == "context_failed":
                    board_api.get_board_context = Mock(return_value=failure)
                elif condition == "maintenance":
                    board_api.health = Mock(
                        return_value={
                            "ok": True,
                            "data": {"status": "ok", "maintenance_mode": True},
                        }
                    )
                elif condition == "invalid_health":
                    board_api.health = Mock(return_value={"ok": True, "data": {"status": []}})
                elif condition == "invalid_context":
                    board_api.get_board_context = Mock(return_value={"ok": True, "data": {}})
                server, state = self._create_store_server(board_api=board_api)
                include_store = condition == "context_failed"
                result = await server._tool_manager.get_tool("get_runtime_status").run(
                    {"include_store_context": include_store}, convert_result=False
                )

                self.assertFalse(result.isError)
                payload = result.structuredContent
                self.assertTrue(payload["ok"])
                self.assertEqual("ready" if condition == "ready" else "degraded", payload["status"])
                self.assertEqual(
                    condition != "ready", "crm_runtime_degraded" in payload["warnings"]
                )
                runtime = payload["data"]["runtime_status"]
                if condition == "health_failed":
                    self.assertEqual(failure["error"], runtime["api_health_error"])
                if condition == "context_failed":
                    self.assertEqual(failure["error"], runtime["board_context_error"])
                    self.assertTrue(payload["data"]["store"]["ok"])
                else:
                    self.assertEqual("not_requested", payload["data"]["store"]["status"])
                    self.assertFalse(any(name.startswith("store_") for name, _ in state["calls"]))

    async def test_bootstrap_query_or_intent_defaults_to_crm_only(self) -> None:
        for context in ({"query": "article-123"}, {"intent": "parts_quote"}):
            for store_option in (
                {},
                {"include_store_context": False},
                {"include_store_context": None},
            ):
                with self.subTest(context=context, store_option=store_option):
                    server, state = self._create_store_server()
                    arguments = {**context, **store_option}

                    result = await server._tool_manager.get_tool("agent_bootstrap").run(
                        arguments, convert_result=False
                    )

                    self.assertTrue(result.structuredContent["ok"])
                    self.assertEqual("ready", result.structuredContent["status"])
                    self.assertEqual(
                        "not_requested", result.structuredContent["summary"]["store"]["status"]
                    )
                    self.assertFalse(any(name.startswith("store_") for name, _ in state["calls"]))
                    self.assertEqual(
                        [
                            {
                                "query": context.get("query", ""),
                                "intent": context.get("intent"),
                                "limit": 8,
                            }
                        ],
                        state["manager_bootstrap_calls"],
                    )

    def test_bootstrap_store_helper_requires_literal_true(self) -> None:
        for value in (None, False, 0, 1, "true"):
            with self.subTest(value=value):
                invoke = Mock()
                requested, requests = bootstrap_store_context_requests(
                    query="article-123",
                    intent="parts_quote",
                    include_store_context=value,
                    invoke=invoke,
                )
                self.assertFalse(requested)
                self.assertEqual([], requests)
                invoke.assert_not_called()

        invoke = Mock()
        requested, requests = bootstrap_store_context_requests(
            query="article-123", intent="parts_quote", invoke=invoke
        )
        self.assertFalse(requested)
        self.assertEqual([], requests)
        invoke.assert_not_called()

    async def test_bootstrap_rejects_non_boolean_store_opt_in_before_any_read(self) -> None:
        server, state = self._create_store_server()
        tool = server._tool_manager.get_tool("agent_bootstrap")
        for value in (1, "true"):
            with self.subTest(value=value), self.assertRaises(ToolError):
                await tool.run(
                    {"query": "article-123", "include_store_context": value}, convert_result=False
                )
        self.assertEqual([], state.get("manager_bootstrap_calls", []))
        self.assertFalse(any(name.startswith("store_") for name, _ in state["calls"]))

    async def test_bootstrap_explicit_store_context_uses_intent_without_query(self) -> None:
        server, state = self._create_store_server()

        result = await server._tool_manager.get_tool("agent_bootstrap").run(
            {"intent": "parts_quote", "include_store_context": True}, convert_result=False
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertTrue(result.structuredContent["summary"]["store"]["requested"])
        calls = [arguments for name, arguments in state["calls"] if name == "store_search"]
        self.assertEqual(2, len(calls))
        self.assertEqual({"store_quote_request", "store_part"}, {call["entity"] for call in calls})
        self.assertTrue(
            all(call["query"] == "parts_quote" and call["limit"] == 4 for call in calls)
        )
