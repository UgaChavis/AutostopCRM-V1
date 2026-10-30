from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService, _request_payload_fingerprint
from minimal_kanban.repair_order import repair_order_cashless_gross_value
from minimal_kanban.services.document_money import (
    build_canonical_document_money,
    included_vat,
    money,
    resolve_document_tax,
)


def act_card(tax="Без НДС", *, prices=("0.02", "0.98"), quantity="1"):
    return Card.from_dict(
        {
            "id": "synthetic-fixed-act",
            "title": "Synthetic",
            "column": "inbox",
            "repair_order": {
                "number": "SYNTHETIC-ACT",
                "client": "Synthetic customer",
                "tax_label": tax,
                "materials": [
                    {"name": f"Synthetic {index}", "quantity": quantity, "price": price}
                    for index, price in enumerate(prices)
                ],
            },
        }
    )


class ActFixedVatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="synthetic-fixed-act-")
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

    def context(self, card, form=None):
        return self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition("completion_act"),
            settings=self.service._read_settings(),
            document_overrides={"completion_act": form} if form else None,
        )["completion_act"]

    def test_new_linked_acts_keep_gross_allocation_for_every_source_tax(self):
        for source in ("", "Без НДС", "НДС 0%", "НДС 7%", "НДС 20%", "НДС 5%"):
            for profile in (False, True):
                card = act_card("" if profile else source)
                self.service.save_settings(
                    {"service_profile": {"tax_label": source if profile else ""}}
                )
                before_card, before_files = card.to_storage_dict(), self.files()
                forms = self.service.get_completion_act_form(card)["linked_forms"]
                for mode, form in forms.items():
                    with self.subTest(source=source, profile=profile, mode=mode):
                        cashless = mode == "order_cashless"
                        base = [Decimal(row.total) for row in card.repair_order.materials]
                        expected = build_canonical_document_money(
                            base,
                            [Decimal(1)] * len(base),
                            target_total=(
                                repair_order_cashless_gross_value(sum(base))
                                if cashless
                                else sum(base)
                            ),
                            net_rate=Decimal(".85") if cashless else Decimal(1),
                            tax_rate=resolve_document_tax(source)["rate"],
                        )
                        calculated = self.context(card, form)
                        self.assertEqual(calculated["totals"]["tax_label"], "НДС 5%")
                        self.assertEqual(Decimal(calculated["totals"]["vat_rate"]), Decimal(5))
                        for item, row in zip(calculated["items"], expected.rows, strict=True):
                            subtotal = Decimal(item["sum_without_vat"])
                            vat = Decimal(item["vat"])
                            self.assertEqual(subtotal + vat, row.gross)
                            self.assertEqual(vat, included_vat(row.gross, Decimal(".05")))
                            self.assertEqual(money(item["price_without_vat"]), subtotal)
                        options = {
                            "selected_document_ids": ["completion_act"],
                            "document_overrides": {"completion_act": form},
                        }
                        preview = self.service.preview_documents(card, **options)
                        self.assertTrue(preview["money_validation"]["valid"], preview)
                        self.service.prepare_documents_pdf(card, **options)
                        self.service.prepare_print_documents(
                            card, printer_name="Synthetic", **options
                        )
                self.assertEqual(card.to_storage_dict(), before_card)
                self.assertEqual(self.files(), before_files)

    def test_manual_net_and_document_without_card_use_five_percent_on_entered_net(self):
        for source in ("", "Без НДС", "НДС 20%"):
            with self.subTest(source=source):
                card = act_card(source, prices=("100",))
                manual = self.service.manual_document_profile(
                    {
                        "document_number": "MANUAL-ACT",
                        "client_name": "Synthetic customer",
                        "tax_label": source,
                        "materials": [{"name": "Synthetic", "quantity": "1", "price": "100"}],
                    }
                ).card
                for source_card in (card, manual):
                    form = self.service.get_completion_act_form(source_card)["form"]
                    form.update(calculation_mode="manual_net")
                    form["items"] = [
                        {"name": "Synthetic", "unit": "шт", "quantity": "1", "price": "100"}
                    ]
                    form = self.service._normalized_completion_act_form(form).to_dict()
                    calculated = self.context(source_card, form)
                    self.assertEqual(calculated["totals"]["base"], "100.00")
                    self.assertEqual(calculated["totals"]["vat"], "5.00")
                    self.assertEqual(calculated["totals"]["gross"], "105.00")
                    preview = self.service.preview_documents(
                        source_card,
                        selected_document_ids=["completion_act"],
                        document_overrides={"completion_act": form},
                    )
                    self.assertTrue(preview["money_validation"]["valid"], preview)

    def test_invalid_linked_precision_and_act_quantity_limits_block_release(self):
        for quantity, price in (
            ("2000000000", "0.00000000102"),
            ("10000000000", "0.00000000001"),
        ):
            with self.subTest(quantity=quantity):
                card = act_card(prices=(price,), quantity=quantity)
                options = {"selected_document_ids": ["completion_act"]}
                rows = self.service._linked_act_rows(
                    card.repair_order, resolve_document_tax("Без НДС"), "order_cashless"
                )
                self.assertTrue(rows[0]["money_error"])
                with self.assertRaises(PrintModuleError) as failure:
                    self.service.prepare_documents_pdf(card, **options)
                self.assertEqual(failure.exception.code, "validation_error")
                self.assertEqual(failure.exception.details["field"], "items[0].quantity")

    def test_saved_prior_tax_is_visible_until_explicitly_recalculated_and_saved(self):
        def prior_fingerprint(payload):
            return _request_payload_fingerprint(
                {key: value for key, value in payload.items() if key != "print_tax_context"}
            )

        for index, source in enumerate(("Без НДС", "НДС 20%")):
            with self.subTest(source=source):
                card = act_card(source, prices=("8500",))
                card.id += str(index)
                source_tax = self.service._document_tax(
                    card.repair_order, self.service._read_settings()
                )
                with (
                    patch(
                        "minimal_kanban.printing.invoice_vat.invoice_tax_payload",
                        return_value=source_tax,
                    ),
                    patch(
                        "minimal_kanban.printing.completion_act_calculation.invoice_tax_payload",
                        return_value=source_tax,
                    ),
                    patch(
                        "minimal_kanban.printing.service._request_payload_fingerprint",
                        side_effect=prior_fingerprint,
                    ),
                ):
                    form = self.service.get_completion_act_form(card)["form"]
                    saved = self.service.save_completion_act_form(
                        card, form_data=form, expected_version=0, idempotency_key="historical"
                    )
                before_files = self.files()
                loaded = self.service.get_completion_act_form(card)
                self.assertTrue(loaded["draft"]["is_stale"])
                self.assertEqual(loaded["totals"], saved["totals"])
                self.assertEqual(loaded["totals"]["tax_label"], source)
                options = {"selected_document_ids": ["completion_act"]}
                preview = self.service.preview_documents(card, **options)
                self.assertIn(
                    "stale_money_snapshot",
                    {issue["code"] for issue in preview["money_validation"]["errors"]},
                )
                with self.assertRaises(PrintModuleError):
                    self.service.prepare_documents_pdf(card, **options)
                self.assertEqual(self.files(), before_files)
                updated_form = deepcopy(loaded["linked_forms"]["order_cashless"])
                reviewed = self.service.preview_documents(
                    card,
                    **options,
                    document_overrides={
                        "completion_act": updated_form,
                        "completion_act_recalculate": True,
                    },
                )["documents"][0]
                save_options = {
                    "form_data": updated_form,
                    "expected_version": loaded["draft"]["version"],
                    "expected_source_fingerprint": loaded["draft"]["current_source_fingerprint"],
                    "idempotency_key": "accepted-five-percent",
                }
                with self.assertRaises(PrintModuleError) as failure:
                    self.service.save_completion_act_form(card, **save_options)
                self.assertEqual(failure.exception.code, "completion_act_recalculation_required")
                updated = self.service.save_completion_act_form(
                    card,
                    **save_options,
                    accepted_calculation_fingerprint=reviewed["calculation_fingerprint"],
                )
                self.assertFalse(updated["draft"]["is_stale"])
                self.assertEqual(updated["totals"]["gross"], saved["totals"]["gross"])
                self.assertEqual(updated["totals"]["tax_label"], "НДС 5%")
                self.assertEqual(updated["totals"]["vat"], "476.19")
                self.service.prepare_documents_pdf(card, **options)
