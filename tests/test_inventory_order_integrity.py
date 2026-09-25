from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.repair_order import REPAIR_ORDER_ROWS_LIMIT
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore
from tests.services_case import CardServiceCase


class InventoryOrderIntegrityTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "Synthetic inventory order"})["card"][
            "id"
        ]
        self.item_id = self.service.save_inventory_item(
            {"name": "Synthetic oil", "quantity": "10", "sale_price": "100", "unit": "л"}
        )["item"]["id"]

    def _write_off(self, **extra) -> dict:
        return self.service.write_off_inventory_item(
            {"card_id": self.card_id, "item_id": self.item_id, "quantity": "1.5", **extra}
        )

    def _fill_materials(self, count: int) -> None:
        self.service.update_repair_order(
            {
                "card_id": self.card_id,
                "repair_order": {
                    "materials": [
                        {"name": f"Synthetic material {index}", "quantity": "1", "price": "1"}
                        for index in range(count)
                    ]
                },
            }
        )

    def _assert_rejected_without_write(self, operation, payload, *, code: str) -> ServiceError:
        state_bytes = self.state_file.read_bytes()
        files = {path: path.read_bytes() for path in Path(self.temp_dir.name).rglob("*.txt")}
        with self.assertRaises(ServiceError) as caught:
            operation(payload)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.state_file.read_bytes(), state_bytes)
        self.assertEqual(
            {path: path.read_bytes() for path in Path(self.temp_dir.name).rglob("*.txt")}, files
        )
        return caught.exception

    def test_return_rejects_another_card_without_restoring_stock(self) -> None:
        written = self._write_off()
        other_id = self.service.create_card({"title": "Unrelated synthetic order"})["card"]["id"]
        error = self._assert_rejected_without_write(
            self.service.return_inventory_movement,
            {"movement_id": written["movement"]["id"], "card_id": other_id},
            code="validation_error",
        )
        self.assertEqual(error.details.get("field"), "card_id")
        returned = self.service.return_inventory_movement(
            {"movement_id": written["movement"]["id"]}
        )
        self.assertEqual(returned["item"]["quantity"], "10")
        self.assertEqual(returned["movement"]["card_id"], self.card_id)
        self.assertEqual(returned["repair_order"]["materials"][0]["inventory_movement_id"], "")

    def test_write_off_cannot_replace_an_active_warehouse_link(self) -> None:
        written = self._write_off()
        self._assert_rejected_without_write(
            self.service.write_off_inventory_item,
            {"card_id": self.card_id, "item_id": self.item_id, "quantity": "2", "row_index": 0},
            code="inventory_material_movement_active",
        )
        self.service.return_inventory_movement({"movement_id": written["movement"]["id"]})
        replaced = self._write_off(row_index=0)
        self.assertEqual(len(replaced["repair_order"]["materials"]), 1)
        self.assertEqual(replaced["item"]["quantity"], "8.5")

    def test_full_materials_reject_append_before_committing_inventory(self) -> None:
        self._fill_materials(REPAIR_ORDER_ROWS_LIMIT)
        error = self._assert_rejected_without_write(
            self.service.write_off_inventory_item,
            {"card_id": self.card_id, "item_id": self.item_id, "quantity": "1"},
            code="validation_error",
        )
        self.assertEqual(error.details.get("field"), "row_index")
        replaced = self._write_off(row_index=REPAIR_ORDER_ROWS_LIMIT - 1)
        self.assertEqual(len(replaced["repair_order"]["materials"]), REPAIR_ORDER_ROWS_LIMIT)
        self.assertEqual(
            replaced["repair_order"]["materials"][-1]["inventory_movement_id"],
            replaced["movement"]["id"],
        )

    def test_last_available_material_slot_retains_committed_movement(self) -> None:
        self._fill_materials(REPAIR_ORDER_ROWS_LIMIT - 1)
        written = self._write_off()
        self.assertEqual(written["meta"]["row_index"], REPAIR_ORDER_ROWS_LIMIT - 1)
        reread = self.service.get_repair_order({"card_id": self.card_id})["repair_order"]
        self.assertEqual(len(reread["materials"]), REPAIR_ORDER_ROWS_LIMIT)
        self.assertEqual(
            reread["materials"][-1]["inventory_movement_id"], written["movement"]["id"]
        )

    def test_parallel_returns_restore_stock_once_in_both_storage_paths(self) -> None:
        peer = CardService(
            JsonStore(self.state_file, self.logger),
            self.logger,
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        for fast in (False, True):
            with self.subTest(fast=fast):
                written = self._write_off()
                payload = {"movement_id": written["movement"]["id"]}
                original_save = self.service._save_bundle

                def save_after_peer(*args, **kwargs):
                    peer.return_inventory_movement(payload)
                    return original_save(*args, **kwargs)

                with (
                    patch(
                        "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                        return_value=fast,
                    ),
                    patch.object(self.service, "_save_bundle", side_effect=save_after_peer),
                    self.assertRaises(ServiceError) as conflict,
                ):
                    self.service.return_inventory_movement(payload)
                self.assertEqual(conflict.exception.code, "state_write_conflict")
                item = self.service.get_inventory_item({"item_id": self.item_id})["item"]
                self.assertEqual(item["quantity"], "10")
                movements = self.store.read_bundle()["inventory_movements"]
                self.assertEqual(
                    sum(m.related_movement_id == payload["movement_id"] for m in movements), 1
                )
                self._assert_rejected_without_write(
                    self.service.return_inventory_movement, payload, code="validation_error"
                )

    def test_inventory_row_lookup_ignores_non_dict_material_rows(self) -> None:
        row_index = self.service._inventory_row_index_by_movement(
            [False, {"inventory_movement_id": "move-1"}], "move-1", 0
        )

        self.assertEqual(row_index, 1)
