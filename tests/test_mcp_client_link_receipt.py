"""MCP client-link receipt remains consistent with the card readback."""

import unittest

from tests.mcp_credentials import MCP_TEST_TOKEN
from tests.test_mcp import (
    _McpServerFixtureMixin,
    create_test_mcp_http_client,
    open_mcp_session,
)


class McpClientLinkReceiptTests(_McpServerFixtureMixin, unittest.IsolatedAsyncioTestCase):
    async def test_client_link_receipt_matches_mcp_card_readback(self) -> None:
        client = self.service.create_client({"display_name": "N" * 150})["client"]
        card = self.service.create_card({"title": "SYNTHETIC MCP LINK"})["card"]
        async with create_test_mcp_http_client(
            headers={"Authorization": f"Bearer {MCP_TEST_TOKEN}"}
        ) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                linked = await session.call_tool(
                    "link_card_to_client",
                    {
                        "card_id": card["id"],
                        "client_id": client["id"],
                        "expected_card_updated_at": card["updated_at"],
                        "expected_client_updated_at": client["updated_at"],
                    },
                )
                self.assertFalse(linked.isError, linked)
                receipt = linked.structuredContent["data"]["card"]
                readback = await session.call_tool("get_card", {"card_id": card["id"]})
                self.assertFalse(readback.isError, readback)
                self.assertEqual(receipt["vehicle_profile"]["customer_name"], "N" * 120)
                self.assertEqual(
                    receipt["vehicle_profile"]["customer_name"],
                    readback.structuredContent["data"]["card"]["vehicle_profile"]["customer_name"],
                )
