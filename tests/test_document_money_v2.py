from __future__ import annotations

import base64
import json
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
from minimal_kanban.printing.formatting import _money_display
from minimal_kanban.printing.service import PrintModuleService
from minimal_kanban.services.document_money import (
    DocumentMoneyError,
    build_canonical_document_money,
    document_number,
    included_vat,
    net_line_money,
)


def money_card(*, price="950", quantity="6", tax="НДС 5%", total="", payments=None):
    return Card.from_dict(
        {
            "id": "money-v2-test",
            "title": "Synthetic",
            "column": "inbox",
            "repair_order": {
                "number": "TEST",
                "date": "28.10.2026",
                "client": "Synthetic customer",
                "tax_label": tax,
                "materials": [
                    {
                        "name": "Synthetic",
                        "quantity": quantity,
                        "price": price,
                        "total": total,
                        "inventory_unit": "л",
                    }
                ],
                "payments": payments or [],
            },
        }
    )


class DocumentMoneyV2Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = PrintModuleService(Path(self.directory.name))
        self.card = money_card()

    def context(self, document="invoice", card=None, overrides=None):
        source = card or self.card
        return self.service._build_document_context(
            source,
            source.repair_order,
            document=self.service._document_definition(document),
            settings=self.service._read_settings(),
            document_overrides=overrides,
        )

    def test_minimum_derived_precision_and_row_vat(self):
        for base, quantity, target, precision in [
            ("1700", "2", "2000", 2),
            ("5700", "6", "6705.88", 3),
            ("10000", "1000000", "11764.71", 8),
            ("1", "1000000000", "1.18", 11),
        ]:
            with self.subTest(precision=precision):
                result = build_canonical_document_money(
                    [Decimal(base)],
                    [Decimal(quantity)],
                    target_total=Decimal(target),
                    net_rate=Decimal(".85"),
                    tax_rate=Decimal(".05"),
                )
                self.assertEqual(result.rows[0].invoice_unit_precision, precision)
                self.assertEqual(result.gross_total, Decimal(target))
        result = build_canonical_document_money(
            [Decimal(1)] * 2,
            [Decimal(1)] * 2,
            target_total=Decimal("2.35"),
            net_rate=Decimal(".85"),
            tax_rate=Decimal(".05"),
        )
        self.assertEqual([row.vat for row in result.rows], [Decimal(".06")] * 2)
        self.assertEqual(result.vat_total, Decimal(".12"))

    def test_source_precision_and_signed_rounding_are_visible(self):
        self.assertEqual(
            self.context("repair_order", money_card(price="1000.0000"))["materials"][0][
                "price_display"
            ],
            "1 000,00",
        )
        row = self.context("repair_order", money_card(price="1.2345"))["materials"][0]
        self.assertEqual(row["price_display"], "1,2345")
        self.assertEqual(row["total_display"], "7,41")
        adjusted = money_card(quantity="3", price="1924.33", total="5773")
        row = self.context("repair_order", adjusted)["materials"][0]
        self.assertEqual(row["rounding_adjustment_display"], "+0,01")
        html = self.service.preview_documents(adjusted, selected_document_ids=["repair_order"])[
            "documents"
        ][0]["pages"][0]["html"]
        self.assertIn("Округление +0,01", html)
        self.assertEqual(_money_display("-0.01"), "−0,01")
        self.assertEqual(
            self.context("repair_order")["totals"]["cash_total_ruble_display"], "5 700,00 ₽"
        )

    def test_linked_documents_share_full_money_without_mutations(self):
        before = self.card.to_storage_dict()
        invoice = self.context()["invoice"]
        act = self.context("completion_act")["completion_act"]
        self.assertEqual(Decimal(act["totals"]["gross"]), invoice["total"])
        self.assertEqual(Decimal(act["totals"]["vat"]), invoice["vat"])
        self.assertEqual(act["items"][0]["price_without_vat_display"], "1 064,425")
        self.assertEqual(self.card.to_storage_dict(), before)

    def test_explicit_vat_zero_and_without_vat(self):
        for tax, has_vat, label in [("Без НДС", False, "Без НДС"), ("НДС 0%", True, "НДС 0%")]:
            context = self.context("completion_act", money_card(tax=tax))
            self.assertEqual(context["completion_act"]["totals"]["has_vat"], has_vat)
            self.assertEqual(context["completion_act"]["totals"]["tax_label"], label)
            self.assertEqual(context["completion_act"]["totals"]["vat"], "0.00")

    def test_unknown_tax_previews_but_blocks_pdf_and_native_backend(self):
        source = money_card(tax="")
        preview = self.service.preview_documents(source, selected_document_ids=["invoice"])
        self.assertFalse(preview["documents"][0]["money_validation"]["valid"])
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf:
            with self.assertRaises(PrintModuleError) as captured:
                self.service.export_documents_pdf(source, selected_document_ids=["invoice"])
            self.assertEqual(captured.exception.code, "document_money_validation_failed")
            pdf.assert_not_called()
        with patch("minimal_kanban.printing.service.print_html") as printer:
            with self.assertRaises(PrintModuleError):
                self.service.print_documents(
                    source, selected_document_ids=["invoice"], printer_name="Synthetic"
                )
            printer.assert_not_called()

    def test_manual_fractional_act_and_batch_mode_guard(self):
        form = self.service.get_completion_act_form(self.card)["form"]
        form.update(calculation_mode="manual_net")
        form["items"] = [
            {"name": "Synthetic", "quantity": "0.1234", "price": "1.2345", "unit": "л"}
        ]
        result = self.context("completion_act", overrides={"completion_act": form})[
            "completion_act"
        ]
        self.assertEqual(result["items"][0]["quantity_display"], "0,1234")
        self.assertEqual(result["items"][0]["price_without_vat_display"], "1,2345")
        batch = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice", "completion_act"],
            document_overrides={"completion_act": form},
        )
        act = next(item for item in batch["documents"] if item["id"] == "completion_act")
        self.assertFalse(act["money_validation"]["valid"])

    def test_manual_vat_is_row_sum(self):
        form = self.service.get_completion_act_form(self.card)["form"]
        form.update(calculation_mode="manual_net")
        form["items"] = [
            {"name": "Synthetic", "quantity": "1", "price": "0.10", "unit": "шт"} for _ in range(3)
        ]
        result = self.context("completion_act", overrides={"completion_act": form})
        self.assertEqual(result["completion_act"]["totals"]["vat"], "0.03")
        self.assertEqual(result["completion_act"]["totals"]["gross"], "0.33")

    def test_cash_prepayment_uses_authoritative_debt(self):
        source = money_card(
            price="0.03",
            quantity="1",
            payments=[{"id": "synthetic-payment", "amount": "0.02", "payment_method": "cash"}],
        )
        context = self.context(card=source)
        self.assertEqual(context["invoice"]["amount_due"], Decimal(".01"))
        self.assertEqual(context["invoice"]["settlement_rounding_adjustment_display"], "−0,01")
        self.assertEqual(context["invoice"]["amount_due"], context["totals"]["noncash_due"])

    def test_snapshot_staleness_and_guarded_policy_upgrade(self):
        loaded = self.service.get_completion_act_form(self.card)
        saved = self.service.save_completion_act_form(
            self.card,
            form_data=loaded["form"],
            expected_version=0,
            expected_source_fingerprint=loaded["draft"]["current_source_fingerprint"],
            idempotency_key="save-v2",
        )
        path = self.service._completion_act_record_path(
            self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["money_policy_version"], 2)
        self.assertTrue(record["money_snapshot"])
        changed = money_card(price="1000")
        stale = self.service.get_completion_act_form(changed)
        self.assertTrue(stale["draft"]["is_stale"])
        self.assertEqual(stale["totals"], saved["totals"])
        with self.assertRaises(PrintModuleError):
            self.service.export_documents_pdf(changed, selected_document_ids=["completion_act"])
        record.pop("money_policy_version")
        record.pop("calculation_mode")
        record.pop("money_snapshot")
        record["overrides"] = {"document_number": "LEGACY"}
        path.write_text(json.dumps(record), encoding="utf-8")
        legacy = self.service.get_completion_act_form(self.card)
        self.assertEqual(legacy["form"]["money_policy_version"], 1)
        self.assertEqual(legacy["form"]["document_number"], "LEGACY")
        upgraded_form = deepcopy(legacy["form"])
        upgraded_form["money_policy_version"] = 2
        reviewed = self.service.preview_documents(
            self.card,
            selected_document_ids=["completion_act"],
            document_overrides={
                "completion_act": upgraded_form,
                "completion_act_recalculate": True,
            },
        )["documents"][0]
        upgraded = self.service.save_completion_act_form(
            self.card,
            form_data=upgraded_form,
            expected_version=legacy["draft"]["version"],
            expected_source_fingerprint=legacy["draft"]["current_source_fingerprint"],
            idempotency_key="upgrade-v2",
            accepted_calculation_fingerprint=reviewed["calculation_fingerprint"],
        )
        self.assertEqual(upgraded["form"]["money_policy_version"], 2)
        self.assertEqual(upgraded["form"]["document_number"], "LEGACY")

    def test_numeric_boundaries_and_fractional_trailing_zeroes(self):
        for value in ("0.1234000", "0,1234", "0.1234"):
            row = self.context("repair_order", money_card(quantity=value))["materials"][0]
            self.assertEqual(row["quantity_display"], "0,1234")
        self.assertEqual(document_number("999999999999999.99"), Decimal("999999999999999.99"))
        self.assertEqual(document_number("0,00000000001"), Decimal("0.00000000001"))
        for value in ("0.000000000001", "1000000000000000", "-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(DocumentMoneyError):
                document_number(value)
        source = money_card(price="999999999999999.99", quantity="0.00000000001")
        preview = self.service.preview_documents(source, selected_document_ids=["invoice"])
        self.assertFalse(preview["money_validation"]["valid"])
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf:
            with self.assertRaises(PrintModuleError) as captured:
                self.service.export_documents_pdf(source, selected_document_ids=["invoice"])
        self.assertEqual(captured.exception.status_code, 422)
        pdf.assert_not_called()
        large = money_card(price="900000000000000", quantity="1")
        large.repair_order.materials.append(deepcopy(large.repair_order.materials[0]))
        result = self.service.preview_documents(large, selected_document_ids=["repair_order"])
        self.assertFalse(result["money_validation"]["valid"])
        self.assertIn(
            "document_total_out_of_range",
            {issue["code"] for issue in result["money_validation"]["errors"]},
        )

    def test_inclusive_vat_checks_original_gross_basis_and_half_cent(self):
        for gross, rate, expected in (("0.10", ".05", "0.00"), ("0.03", ".20", "0.01")):
            with self.subTest(gross=gross):
                self.assertEqual(included_vat(Decimal(gross), Decimal(rate)), Decimal(expected))
        self.assertEqual(
            net_line_money(Decimal(".10"), Decimal(1), Decimal(".05")),
            (Decimal(".10"), Decimal(".01"), Decimal(".11")),
        )
        self.assertEqual(net_line_money(Decimal("1.005"), Decimal(1))[0], Decimal("1.01"))

    def test_one_kopeck_adjustment_is_signed_and_larger_error_blocks_release(self):
        for total, signed in (("2.99", "−0,01"), ("3.01", "+0,01")):
            source = money_card(price="1", quantity="3", total=total)
            document = self.service.preview_documents(source, selected_document_ids=["parts_sale"])[
                "documents"
            ][0]
            self.assertTrue(document["money_validation"]["valid"])
            self.assertIn("Округление " + signed, document["pages"][0]["html"])
        source = money_card(price="1", quantity="3", total="3.02")
        source.repair_order.materials[
            0
        ].total = "3.02"  # Simulate an inconsistent in-memory source.
        with self.assertRaises(PrintModuleError) as captured:
            self.service.export_documents_pdf(source, selected_document_ids=["repair_order"])
        self.assertEqual(captured.exception.status_code, 422)

    def test_tax_requires_explicit_source_and_order_has_precedence(self):
        source = money_card(tax="")
        self.service.save_settings({"copies": 2})
        self.assertFalse(
            self.service.preview_documents(source, selected_document_ids=["invoice"])[
                "money_validation"
            ]["valid"]
        )
        self.service.save_settings({"service_profile": {"tax_label": "НДС 7%"}})
        valid = self.service.preview_documents(source, selected_document_ids=["invoice"])
        self.assertTrue(valid["money_validation"]["valid"])
        self.assertEqual(
            self.context(card=source)["money_tax_context"]["source"], "service_profile"
        )
        unknown = self.service.preview_documents(
            money_card(tax="УСН неизвестно"), selected_document_ids=["invoice"]
        )
        self.assertFalse(unknown["money_validation"]["valid"])
        self.assertEqual(self.context(card=money_card(tax="Без НДС"))["invoice"]["vat"], Decimal(0))
        self.service.save_settings({"service_profile": {"tax_label": ""}})
        self.assertFalse(
            self.service.preview_documents(source, selected_document_ids=["invoice"])[
                "money_validation"
            ]["valid"]
        )

    def test_cash_and_manual_acts_are_independent_bases(self):
        loaded = self.service.get_completion_act_form(self.card)
        cash = loaded["linked_forms"]["order_cash"]
        context = self.context("completion_act", overrides={"completion_act": cash})
        self.assertEqual(context["completion_act"]["totals"]["gross"], "5700.00")
        invalid_order = money_card(price="1", quantity="1", total="2")
        manual = deepcopy(cash)
        manual["calculation_mode"] = "manual_net"
        manual["items"] = [{"name": "Synthetic", "unit": "л", "quantity": "0.5", "price": "100"}]
        preview = self.service.preview_documents(
            invalid_order,
            selected_document_ids=["completion_act"],
            document_overrides={"completion_act": manual},
        )
        self.assertTrue(preview["money_validation"]["valid"])

    def test_payment_matrix_and_visible_small_settlement_adjustment(self):
        cases = (
            [],
            [("cash", "100")],
            [("cashless", "100")],
            [("card", "100")],
            [("cash", "100"), ("cashless", "200")],
            [("cash", "5700")],
            [("cashless", "6705.88")],
            [("cash", "7000")],
        )
        for payments in cases:
            source = money_card(
                payments=[
                    {"id": str(index), "amount": amount, "payment_method": method}
                    for index, (method, amount) in enumerate(payments)
                ]
            )
            before = source.to_storage_dict()
            context = self.context(card=source)
            invoice = context["invoice"]
            self.assertEqual(
                invoice["amount_due"], source.repair_order.payment_summary_value()["noncash_due"]
            )
            self.assertLessEqual(abs(invoice["settlement_rounding_adjustment"]), Decimal(".02"))
            self.assertEqual(source.to_storage_dict(), before)

    def test_settlement_mismatch_above_two_kopecks_blocks_output(self):
        order = self.card.repair_order
        summary = order.payment_summary_value()
        summary["noncash_due"] += Decimal(".03")
        with patch.object(type(order), "payment_summary_value", return_value=summary):
            preview = self.service.preview_documents(self.card, selected_document_ids=["invoice"])
            self.assertFalse(preview["money_validation"]["valid"])
            self.assertEqual(preview["money_validation"]["errors"][0]["delta"], "0.03")
            with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf:
                with self.assertRaises(PrintModuleError):
                    self.service.export_documents_pdf(self.card, selected_document_ids=["invoice"])
                pdf.assert_not_called()

    def test_custom_templates_require_safe_fields_and_rounding_notes(self):
        source = money_card(price="1", quantity="3", total="3.01")
        for content in (
            "{{#materials}}{{price}}{{/materials}}",
            "{{#materials}}{{total_display}}{{/materials}}",
        ):
            result = self.service.preview_documents(
                source,
                selected_document_ids=["parts_sale"],
                template_overrides={"parts_sale": content},
            )
            self.assertFalse(result["money_validation"]["valid"])
        content = "{{#materials}}{{price_display}} {{total_display}}{{/materials}}{{{rounding_notes_html}}}"
        result = self.service.preview_documents(
            source, selected_document_ids=["parts_sale"], template_overrides={"parts_sale": content}
        )
        self.assertTrue(result["money_validation"]["valid"])
        self.assertIn("+0,01", result["documents"][0]["pages"][0]["html"])

    def test_incomplete_monetary_row_can_be_drafted_but_not_issued(self):
        loaded = self.service.get_completion_act_form(self.card)
        form = loaded["form"]
        form["calculation_mode"] = "manual_net"
        form["items"] = [{"name": "Synthetic", "unit": "шт", "quantity": "1", "price": ""}]
        self.service.save_completion_act_form(
            self.card, form_data=form, expected_version=0, idempotency_key="partial-draft"
        )
        preview = self.service.preview_documents(
            self.card, selected_document_ids=["completion_act"]
        )
        self.assertFalse(preview["money_validation"]["valid"])
        self.assertEqual(preview["money_validation"]["errors"][0]["code"], "line_money_missing")

    def test_snapshot_corruption_fails_closed_and_legacy_requests_inherit_mode(self):
        loaded = self.service.get_completion_act_form(self.card)
        saved = self.service.save_completion_act_form(
            self.card, form_data=loaded["form"], expected_version=0, idempotency_key="first"
        )
        form = deepcopy(saved["form"])
        form.pop("money_policy_version")
        form.pop("calculation_mode")
        updated = self.service.save_completion_act_form(
            self.card, form_data=form, expected_version=1, idempotency_key="old-client"
        )
        self.assertEqual(updated["form"]["calculation_mode"], "order_cashless")
        key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        path = self.service._completion_act_record_path(key)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["money_snapshot"]["calculation"]["totals"]["gross"] = "0.00"
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(PrintModuleError) as captured:
            self.service.get_completion_act_form(self.card)
        self.assertEqual(captured.exception.code, "completion_act_store_corrupt")

    def test_stale_tax_snapshot_changes_only_after_explicit_recalculation_and_save(self):
        loaded = self.service.get_completion_act_form(self.card)
        form = deepcopy(loaded["form"])
        form["calculation_mode"] = "manual_net"
        form["items"] = [{"name": "Synthetic", "quantity": ".1234", "price": "1.2345", "unit": "л"}]
        saved = self.service.save_completion_act_form(
            self.card, form_data=form, expected_version=0, idempotency_key="manual-snapshot"
        )
        self.assertEqual(saved["totals"]["gross"], "0.16")
        changed = money_card(tax="НДС 20%")
        stale = self.service.get_completion_act_form(changed)
        self.assertEqual(stale["totals"]["gross"], "0.16")
        request = {"completion_act": saved["form"]}
        preview = self.service.preview_documents(
            changed, selected_document_ids=["completion_act"], document_overrides=request
        )
        self.assertEqual(preview["documents"][0]["computed_totals"]["gross"], "0.16")
        request["completion_act_recalculate"] = True
        preview = self.service.preview_documents(
            changed, selected_document_ids=["completion_act"], document_overrides=request
        )
        self.assertEqual(preview["documents"][0]["computed_totals"]["gross"], "0.18")
        self.assertFalse(preview["money_validation"]["valid"])
        updated = self.service.save_completion_act_form(
            changed,
            form_data=saved["form"],
            expected_version=1,
            expected_source_fingerprint=stale["draft"]["current_source_fingerprint"],
            idempotency_key="accept-recalculation",
            accepted_calculation_fingerprint=preview["documents"][0]["calculation_fingerprint"],
        )
        self.assertEqual(updated["totals"]["gross"], "0.18")
        self.assertFalse(updated["draft"]["is_stale"])

    def test_v2_snapshot_survives_standard_backup_and_restore(self):
        from tests.test_agent_release_backup import AgentReleaseBackupTests, load_module

        root = Path(self.directory.name) / "backup-case"
        root.mkdir()
        crm_data, manager_db, output_root = AgentReleaseBackupTests()._fixture(root)
        service = PrintModuleService(crm_data)
        form = service.get_completion_act_form(self.card)["form"]
        saved = service.save_completion_act_form(
            self.card, form_data=form, expected_version=0, idempotency_key="backup-v2"
        )
        module = load_module()
        backup = Path(
            module.create_backup(
                output_root=output_root, crm_data_dir=crm_data, manager_db=manager_db
            )["backup_dir"]
        )
        key = service._completion_act_cycle_key(self.card, self.card.repair_order)
        snapshot = json.loads((backup / "completion_act_forms.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot[key]["money_policy_version"], 2)
        service.reset_completion_act_form(self.card, expected_version=1, idempotency_key="reset")
        module.restore_crm_state_and_feed(backup)
        restored = PrintModuleService(crm_data).get_completion_act_form(self.card)
        self.assertEqual(restored["totals"], saved["totals"])
        self.assertEqual(restored["form"], saved["form"])

    def test_mcp_qa_denies_financial_or_document_validation_mismatch(self):
        from minimal_kanban.mcp.workflow_guards import invoice_document_guard

        guard = {
            "money_basis": "cashless",
            "rendered_total": "1.00",
            "repair_order_total": "1.00",
            "tax_status": "НДС 0%",
            "financial_mismatch": False,
            "tax_mismatch": False,
            "financial_or_tax_mismatch": False,
            "mismatch_with_current_repair_order": False,
        }
        result = {
            "ok": True,
            "data": {
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(b"%PDF-1.4 synthetic").decode(),
                "meta": {
                    "documents": [
                        {
                            "id": "invoice",
                            "document_guard": guard,
                            "money_validation": {"valid": True},
                        }
                    ]
                },
            },
        }
        self.assertTrue(invoice_document_guard(result)["qa_passed"])
        result["data"]["meta"]["documents"][0]["money_validation"]["valid"] = False
        self.assertFalse(invoice_document_guard(result)["qa_passed"])
        result["data"]["meta"]["documents"][0]["money_validation"]["valid"] = True
        guard.update(
            financial_mismatch=True,
            financial_or_tax_mismatch=True,
            mismatch_with_current_repair_order=True,
        )
        self.assertFalse(invoice_document_guard(result)["qa_passed"])
