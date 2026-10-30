from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.formatting import _money_display, _money_words_display
from minimal_kanban.printing.money_validation import batch_document_money_summary
from minimal_kanban.printing.service import PrintModuleService

TAX_LABELS = ("", "Без НДС", "НДС 0%", "НДС 20%", "НДС 5%")
DOCUMENTS = ("invoice", "invoice_factura", "upd", "completion_act")


def synthetic_card(tax="", *, prices=("0.02", "0.98"), payments=()):
    return Card.from_dict(
        {
            "id": "synthetic-tax5-policy",
            "title": "Synthetic tax policy",
            "column": "inbox",
            "repair_order": {
                "number": "SYNTHETIC-TAX5",
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


def included_five(gross):
    return (Decimal(gross) * Decimal(5) / Decimal(105)).quantize(
        Decimal(".01"), rounding=ROUND_HALF_UP
    )


class Tax5DocumentsPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="synthetic-tax5-policy-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.service = PrintModuleService(self.root)
        self.service.save_settings({"service_profile": {"tax_label": ""}})

    def files(self):
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def context(self, card, document, overrides=None):
        return self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition(document),
            settings=self.service._read_settings(),
            document_overrides=overrides,
        )

    def money_summary(self, card, document, overrides=None):
        return batch_document_money_summary(
            self.service._document_definition(document), self.context(card, document, overrides)
        )

    def assert_five_percent(self, summary):
        self.assertEqual(summary["tax_status"], {"known": True, "has_vat": True, "rate": "0.05"})

    def test_all_order_and_profile_tax_statuses_use_five_percent_without_source_writes(self):
        for order_tax in TAX_LABELS:
            for profile_tax in TAX_LABELS:
                with self.subTest(order_tax=order_tax, profile_tax=profile_tax):
                    self.service.save_settings({"service_profile": {"tax_label": profile_tax}})
                    card = synthetic_card(order_tax, prices=("8500",))
                    before_card, before_files = deepcopy(card.to_storage_dict()), self.files()
                    for document in DOCUMENTS:
                        with self.subTest(document=document):
                            summary = self.money_summary(card, document)
                            self.assert_five_percent(summary)
                            self.assertEqual(Decimal(summary["total"]), Decimal("10000.00"))
                            self.assertEqual(Decimal(summary["vat"]), Decimal("476.19"))
                    self.assertEqual(card.to_storage_dict(), before_card)
                    self.assertEqual(self.files(), before_files)

    def test_cent_allocation_and_gross_prices_survive_tax_override_in_every_document(self):
        # These accepted gross allocations were captured before the policy change.
        # Reallocating the source rows using 5% moves a cent between the two rows.
        for tax in TAX_LABELS:
            with self.subTest(tax=tax):
                card = synthetic_card(tax)
                expected = (
                    [Decimal(".02"), Decimal("1.16")]
                    if tax == "НДС 5%"
                    else [Decimal(".03"), Decimal("1.15")]
                )
                expected_vat = [included_five(value) for value in expected]
                for document in DOCUMENTS:
                    with self.subTest(document=document):
                        context = self.context(card, document)
                        if document == "completion_act":
                            rows = context["completion_act"]["items"]
                            gross = [
                                Decimal(row["sum_without_vat"]) + Decimal(row["vat"])
                                for row in rows
                            ]
                        else:
                            rows = (
                                context["invoice"]["line_items"]
                                if document == "invoice"
                                else context["regulated"]["rows"]
                            )
                            gross = [row["total"] for row in rows]
                            expected_prices = (
                                expected
                                if document == "invoice"
                                else [
                                    gross - vat
                                    for gross, vat in zip(expected, expected_vat, strict=True)
                                ]
                            )
                            self.assertEqual([row["price"] for row in rows], expected_prices)
                        self.assertEqual(gross, expected)
                        self.assertEqual([Decimal(row["vat"]) for row in rows], expected_vat)

    def test_public_batch_validates_same_total_vat_and_status_for_all_four_documents(self):
        for tax in TAX_LABELS:
            with self.subTest(tax=tax):
                card = synthetic_card(tax)
                result = self.service.preview_documents(card, selected_document_ids=list(DOCUMENTS))
                self.assertTrue(result["money_validation"]["valid"], result["money_validation"])
                summaries = [self.money_summary(card, document) for document in DOCUMENTS]
                self.assertTrue(all(summary == summaries[0] for summary in summaries))
                self.assert_five_percent(summaries[0])
                for document in result["documents"]:
                    rendered = "".join(page["html"] for page in document["pages"])
                    self.assertIn("5%", rendered)

    def test_vat_rounds_per_line_before_summing_across_the_batch(self):
        card = synthetic_card("Без НДС", prices=("0.09", "0.04"))
        for document in DOCUMENTS:
            with self.subTest(document=document):
                summary = self.money_summary(card, document)
                self.assert_five_percent(summary)
                self.assertEqual(Decimal(summary["total"]), Decimal(".15"))
                # VAT(.10) + VAT(.05) is zero, while VAT(.15) would be .01.
                self.assertEqual(Decimal(summary["vat"]), Decimal(".00"))

    def test_each_act_mode_uses_five_percent_and_retains_its_calculation_basis(self):
        for tax in TAX_LABELS:
            for mode, net, vat, gross in (
                ("order_cashless", "9523.81", "476.19", "10000.00"),
                ("order_cash", "8095.24", "404.76", "8500.00"),
                ("manual_net", "8500.00", "425.00", "8925.00"),
            ):
                with self.subTest(tax=tax, mode=mode):
                    card = synthetic_card(tax, prices=("8500",))
                    loaded = self.service.get_completion_act_form(card)
                    form = deepcopy(
                        loaded["form"] if mode == "manual_net" else loaded["linked_forms"][mode]
                    )
                    if mode == "manual_net":
                        form["calculation_mode"] = mode
                        form["items"] = [
                            {
                                "name": "Synthetic net service",
                                "quantity": "1",
                                "price": "8500",
                                "unit": "шт",
                            }
                        ]
                    before_card, before_files = deepcopy(card.to_storage_dict()), self.files()
                    preview = self.service.preview_documents(
                        card,
                        selected_document_ids=["completion_act"],
                        document_overrides={"completion_act": form},
                    )
                    self.assertTrue(
                        preview["money_validation"]["valid"], preview["money_validation"]
                    )
                    totals = preview["documents"][0]["computed_totals"]
                    self.assertEqual(totals["tax_label"], "НДС 5%")
                    self.assertTrue(totals["has_vat"])
                    self.assertEqual(
                        (totals["base"], totals["vat"], totals["gross"]), (net, vat, gross)
                    )
                    self.assertEqual(card.to_storage_dict(), before_card)
                    self.assertEqual(self.files(), before_files)

    def test_prepayments_debt_and_repair_order_math_are_not_recomputed_by_tax_policy(self):
        for payments, prepaid, due in (
            ((), "0", "10000"),
            ((("cash", "850"),), "1000", "9000"),
            ((("card", "850"),), "1000", "9000"),
            ((("cashless", "1000"),), "1000", "9000"),
            ((("cash", "850"), ("cashless", "1000")), "2000", "8000"),
            ((("cashless", "10000"),), "10000", "0"),
        ):
            with self.subTest(payments=payments):
                card = synthetic_card("Без НДС", prices=("8500",), payments=payments)
                before_card, before_files = deepcopy(card.to_storage_dict()), self.files()
                repair = self.context(card, "repair_order")
                self.assertEqual(repair["totals"]["cash_total"], Decimal("8500"))
                self.assertEqual(repair["totals"]["noncash_total"], Decimal("10000"))
                invoice = self.context(card, "invoice")["invoice"]
                self.assertEqual(invoice["total"], Decimal("10000"))
                self.assertEqual(invoice["prepayment"], Decimal(prepaid))
                self.assertEqual(invoice["amount_due"], Decimal(due))
                self.assertEqual(
                    card.repair_order.payment_summary_value()["noncash_due"], Decimal(due)
                )
                result = self.service.preview_documents(card, selected_document_ids=list(DOCUMENTS))
                self.assertTrue(result["money_validation"]["valid"], result["money_validation"])
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)

    def test_prepared_pdf_and_native_jobs_use_the_same_policy_without_printing(self):
        card = synthetic_card("Без НДС", prices=("8500",))
        before_card, before_files = deepcopy(card.to_storage_dict()), self.files()
        pdf = self.service.prepare_documents_pdf(card, selected_document_ids=list(DOCUMENTS))
        native = self.service.prepare_print_documents(
            card, selected_document_ids=list(DOCUMENTS), printer_name="Synthetic"
        )
        self.assertIn("В том числе НДС 5%", pdf.html)
        self.assertIn("В том числе НДС 5%", native.html)
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)

    def test_historical_saved_act_stays_unchanged_until_explicit_recalculation_and_save(self):
        card = synthetic_card("Без НДС", prices=("100",))
        form = deepcopy(self.service.get_completion_act_form(card)["form"])
        form["calculation_mode"] = "manual_net"
        form["items"] = [
            {
                "id": "historical-row",
                "name": "Synthetic historical service",
                "quantity": "1",
                "price": "100",
                "unit": "шт",
            }
        ]
        self.service.save_completion_act_form(
            card, form_data=form, expected_version=0, idempotency_key="synthetic-history"
        )
        key = self.service._completion_act_cycle_key(card, card.repair_order)
        path = self.service._completion_act_record_path(key)
        record = json.loads(path.read_text(encoding="utf-8"))
        # Install a valid historical v2 snapshot with the previous 20% tax.
        # Only this temporary synthetic record is edited, never a production file.
        calculation = record["money_snapshot"]["calculation"]
        calculation["tax_context"].update(
            label="НДС 20%", rate="0.20", rate_display="20%", known=True, has_vat=True
        )
        calculation["items"][0]["vat"] = "20.00"
        calculation["totals"].update(
            tax_label="НДС 20%", vat_rate="20", vat_rate_display="20%", has_vat=True
        )
        for field, value in (("base", "100.00"), ("vat", "20.00"), ("gross", "120.00")):
            calculation["totals"].update(
                {
                    field: value,
                    f"{field}_display": _money_display(Decimal(value)),
                    f"{field}_words_display": _money_words_display(Decimal(value)),
                }
            )
        record["source_fingerprint"] = "0" * 64
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        before_card, before_files = deepcopy(card.to_storage_dict()), self.files()

        loaded = self.service.get_completion_act_form(card)
        self.assertTrue(loaded["draft"]["is_stale"])
        self.assertEqual(loaded["totals"]["tax_label"], "НДС 20%")
        self.assertEqual(loaded["totals"]["gross"], "120.00")
        preview = self.service.preview_documents(card, selected_document_ids=["completion_act"])
        self.assertEqual(preview["documents"][0]["computed_totals"]["gross"], "120.00")
        self.assertFalse(preview["money_validation"]["valid"])
        with self.assertRaises(PrintModuleError):
            self.service.prepare_documents_pdf(card, selected_document_ids=["completion_act"])
        recalculated = self.service.preview_documents(
            card,
            selected_document_ids=["completion_act"],
            document_overrides={
                "completion_act": loaded["form"],
                "completion_act_recalculate": True,
            },
        )
        document = recalculated["documents"][0]
        self.assertEqual(document["computed_totals"]["tax_label"], "НДС 5%")
        self.assertEqual(document["computed_totals"]["gross"], "105.00")
        self.assertFalse(recalculated["money_validation"]["valid"])
        self.assertEqual(card.to_storage_dict(), before_card)
        self.assertEqual(self.files(), before_files)
        updated = self.service.save_completion_act_form(
            card,
            form_data=loaded["form"],
            expected_version=1,
            expected_source_fingerprint=loaded["draft"]["current_source_fingerprint"],
            accepted_calculation_fingerprint=document["calculation_fingerprint"],
            idempotency_key="synthetic-history-accepted",
        )
        self.assertFalse(updated["draft"]["is_stale"])
        self.assertEqual(updated["totals"]["tax_label"], "НДС 5%")
        self.assertEqual(updated["totals"]["gross"], "105.00")
        self.assertEqual(card.to_storage_dict(), before_card)


if __name__ == "__main__":
    unittest.main()
