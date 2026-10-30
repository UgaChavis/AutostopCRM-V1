from __future__ import annotations

import html
import re
import tempfile
import unittest
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService, _canonical_cashless_line_items
from minimal_kanban.repair_order import repair_order_cashless_gross_value
from minimal_kanban.services.document_money import resolve_document_tax


def invoice_card(tax: str, *, prices=("0.02", "0.98"), payments=()) -> Card:
    return Card.from_dict(
        {
            "id": "fixed-vat-invoice",
            "title": "Synthetic invoice",
            "column": "inbox",
            "repair_order": {
                "number": "SYNTHETIC-5",
                "date": "30.10.2026",
                "client": "Synthetic customer",
                "tax_label": tax,
                "materials": [
                    {"name": f"Synthetic row {index}", "quantity": "1", "price": price}
                    for index, price in enumerate(prices)
                ],
                "payments": [
                    {"id": f"payment-{index}", "payment_method": method, "amount": amount}
                    for index, (method, amount) in enumerate(payments)
                ],
            },
        }
    )


def number(text: str) -> Decimal:
    return Decimal(html.unescape(text).replace(" ", "").replace("\xa0", "").replace(",", "."))


class InvoiceFixedVatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="synthetic-invoice-fixed-vat-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.service = PrintModuleService(self.root)
        self.service.save_settings({"service_profile": {"tax_label": ""}})

    def files(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def original_rows(self, card, *, profile=""):
        order = card.repair_order
        tax = resolve_document_tax(order.tax_label, profile)
        return _canonical_cashless_line_items(
            [
                {"total": Decimal(row.total), "quantity": Decimal(row.quantity)}
                for row in order.materials
            ],
            target_total=repair_order_cashless_gross_value(order.subtotal_value()),
            tax_rate=tax["rate"],
        )

    def preview(self, card, **kwargs):
        result = self.service.preview_documents(card, selected_document_ids=["invoice"], **kwargs)
        self.assertTrue(result["money_validation"]["valid"], result["money_validation"])
        document = result["documents"][0]
        self.assertFalse(document["document_guard"]["financial_or_tax_mismatch"])
        return document, document["pages"][0]["html"]

    def assert_money(self, rendered, expected_rows):
        gross = [
            number(value)
            for value in re.findall(r'<td class="inv-v5-money inv-v5-line-total">([^<]+)', rendered)
        ]
        prices = [
            number(value)
            for value in re.findall(r'<td class="inv-v5-money">([^<]+)</td>', rendered)
        ]
        self.assertEqual(gross, [row["total"] for row in expected_rows])
        self.assertEqual(prices, [row["price"] for row in expected_rows])
        tax = re.search(
            r'<tr class="inv-v5-tax"><td>В том числе НДС 5%</td><td>([^<]+) ₽</td></tr>',
            rendered,
        )
        self.assertIsNotNone(tax, "The invoice must show the included 5% VAT and amount")
        expected_vat = sum(
            (
                (value * Decimal(5) / Decimal(105)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                for value in gross
            ),
            Decimal(0),
        )
        self.assertEqual(number(tax.group(1)), expected_vat)
        total = re.search(r"<tr><td>Сумма счёта</td><td>([^<]+) ₽</td></tr>", rendered)
        self.assertIsNotNone(total)
        self.assertEqual(number(total.group(1)), sum(gross, Decimal(0)))

    def test_every_order_tax_status_uses_five_percent_without_rewriting_sources(self):
        for tax in ("", "Без НДС", "НДС 0%", "НДС 7%", "НДС 20%", "НДС 5%"):
            with self.subTest(tax=tax):
                card = invoice_card(tax)
                before_card, before_files = card.to_storage_dict(), self.files()
                expected = self.original_rows(card)
                if tax == "Без НДС":
                    self.assertEqual(
                        [row["total"] for row in expected], [Decimal(".03"), Decimal("1.15")]
                    )
                document, rendered = self.preview(card)
                self.assertEqual(document["document_guard"]["tax_status"], "НДС 5%")
                self.assert_money(rendered, expected)
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)

    def test_vat_is_rounded_per_row_before_summing(self):
        card = invoice_card("Без НДС", prices=("0.09", "0.04"))
        expected = self.original_rows(card)
        self.assertEqual([row["total"] for row in expected], [Decimal(".10"), Decimal(".05")])
        _, rendered = self.preview(card)
        self.assert_money(rendered, expected)
        self.assertIn("В том числе НДС 5%</td><td>0,00 ₽", rendered)

    def test_profile_rate_keeps_original_gross_allocation_but_invoice_tax_is_five(self):
        for profile in ("Без НДС", "НДС 0%", "НДС 7%", "НДС 20%"):
            with self.subTest(profile=profile):
                self.service.save_settings({"service_profile": {"tax_label": profile}})
                card = invoice_card("")
                before_card, before_files = card.to_storage_dict(), self.files()
                expected = self.original_rows(card, profile=profile)
                _, rendered = self.preview(card)
                self.assert_money(rendered, expected)
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)

    def test_prepaid_amounts_and_authoritative_debt_do_not_change(self):
        for payments, prepaid, due in (
            ((), None, "10000"),
            ((("cash", "850"),), "1000", "9000"),
            ((("card", "850"),), "1000", "9000"),
            ((("cashless", "1000"),), "1000", "9000"),
            ((("cash", "850"), ("cashless", "1000")), "2000", "8000"),
            ((("cashless", "10000"),), "10000", "0"),
        ):
            with self.subTest(payments=payments):
                card = invoice_card("Без НДС", prices=("8500",), payments=payments)
                before = card.to_storage_dict()
                document, rendered = self.preview(card)
                self.assert_money(rendered, self.original_rows(card))
                self.assertEqual(number(document["document_guard"]["rendered_total"]), Decimal(due))
                self.assertEqual(
                    card.repair_order.payment_summary_value()["noncash_due"], Decimal(due)
                )
                match = re.search(r"<td>Учтённая предоплата</td><td>- ([^<]+) ₽</td>", rendered)
                if prepaid is None:
                    self.assertIsNone(match)
                else:
                    self.assertIsNotNone(match)
                    self.assertEqual(number(match.group(1)), Decimal(prepaid))
                self.assertEqual(card.to_storage_dict(), before)

    def test_duplicate_template_and_prepared_pdf_and_print_use_the_same_tax(self):
        custom = self.service.duplicate_template(template_id="builtin:invoice:standard")["template"]
        card = invoice_card("", prices=("8500",))
        options = {"selected_template_ids": {"invoice": custom["id"]}}
        before_card, before_files = card.to_storage_dict(), self.files()
        _, rendered = self.preview(card, **options)
        expected = self.original_rows(card)
        self.assert_money(rendered, expected)
        pdf = self.service.prepare_documents_pdf(card, selected_document_ids=["invoice"], **options)
        native = self.service.prepare_print_documents(
            card, selected_document_ids=["invoice"], printer_name="Synthetic", **options
        )
        self.assert_money(pdf.html, expected)
        self.assert_money(native.html, expected)
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)

    def assert_backends_blocked(self, card, options):
        with (
            patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for operation in (
                lambda: self.service.export_documents_pdf(card, **options),
                lambda: self.service.print_documents(card, printer_name="Synthetic", **options),
            ):
                with self.assertRaises(PrintModuleError) as failure:
                    operation()
                self.assertEqual(failure.exception.code, "document_money_validation_failed")
                self.assertEqual(failure.exception.status_code, 422)
            pdf.assert_not_called()
            printer.assert_not_called()

    def test_saved_custom_template_requires_visible_vat_without_rewriting_sources(self):
        card = invoice_card("НДС 20%", prices=("8500",))
        content = (
            '<div class="document-page"><h1>Сохранённый счёт</h1>'
            "<p>Сумма счёта {{invoice.total_display}} ₽</p>"
            "<p>К оплате {{invoice.amount_due_display}} ₽</p></div>"
        )
        custom = self.service.save_template(
            document_type="invoice", name="Сохранённый счёт", content=content
        )["template"]
        self.service.set_default_template(document_type="invoice", template_id=custom["id"])
        before_card, before_files = card.to_storage_dict(), self.files()
        options = {"selected_document_ids": ["invoice"]}
        missing = self.service.preview_documents(card, **options)
        self.assertFalse(missing["money_validation"]["valid"])
        self.assertIn(
            "invoice_vat_summary_missing",
            {issue["code"] for issue in missing["money_validation"]["errors"]},
        )
        document = missing["documents"][0]
        self.assertEqual(document["template"]["id"], custom["id"])
        self.assertEqual(document["template"]["content"], content)
        self.assertTrue(document["template"]["is_default"])
        self.assertNotIn("В том числе НДС", document["pages"][0]["html"])
        self.assert_backends_blocked(card, options)
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)

        visible = content.replace(
            "</div>",
            "<p>В том числе {{invoice.tax_label}}: {{invoice.vat_display}} ₽</p></div>",
        )
        fixed_options = {**options, "template_overrides": {"invoice": visible}}
        corrected = self.service.preview_documents(card, **fixed_options)
        self.assertTrue(corrected["money_validation"]["valid"], corrected["money_validation"])
        self.assertIn("В том числе НДС 5%: 476,19 ₽", corrected["documents"][0]["pages"][0]["html"])
        with (
            patch(
                "minimal_kanban.printing.service.render_html_to_pdf_bytes",
                return_value=b"%PDF-synthetic",
            ) as pdf,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            self.service.export_documents_pdf(card, **fixed_options)
            self.service.print_documents(card, printer_name="Synthetic", **fixed_options)
            pdf.assert_called_once()
            printer.assert_called_once()
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)

    def test_invoice_and_factura_tax_mismatch_blocks_batch_without_rewriting_documents(self):
        card = invoice_card("НДС 20%", prices=("8500",))
        before_card, before_files = card.to_storage_dict(), self.files()
        document_ids = ["invoice", "invoice_factura"]
        individual = {}
        for document_id in document_ids:
            preview = self.service.preview_documents(card, selected_document_ids=[document_id])
            self.assertTrue(preview["money_validation"]["valid"], preview["money_validation"])
            individual[document_id] = preview["documents"][0]
        self.assert_money(individual["invoice"]["pages"][0]["html"], self.original_rows(card))
        factura_html = individual["invoice_factura"]["pages"][0]["html"]
        self.assertIn("20%", factura_html)
        self.assertIn("1 666,67", factura_html)

        options = {"selected_document_ids": document_ids}
        batch = self.service.preview_documents(card, **options)
        self.assertFalse(batch["money_validation"]["valid"])
        self.assertEqual({document["id"] for document in batch["documents"]}, set(document_ids))
        for document in batch["documents"]:
            self.assertFalse(document["money_validation"]["valid"])
            self.assertIn(
                "document_totals_mismatch",
                {issue["code"] for issue in document["money_validation"]["errors"]},
            )
            self.assertEqual(document["pages"], individual[document["id"]]["pages"])
        self.assert_backends_blocked(card, options)
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)

    def test_small_batch_rejects_equal_vat_with_different_tax_status(self):
        for tax in ("НДС 20%", "НДС 0%", "Без НДС"):
            for regulated in ("invoice_factura", "upd"):
                with self.subTest(tax=tax, regulated=regulated):
                    card = invoice_card(tax, prices=("0.01",))
                    before_card, before_files = card.to_storage_dict(), self.files()
                    options = {"selected_document_ids": ["invoice", regulated]}
                    preview = self.service.preview_documents(card, **options)
                    self.assertFalse(preview["money_validation"]["valid"])
                    self.assertIn(
                        "document_totals_mismatch",
                        {issue["code"] for issue in preview["money_validation"]["errors"]},
                    )
                    self.assertIn(
                        "В том числе НДС 5%</td><td>0,00 ₽",
                        preview["documents"][0]["pages"][0]["html"],
                    )
                    self.assert_backends_blocked(card, options)
                    self.assertEqual(card.to_storage_dict(), before_card)
                    self.assertEqual(self.files(), before_files)

    def test_saved_act_tax_status_is_compared_even_when_rounded_vat_matches(self):
        for index, (saved_tax, current_tax, other) in enumerate(
            (
                ("НДС 20%", "НДС 20%", "invoice"),
                ("НДС 20%", "НДС 5%", "invoice"),
                ("НДС 0%", "Без НДС", "invoice_factura"),
            )
        ):
            with self.subTest(saved_tax=saved_tax, current_tax=current_tax):
                card = invoice_card(saved_tax, prices=("0.01",))
                card.id = f"saved-tax-status-{index}"
                form = self.service.get_completion_act_form(card)["form"]
                self.service.save_completion_act_form(
                    card,
                    form_data=form,
                    expected_version=0,
                    idempotency_key=f"save-tax-status-{index}",
                )
                card.repair_order.tax_label = current_tax
                before_card, before_files = card.to_storage_dict(), self.files()
                options = {"selected_document_ids": [other, "completion_act"]}
                preview = self.service.preview_documents(card, **options)
                self.assertFalse(preview["money_validation"]["valid"])
                self.assertIn(
                    "document_totals_mismatch",
                    {issue["code"] for issue in preview["money_validation"]["errors"]},
                )
                totals = preview["documents"][1]["computed_totals"]
                self.assertEqual(totals["gross"], "0.01")
                self.assertEqual(totals["vat"], "0.00")
                self.assertEqual(totals["tax_label"], saved_tax)
                self.assert_backends_blocked(card, options)
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)

    def test_equivalent_five_percent_labels_allow_small_saved_act_batch(self):
        for index, tax in enumerate(("НДС 5%", "5%", "НДС (5,00%)", "НДС 5.00%")):
            with self.subTest(tax=tax):
                card = invoice_card(tax, prices=("0.01",))
                card.id = f"equivalent-tax-status-{index}"
                form = self.service.get_completion_act_form(card)["form"]
                self.service.save_completion_act_form(
                    card,
                    form_data=form,
                    expected_version=0,
                    idempotency_key=f"save-equivalent-tax-{index}",
                )
                before_card, before_files = card.to_storage_dict(), self.files()
                options = {
                    "selected_document_ids": ["invoice", "invoice_factura", "upd", "completion_act"]
                }
                preview = self.service.preview_documents(card, **options)
                self.assertTrue(preview["money_validation"]["valid"], preview["money_validation"])
                self.service.prepare_documents_pdf(card, **options)
                self.service.prepare_print_documents(card, printer_name="Synthetic", **options)
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)
