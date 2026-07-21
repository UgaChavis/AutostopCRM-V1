from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.agent_gateway_v2 import PERMANENT_AGENT_GATEWAY_TOOL_NAMES
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import GATEWAY_ENV, FakeBoardApi


class StoreOwnerGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def _register_store_owner_tools(self, server, _logger) -> None:
        @server.tool(
            name="store_owner_capabilities",
            description="READ_ONLY RAW_CAPABILITY Store owner operation inventory",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )
        def store_owner_capabilities(query: str = "", limit: int = 50) -> dict:
            arguments = {"query": query, "limit": limit}
            self.calls.append(("store_owner_capabilities", arguments))
            return {"ok": True, "items": [{"operation_id": "get_part"}]}

        @server.tool(
            name="store_owner_api",
            description="OWNER_SCOPED RAW_CAPABILITY Store owner transport",
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
        )
        def store_owner_api(operation_id: str, mode: str = "read") -> dict:
            arguments = {"operation_id": operation_id, "mode": mode}
            self.calls.append(("store_owner_api", arguments))
            return {"ok": True, "status": "completed"}

    def _server(self):
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=self._register_store_owner_tools,
        ):
            return create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41847,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

    async def test_owner_tools_use_discovery_schema_and_guarded_call(self) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")
        schema = server._tool_manager.get_tool("get_raw_capability_schema")
        raw_call = server._tool_manager.get_tool("call_raw_capability")

        discovered = await discover.run({"query": "store_owner"}, convert_result=False)
        capabilities = {
            item["name"]: item for item in discovered.structuredContent["data"]["capabilities"]
        }
        self.assertEqual(
            {"store_owner_capabilities", "store_owner_api"},
            set(capabilities),
        )
        self.assertEqual("read", capabilities["store_owner_capabilities"]["risk"])
        self.assertEqual("write", capabilities["store_owner_api"]["risk"])

        read_schema = await schema.run({"name": "store_owner_capabilities"}, convert_result=False)
        write_schema = await schema.run({"name": "store_owner_api"}, convert_result=False)
        self.assertEqual("read", read_schema.structuredContent["summary"]["risk"])
        self.assertEqual("write", write_schema.structuredContent["summary"]["risk"])

        read = await raw_call.run(
            {
                "name": "store_owner_capabilities",
                "arguments": {"query": "parts", "limit": 10},
                "schema_hash": read_schema.structuredContent["summary"]["schema_hash"],
            },
            convert_result=False,
        )
        self.assertTrue(read.structuredContent["ok"])
        self.assertEqual(
            [("store_owner_capabilities", {"query": "parts", "limit": 10})],
            self.calls,
        )

        blocked_write = await raw_call.run(
            {
                "name": "store_owner_api",
                "arguments": {"operation_id": "update_part", "mode": "apply"},
                "schema_hash": write_schema.structuredContent["summary"]["schema_hash"],
            },
            convert_result=False,
        )
        self.assertFalse(blocked_write.structuredContent["ok"])
        self.assertIn(
            "idempotency_key_required_for_raw_write",
            blocked_write.structuredContent["warnings"],
        )
        self.assertFalse(any(name == "store_owner_api" for name, _ in self.calls))

    def test_public_gateway_surface_remains_exactly_24(self) -> None:
        server = self._server()

        self.assertEqual(24, len(PERMANENT_AGENT_GATEWAY_TOOL_NAMES))
        self.assertLessEqual(set(server._tool_manager._tools), PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
        self.assertNotIn("store_owner_capabilities", server._tool_manager._tools)
        self.assertNotIn("store_owner_api", server._tool_manager._tools)


if __name__ == "__main__":
    unittest.main()
