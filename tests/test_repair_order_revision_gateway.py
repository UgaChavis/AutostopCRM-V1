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

from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import GATEWAY_ENV, register_fake_store_manager_tools


class RepairOrderRevisionGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.manager_state: dict = {}
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager_patch = patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=lambda server, logger: register_fake_store_manager_tools(
                server, logger, self.manager_state
            ),
        )
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.board_api = BoardApiClient("https://crm.example", bearer_token="synthetic")
        # Stop at the HTTP boundary while exercising the actual gateway, MCP tool,
        # and BoardApiClient payload construction. No network or CRM data is used.
        self.request_patch = patch.object(
            self.board_api,
            "_request",
            return_value={
                "ok": False,
                "error": {
                    "code": "card_update_conflict",
                    "message": "Synthetic stale revision",
                },
            },
        )
        self.request = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41842,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def _call(self, name: str, arguments: dict):
        tool = self.server._tool_manager.get_tool(name)
        self.assertIsNotNone(tool)
        return await tool.run(arguments, convert_result=False)

    async def _replace(self, name: str, *, expected_updated_at: str | None = None):
        schema = await self._call("get_raw_capability_schema", {"name": name})
        self.assertTrue(schema.structuredContent["ok"])
        arguments = {
            "card_id": "synthetic-card",
            "rows": [{"id": "row-1", "name": "Synthetic row", "quantity": "1", "price": "100"}],
        }
        if expected_updated_at is not None:
            arguments["expected_updated_at"] = expected_updated_at
        return await self._call(
            "call_raw_capability",
            {
                "name": name,
                "arguments": arguments,
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": f"repair-order-revision:{name}",
            },
        )

    async def test_missing_revision_blocks_both_tables_and_capability_names_before_ledger(
        self,
    ) -> None:
        for table in ("works", "materials"):
            for prefix in ("", "api:/api/"):
                name = f"{prefix}replace_repair_order_{table}"
                with self.subTest(capability=name):
                    result = await self._replace(name)

                    self.assertFalse(result.structuredContent["ok"])
                    self.assertEqual(result.structuredContent["status"], "blocked")
                    self.assertIn(
                        "expected_updated_at_required_reread_exact_card_first",
                        result.structuredContent["warnings"],
                    )
                    self.request.assert_not_called()
                    self.assertEqual(self.manager_state["workflow_runs"], {})

    async def test_supplied_revision_reaches_http_for_both_tables_and_capability_names(
        self,
    ) -> None:
        revision = "2026-10-30T08:00:00+00:00"
        for table in ("works", "materials"):
            for prefix in ("", "api:/api/"):
                name = f"{prefix}replace_repair_order_{table}"
                with self.subTest(capability=name):
                    self.request.reset_mock()
                    result = await self._replace(name, expected_updated_at=revision)

                    self.request.assert_called_once()
                    route, payload = self.request.call_args.args[:2]
                    self.assertEqual(route, f"/api/replace_repair_order_{table}")
                    self.assertEqual(payload["card_id"], "synthetic-card")
                    self.assertEqual(payload.get("expected_updated_at"), revision)
                    self.assertEqual(payload["rows"][0]["id"], "row-1")
                    self.assertEqual(payload["rows"][0]["name"], "Synthetic row")
                    self.assertFalse(result.structuredContent["ok"])
                    self.assertEqual(result.structuredContent["status"], "failed")


if __name__ == "__main__":
    unittest.main()
