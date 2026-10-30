"""Required invoice VAT fields must be canonical, visible and presented together."""

from __future__ import annotations

import unittest
from copy import deepcopy

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.invoice_tax_visibility import invoice_tax_summary_visible


class InvoiceTaxVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "invoice": {"has_vat": True, "tax_label": "НДС 5%", "vat_display": "500,00"},
            "other": {"label": "НДС 5%", "amount": "500,00"},
        }
        self.row = "<p>В том числе {{invoice.tax_label}}: {{invoice.vat_display}} ₽</p>"

    def check(self, content, expected):
        baseline = deepcopy(self.context)
        self.assertEqual(invoice_tax_summary_visible(content, self.context), expected, content)
        self.assertEqual(self.context, baseline)

    def test_visible_rows_and_inline_formatting(self):
        for content in (
            self.row,
            "<table><tr><td>В том числе {{invoice.tax_label}}</td><td>{{invoice.vat_display}} ₽</td></tr></table>",
            "<div><b>В том</b> числе&nbsp;<span>{{invoice.tax_label}}</span>: <strong>{{invoice.vat_display}}</strong></div>",
            "{{#invoice.has_vat}}" + self.row + "{{/invoice.has_vat}}",
            "<p>В том числе {{#invoice.tax_label}}{{.}}{{/invoice.tax_label}}: {{#invoice.vat_display}}{{.}}{{/invoice.vat_display}}</p>",
            '<style>@media screen {.tax {display:none}}</style><section class="tax">'
            + self.row
            + "</section>",
            '<style>.tax {display:none} @media print {.tax {display:block}}</style><section class="tax">'
            + self.row
            + "</section>",
        ):
            with self.subTest(content=content):
                self.check(content, True)

    def test_literals_and_other_fields_cannot_substitute_for_canonical_values(self):
        for content in (
            "<p>В том числе НДС 5%: 500,00 ₽</p>",
            self.row.replace("{{invoice.tax_label}}", "{{other.label}}"),
            self.row.replace("{{invoice.vat_display}}", "{{other.amount}}"),
            self.row.replace("{{invoice.vat_display}}", "500,00"),
            self.row.replace("{{invoice.tax_label}}", "НДС 5%"),
            "<!--" + self.row + "--><p>В том числе НДС 5%: 500,00 ₽</p>",
            '<p title="В том числе {{invoice.tax_label}}: {{invoice.vat_display}}">Text</p>',
            "{{#missing}}" + self.row + "{{/missing}}",
            "{{^invoice.has_vat}}" + self.row + "{{/invoice.has_vat}}",
        ):
            with self.subTest(content=content):
                self.check(content, False)

    def test_hidden_or_unverifiable_fields_do_not_count(self):
        for content in (
            "<div hidden>" + self.row + "</div>",
            '<style>@media print {.tax {display:none}}</style><div class="tax">'
            + self.row
            + "</div>",
            self.row.replace(
                "{{invoice.tax_label}}",
                '<span style="visibility:hidden">{{invoice.tax_label}}</span>',
            ),
            self.row.replace(
                "{{invoice.vat_display}}", '<span style="opacity:0">{{invoice.vat_display}}</span>'
            ),
            self.row.replace("В том числе", "<span hidden>В том числе</span>"),
            "<details><summary>Tax</summary>" + self.row + "</details>",
            '<div style="position:absolute">' + self.row + "</div>",
            '<link rel="stylesheet" href="external.css">' + self.row,
            "<script>showTax()</script>" + self.row,
        ):
            with self.subTest(content=content):
                self.check(content, False)

    def test_phrase_label_and_amount_must_belong_to_same_summary(self):
        for content in (
            "<p>В том числе</p><p>{{invoice.tax_label}}: {{invoice.vat_display}}</p>",
            "<div>В том числе {{invoice.tax_label}}</div><footer>{{invoice.vat_display}}</footer>",
            "<table><tr><td>В том числе {{invoice.tax_label}}</td></tr><tr><td>{{invoice.vat_display}}</td></tr></table>",
            "<p>В том числе посторонняя надпись {{invoice.tax_label}}: {{invoice.vat_display}}</p>",
            "В том числе<div>Unrelated</div>{{invoice.tax_label}}: {{invoice.vat_display}}",
            "В том числе<br>{{invoice.tax_label}}: {{invoice.vat_display}}",
            "<p>{{invoice.vat_display}} — В том числе {{invoice.tax_label}}</p>",
        ):
            with self.subTest(content=content):
                self.check(content, False)

    def test_standard_invoice_template(self):
        from minimal_kanban.printing.invoice_v5 import INVOICE_V5_STYLES, INVOICE_V5_TEMPLATE

        self.check(INVOICE_V5_TEMPLATE, True)
        self.check(f"<style>{INVOICE_V5_STYLES}</style>{INVOICE_V5_TEMPLATE}", True)

    def test_visible_conflicting_tax_status_blocks_an_otherwise_valid_summary(self):
        for legacy_status in (
            "<p>НДС 20%</p>",
            "<p>НДС: 0 %</p>",
            "<p>НДС (7%)</p>",
            "<p>НД<span>С 20%</span></p>",
            "<table><tr><td>НДС</td><td>20%</td></tr></table>",
            "<p>Условия</p><p>НДС 20%</p>",
        ):
            with self.subTest(legacy_status=legacy_status):
                self.check(self.row + legacy_status, False)

    def test_consistent_or_hidden_tax_status_does_not_block(self):
        for suffix in (
            "<p>НДС 5%</p>",
            "<p>НДС: 5,00 %</p>",
            "<p>НДС (5.0%)</p>",
            "<!-- Без НДС; НДС 20% -->",
            "<p hidden>Без НДС; НДС 20%</p>",
            '<p style="display:none">Без НДС; НДС 20%</p>',
            "{{#missing}}<p>Без НДС; НДС 20%</p>{{/missing}}",
            '<p title="Без НДС; НДС 20%">Условия оплаты</p>',
        ):
            with self.subTest(suffix=suffix):
                self.check(self.row + suffix, True)

    def test_net_price_captions_are_not_mistaken_for_a_tax_status(self):
        for caption in (
            "<p>Цена без НДС: 10 000,00 ₽</p>",
            "<p>Сумма без НДС: 10 000,00 ₽</p>",
            "<table><tr><th>Без НДС</th><th>С НДС</th></tr><tr><td>10 000,00</td><td>10 500,00</td></tr></table>",
            "<p>Цена без <strong>НДС</strong>: 10 000,00 ₽</p>",
        ):
            with self.subTest(caption=caption):
                self.check(self.row + caption, True)

    def test_unverifiable_markup_and_missing_context_fail_closed(self):
        self.check("<div>" * 130 + self.row + "</div>" * 130, False)
        self.assertFalse(invoice_tax_summary_visible(self.row, {}))
        self.context["invoice"]["vat_display"] = ""
        self.check(self.row, False)


if __name__ == "__main__":
    unittest.main()
