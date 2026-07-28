from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.storage.json_store import JsonStore


class InventoryRevisionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        logger = logging.getLogger("inventory-revision-service")
        store = JsonStore(
            state_file=Path(self.temp_dir.name) / "board.json",
            logger=logger,
        )
        self.service = CardService(store, logger)

    def test_backend_rejects_stale_item_and_card_revisions(self) -> None:
        card = self.service.create_card(
            {
                "vehicle": "AutoStop Synthetic",
                "title": "Inventory revision contract",
                "deadline": {"hours": 1},
            }
        )["card"]
        item = self.service.save_inventory_item(
            {"name": "Synthetic item", "unit": "шт", "quantity": "1"}
        )["item"]

        for operation, payload in (
            (
                self.service.save_inventory_item,
                {
                    "item_id": item["id"],
                    "name": "Stale update",
                    "expected_updated_at": "2000-01-01T00:00:00+00:00",
                },
            ),
            (
                self.service.replenish_inventory_item,
                {
                    "item_id": item["id"],
                    "quantity": "1",
                    "expected_updated_at": "2000-01-01T00:00:00+00:00",
                },
            ),
            (
                self.service.write_off_inventory_item,
                {
                    "item_id": item["id"],
                    "card_id": card["id"],
                    "quantity": "1",
                    "expected_updated_at": item["updated_at"],
                    "expected_card_updated_at": "2000-01-01T00:00:00+00:00",
                },
            ),
        ):
            with self.assertRaises(ServiceError) as conflict:
                operation(payload)
            self.assertIn(
                conflict.exception.code,
                {"inventory_item_update_conflict", "card_update_conflict"},
            )

        self.assertEqual(
            self.service.get_inventory_item({"item_id": item["id"]})["item"]["quantity"],
            "1",
        )

if __name__ == "__main__":
    unittest.main()
