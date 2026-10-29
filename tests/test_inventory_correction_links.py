from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.errors import ServiceError
from tests.services_case import CardServiceCase


class InventoryCorrectionLinksTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        self.item = self.service.save_inventory_item(
            {
                "name": "Synthetic oil",
                "unit": "л",
                "quantity": "10",
                "cost_price": "500",
                "sale_price": "800",
            }
        )["item"]
        card = self.service.create_card({"title": "Synthetic inventory correction"})["card"]
        self.written = self.service.write_off_inventory_item(
            {"item_id": self.item["id"], "card_id": card["id"], "quantity": "2"}
        )
        self.service.update_repair_order(
            {
                "card_id": card["id"],
                "repair_order": {"payments": [{"amount": "1600"}]},
            }
        )
        card = self.service.get_card({"card_id": card["id"]})["card"]
        card = self.service.set_repair_order_status(
            {
                "card_id": card["id"],
                "status": "closed",
                "expected_updated_at": card["updated_at"],
            }
        )["card"]
        self.card = self.service.reopen_repair_order(
            {
                "card_id": card["id"],
                "expected_updated_at": card["updated_at"],
                "reason_code": "material_error",
                "reason_note": "Synthetic correction",
                "idempotency_key": "synthetic-inventory-correction",
            }
        )["card"]

    def test_correction_link_cannot_be_cleared_or_substituted_by_order_edits(self) -> None:
        original = self.card["repair_order"]["materials"][0]
        for method in ("update_repair_order", "update_card", "replace_repair_order_materials"):
            for field in ("inventory_movement_id", "inventory_item_id", "inventory_unit"):
                for value in ("", "unrelated"):
                    with self.subTest(method=method, field=field, value=value):
                        row = {**original, field: value}
                        payload = {
                            "card_id": self.card["id"],
                            "expected_updated_at": self.card["updated_at"],
                            # Client input must not grant the native return exception.
                            "operation": "inventory_return",
                        }
                        if method == "replace_repair_order_materials":
                            payload["rows"] = [row]
                        else:
                            payload["repair_order"] = {"materials": [row]}
                        before = self.state_file.read_bytes()
                        with self.assertRaises(ServiceError) as caught:
                            getattr(self.service, method)(payload)
                        self.assertEqual(
                            caught.exception.code, "inventory_material_movement_active"
                        )
                        self.assertEqual(caught.exception.status_code, 409)
                        self.assertEqual(self.state_file.read_bytes(), before)

    def test_native_return_keeps_finances_and_allows_subsequent_correction(self) -> None:
        before = self.store.read_bundle()
        previous_order = deepcopy(self.card["repair_order"])
        returned = self.service.return_inventory_movement(
            {"movement_id": self.written["movement"]["id"]}
        )
        self.assertEqual(returned["item"]["quantity"], "10")
        row = returned["repair_order"]["materials"][0]
        self.assertEqual(row["inventory_movement_id"], "")
        self.assertEqual(row["quantity"], "2")
        self.assertEqual(returned["repair_order"]["payments"], previous_order["payments"])
        self.assertEqual(
            self.store.read_bundle()["cards"][0].repair_order.cycles,
            before["cards"][0].repair_order.cycles,
        )
        self.assertEqual(self.store.read_bundle()["cash_transactions"], before["cash_transactions"])
        updated = self.service.update_repair_order(
            {
                "card_id": self.card["id"],
                "expected_updated_at": returned["card"]["updated_at"],
                "repair_order": {"materials": [{**row, "quantity": "1", "total": "800"}]},
            }
        )
        self.assertEqual(updated["repair_order"]["materials"][0]["quantity"], "1")
        self.assertEqual(
            [movement.kind for movement in self.store.read_bundle()["inventory_movements"]],
            ["incoming", "write_off", "return"],
        )

    def test_correction_cannot_copy_or_invent_a_warehouse_link(self) -> None:
        original = self.card["repair_order"]["materials"][0]
        for method in ("update_repair_order", "update_card", "replace_repair_order_materials"):
            for movement_id in (original["inventory_movement_id"], "unrelated-movement"):
                with self.subTest(method=method, movement_id=movement_id):
                    copied = {
                        **original,
                        "id": "synthetic-copy",
                        "inventory_movement_id": movement_id,
                    }
                    payload = {
                        "card_id": self.card["id"],
                        "expected_updated_at": self.card["updated_at"],
                        "operation": "inventory_write_off",
                    }
                    if method == "replace_repair_order_materials":
                        payload["rows"] = [original, copied]
                    else:
                        payload["repair_order"] = {"materials": [original, copied]}
                    before = self.state_file.read_bytes()
                    with self.assertRaises(ServiceError) as caught:
                        getattr(self.service, method)(payload)
                    self.assertEqual(caught.exception.code, "inventory_material_movement_active")
                    self.assertEqual(self.state_file.read_bytes(), before)

    def test_correction_allows_native_writeoff_and_valid_material_edits(self) -> None:
        original = self.card["repair_order"]["materials"][0]
        unchanged = self.service.update_repair_order(
            {"card_id": self.card["id"], "repair_order": {"materials": [original]}}
        )
        self.assertEqual(unchanged["repair_order"]["materials"][0], original)
        written = self.service.write_off_inventory_item(
            {"card_id": self.card["id"], "item_id": self.item["id"], "quantity": "1"}
        )
        self.assertEqual(written["item"]["quantity"], "7")
        self.assertEqual(len(written["repair_order"]["materials"]), 2)
        rows = list(reversed(written["repair_order"]["materials"]))
        rows[0] = {**rows[0], "name": "Corrected name", "price": "900", "total": "900"}
        updated = self.service.update_repair_order(
            {"card_id": self.card["id"], "repair_order": {"materials": rows}}
        )
        self.assertEqual(updated["repair_order"]["materials"][0]["price"], "900")
        returned = self.service.return_inventory_movement(
            {"movement_id": written["movement"]["id"]}
        )
        self.assertEqual(returned["item"]["quantity"], "8")
        self.assertEqual(returned["repair_order"]["materials"][0]["inventory_movement_id"], "")
        self.assertEqual(
            returned["repair_order"]["materials"][1]["inventory_movement_id"],
            original["inventory_movement_id"],
        )

    def test_return_failure_does_not_clear_link_or_restore_stock(self) -> None:
        before = self.state_file.read_bytes()
        with (
            patch.object(self.service, "_save_bundle", side_effect=OSError("synthetic failure")),
            self.assertRaises(OSError),
        ):
            self.service.return_inventory_movement({"movement_id": self.written["movement"]["id"]})
        self.assertEqual(self.state_file.read_bytes(), before)
        reread = self.service.get_repair_order({"card_id": self.card["id"]})["repair_order"]
        self.assertEqual(
            reread["materials"][0]["inventory_movement_id"], self.written["movement"]["id"]
        )
        self.assertEqual(
            self.service.get_inventory_item({"item_id": self.item["id"]})["item"]["quantity"],
            "8",
        )
