from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService
from tests.test_act_fixed_vat5 import act_card


class RegulatedTaxVisibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="synthetic-regulated-tax-")
        self.addCleanup(folder.cleanup)
        self.service = PrintModuleService(Path(folder.name))
        self.service.save_settings({"service_profile": {"tax_label": "Без НДС"}})
        self.card = act_card("", prices=("8500", "1700"))

    def options(self, document_id, content):
        return {
            "selected_document_ids": [document_id],
            "template_overrides": {document_id: '<div class="document-page">' + content + "</div>"},
        }

    def assert_blocked(self, document_id, content):
        options = self.options(document_id, content)
        preview = self.service.preview_documents(self.card, **options)
        self.assertFalse(preview["money_validation"]["valid"])
        self.assertIn(
            "regulated_vat_summary_unverified",
            {error["code"] for error in preview["money_validation"]["errors"]},
        )
        with (
            patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for release in (
                lambda: self.service.export_documents_pdf(self.card, **options),
                lambda: self.service.print_documents(
                    self.card, printer_name="Synthetic", **options
                ),
            ):
                with self.assertRaises(PrintModuleError) as failure:
                    release()
                self.assertEqual(failure.exception.code, "document_money_validation_failed")
                self.assertEqual(failure.exception.status_code, 422)
            pdf.assert_not_called()
            printer.assert_not_called()

    def test_builtin_copies_keep_editability_and_canonical_row_tax(self):
        for document_id in ("invoice_factura", "upd"):
            with self.subTest(document_id=document_id):
                duplicate = self.service.duplicate_template(
                    template_id=f"builtin:{document_id}:standard"
                )["template"]
                content = duplicate["content"]
                for rate in ("0%", "20%", "Без НДС", "5%"):
                    with self.subTest(rate=rate):
                        self.assert_blocked(
                            document_id, content.replace("{{tax_rate_display}}", rate)
                        )
                self.assert_blocked(
                    document_id,
                    content.replace("{{vat_display}}", "0,00").replace(
                        "{{regulated.vat_display}}", "0,00"
                    ),
                )
                for template_id in (f"builtin:{document_id}:standard", duplicate["id"]):
                    options = {
                        "selected_document_ids": [document_id],
                        "selected_template_ids": {document_id: template_id},
                    }
                    preview = self.service.preview_documents(self.card, **options)
                    self.assertTrue(preview["money_validation"]["valid"], preview)
                    self.service.prepare_documents_pdf(self.card, **options)
                    self.service.prepare_print_documents(
                        self.card, printer_name="Synthetic", **options
                    )
                self.assertEqual(self.service._find_template(duplicate["id"]).content, content)

    def test_hidden_or_unrelated_matching_fields_and_literals_cannot_qualify(self):
        pair = "{{regulated.tax_rate_display}} {{regulated.vat_display}}"
        contents = (
            "<p>НДС 5% 571,43</p>",
            "<p>{{regulated.tax_rate_display}} 571,43</p>",
            "<p>НДС 5% {{regulated.vat_display}}</p>",
            "<p>{{invoice.tax_label}} {{invoice.vat_display}}</p>",
            '<p style="display:none">' + pair + "</p><p>5% 571,43</p>",
            "<p>{{regulated.tax_rate_display}} <span hidden>{{regulated.vat_display}}</span></p>",
            '<p><span style="color:white">{{regulated.tax_rate_display}}</span> {{regulated.vat_display}}</p>',
            '<style>@media print {.tax {display:none}}</style><p class="tax">' + pair + "</p>",
            "<!-- <p>" + pair + "</p> --><p>5% 571,43</p>",
            '<p title="' + pair + '">5% 571,43</p>',
            "<style>p:first-of-type {display:none}</style>{{#regulated.rows}}"
            "<p>{{tax_rate_display}} {{vat_display}}</p>{{/regulated.rows}}",
        )
        for document_id in ("invoice_factura", "upd"):
            for content in contents:
                with self.subTest(document_id=document_id, content=content):
                    self.assert_blocked(document_id, content)

    def test_conflicting_visible_status_blocks_but_net_price_headings_are_allowed(self):
        pair = "<p>{{regulated.tax_label}} {{regulated.vat_display}}</p>"
        for document_id in ("invoice_factura", "upd"):
            for conflict in (
                "НДС 0%",
                "НДС 20%",
                "НДС по ставке 20%",
                "НДС (ставка 20%)",
                "20 % НДС",
                "Без НДС",
                "В том числе Без НДС",
                "Налоговая ставка: Без НДС",
                "20%",
                "{{service.tax_label}}",
                "Цена {{service.tax_label}}",
            ):
                with self.subTest(document_id=document_id, conflict=conflict):
                    self.assert_blocked(document_id, pair + "<p>" + conflict + "</p>")
            for extra in (
                "<p>Цена без НДС</p><p>Стоимость без НДС</p><p>Сумма без НДС</p>",
                '<p hidden>Без НДС</p><p style="display:none">НДС 20%</p>',
                "<p>Надбавка 15%</p>",
                "<p>Безналичный расчёт, включая налоги и сборы 15%</p>",
            ):
                for content in (pair + extra, extra + pair):
                    options = self.options(document_id, content)
                    preview = self.service.preview_documents(self.card, **options)
                    self.assertTrue(preview["money_validation"]["valid"], preview)
                    self.service.prepare_documents_pdf(self.card, **options)

    def test_scalar_sections_and_each_canonical_row_pair_are_supported(self):
        for document_id in ("invoice_factura", "upd"):
            for content in (
                "<p>{{#regulated.tax_rate_display}}{{.}}{{/regulated.tax_rate_display}} "
                "{{#regulated.vat_display}}{{.}}{{/regulated.vat_display}}</p>",
                "{{#regulated.rows}}<p>{{#tax_rate_display}}{{.}}{{/tax_rate_display}} "
                "{{#vat_display}}{{.}}{{/vat_display}}</p>{{/regulated.rows}}",
            ):
                options = self.options(document_id, content)
                preview = self.service.preview_documents(self.card, **options)
                self.assertTrue(preview["money_validation"]["valid"], preview)
                self.service.prepare_documents_pdf(self.card, **options)
                self.service.prepare_print_documents(self.card, printer_name="Synthetic", **options)
