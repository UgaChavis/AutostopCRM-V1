"""Data and compatibility regressions for the approved invoice layout."""

from __future__ import annotations

import html
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.models import PrintModuleSettings  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from tests.browser_ui_support import UiBrowserTestCase  # noqa: E402
from tests.test_document_money_v2 import money_card  # noqa: E402
from tests.test_printing_service import build_business_client, build_card  # noqa: E402


class InvoiceV5Tests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.service = PrintModuleService(self.root)
        self.card = build_card()

    def preview(self, card=None, service=None, **kwargs) -> dict:
        return (service or self.service).preview_documents(
            card or self.card, selected_document_ids=["invoice"], **kwargs
        )["documents"][0]

    def context(self, card=None, settings=None, client=None) -> dict:
        card = card or self.card
        return self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=settings or self.service._read_settings(),
            client=client,
        )

    def test_standard_id_and_logical_preview_contract_remain_stable(self) -> None:
        document = self.preview()
        self.assertEqual(document["template"]["id"], "builtin:invoice:standard")
        self.assertEqual(document["page_count"], 1)
        self.assertEqual(document["pages"][0]["number"], 1)
        rendered = document["pages"][0]["html"]
        self.assertIn('class="document-page invoice-v5"', rendered)
        self.assertEqual(rendered.lower().count("<!doctype html>"), 1)
        self.assertEqual(rendered.lower().count("<body>"), 1)
        self.assertNotIn("Демонстрационный макет", rendered)
        self.assertNotIn("Не для оплаты", rendered)
        self.assertNotIn("{{", rendered)
        self.assertTrue(document["money_validation"]["valid"])

    def test_payment_purpose_comes_from_saved_settings_and_is_escaped(self) -> None:
        purpose = 'Оплата по договору <A-12> & "специальное назначение".'
        self.service.save_settings({"service_profile": {"payment_purpose": purpose}})
        reloaded = PrintModuleService(self.root)
        rendered = self.preview(service=reloaded)["pages"][0]["html"]
        self.assertIn(html.escape(purpose), rendered)
        self.assertNotIn("Оплата работ и запасных частей по счёту", rendered)
        self.assertEqual(reloaded._read_settings().service_profile.payment_purpose, purpose)

    def test_legacy_custom_default_and_all_legacy_fields_survive_reload(self) -> None:
        content = (
            '<div class="document-page"><h1>Сохранённый счёт</h1>'
            "{{client.checking_account}} {{client.bank_name}} {{client.contact_person}} "
            "{{client.email_display}} {{service.payment_purpose}}</div>"
        )
        custom = self.service.save_template(
            document_type="invoice", name="Прежний шаблон", content=content
        )["template"]
        self.service.set_default_template(document_type="invoice", template_id=custom["id"])
        before = (
            self.service._templates_path.read_bytes(),
            self.service._settings_path.read_bytes(),
        )
        reloaded = PrintModuleService(self.root)
        client = build_business_client()
        document = self.preview(service=reloaded, client=client)
        self.assertEqual(document["template"]["id"], custom["id"])
        self.assertTrue(document["template"]["is_default"])
        self.assertEqual(document["template"]["content"], content)
        rendered = document["pages"][0]["html"]
        for value in (
            client.checking_account,
            client.bank_name,
            client.contact_person,
            client.email,
        ):
            self.assertIn(html.escape(value), rendered)
        explicit = self.preview(
            service=reloaded,
            selected_template_ids={"invoice": "builtin:invoice:standard"},
        )
        self.assertEqual(explicit["template"]["id"], "builtin:invoice:standard")
        self.assertEqual(reloaded._templates_path.read_bytes(), before[0])
        self.assertEqual(reloaded._settings_path.read_bytes(), before[1])

    def test_duplicated_v5_keeps_visible_settlement_rounding_and_can_export(self) -> None:
        card = money_card(
            price=".03", quantity="1", payments=[{"amount": ".02", "payment_method": "cash"}]
        )
        custom = self.service.duplicate_template(template_id="builtin:invoice:standard")["template"]
        options = {"selected_template_ids": {"invoice": custom["id"]}}
        context = self.context(card)
        self.assertEqual(context["invoice"]["settlement_rounding_adjustment"], Decimal("-.01"))
        document = self.preview(card, **options)
        self.assertFalse(document["template"]["is_builtin"])
        self.assertTrue(document["money_validation"]["valid"], document["money_validation"])
        self.assertIn("Округление зачёта предоплаты", document["pages"][0]["html"])
        self.assertIn("−0,01", document["pages"][0]["html"])
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes", return_value=b"%PDF-test"
        ) as backend:
            result = self.service.export_documents_pdf(
                card, selected_document_ids=["invoice"], **options
            )
        self.assertEqual(result[0], b"%PDF-test")
        backend.assert_called_once()

    def test_custom_copy_traces_rounding_to_the_canonical_invoice_row(self) -> None:
        custom = self.service.duplicate_template(template_id="builtin:invoice:standard")["template"]
        context = self.context()
        # Canonical invoices normally need no row adjustment. Exercise the shared
        # template contract with an annotated canonical row, not a copied view row.
        row = context["invoice"]["line_items"][0]
        row.update(
            has_rounding_adjustment=True,
            rounding_adjustment=Decimal(".01"),
            rounding_adjustment_display="+0,01",
        )
        with patch.object(self.service, "_build_document_context", return_value=context):
            document = self.preview(selected_template_ids={"invoice": custom["id"]})
        self.assertTrue(document["money_validation"]["valid"], document["money_validation"])
        self.assertIn("Округление +0,01", document["pages"][0]["html"])

    def test_payment_variants_render_authoritative_due_without_mutating_order(self) -> None:
        for payments in (
            [],
            [("cash", "100")],
            [("card", "100")],
            [("cashless", "100")],
            [("cash", "100"), ("cashless", "200")],
            [("cashless", "6705.88")],
            [("cashless", "7000")],
        ):
            with self.subTest(payments=payments):
                card = money_card(
                    payments=[
                        {"id": str(index), "amount": amount, "payment_method": method}
                        for index, (method, amount) in enumerate(payments)
                    ]
                )
                before = card.to_storage_dict()
                invoice = self.context(card)["invoice"]
                document = self.preview(card)
                rendered = document["pages"][0]["html"]
                self.assertTrue(document["money_validation"]["valid"])
                self.assertEqual(
                    invoice["amount_due"], card.repair_order.payment_summary_value()["noncash_due"]
                )
                self.assertIn(invoice["amount_due_display"], rendered)
                self.assertIn(invoice["amount_due_words_display"], rendered)
                self.assertIn("1 117,647", rendered)
                if payments:
                    self.assertIn("Учтённая предоплата", rendered)
                    self.assertIn("- " + invoice["prepayment_display"], rendered)
                else:
                    self.assertNotIn("Учтённая предоплата</td>", rendered)
                self.assertEqual(card.to_storage_dict(), before)

    def test_absent_fields_use_fallbacks_without_fake_kpp(self) -> None:
        for reception, phone, expected in (("", "111-22-33", "111-22-33"), ("", "", "—")):
            with self.subTest(phone=phone):
                settings = {
                    "service_profile": {"reception_phone": reception, "phone": phone, "kpp": ""}
                }
                rendered = self.preview(print_settings=settings)["pages"][0]["html"]
                self.assertIn(expected, rendered)
                self.assertNotIn("КПП —", rendered)
                context = self.context(settings=PrintModuleSettings.from_dict(settings))
                self.assertEqual(context["service"]["reception_phone_display"], expected)

    def test_recipient_kpp_is_hidden_and_buyer_kpp_is_preserved(self) -> None:
        document = self.preview(
            money_card(tax="НДС 5%"),
            client=build_business_client(),
            print_settings={"service_profile": {"inn": "123456789012", "kpp": "999999999"}},
        )
        rendered = document["pages"][0]["html"]
        self.assertTrue(document["money_validation"]["valid"])
        self.assertIn("ИНН 123456789012", rendered)
        self.assertNotIn("КПП 999999999", rendered)
        self.assertIn("КПП 246801001", rendered)
        self.assertIn("В том числе НДС 5%", rendered)

    def test_sections_empty_or_present_and_untrusted_names_remain_data(self) -> None:
        for works, materials in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(works=works, materials=materials):
                card = build_card()
                card.repair_order.works = card.repair_order.works if works else []
                card.repair_order.materials = card.repair_order.materials if materials else []
                if works:
                    card.repair_order.works[0].name = '<img src=x onerror="bad()"> & Работа'
                document = self.preview(card)
                rendered = document["pages"][0]["html"]
                self.assertEqual(document["page_count"], 1)
                self.assertNotIn("{{", rendered)
                self.assertNotIn('<img src=x onerror="bad()">', rendered)
                if works:
                    self.assertIn(html.escape(card.repair_order.works[0].name), rendered)
                if not works and not materials:
                    self.assertIn("Работы и материалы не указаны", rendered)

    def test_tax_zero_is_distinct_from_without_vat(self) -> None:
        for tax, expected in (
            ("Без НДС", "Без НДС"),
            ("НДС 0%", "В том числе НДС 0%"),
            ("НДС 5%", "В том числе НДС 5%"),
        ):
            with self.subTest(tax=tax):
                document = self.preview(money_card(tax=tax))
                self.assertTrue(document["money_validation"]["valid"])
                self.assertIn(expected, document["pages"][0]["html"])

    def test_invoice_number_cannot_escape_the_running_header_style(self) -> None:
        self.card.repair_order.number = 'X"; } </style><script>invoiceNumberProbe()</script>'
        rendered = self.preview()["pages"][0]["html"]
        self.assertNotIn("<script>invoiceNumberProbe()", rendered)
        self.assertNotIn("</style><script>", rendered)
        self.assertIn(html.escape(self.card.repair_order.number), rendered)


class InvoiceV5LayoutBrowserTests(UiBrowserTestCase):
    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = PrintModuleService(Path(directory.name))
        # A4 less the named page's two 9 mm margins, including the form's padding.
        self.page.set_viewport_size({"width": 726, "height": 1100})
        self.page.emulate_media(media="print")

    def show_invoice(self, card) -> None:
        document = self.service.preview_documents(card, selected_document_ids=["invoice"])[
            "documents"
        ][0]
        self.assertTrue(document["money_validation"]["valid"])
        self.page.set_content(document["pages"][0]["html"])

    def test_long_units_fit_beside_exact_quantities_and_prices(self) -> None:
        for unit, quantity, price in (
            ("шт", "1", "1000"),
            ("ММММММММ", "1", "12345678.90"),
            ("упаковка", "0.12345678901", "123.12345678901"),
        ):
            with self.subTest(unit=unit, quantity=quantity):
                card = build_card()
                card.repair_order.works = []
                card.repair_order.materials = card.repair_order.materials[:1]
                row = card.repair_order.materials[0]
                row.inventory_unit, row.quantity, row.price, row.total = unit, quantity, price, ""
                card.repair_order.payments = []
                self.show_invoice(card)
                geometry = self.page.locator(".inv-v5-items tbody tr:not(.inv-v5-group)").evaluate("""row => {
                  const rect = node => { const b = node.getBoundingClientRect();
                    return {left: b.left, right: b.right, width: b.width}; };
                  const text = node => { const range = document.createRange();
                    range.selectNodeContents(node); return rect(range); };
                  const unit = row.querySelector('.inv-v5-quantity-unit');
                  const value = row.querySelector('.inv-v5-quantity-value');
                  return {unit: {value: unit.textContent, box: rect(unit), text: text(unit)},
                    quantity: {value: value.textContent, text: text(value)},
                    cells: Array.from(row.cells).map(cell => ({box: rect(cell), text: text(cell)})),
                    tableWidth: row.closest('table').getBoundingClientRect().width};
                }""")
                self.assertEqual(geometry["unit"]["value"], unit)
                self.assertEqual(geometry["quantity"]["value"], quantity.replace(".", ","))
                self.assertLessEqual(
                    geometry["unit"]["text"]["right"], geometry["unit"]["box"]["right"] + 0.1
                )
                self.assertLessEqual(
                    geometry["quantity"]["text"]["right"] + 4.5,
                    geometry["unit"]["text"]["left"],
                )
                for cell in geometry["cells"][2:]:
                    self.assertGreaterEqual(cell["text"]["left"], cell["box"]["left"] + 6.5)
                    self.assertLessEqual(cell["text"]["right"], cell["box"]["right"] - 6.5)
                if unit == "шт":
                    for cell, percent in zip(geometry["cells"], (4, 53, 15, 14, 14), strict=True):
                        self.assertAlmostEqual(
                            cell["box"]["width"] / geometry["tableWidth"] * 100, percent, delta=0.1
                        )

    def test_long_vehicle_wraps_without_widening_the_printed_page(self) -> None:
        baseline = None
        for vehicle in ("Lada Vesta", ("ДемонстрационныйАвтомобиль" * 8)[:160]):
            with self.subTest(vehicle=vehicle):
                card = build_card()
                card.vehicle = card.repair_order.vehicle = vehicle
                self.show_invoice(card)
                geometry = self.page.evaluate("""() => {
                  const link = document.querySelector('.inv-v5-order-link');
                  const box = link.getBoundingClientRect();
                  const car = document.querySelector('.inv-v5-car');
                  const range = document.createRange(); range.selectNodeContents(car);
                  const text = range.getBoundingClientRect();
                  const stamp = document.querySelector('.inv-v5-stamp').getBoundingClientRect();
                  return {vehicle: car.textContent, left: box.left, right: box.right,
                    textLeft: text.left, textRight: text.right,
                    scrollWidth: document.documentElement.scrollWidth,
                    stampWidth: stamp.width, stampHeight: stamp.height};
                }""")
                self.assertIn(vehicle, geometry["vehicle"])
                self.assertGreaterEqual(geometry["textLeft"], geometry["left"] - 0.1)
                self.assertLessEqual(geometry["textRight"], geometry["right"] + 0.1)
                self.assertLessEqual(geometry["scrollWidth"], 726)
                self.assertAlmostEqual(geometry["stampWidth"], 40 * 96 / 25.4, delta=0.1)
                self.assertAlmostEqual(geometry["stampHeight"], 40 * 96 / 25.4, delta=0.1)
                if baseline is None:
                    baseline = geometry["scrollWidth"]
                else:
                    self.assertEqual(geometry["scrollWidth"], baseline)
