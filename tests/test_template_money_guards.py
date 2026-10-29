"""Synthetic regressions for custom-template money guard boundaries."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService
from tests.test_document_money_v2 import money_card


class TemplateMoneyGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = PrintModuleService(Path(self.directory.name))

    def assert_guard(self, card, document, content, *, valid, error_code=None):
        arguments = {
            "selected_document_ids": [document],
            "template_overrides": {document: content},
        }
        baseline = card.to_storage_dict()
        preview = self.service.preview_documents(card, **arguments)
        self.assertEqual(preview["money_validation"]["valid"], valid)
        if error_code:
            self.assertIn(
                error_code,
                {issue["code"] for issue in preview["money_validation"]["errors"]},
            )
        with (
            patch(
                "minimal_kanban.printing.service.render_html_to_pdf_bytes",
                return_value=b"%PDF-synthetic",
            ) as pdf,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for operation in (
                lambda: self.service.export_documents_pdf(card, **arguments),
                lambda: self.service.print_documents(card, printer_name="Synthetic", **arguments),
            ):
                if valid:
                    operation()
                else:
                    with self.assertRaises(PrintModuleError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, "document_money_validation_failed")
                    self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(pdf.called, valid)
            self.assertEqual(printer.called, valid)
        self.assertEqual(card.to_storage_dict(), baseline)
        return preview

    def test_css_and_html_hidden_required_notes_block_both_backends(self):
        card = money_card(price="1", quantity="3", total="3.01")
        table = "{{#materials}}{{quantity_display}} * {{price_display}} = {{total_display}}{{/materials}}"
        for suffix in (
            '<style>.note { display:none }</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>@media print {.note {display:none}}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>.hidden {display:none}</style><section class="hidden"><div>{{{rounding_notes_html}}}</div></section>',
            "<details><summary>More</summary>{{{rounding_notes_html}}}</details>",
            '<div style="content-visibility:hidden">{{{rounding_notes_html}}}</div>',
            '<style>#rounding {visibility:hidden}</style><div id="rounding">{{{rounding_notes_html}}}</div>',
            '<style>.note {opacity:0}</style><div class="note">{{{rounding_notes_html}}}</div>',
        ):
            with self.subTest(suffix=suffix):
                self.assert_guard(
                    card,
                    "parts_sale",
                    table + suffix,
                    valid=False,
                    error_code="template_money_format_unverified",
                )

    def test_print_visible_notes_survive_screen_styles_and_css_cascade(self):
        card = money_card(price="1", quantity="3", total="3.01")
        table = "{{#materials}}{{quantity_display}} * {{price_display}} = {{total_display}}{{/materials}}"
        for suffix in (
            '<style>.unused {display:none}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>.note {display:none}@media print {.note {display:block}}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>@media screen {.note {display:none}}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>.note[hidden] {display:none}</style><div class="note">{{{rounding_notes_html}}}</div>',
            "<details open><summary>More</summary>{{{rounding_notes_html}}}</details>",
            '<style>.note {display:none}.note {display:block}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>.note {display:none!important}@media print {.note {display:block!important}}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<div style="opacity:.5">{{{rounding_notes_html}}}</div>',
        ):
            with self.subTest(suffix=suffix):
                self.assert_guard(card, "parts_sale", table + suffix, valid=True)

    def test_invoice_settlement_note_follows_used_fields_across_document_types(self):
        card = money_card(
            price=".03", quantity="1", payments=[{"amount": ".02", "payment_method": "cash"}]
        )
        table = "{{invoice.total_display}} {{invoice.prepayment_display}} {{invoice.amount_due_display}}"
        for document in ("invoice", "inspection_sheet", "parts_sale"):
            with self.subTest(document=document):
                self.assert_guard(
                    card,
                    document,
                    table,
                    valid=False,
                    error_code="template_money_format_unverified",
                )
                self.assert_guard(
                    card,
                    document,
                    table + " {{invoice.settlement_rounding_adjustment_display}}",
                    valid=True,
                )

    def test_cashless_row_precision_validation_follows_invoice_fields(self):
        card = money_card(price="999999999999999.99", quantity="0.00000000001")
        content = (
            "{{#invoice.line_items}}{{quantity_display}} * {{price_display}} = "
            "{{total_display}}{{/invoice.line_items}} Total {{invoice.total_display}}"
        )
        for document in ("invoice", "inspection_sheet", "parts_sale", "repair_order"):
            with self.subTest(document=document):
                self.assert_guard(
                    card, document, content, valid=False, error_code="unit_precision_unavailable"
                )

    def test_source_rounding_note_is_retained_when_invoice_totals_are_also_used(self):
        card = money_card(price="1", quantity="3", total="3.01")
        content = (
            "{{#materials}}{{quantity_display}} * {{price_display}} = {{total_display}}"
            "{{/materials}} Cashless total {{invoice.total_display}}"
        )
        self.assert_guard(
            card,
            "repair_order",
            content,
            valid=False,
            error_code="template_money_format_unverified",
        )
        self.assert_guard(card, "repair_order", content + "{{{rounding_notes_html}}}", valid=True)

    def test_regulated_cashless_rows_are_validated_on_nonmoney_custom_form(self):
        card = money_card(price="999999999999999.99", quantity="0.00000000001")
        content = (
            "{{#regulated.rows}}{{quantity_display}} * {{price_display}} = "
            "{{total_with_tax_display}}{{/regulated.rows}}"
        )
        self.assert_guard(
            card, "inspection_sheet", content, valid=False, error_code="unit_precision_unavailable"
        )

    def test_nested_raw_decimal_cannot_escape_safe_money_field_validation(self):
        card = money_card(price="1.2345", quantity="1")
        for document in ("invoice", "inspection_sheet", "parts_sale"):
            for content in (
                "{{#invoice.total}}{{real}}{{/invoice.total}}",
                "{{#invoice}}{{#total}}{{real}}{{/total}}{{/invoice}}",
            ):
                with self.subTest(document=document, content=content):
                    self.assert_guard(
                        card,
                        document,
                        content,
                        valid=False,
                        error_code="template_money_format_unverified",
                    )

    def test_nested_raw_money_on_nonmoney_form_cannot_hide_inconsistent_source(self):
        card = money_card(price="1", quantity="3")
        card.repair_order.materials[0].total = "4"  # Corrupted in-memory source only.
        result = self.assert_guard(
            card,
            "inspection_sheet",
            "Raw {{#invoice.total}}{{real}}{{/invoice.total}}",
            valid=False,
            error_code="line_amount_mismatch",
        )
        self.assertIn(
            "template_money_format_unverified",
            {issue["code"] for issue in result["money_validation"]["errors"]},
        )

    def test_safe_display_fields_remain_valid_inside_monetary_sections(self):
        card = money_card(price="1", quantity="3")
        for content in (
            "{{#invoice.total}}{{invoice.total_display}}{{/invoice.total}}",
            "{{#invoice}}{{total_display}}{{/invoice}}",
            "{{#invoice}}{{#total}}{{invoice.total_display}}{{/total}}{{/invoice}}",
            "{{#materials}}{{price_display}} {{quantity_display}} {{total_display}}{{/materials}}",
        ):
            with self.subTest(content=content):
                self.assert_guard(card, "invoice", content, valid=True)

    def test_aggregate_does_not_cover_a_partly_hidden_set_of_notes(self):
        card = money_card(price="1", quantity="3", total="3.01")
        card.repair_order.materials.append(deepcopy(card.repair_order.materials[0]))
        self.assert_guard(
            card,
            "parts_sale",
            "<style>.notes > div:first-child{display:none}</style>"
            '<div class="notes">{{{rounding_notes_html}}}</div>',
            valid=False,
            error_code="template_money_format_unverified",
        )

    def test_limited_css_visibility_rejects_unverifiable_notes_without_crashing(self):
        card = money_card(price="1", quantity="3", total="3.01")
        for suffix in (
            "<style>" + "div " * 100 + ".note{display:none}</style>",
            "<style>" + ":not(" * 300 + ".note" + ")" * 300 + "{display:none}</style>",
            '<link rel="stylesheet" href="https://invalid.example/print.css">',
            "<style>@supports(display:grid){.note{display:none}}</style>",
            "<style>.note{position:absolute;left:-10000px}</style>",
            "<style>.note{overflow:hidden;height:0}</style>",
            "<style>.note{color:transparent}</style>",
            "<style>.note{color:white}</style>",
            "<style>.note{display:var(--missing, none )}</style>",
            "<style>.note{font-size:0}</style>",
            "<div>" * 130,
        ):
            with self.subTest(suffix=suffix):
                self.assert_guard(
                    card,
                    "parts_sale",
                    suffix + '<div class="note">{{{rounding_notes_html}}}</div>',
                    valid=False,
                )

    def test_readable_notes_support_inheritance_variables_and_inline_cascade(self):
        card = money_card(price="1", quantity="3", total="3.01")
        for content in (
            '<section style="font-size:0"><div style="font-size:12px">{{{rounding_notes_html}}}</div></section>',
            '<section style="line-height:0"><div style="line-height:normal">{{{rounding_notes_html}}}</div></section>',
            '<section style="visibility:hidden"><div style="visibility:visible">{{{rounding_notes_html}}}</div></section>',
            '<style>:root{--ink:#25292e}.note{color:var(--ink);font:12px/1.4 Arial}</style><div class="note">{{{rounding_notes_html}}}</div>',
            '<style>.note{display:none!important}</style><div class="note" style="display:block!important">{{{rounding_notes_html}}}</div>',
        ):
            with self.subTest(content=content):
                self.assert_guard(card, "parts_sale", content, valid=True)

    def test_raw_total_namespace_aliases_on_nonmoney_form_still_validate_source(self):
        card = money_card(price="1", quantity="3")
        card.repair_order.materials[0].total = "4"
        self.assert_guard(
            card,
            "inspection_sheet",
            "{{totals.materials_display}}",
            valid=False,
            error_code="line_amount_mismatch",
        )

    def test_invoice_copy_checks_its_own_rounding_basis(self):
        card = money_card(price="1", quantity="3", total="3.01")
        template = self.service.duplicate_template(template_id="builtin:invoice:standard")[
            "template"
        ]
        self.assert_guard(card, "invoice", template["content"], valid=True)


if __name__ == "__main__":
    unittest.main()
