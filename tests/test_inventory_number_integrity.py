from __future__ import annotations

import json
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import InventoryMovement, normalize_decimal_text
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore
from tests.services_case import CardServiceCase


class InventoryNumberIntegrityTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        self.store.read_bundle()

    def _item(self, quantity: str, **extra) -> dict:
        return self.service.save_inventory_item(
            {"name": "Synthetic precise stock", "quantity": quantity, "sale_price": "0", **extra}
        )["item"]

    def test_quantity_and_prices_survive_cold_storage_without_rounding(self) -> None:
        value = "12345678901234567890123456789"
        item = self._item(value, cost_price=value, sale_price=value)
        stored = JsonStore(state_file=self.state_file).read_bundle()["inventory_items"][0]
        for field in ("quantity", "cost_price", "sale_price"):
            self.assertEqual(item[field], value)
            self.assertEqual(getattr(stored, field), value)
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(normalize_decimal_text(value), value)

    def test_replenishment_cannot_record_a_delta_without_changing_stock(self) -> None:
        item = self._item("1")
        result = self.service.replenish_inventory_item({"item_id": item["id"], "quantity": "1e-28"})
        self.assertEqual(result["item"]["quantity"], "1.0000000000000000000000000001")
        self.assertEqual(result["movement"]["quantity"], "0.0000000000000000000000000001")

    def test_legacy_movement_infers_exact_signed_delta(self) -> None:
        quantity = "12345678901234567890123456789"
        movement = InventoryMovement.from_dict(
            {"item_id": "synthetic-item", "kind": "write_off", "quantity": quantity}
        )
        self.assertEqual(movement.quantity_delta, "-" + quantity)

    def test_precise_writeoff_and_return_preserve_row_movement_and_stock(self) -> None:
        quantity = "0.12345678901234567890123456789"
        item = self._item("1")
        card = self.service.create_card({"title": "Synthetic precise order"})["card"]
        result = self.service.write_off_inventory_item(
            {"item_id": item["id"], "card_id": card["id"], "quantity": quantity}
        )
        self.assertEqual(result["repair_order"]["materials"][0]["quantity"], quantity)
        self.assertEqual(result["movement"]["quantity"], quantity)
        self.assertEqual(result["movement"]["quantity_delta"], "-" + quantity)
        self.assertEqual(result["item"]["quantity"], "0.87654321098765432109876543211")
        returned = self.service.return_inventory_movement({"movement_id": result["movement"]["id"]})
        self.assertEqual(returned["item"]["quantity"], "1")

    def test_unrepresentable_input_rejects_before_formatting_or_writing(self) -> None:
        before = self.state_file.read_bytes()
        for field in ("quantity", "cost_price", "sale_price"):
            for value in ("1e50", "1e-50", "1e1000000000"):
                with self.subTest(field=field, value=value):
                    with (
                        patch(
                            "minimal_kanban.services.card_service_inventory.normalize_decimal_text",
                            side_effect=AssertionError("unbounded formatting must not run"),
                        ),
                        self.assertRaises(ServiceError) as caught,
                    ):
                        self.service.save_inventory_item(
                            {"name": "Synthetic rejected number", field: value}
                        )
                    self.assertEqual(caught.exception.code, "validation_error")
                    self.assertEqual(caught.exception.details["field"], field)
                    self.assertEqual(self.state_file.read_bytes(), before)

    def test_stock_overflow_rejects_without_losing_history(self) -> None:
        item = self._item("9" * 40)
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError):
            self.service.replenish_inventory_item({"item_id": item["id"], "quantity": "1"})
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_legacy_oversized_prices_cannot_be_truncated_during_writeoff(self) -> None:
        item = self._item("1")
        card = self.service.create_card({"title": "Synthetic legacy price"})["card"]
        original = json.loads(self.state_file.read_bytes())
        for field in ("cost_price", "sale_price"):
            with self.subTest(field=field):
                state = json.loads(json.dumps(original))
                state["inventory_items"][0][field] = "1e50"
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.state_file.read_bytes()
                with self.assertRaises(ServiceError) as caught:
                    self.service.write_off_inventory_item(
                        {"item_id": item["id"], "card_id": card["id"], "quantity": "1"}
                    )
                self.assertEqual(caught.exception.details["field"], field)
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_row_format_boundaries_remain_supported(self) -> None:
        for quantity in ("1e39", "1e-38"):
            with self.subTest(quantity=quantity):
                item = self._item(quantity)
                card = self.service.create_card({"title": "Synthetic boundary order"})["card"]
                result = self.service.write_off_inventory_item(
                    {"item_id": item["id"], "card_id": card["id"], "quantity": quantity}
                )
                self.assertEqual(Decimal(result["movement"]["quantity"]), Decimal(quantity))
                self.assertEqual(
                    result["repair_order"]["materials"][0]["quantity"],
                    result["movement"]["quantity"],
                )
                self.assertEqual(result["item"]["quantity"], "0")
