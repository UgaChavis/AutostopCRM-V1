from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService
from minimal_kanban.services.document_money import (
    DocumentMoneyError,
    build_canonical_document_money,
    minimum_unit_price,
)
from tests.test_document_money_v2 import money_card


class IndependentPrintingReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = PrintModuleService(Path(self.directory.name))

    def test_invalid_quantity_is_rejected_before_deriving_or_formatting_prices(self):
        for quantity in ("1E-100000", "1E-1000000", "0.000000000001"):
            with self.subTest(quantity=quantity):
                with self.assertRaises(DocumentMoneyError):
                    minimum_unit_price(Decimal("0.01"), Decimal(quantity))
                with self.assertRaises(DocumentMoneyError):
                    build_canonical_document_money(
                        [Decimal("0.01")],
                        [Decimal(quantity)],
                        target_total=Decimal("0.01"),
                        net_rate=Decimal(1),
                        tax_rate=Decimal(".05"),
                    )
                card = money_card(price="1", quantity=quantity, total="0.01")
                before = card.to_storage_dict()
                preview = self.service.preview_documents(card, selected_document_ids=["invoice"])
                self.assertFalse(preview["money_validation"]["valid"])
                self.assertIn(
                    "number_out_of_range",
                    [issue["code"] for issue in preview["money_validation"]["errors"]],
                )
                with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as render:
                    with self.assertRaises(PrintModuleError) as failure:
                        self.service.export_documents_pdf(card, selected_document_ids=["invoice"])
                    self.assertEqual(failure.exception.status_code, 422)
                    render.assert_not_called()
                self.assertEqual(card.to_storage_dict(), before)

    def test_custom_tax_fields_require_known_tax_on_every_document_type(self):
        for kind in ("inspection_sheet", "parts_sale"):
            for tax, valid in (
                ("", False),
                ("неизвестно", False),
                ("Без НДС", True),
                ("НДС 0%", True),
                ("НДС 5%", True),
            ):
                with self.subTest(document=kind, tax=tax):
                    card = money_card(price="100", quantity="1", tax=tax)
                    content = "Итого {{invoice.total_display}} НДС {{invoice.vat_display}} {{invoice.tax_label}}"
                    arguments = {
                        "selected_document_ids": [kind],
                        "template_overrides": {kind: content},
                    }
                    preview = self.service.preview_documents(card, **arguments)
                    self.assertEqual(preview["money_validation"]["valid"], valid)
                    if valid:
                        continue
                    self.assertIn(
                        "tax_status_unknown",
                        [issue["code"] for issue in preview["money_validation"]["errors"]],
                    )
                    with patch(
                        "minimal_kanban.printing.service.render_html_to_pdf_bytes"
                    ) as render:
                        with self.assertRaises(PrintModuleError) as failure:
                            self.service.export_documents_pdf(card, **arguments)
                        self.assertEqual(failure.exception.status_code, 422)
                        render.assert_not_called()
                    with patch("minimal_kanban.printing.service.print_html") as printer:
                        with self.assertRaises(PrintModuleError) as failure:
                            self.service.print_documents(
                                card, printer_name="Synthetic", **arguments
                            )
                        self.assertEqual(failure.exception.status_code, 422)
                        printer.assert_not_called()

        # A tax-only letter requires a status, but does not require priced source rows.
        card = money_card(price="", quantity="", tax="Без НДС")
        arguments = {
            "selected_document_ids": ["inspection_sheet"],
            "template_overrides": {"inspection_sheet": "{{service.tax_label}}"},
        }
        self.assertTrue(
            self.service.preview_documents(card, **arguments)["money_validation"]["valid"]
        )
        card.repair_order.tax_label = ""
        self.assertFalse(
            self.service.preview_documents(card, **arguments)["money_validation"]["valid"]
        )
