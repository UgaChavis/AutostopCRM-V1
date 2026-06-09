from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiClient


class BoardApiClientInventoryTests(unittest.TestCase):
    def test_inventory_helpers_call_expected_api_endpoints(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.list_inventory_items()
            client.search_inventory_items(query="масло", limit=10)
            client.get_inventory_item("item-1")
            client.list_inventory_movements(item_id="item-1")
            client.save_inventory_item(
                {
                    "name": "Масло",
                    "unit": "л",
                    "quantity": "5.5",
                    "cost_price": "500",
                    "sale_price": "800",
                },
                actor_name="ADMIN",
            )
            client.replenish_inventory_item("item-1", "2", actor_name="ADMIN")
            client.write_off_inventory_item(
                "item-1",
                card_id="card-1",
                quantity="1.25",
                actor_name="ADMIN",
            )
            client.return_inventory_movement(
                "movement-1",
                card_id="card-1",
                actor_name="ADMIN",
            )

        self.assertEqual(
            request.call_args_list,
            [
                call("/api/list_inventory_items", method="GET"),
                call(
                    "/api/search_inventory_items",
                    {"query": "масло", "limit": 10},
                    method="POST",
                ),
                call(
                    "/api/get_inventory_item",
                    {"item_id": "item-1"},
                    method="POST",
                ),
                call(
                    "/api/list_inventory_movements",
                    {"item_id": "item-1"},
                    method="POST",
                ),
                call(
                    "/api/save_inventory_item",
                    {
                        "name": "Масло",
                        "unit": "л",
                        "quantity": "5.5",
                        "cost_price": "500",
                        "sale_price": "800",
                        "source": "mcp",
                        "actor_name": "ADMIN",
                    },
                ),
                call(
                    "/api/replenish_inventory_item",
                    {
                        "item_id": "item-1",
                        "quantity": "2",
                        "source": "mcp",
                        "actor_name": "ADMIN",
                    },
                ),
                call(
                    "/api/write_off_inventory_item",
                    {
                        "item_id": "item-1",
                        "card_id": "card-1",
                        "quantity": "1.25",
                        "source": "mcp",
                        "actor_name": "ADMIN",
                    },
                ),
                call(
                    "/api/return_inventory_movement",
                    {
                        "movement_id": "movement-1",
                        "card_id": "card-1",
                        "source": "mcp",
                        "actor_name": "ADMIN",
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
