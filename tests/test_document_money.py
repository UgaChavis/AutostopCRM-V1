from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card
from minimal_kanban.printing.document_money import build_canonical_document_money
from minimal_kanban.printing.service import PrintModuleService


class CanonicalDocumentMoneyTests(unittest.TestCase):
    def test_150_row_regression_matches_order_18_document_totals(self) -> None:
        target_base_cents = 187_433_913
        base_cents = [
            50_000 + ((2_006 * index + 7_919 * index * index + 12_345) % 900_001)
            for index in range(149)
        ]
        base_cents.append(target_base_cents - sum(base_cents))
        money = build_canonical_document_money(
            [Decimal(value) / 100 for value in base_cents],
            [Decimal("1")] * 150,
            target_total=Decimal("2205104.86"),
            net_rate=Decimal("0.85"),
            tax_rate=Decimal("0.05"),
        )

        self.assertEqual(len(money.rows), 150)
        self.assertEqual(money.gross_total, Decimal("2205104.86"))
        self.assertEqual(money.vat_total, Decimal("105004.99"))
        self.assertEqual(money.subtotal_total, Decimal("2100099.87"))
        self.assertTrue(all(row.vat_adjustment == 0 for row in money.rows))
        self.assertTrue(all(row.invoice_unit_price == row.gross for row in money.rows))
        self.assertTrue(all(row.regulated_unit_price == row.subtotal for row in money.rows))
        self.assertTrue(
            all(
                row.vat
                == (row.gross * Decimal("5") / Decimal("105")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                for row in money.rows
            )
        )

    def test_deterministic_fallback_changes_minimum_number_of_vat_rows(self) -> None:
        money = build_canonical_document_money(
            [Decimal("1.00"), Decimal("1.00")],
            [Decimal("1"), Decimal("1")],
            target_total=Decimal("2.35"),
            net_rate=Decimal("0.85"),
            tax_rate=Decimal("0.05"),
        )

        self.assertEqual([row.gross for row in money.rows], [Decimal("1.18"), Decimal("1.17")])
        self.assertEqual([row.vat for row in money.rows], [Decimal("0.06"), Decimal("0.05")])
        self.assertEqual(
            [row.vat_adjustment for row in money.rows],
            [Decimal("0.00"), Decimal("-0.01")],
        )
        self.assertEqual(money.vat_total, Decimal("0.11"))
        self.assertTrue(all(row.subtotal + row.vat == row.gross for row in money.rows))

    def test_included_vat_uses_direct_rate_fraction_at_half_cent(self) -> None:
        money = build_canonical_document_money(
            [Decimal("0.03")],
            [Decimal("1")],
            target_total=Decimal("0.03"),
            net_rate=Decimal("1"),
            tax_rate=Decimal("0.20"),
        )

        self.assertEqual(money.rows[0].vat, Decimal("0.01"))
        self.assertEqual(money.rows[0].subtotal, Decimal("0.02"))

    def test_multiple_quantity_prices_reproduce_both_line_totals(self) -> None:
        money = build_canonical_document_money(
            [Decimal("5700.00")],
            [Decimal("6")],
            target_total=Decimal("6705.88"),
            net_rate=Decimal("0.85"),
            tax_rate=Decimal("0.05"),
        )
        row = money.rows[0]

        self.assertEqual(row.invoice_unit_price, Decimal("1117.6467"))
        self.assertEqual(row.invoice_unit_precision, 4)
        self.assertEqual(row.regulated_unit_price, Decimal("1064.4250"))
        self.assertEqual(row.regulated_unit_precision, 4)
        self.assertEqual(
            (row.invoice_unit_price * Decimal("6")).quantize(Decimal("0.01")), row.gross
        )
        self.assertEqual(
            (row.regulated_unit_price * Decimal("6")).quantize(Decimal("0.01")),
            row.subtotal,
        )


class DocumentMoneyPrintingIntegrationTests(unittest.TestCase):
    def test_invoice_invoice_factura_and_upd_share_canonical_rows(self) -> None:
        card = Card.from_dict(
            {
                "id": "canonical-document-integration",
                "title": "Synthetic document fixture",
                "column": "inbox",
                "repair_order": {
                    "number": "CANONICAL-1",
                    "payment_method": "cashless",
                    "tax_label": "НДС 5%",
                    "works": [
                        {"name": "Work A", "quantity": "1", "price": "1.00"},
                        {"name": "Work B", "quantity": "1", "price": "1.00"},
                    ],
                    "materials": [
                        {"name": "Material", "quantity": "6", "price": "950.00"},
                    ],
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            service = PrintModuleService(Path(temp_dir))
            settings = service._read_settings()
            contexts = {
                document_id: service._build_document_context(
                    card,
                    card.repair_order,
                    document=service._document_definition(document_id),
                    settings=settings,
                )
                for document_id in ("invoice", "invoice_factura", "upd")
            }

        invoice = contexts["invoice"]["invoice"]
        invoice_gross = [row["total"] for row in invoice["line_items"]]
        for document_id in ("invoice_factura", "upd"):
            regulated = contexts[document_id]["regulated"]
            self.assertEqual([row["total_with_tax"] for row in regulated["rows"]], invoice_gross)
            self.assertEqual(regulated["total_with_tax"], invoice["total"])
            self.assertEqual(regulated["vat"], invoice["vat"])
            self.assertEqual(
                sum((row["vat"] for row in regulated["rows"]), Decimal("0")), invoice["vat"]
            )
            self.assertTrue(
                all(
                    row["subtotal"] + row["vat"] == row["total_with_tax"]
                    for row in regulated["rows"]
                )
            )
