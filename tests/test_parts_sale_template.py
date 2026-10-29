"""Parts-only lines must not misrepresent the repair order's payment balance."""

from __future__ import annotations

import html
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card  # noqa: E402
from minimal_kanban.printing.errors import PrintModuleError  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from tests.test_document_money_v2 import money_card  # noqa: E402


class PartsSaleTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = PrintModuleService(Path(directory.name))

    def test_totals_separate_goods_value_from_order_wide_payments_and_balance(self) -> None:
        payments = {
            "none": [],
            "cash": [{"payment_method": "cash", "amount": "300"}],
            "card": [{"payment_method": "card", "amount": "300"}],
            "cashless": [{"payment_method": "cashless", "amount": "300"}],
            "mixed": [
                {"payment_method": method, "amount": "100"}
                for method in ("cash", "card", "cashless")
            ],
            "full_cash": [{"payment_method": "cash", "amount": "1000"}],
            "full_cashless": [{"payment_method": "cashless", "amount": "1176.47"}],
            "overpaid": [{"payment_method": "cash", "amount": "2000"}],
        }
        # Values are established settlement examples, including the different
        # credit for cash versus cashless payments. No allocation to parts is implied.
        cases = (
            (False, "cash", "none", "Наличный", "0,00", "1 000,00"),
            (False, "cashless", "none", "Безналичный", "0,00", "1 176,47"),
            (False, "cash", "cash", "Наличный", "300,00", "700,00"),
            (False, "cash", "card", "На карту", "300,00", "700,00"),
            (False, "cash", "cashless", "Безналичный", "300,00", "876,47"),
            (False, "cash", "mixed", "Безналичный", "300,00", "841,18"),
            (False, "cash", "full_cash", "Наличный", "1 000,00", "0,00"),
            (False, "cash", "full_cashless", "Безналичный", "1 176,47", "0,00"),
            (False, "cash", "overpaid", "Наличный", "2 000,00", "0,00"),
            (True, "cash", "none", "Наличный", "0,00", "1 500,00"),
            (True, "cash", "cash", "Наличный", "300,00", "1 200,00"),
            (True, "cash", "card", "На карту", "300,00", "1 200,00"),
            (True, "cash", "cashless", "Безналичный", "300,00", "1 464,71"),
            (True, "cash", "mixed", "Безналичный", "300,00", "1 429,41"),
            (True, "cashless", "cash", "Безналичный", "300,00", "1 411,76"),
            (True, "cashless", "overpaid", "Безналичный", "2 000,00", "0,00"),
        )
        for with_works, method, payment, label, paid, due in cases:
            with self.subTest(with_works=with_works, method=method, payment=payment):
                source = money_card(price="1000", quantity="1", tax="Без НДС").to_storage_dict()
                source["repair_order"].update(
                    payment_method=method,
                    payments=payments[payment],
                    works=[{"name": "Synthetic work", "quantity": "1", "price": "500"}]
                    if with_works
                    else [],
                )
                card = Card.from_dict(source)
                before = card.to_storage_dict()
                preview = self.service.preview_documents(card, selected_document_ids=["parts_sale"])
                document = preview["documents"][0]
                self.assertTrue(document["money_validation"]["valid"])
                rendered = document["pages"][0]["html"]
                rows = [
                    (html.unescape(title), html.unescape(value))
                    for title, value in re.findall(
                        r'<div class="doc-totals__row[^"]*"><span>(.*?)</span><span>(.*?)</span></div>',
                        rendered,
                    )
                ]
                expected = [("Товары", "1 000,00")]
                if payment != "none":
                    expected.append(("Оплачено по заказ-наряду", paid))
                expected.append((f"К оплате по заказ-наряду ({label})", due))
                self.assertEqual(rows, expected)
                self.assertNotIn("Synthetic work", rendered)
                self.assertIn("Synthetic", rendered)
                self.assertEqual(card.to_storage_dict(), before)

    def test_invalid_material_amount_still_blocks_pdf_and_native_print(self) -> None:
        card = money_card(price="1000", quantity="1", total="900", tax="Без НДС")
        # Loading normalizes inconsistent totals; simulate a corrupted in-memory row.
        card.repair_order.materials[0].total = "900"
        preview = self.service.preview_documents(card, selected_document_ids=["parts_sale"])
        validation = preview["documents"][0]["money_validation"]
        self.assertFalse(validation["valid"])
        self.assertIn("line_amount_mismatch", {error["code"] for error in validation["errors"]})
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as renderer:
            with self.assertRaises(PrintModuleError) as caught:
                self.service.export_documents_pdf(card, selected_document_ids=["parts_sale"])
            self.assertEqual(caught.exception.code, "document_money_validation_failed")
            renderer.assert_not_called()
        with patch("minimal_kanban.printing.service.print_html") as printer:
            with self.assertRaises(PrintModuleError) as caught:
                self.service.print_documents(
                    card, selected_document_ids=["parts_sale"], printer_name="Synthetic"
                )
            self.assertEqual(caught.exception.code, "document_money_validation_failed")
            printer.assert_not_called()
