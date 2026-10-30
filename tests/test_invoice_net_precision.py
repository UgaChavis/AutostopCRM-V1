"""Net unit precision must not invalidate an exact gross invoice price."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService


class InvoiceNetPrecisionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="synthetic-invoice-net-precision-")
        self.addCleanup(folder.cleanup)
        self.service = PrintModuleService(Path(folder.name))
        self.card = Card.from_dict(
            {
                "id": "synthetic-net-precision",
                "title": "Synthetic",
                "column": "inbox",
                "repair_order": {
                    "number": "SYNTHETIC",
                    "client": "Synthetic",
                    "tax_label": "Без НДС",
                    "materials": [
                        {
                            "name": "Synthetic row",
                            "quantity": "2000000000",
                            "price": "0.00000000102",
                        }
                    ],
                },
            }
        )

    def context(self):
        return self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=self.service._read_settings(),
        )

    def assert_release_blocked(self, options):
        for prepare in (
            lambda: self.service.prepare_documents_pdf(self.card, **options),
            lambda: self.service.prepare_print_documents(
                self.card, printer_name="Synthetic", **options
            ),
        ):
            with self.assertRaises(PrintModuleError) as failure:
                prepare()
            self.assertEqual(failure.exception.code, "document_money_validation_failed")

    def test_exact_gross_invoice_still_prepares_when_net_unit_price_is_unavailable(self):
        original = self.card.to_storage_dict()
        with patch(
            "minimal_kanban.printing.service.invoice_vat_for_document",
            side_effect=lambda document_id, rows, tax: (rows, tax),
        ):
            old_context = self.context()["invoice"]
            old_preview = self.service.preview_documents(
                self.card, selected_document_ids=["invoice"]
            )
            self.assertTrue(old_preview["money_validation"]["valid"])
        invoice = self.context()["invoice"]
        self.assertEqual(invoice["total"], Decimal("2.40"))
        self.assertEqual(invoice["amount_due"], old_context["amount_due"])
        self.assertEqual(invoice["vat"], Decimal("0.11"))
        self.assertEqual(invoice["line_items"][0]["price"], old_context["line_items"][0]["price"])
        duplicate = self.service.duplicate_template(template_id="builtin:invoice:standard")[
            "template"
        ]
        for template_id in ("builtin:invoice:standard", duplicate["id"]):
            with self.subTest(template_id=template_id):
                options = {
                    "selected_document_ids": ["invoice"],
                    "selected_template_ids": {"invoice": template_id},
                }
                preview = self.service.preview_documents(self.card, **options)
                self.assertTrue(preview["money_validation"]["valid"], preview["money_validation"])
                for prepared in (
                    self.service.prepare_documents_pdf(self.card, **options),
                    self.service.prepare_print_documents(
                        self.card, printer_name="Synthetic", **options
                    ),
                ):
                    self.assertIn("В том числе НДС 5%</td><td>0,11", prepared.html)
                    self.assertIn("0,0000000012", prepared.html)
        self.assertEqual(self.card.to_storage_dict(), original)

    def test_custom_net_price_fields_still_block_release(self):
        for rows, price in (
            ("regulated.rows", "price_display"),
            ("invoice.line_items", "regulated_price_display"),
            ("line_items", "regulated_price_display"),
        ):
            with self.subTest(rows=rows):
                content = (
                    '<div class="document-page">'
                    "<p>В том числе {{invoice.tax_label}}: {{invoice.vat_display}} ₽</p>"
                    "{{#" + rows + "}}<p>{{" + price + "}}</p>{{/" + rows + "}}"
                    "</div>"
                )
                options = {
                    "selected_document_ids": ["invoice"],
                    "template_overrides": {"invoice": content},
                }
                preview = self.service.preview_documents(self.card, **options)
                self.assertFalse(preview["money_validation"]["valid"])
                self.assertEqual(
                    {issue["code"] for issue in preview["money_validation"]["errors"]},
                    {"unit_precision_unavailable"},
                )
                self.assert_release_blocked(options)

    def test_original_gross_precision_errors_still_block_builtin_invoice(self):
        row = self.card.repair_order.materials[0]
        row.quantity, row.price, row.total = "10000000000", "0.00000000001", "0.10"
        projected = self.context()["invoice"]["line_items"][0]
        self.assertTrue(projected["money_error"])
        self.assertNotIn("regulated_money_error", projected)
        options = {"selected_document_ids": ["invoice"]}
        preview = self.service.preview_documents(self.card, **options)
        self.assertFalse(preview["money_validation"]["valid"])
        self.assertIn(
            "unit_precision_unavailable",
            {issue["code"] for issue in preview["money_validation"]["errors"]},
        )
        self.assert_release_blocked(options)
