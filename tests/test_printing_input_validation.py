from __future__ import annotations

import copy
import unittest
from decimal import Decimal
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card  # noqa: E402
from minimal_kanban.printing.errors import PrintModuleError  # noqa: E402
from minimal_kanban.printing.input_validation import validate_print_order_input  # noqa: E402
from minimal_kanban.repair_order import RepairOrder  # noqa: E402
from tests import test_api as api_fixtures  # noqa: E402


class PrintingInputValidationTests(unittest.TestCase):
    def test_validation_does_not_change_valid_precision_rounding_or_signed_payments(self):
        payloads = (
            {"works": [{"name": "Synthetic", "qty": "3", "price": "1,17666666667", "sum": "3.53"}]},
            {"works": [{"quantity": "0.12345678901", "price": "1.12345678901"}]},
            {"materials": [{"quantity": "0", "price": "999999999999999.99"}]},
            {"prepayment": "12.34"},
            {"advancePayment": "12.34"},
            {"payment_history": [{"value": "12.34"}, {"value": "-1.23"}]},
            {"works": [{"quantity": "2", "price": "1\u00a0000,50", "sum": "2\u202f001"}]},
            {"prepayment": "1\u00a0000,50"},
            {"prepayment": "12.1234567890123"},
            {"payments": [{"amount": "12.1234567890123"}]},
            {"works": [{"quantity": "1", "price": "3", "total": "3.000000000001"}]},
            {"works": [{"name": "Synthetic incomplete row"}]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                original = copy.deepcopy(payload)
                before = RepairOrder.from_dict(payload).to_storage_dict()
                validate_print_order_input(payload)
                self.assertEqual(payload, original)
                self.assertEqual(RepairOrder.from_dict(payload).to_storage_dict(), before)

    def test_individually_valid_operands_cannot_overflow_the_model_product(self):
        with self.assertRaises(PrintModuleError) as raised:
            validate_print_order_input({"works": [{"quantity": "1E14", "price": "1E14"}]})
        self.assertEqual(raised.exception.details, {"field": "works[0].total"})


class PrintingInputValidationApiTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def _assert_blocked_without_effects(self, payloads):
        before = copy.deepcopy(self.store.read_bundle())
        with (
            patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as renderer,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for payload in payloads:
                for route in (
                    "preview_repair_order_print_documents",
                    "export_repair_order_print_pdf",
                    "print_repair_order_documents",
                ):
                    with self.subTest(payload=payload, route=route):
                        status, response = self.request(
                            f"/api/{route}",
                            {
                                **payload,
                                "selected_document_ids": ["invoice"],
                                "printer_name": "Synthetic Printer",
                            },
                        )
                        self.assertEqual(status, 400, response)
                        self.assertEqual(response["error"]["code"], "validation_error")
                        self.assertEqual(set(response["error"]["details"]), {"field"})
            renderer.assert_not_called()
            printer.assert_not_called()
        self.assertEqual(self.store.read_bundle(), before)

    def test_manual_and_card_inputs_fail_before_model_arithmetic(self):
        status, created = self.request("/api/create_card", {"title": "Synthetic input validation"})
        self.assertEqual(status, 200, created)
        card_id = created["data"]["card"]["id"]
        payloads = []
        for section in ("works", "materials"):
            for field in ("quantity", "price", "total"):
                for value in ("NaN", "sNaN", "Infinity", "-Infinity", "1E100000", "not a number"):
                    row = {"name": "Synthetic", "quantity": "3", "price": "1", field: value}
                    order = {"tax_label": "Без НДС", section: [row]}
                    payloads.extend(
                        (
                            {"document_without_card": True, "manual_document": order},
                            {"card_id": card_id, "repair_order": order},
                        )
                    )
        self._assert_blocked_without_effects(payloads)

    def test_aliases_text_payments_and_derived_overflow_are_checked(self):
        status, created = self.request("/api/create_card", {"title": "Synthetic input aliases"})
        self.assertEqual(status, 200, created)
        card_id = created["data"]["card"]["id"]
        manual_orders = [
            {"materials": [{"name": "Synthetic", "qty": "NaN", "unit_price": "1"}]},
            {"works": [{"name": "Synthetic", "qty": "1", "unit_price": "Infinity"}]},
            {"works": [{"name": "Synthetic", "amount": "NaN"}]},
            {"works": [{"name": "Synthetic", "quantity": "1E14", "price": "1E14"}]},
            {"prepayment": "Infinity"},
            {"payments": [{"amount": "NaN"}]},
        ]
        card_orders = [
            {"works": [{"name": "Synthetic", "qty": "NaN", "price": "1"}]},
            {"materials": [{"name": "Synthetic", "sum": "Infinity"}]},
            {"advance_payment": "NaN"},
            {"payment_history": [{"value": "1E100000"}]},
            {"works": [{"name": "Synthetic", "work_salary_guarantee": "NaN"}]},
            {"cycles": None},
            {"repair_order_cycles": None},
            {"payroll_postings": None},
        ]
        payloads = [
            {"document_without_card": True, "manual_document": order} for order in manual_orders
        ] + [{"card_id": card_id, "repair_order": order} for order in card_orders]
        payloads.extend(
            {"document_without_card": True, "request_text": request}
            for request in (
                "Работы:\nSynthetic | 1 | Infinity",
                "Материалы:\nSynthetic | NaN | 1",
                "Оплаты:\n1E100000 | 01.01.2026 | cash",
            )
        )
        self._assert_blocked_without_effects(payloads)

    def test_valid_manual_precision_and_payment_semantics_remain_unchanged(self):
        profile = self.service._print_module.manual_document_profile(
            {
                "tax_label": "Без НДС",
                "works": [{"name": "Synthetic", "qty": "3", "unit_price": "1.17666666667"}],
                "payments": [
                    {"amount": "1.23", "payment_method": "cash"},
                    {"amount": "-0.23", "payment_method": "cash"},
                ],
            }
        )
        self.assertEqual(profile.card.repair_order.works[0].price, "1.17666666667")
        self.assertEqual(profile.card.repair_order.works[0].total_value(), Decimal("3.53"))
        self.assertEqual(profile.card.repair_order.prepayment_value(), Decimal("1.00"))

    def test_both_adapters_preserve_all_grouping_whitespace(self):
        for separator in ("\u00a0", "\u202f", "\t", "\n"):
            payload = {
                "works": [{"name": "Synthetic", "quantity": "2", "price": f"1{separator}000,50"}],
                "prepayment": f"1{separator}000,50",
            }
            for manual in (False, True):
                with self.subTest(separator=repr(separator), manual=manual):
                    module = self.service._print_module
                    card = (
                        module.manual_document_profile(payload).card
                        if manual
                        else module.card_with_editor_overrides(Card.from_dict({}), payload)
                    )
                    self.assertEqual(card.repair_order.works[0].total_value(), Decimal("2001"))
                    self.assertEqual(card.repair_order.prepayment_value(), Decimal("1000.50"))

    def test_salary_metadata_retains_its_own_precision_and_clamping(self):
        module = self.service._print_module
        for field in (
            "work_salary_guarantee",
            "work_salary_percent_override",
            "work_salary_cost_price",
        ):
            for value in ("16.666666666667", "-1E100000", "1E16", "legacy free text"):
                with self.subTest(field=field, value=value):
                    payload = {
                        "works": [
                            {"name": "Synthetic", "quantity": "1", "price": "3", field: value}
                        ]
                    }
                    expected = RepairOrder.from_dict(payload).works[0]
                    actual = module.card_with_editor_overrides(Card.from_dict({}), payload)
                    self.assertEqual(
                        getattr(actual.repair_order.works[0], field), getattr(expected, field)
                    )


if __name__ == "__main__":
    unittest.main()
