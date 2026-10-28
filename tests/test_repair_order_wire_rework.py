from __future__ import annotations

# The service fixture module selects this checkout before application imports.
# ruff: noqa: I001
import json
import logging
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from mcp.types import CallToolRequest, CallToolRequestParams

from tests.services_case import CardServiceCase  # noqa: F401
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class RepairOrderWireReworkTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict("os.environ", {**GATEWAY_ENV, "APPDATA": self.temp_dir.name})
        env.start()
        self.addCleanup(env.stop)
        manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        manager.start()
        self.addCleanup(manager.stop)
        logger = logging.getLogger("test.repair_order_wire_rework")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.server = create_mcp_server(
            FakeBoardApi(),
            logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def wire_call(self, name, redact, marker):
        request = CallToolRequest(
            params=CallToolRequestParams(
                name="call_raw_capability",
                arguments={
                    "name": name,
                    "arguments": {"redact_private": redact},
                    "schema_hash": {"secret": marker},
                },
            )
        )
        result = await self.server._mcp_server.request_handlers[CallToolRequest](request)
        self.assertTrue(result.root.isError)
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    async def test_spaced_names_are_masked_but_unredacted_and_non_target_are_unchanged(self):
        marker = "PRIVATE_WIRE_REWORK_SENTINEL"
        for name in (
            " list_repair_orders ",
            "\tlist_repair_orders\n",
            "\u00a0list_repair_orders\u00a0",
        ):
            with self.subTest(name=name, redact=True):
                self.assertNotIn(marker, await self.wire_call(name, True, marker))
            with self.subTest(name=name, redact=False):
                self.assertIn(marker, await self.wire_call(name, False, marker))
        with self.subTest(name="non-target"):
            self.assertIn(marker, await self.wire_call(" list_repair_orders_extra ", True, marker))
