from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest import TestCase

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.invoice_vat import (
    invoice_tax_payload,
    invoice_vat_for_document,
    invoice_vat_rows,
)
from minimal_kanban.services.document_money import build_canonical_document_money, money


class InvoiceVatProjectionTests(TestCase):
    def test_tax_payload_is_explicit_five_percent(self):
        tax = invoice_tax_payload()
        self.assertEqual(tax["label"], "НДС 5%")
        self.assertEqual(tax["rate"], Decimal("0.05"))
        self.assertEqual(tax["source"], "invoice_policy")
        self.assertTrue(tax["has_vat"])
        self.assertTrue(tax["known"])

    def test_document_dispatch_projects_financial_documents_and_preserves_other_documents(self):
        rows = [{"quantity": Decimal(1), "total": Decimal("105.00"), "price": Decimal("105")}]
        source_tax = {"label": "НДС 20%", "rate": Decimal("0.20"), "source": "repair_order"}
        for document_id in ("repair_order", "parts_sale", "vehicle_acceptance_act"):
            with self.subTest(document_id=document_id):
                untouched_rows, untouched_tax = invoice_vat_for_document(
                    document_id, rows, source_tax
                )
                self.assertIs(untouched_rows, rows)
                self.assertIs(untouched_tax, source_tax)
        for document_id in ("invoice", "invoice_factura", "upd", "completion_act"):
            with self.subTest(document_id=document_id):
                projected, tax = invoice_vat_for_document(document_id, rows, source_tax)
                self.assertIsNot(projected, rows)
                self.assertEqual(projected[0]["total"], rows[0]["total"])
                self.assertEqual(projected[0]["price"], rows[0]["price"])
                self.assertEqual(projected[0]["vat"], Decimal("5.00"))
                self.assertEqual(tax, invoice_tax_payload())
                self.assertEqual(source_tax["rate"], Decimal("0.20"))
                self.assertNotIn("vat", rows[0])

    def test_projection_preserves_gross_price_and_original_rows(self):
        row = {
            "name": "Synthetic row",
            "quantity": Decimal("1"),
            "total": Decimal("1050.00"),
            "total_display": "1 050,00",
            "price": Decimal("1050.00"),
            "price_display": "1 050,00",
            "subtotal": Decimal("875.00"),
            "vat": Decimal("175.00"),
            "vat_adjustment": Decimal("0.00"),
            "regulated_price": Decimal("875.00"),
            "regulated_price_display": "875,00",
            "money_error": False,
        }
        before = deepcopy(row)
        projected = invoice_vat_rows([row])[0]
        self.assertEqual(row, before)
        self.assertIsNot(projected, row)
        self.assertEqual(projected["vat"], Decimal("50.00"))
        self.assertEqual(projected["subtotal"], Decimal("1000.00"))
        self.assertEqual(projected["regulated_price"], Decimal("1000.00"))
        self.assertEqual(projected["regulated_price_display"], "1 000,00")
        changed = {"vat", "subtotal", "regulated_price", "regulated_price_display"}
        self.assertEqual(
            {key: value for key, value in projected.items() if key not in changed},
            {key: value for key, value in before.items() if key not in changed},
        )

    def test_vat_projection_does_not_reallocate_gross_cents(self):
        original = build_canonical_document_money(
            [Decimal("0.09"), Decimal("0.04")],
            [Decimal(1), Decimal(1)],
            target_total=Decimal("0.15"),
            net_rate=Decimal("0.85"),
            tax_rate=Decimal(0),
        )
        rows = [
            {"quantity": Decimal(1), "total": row.gross, "price": row.invoice_unit_price}
            for row in original.rows
        ]
        projected = invoice_vat_rows(rows)
        self.assertEqual([row["total"] for row in projected], [Decimal(".10"), Decimal(".05")])
        self.assertEqual([row["price"] for row in projected], [row["price"] for row in rows])
        self.assertEqual(sum(row["vat"] for row in projected), Decimal("0.00"))
        self.assertTrue(all(row["subtotal"] + row["vat"] == row["total"] for row in projected))

    def test_fractional_and_repeating_quantities_reproduce_net_cents(self):
        for quantity in (Decimal("0.125"), Decimal("3"), Decimal("0.00001")):
            with self.subTest(quantity=quantity):
                row = {"quantity": quantity, "total": Decimal("1.18"), "price": "unchanged"}
                projected = invoice_vat_rows([row])[0]
                self.assertFalse(projected.get("money_error"))
                self.assertEqual(projected["price"], "unchanged")
                self.assertEqual(projected["vat"], Decimal("0.06"))
                self.assertEqual(projected["subtotal"], Decimal("1.12"))
                self.assertEqual(
                    money(projected["regulated_price"] * quantity), projected["subtotal"]
                )
                displayed = Decimal(
                    projected["regulated_price_display"].replace(" ", "").replace(",", ".")
                )
                self.assertEqual(displayed, projected["regulated_price"])

    def test_empty_rows_and_missing_or_zero_quantity_keep_canonical_behavior(self):
        self.assertEqual(invoice_vat_rows([]), [])
        for quantity in (None, "", Decimal(0)):
            with self.subTest(quantity=quantity):
                projected = invoice_vat_rows([{"quantity": quantity, "total": Decimal(0)}])[0]
                self.assertEqual(projected["vat"], Decimal("0.00"))
                self.assertEqual(projected["regulated_price_display"], "0,00")
                self.assertEqual(projected["quantity"], quantity)

    def test_invalid_rows_are_not_normalized_into_valid_amounts(self):
        invalid = {"total": Decimal("105.00"), "quantity": "1", "money_error": True}
        self.assertEqual(invoice_vat_rows([invalid])[0], invalid)
        for total, quantity in (
            ("NaN", "1"),
            ("not money", "1"),
            (None, "1"),
            ("-1", "1"),
            ("0.001", "1"),
            ("1", "NaN"),
        ):
            with self.subTest(total=total, quantity=quantity):
                row = {"total": total, "quantity": quantity}
                projected = invoice_vat_rows([row])[0]
                self.assertTrue(projected["money_error"])
                self.assertEqual(projected["total"], total)
                self.assertNotIn("vat", projected)
                self.assertEqual(row, {"total": total, "quantity": quantity})

    def test_unavailable_net_price_keeps_valid_gross_price_and_tax(self):
        row = {
            "quantity": Decimal("2000000000"),
            "price": Decimal("0.0000000012"),
            "total": Decimal("2.40"),
            "money_error": False,
        }
        projected = invoice_vat_rows([row])[0]
        self.assertEqual(projected["price"], row["price"])
        self.assertEqual(projected["total"], row["total"])
        self.assertEqual(projected["vat"], Decimal("0.11"))
        self.assertEqual(projected["subtotal"], Decimal("2.29"))
        self.assertFalse(projected["money_error"])
        self.assertTrue(projected["regulated_money_error"])
        self.assertIsNone(projected["regulated_price"])
        self.assertEqual(projected["regulated_price_display"], "—")
