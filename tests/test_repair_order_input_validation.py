from __future__ import annotations

# Fixture imports select this checkout before application modules.
# ruff: noqa: I001

import copy
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests import test_api as api_fixtures
from tests.services_case import CardServiceCase

from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.repair_order_input import validate_order_input


class RepairOrderInputApiTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def _card(self):
        status, result = self.request("/api/create_card", {"title": "Synthetic input boundary"})
        self.assertEqual(status, 200, result)
        return result["data"]["card"]["id"]

    def _assert_rejected(self, card_id, cases):
        state_file = Path(self.temp_dir.name) / "state.json"
        before = state_file.read_bytes()
        bundle = copy.deepcopy(self.store.read_bundle())
        with patch.object(self.service, "_save_bundle") as save:
            for route, payload in cases:
                with self.subTest(route=route, payload=payload):
                    status, result = self.request("/api/" + route, {"card_id": card_id, **payload})
                    self.assertEqual(status, 400, result)
                    self.assertEqual(result["error"]["code"], "validation_error")
                    self.assertIn("field", result["error"]["details"])
                    self.assertEqual(state_file.read_bytes(), before)
            save.assert_not_called()
        self.assertEqual(self.store.read_bundle(), bundle)
        for route, payload in (
            ("get_card", {"card_id": card_id}),
            ("list_repair_orders", {"status": "all"}),
            (
                "preview_repair_order_print_documents",
                {"card_id": card_id, "selected_document_ids": ["invoice"]},
            ),
        ):
            status, result = self.request("/api/" + route, payload)
            self.assertEqual(status, 200, result)

    def test_invalid_rows_cannot_corrupt_state_or_crash_any_public_mutation(self):
        card_id = self._card()
        cases = []
        for section in ("works", "materials"):
            for field in ("quantity", "price", "total", "cost_price"):
                for value in ("garbage", "NaN", "Infinity", "1E100000", True, {}, "9" * 41):
                    row = {"name": "Synthetic", "quantity": "1", "price": "10", field: value}
                    for route in ("update_card", "update_repair_order"):
                        cases.append((route, {"repair_order": {section: [row]}}))
                    cases.append((f"replace_repair_order_{section}", {"rows": [row]}))
            for row in (
                {"name": "Synthetic", "quantity": "1", "price": "1E100000"},
                {"name": "Synthetic", "quantity": "1E14", "price": "1E14"},
                {"name": "Synthetic", "total": "1E100000"},
                {"name": "Synthetic", "work_salary_guarantee": "NaN"},
                {"name": "Synthetic", "work_salary_percent_override": "Infinity"},
                {"name": "Synthetic", "work_salary_guarantee": "-Infinity"},
                {"name": "Synthetic", "work_salary_cost_price": "-Infinity"},
                {"name": "Synthetic", "work_salary_guarantee": "1E100000"},
                {"name": "Synthetic", "work_salary_cost_price": "1E100000"},
            ):
                for route in ("update_card", "update_repair_order"):
                    cases.append((route, {"repair_order": {section: [row]}}))
                cases.append((f"replace_repair_order_{section}", {"rows": [row]}))
        self._assert_rejected(card_id, cases)

    def test_invalid_payments_and_truncated_collections_fail_before_save(self):
        card_id = self._card()
        cases = []
        for route in ("update_card", "update_repair_order"):
            for value in ("garbage", "NaN", "Infinity", "1E100000", True, {}, "9" * 41):
                for order in (
                    {"prepayment": value},
                    {"advancePayment": value},
                    {"payments": [{"id": "synthetic", "amount": value}]},
                    {"payment_history": [{"id": "synthetic", "value": value}]},
                ):
                    cases.append((route, {"repair_order": order}))
            for section, limit in (("works", 150), ("materials", 150), ("payments", 200)):
                for value in (
                    ["malformed"],
                    [None],
                    [{"name": "Synthetic", "amount": "1"}] * (limit + 1),
                ):
                    cases.append((route, {"repair_order": {section: value}}))
        self._assert_rejected(card_id, cases)

    def test_overflow_after_aggregating_valid_rows_is_rejected_before_save(self):
        card_id = self._card()
        rows = [{"name": "Synthetic", "quantity": "1", "price": "9E25"}] * 2
        self._assert_rejected(
            card_id,
            [("update_repair_order", {"repair_order": {"works": rows}})],
        )

    def test_card_fields_payments_and_artifacts_are_unchanged_on_invalid_order(self):
        card_id = self._card()
        status, result = self.request("/api/create_cashbox", {"name": "Наличный"})
        self.assertEqual(status, 200, result)
        cashbox = result["data"]["cashbox"]
        self._assert_rejected(
            card_id,
            [
                (
                    "update_card",
                    {
                        "title": "Must not be persisted",
                        "repair_order": {
                            "works": [{"name": "Synthetic", "quantity": "1", "price": "bad"}],
                            "payments": [{"amount": "100", "cashbox_id": cashbox["id"]}],
                        },
                    },
                )
            ],
        )


class RepairOrderInputCompatibilityTests(CardServiceCase):
    def test_existing_formats_aliases_rounding_and_precision_are_preserved(self):
        self.addCleanup(self.store.close)
        card_id = self.service.create_card({"title": "Synthetic compatible input"})["card"]["id"]
        rows = [
            {"name": "Incomplete"},
            {"name": "Comma", "qty": "2", "price": "1,235", "sum": "2.47"},
            {"name": "Grouped", "quantity": "1", "price": "1\u00a0234,50"},
            {"name": "Fine quantity", "quantity": "0.12345678901234567890123456789", "price": "10"},
            {"name": "Fine price", "quantity": "1", "price": "1.1234567890123"},
            {"name": "Signed", "quantity": "-1", "price": "2"},
            {"name": "Zero total", "quantity": "1e39", "price": "0"},
            {"name": "Tiny", "quantity": "1e-38", "price": "0"},
            {"name": "Canonical", "quantity": "1", "qty": "unused", "price": "10"},
            {"name": "Salary", "work_salary_percent_override": "16.666666666667"},
        ]
        payload = {
            "works": rows,
            "payments": [{"id": "p", "amount": "16.666666666667"}],
            "tax_label": "Без НДС",
        }
        original = copy.deepcopy(payload)
        validate_order_input(payload)
        expected = RepairOrder.from_dict(payload)
        result = self.service.update_repair_order({"card_id": card_id, "repair_order": payload})
        actual = result["repair_order"]
        self.assertEqual(payload, original)
        self.assertEqual(actual["grand_total"], expected.grand_total_amount())
        self.assertEqual(actual["paid_total"], expected.prepayment_amount())
        for supplied, stored in zip(rows, actual["works"], strict=True):
            model = RepairOrder.from_dict({"works": [supplied]}).works[0]
            self.assertEqual(stored["quantity"], model.quantity)
            self.assertEqual(stored["price"], model.price)
            self.assertEqual(stored["total"], model.total)
        read = self.service.get_card({"card_id": card_id})["card"]["repair_order"]
        self.assertEqual(read["grand_total"], actual["grand_total"])
        self.assertEqual(Decimal(read["paid_total"]), Decimal("16.67"))


if __name__ == "__main__":
    unittest.main()
