from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.raw_capability_discovery import raw_capability_discovery_score
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import FakeBoardApi

GATEWAY_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
    "AUTOSTOP_MCP_OAUTH_STATE_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
}


class _DiscoveryBoardApi:
    base_url = "http://127.0.0.1:41731"


class RawCapabilityDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    def _server(self):
        return create_mcp_server(
            _DiscoveryBoardApi(),
            self.logger,
            host="127.0.0.1",
            port=41840,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def test_semantic_discovery_hides_writes_but_exact_name_keeps_raw_escape_hatch(
        self,
    ) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")

        semantic = await discover.run({"query": "sticky"}, convert_result=False)
        self.assertNotIn(
            "create_sticky",
            [item["name"] for item in semantic.structuredContent["data"]["capabilities"]],
        )

        exact = await discover.run({"query": "create_sticky"}, convert_result=False)
        selected = next(
            item
            for item in exact.structuredContent["data"]["capabilities"]
            if item["name"] == "create_sticky"
        )
        self.assertEqual("write", selected["risk"])

    def test_automotive_source_aliases_cover_repairs_and_fluids(self) -> None:
        for query in ("метки грм", "какое масло и допуск акпп"):
            with self.subTest(query=query):
                score, matched_terms, exact = raw_capability_discovery_score(
                    query,
                    name="recommend_automotive_sources",
                    description="",
                    schema={},
                )
                self.assertGreater(score, 0)
                self.assertTrue(matched_terms)
                self.assertFalse(exact)


class RawGatewaySafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.board_api = FakeBoardApi()
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    def _server(self, board_api: FakeBoardApi | None = None, *, port: int = 41840):
        return create_mcp_server(
            board_api or self.board_api,
            self.logger,
            host="127.0.0.1",
            port=port,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def _call(self, server, name: str, arguments: dict):
        return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

    async def _raw_write(self, server, key: str):
        schema = await self._call(server, "get_raw_capability_schema", {"name": "create_sticky"})
        return await self._call(
            server,
            "call_raw_capability",
            {
                "name": "create_sticky",
                "arguments": {
                    "text": "ledger safety test",
                    "x": 0,
                    "y": 0,
                    "deadline": {"total_seconds": 60},
                },
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": key,
            },
        )

    async def test_raw_master_switch_fails_closed_before_capability_resolution(self) -> None:
        with patch.dict(
            "os.environ", {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "0"}, clear=False
        ):
            result = await self._call(
                self._server(port=41832),
                "call_raw_capability",
                {"name": "get_cards", "arguments": {}, "schema_hash": "unused"},
            )

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("agent_gateway_raw_disabled", result.structuredContent["warnings"])
        self.assertEqual([], self.board_api.raw_requests)

    async def test_maintenance_blocks_domain_raw_and_ledger_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / ".agent-gateway-maintenance"
            marker.touch()
            with patch.dict(
                "os.environ",
                {**GATEWAY_ENV, "AUTOSTOP_MAINTENANCE_MARKER": str(marker)},
                clear=False,
            ):
                server = self._server(port=41833)
                inventory = await self._call(
                    server,
                    "agent_inventory_workflow",
                    {
                        "operation": "save_inventory_item",
                        "payload": {},
                        "idempotency_key": "maintenance-inventory-write",
                    },
                )
                ledger = await self._call(
                    server,
                    "start_workflow",
                    {
                        "workflow_id": "maintenance-test",
                        "intent": "prove writes are blocked",
                        "idempotency_key": "maintenance-ledger-write",
                    },
                )
                schema = await self._call(
                    server, "get_raw_capability_schema", {"name": "create_sticky"}
                )
                raw = await self._call(
                    server,
                    "call_raw_capability",
                    {
                        "name": "create_sticky",
                        "arguments": {
                            "text": "maintenance safety test",
                            "x": 0,
                            "y": 0,
                            "deadline": {"total_seconds": 60},
                        },
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                        "idempotency_key": "maintenance-raw-write",
                    },
                )

        self.assertFalse(inventory.structuredContent["ok"])
        self.assertIn(
            "maintenance_mode_domain_writes_blocked", inventory.structuredContent["warnings"]
        )
        self.assertFalse(ledger.structuredContent["ok"])
        self.assertIn(
            "maintenance_mode_workflow_ledger_write_blocked", ledger.structuredContent["warnings"]
        )
        self.assertFalse(raw.structuredContent["ok"])
        self.assertIn("maintenance_mode_raw_write_blocked", raw.structuredContent["warnings"])
        self.assertEqual([], self.board_api.raw_requests)

    async def test_raw_discovery_unknown_and_invalid_virtual_calls_fail_closed(self) -> None:
        server = self._server()
        discovered = await self._call(server, "discover_raw_capabilities", {})
        self.assertTrue(
            all(
                item["risk"] == "read"
                for item in discovered.structuredContent["data"]["capabilities"]
            )
        )
        unknown = await self._call(
            server,
            "call_raw_capability",
            {"name": "not-a-capability", "arguments": {}, "schema_hash": "unused"},
        )
        self.assertFalse(unknown.structuredContent["ok"])
        self.assertIn("capability_not_found", unknown.structuredContent["warnings"])
        schema = await self._call(
            server, "get_raw_capability_schema", {"name": "api:/api/change_feed/read"}
        )
        invalid = await self._call(
            server,
            "call_raw_capability",
            {
                "name": "api:/api/change_feed/read",
                "arguments": {},
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            },
        )
        self.assertFalse(invalid.structuredContent["ok"])
        self.assertIn("raw_schema_validation_failed", invalid.structuredContent["warnings"])
        self.assertEqual([], self.board_api.raw_requests)

    async def test_raw_write_requires_a_usable_durable_ledger_before_execution(self) -> None:
        def register_start(server, _logger, payload: dict) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
                dry_run: bool = False,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata, dry_run
                return payload

        def register_without_run_id(server, logger) -> None:
            register_start(server, logger, {"ok": True, "status": "planned", "summary": {}})

        def register_without_transition(server, logger) -> None:
            register_start(
                server, logger, {"ok": True, "run_id": 91, "status": "planned", "summary": {}}
            )

        self.manager_register.side_effect = register_without_run_id
        no_run_board = FakeBoardApi()
        no_run = await self._raw_write(
            self._server(no_run_board, port=41834), "raw-write-without-run-id"
        )
        self.manager_register.side_effect = register_without_transition
        no_transition_board = FakeBoardApi()
        no_transition = await self._raw_write(
            self._server(no_transition_board, port=41835), "raw-write-without-transition"
        )

        self.assertFalse(no_run.structuredContent["ok"])
        self.assertIn("durable_workflow_run_id_unavailable", no_run.structuredContent["warnings"])
        self.assertFalse(no_transition.structuredContent["ok"])
        self.assertIn(
            "workflow_enter_executing_failed", no_transition.structuredContent["warnings"]
        )
        self.assertEqual([], no_run_board.raw_requests)
        self.assertEqual([], no_transition_board.raw_requests)
