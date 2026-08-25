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

from minimal_kanban.mcp.server import create_mcp_server

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
