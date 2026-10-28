from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.formatting import _number_display
from minimal_kanban.printing.service import PrintModuleService
from minimal_kanban.services.document_money import DocumentMoneyError, document_number
from tests.test_document_money_v2 import money_card


class PrintingReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = PrintModuleService(Path(self.directory.name))
        self.card = money_card(price="100", quantity="1")

    def manual_snapshot(self):
        form = self.service.get_completion_act_form(self.card)["form"]
        form.update(calculation_mode="manual_net")
        form["items"] = [{"name": "Synthetic", "unit": "шт", "quantity": "1", "price": "100"}]
        return self.service.save_completion_act_form(
            self.card, form_data=form, expected_version=0, idempotency_key="initial"
        )

    def test_stale_save_requires_exact_reviewed_calculation_and_preserves_replay(self):
        saved = self.manual_snapshot()
        changed = deepcopy(self.card)
        changed.repair_order.tax_label = "НДС 20%"
        stale = self.service.get_completion_act_form(changed)
        path = self.service._completion_act_record_path(stale["draft"]["cycle_key"])
        before = path.read_bytes()
        arguments = {
            "form_data": stale["form"],
            "expected_version": 1,
            "expected_source_fingerprint": stale["draft"]["current_source_fingerprint"],
            "idempotency_key": "accept-new-calculation",
        }
        with self.assertRaises(PrintModuleError) as missing:
            self.service.save_completion_act_form(changed, **arguments)
        self.assertEqual(missing.exception.code, "completion_act_recalculation_required")
        self.assertEqual(missing.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        proposed = self.service.save_completion_act_form(changed, **arguments, dry_run=True)
        projection = proposed["dry_run"]
        self.assertEqual(projection["projected_totals"]["gross"], "120.00")
        self.assertEqual(projection["projected_tax_context"]["label"], "НДС 20%")
        self.assertTrue(projection["recalculation_required"])
        self.assertEqual(len(projection["calculation_fingerprint"]), 64)
        self.assertEqual(path.read_bytes(), before)
        preview = self.service.preview_documents(
            changed,
            selected_document_ids=["completion_act"],
            document_overrides={
                "completion_act": stale["form"],
                "completion_act_recalculate": True,
            },
        )["documents"][0]
        self.assertEqual(preview["calculation_fingerprint"], projection["calculation_fingerprint"])
        bad_form = deepcopy(arguments["form_data"])
        bad_form["items"][0]["price"] = "110"
        with self.assertRaises(PrintModuleError) as altered:
            self.service.save_completion_act_form(
                changed,
                **{**arguments, "form_data": bad_form},
                accepted_calculation_fingerprint=projection["calculation_fingerprint"],
            )
        self.assertEqual(altered.exception.code, "completion_act_recalculation_required")
        self.assertEqual(path.read_bytes(), before)
        updated = self.service.save_completion_act_form(
            changed,
            **arguments,
            accepted_calculation_fingerprint=projection["calculation_fingerprint"],
        )
        self.assertEqual(saved["totals"]["gross"], "105.00")
        self.assertEqual(updated["totals"]["gross"], "120.00")
        replay = self.service.save_completion_act_form(
            changed,
            **arguments,
            accepted_calculation_fingerprint=projection["calculation_fingerprint"],
        )
        self.assertTrue(replay["draft"]["idempotent_replay"])
        self.assertEqual(replay["draft"]["version"], 2)

    def test_fraction_limits_precede_formatting_and_preserve_source(self):
        for value in ("1E-100000", "1E-10000000", "0.000000000001"):
            with self.subTest(value=value):
                with self.assertRaises(DocumentMoneyError):
                    document_number(value)
                self.assertEqual(_number_display(value, minimum_places=2), "—")
        card = money_card(price="1E-100000", quantity="1")
        before = card.to_storage_dict()
        preview = self.service.preview_documents(card, selected_document_ids=["repair_order"])
        self.assertFalse(preview["money_validation"]["valid"])
        ordinary = self.service.preview_documents(
            money_card(price="0", quantity="1"), selected_document_ids=["repair_order"]
        )
        self.assertLessEqual(
            len(preview["documents"][0]["pages"][0]["html"]),
            len(ordinary["documents"][0]["pages"][0]["html"]) + 256,
        )
        self.assertEqual(card.to_storage_dict(), before)
        self.assertEqual(_number_display("1000.0000", minimum_places=2), "1 000,00")
        self.assertEqual(_number_display("1.12345678901", minimum_places=2), "1,12345678901")

    def test_snapshot_display_corruption_fails_closed_without_repairing_file(self):
        saved = self.manual_snapshot()
        path = self.service._completion_act_record_path(saved["draft"]["cycle_key"])
        original = path.read_bytes()
        cases = [
            ("totals", "gross_display", "999 999,00"),
            ("totals", "gross_words_display", "Неверная сумма"),
            ("totals", "tax_label", "Без НДС"),
            ("items", "sum_without_vat_display", "999 998,00"),
            ("items", "price_without_vat_display", "999,00"),
            ("items", "quantity_display", "1,0000"),
        ]
        for section, field, value in cases:
            with self.subTest(field=field):
                record = json.loads(original)
                target = record["money_snapshot"]["calculation"][section]
                if section == "items":
                    target = target[0]
                target[field] = value
                path.write_text(json.dumps(record), encoding="utf-8")
                corrupt = path.read_bytes()
                with self.assertRaises(PrintModuleError) as caught:
                    self.service.preview_documents(
                        self.card, selected_document_ids=["completion_act"]
                    )
                self.assertEqual(caught.exception.code, "completion_act_store_corrupt")
                self.assertEqual(caught.exception.status_code, 503)
                self.assertEqual(path.read_bytes(), corrupt)
        path.write_bytes(original)
        self.assertEqual(
            self.service.get_completion_act_form(self.card)["totals"]["gross"], "105.00"
        )

    def test_invalid_calculation_mode_types_are_validation_errors(self):
        for mode in ({}, [], None, 1, True):
            with self.subTest(mode=mode), self.assertRaises(PrintModuleError) as caught:
                self.service.preview_documents(
                    self.card,
                    selected_document_ids=["completion_act"],
                    document_overrides={"completion_act": {"calculation_mode": mode}},
                )
            self.assertEqual(caught.exception.code, "validation_error")
            self.assertEqual(caught.exception.status_code, 400)

    def test_custom_nonmoney_form_checks_money_placeholders_and_output_guard(self):
        card = money_card(price="1", quantity="3", total="3.01")
        options = {
            "selected_document_ids": ["inspection_sheet"],
            "template_overrides": {
                "inspection_sheet": "{{#materials}}{{quantity}} * {{price}} = {{total}}{{/materials}}"
            },
        }
        preview = self.service.preview_documents(card, **options)
        self.assertFalse(preview["money_validation"]["valid"])
        self.assertIn(
            "template_money_format_unverified",
            {item["code"] for item in preview["money_validation"]["errors"]},
        )
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf:
            with self.assertRaises(PrintModuleError) as caught:
                self.service.export_documents_pdf(card, **options)
        self.assertEqual(caught.exception.status_code, 422)
        pdf.assert_not_called()
        with patch("minimal_kanban.printing.service.print_html") as printer:
            with self.assertRaises(PrintModuleError) as caught:
                self.service.print_documents(card, **options, printer_name="Synthetic")
        self.assertEqual(caught.exception.status_code, 422)
        printer.assert_not_called()
        # A quantity-only inspection form still supports orders without prices.
        card.repair_order.materials[0].price = ""
        options["template_overrides"]["inspection_sheet"] = (
            "{{#materials}}{{quantity_display}}{{/materials}}"
        )
        self.assertTrue(
            self.service.preview_documents(card, **options)["money_validation"]["valid"]
        )

    def test_rounding_notes_must_render_for_the_correct_row(self):
        card = money_card(price="1", quantity="3", total="3.01")
        card.repair_order.works.append(deepcopy(card.repair_order.materials[0]))
        table = "{{#materials}}{{quantity_display}} * {{price_display}} = {{total_display}}{{/materials}}"
        invalid = [
            "{{#works}}{{rounding_adjustment_display}}{{/works}}",
            "<!-- rounding_notes_html -->",
            "<!-- {{{rounding_notes_html}}} -->",
            "<script>{{{rounding_notes_html}}}</script>",
            "<style>{{{rounding_notes_html}}}</style>",
            "<div hidden>{{{rounding_notes_html}}}</div>",
            '<div style="display: none">{{{rounding_notes_html}}}</div>',
            '<div style="opacity: 0.0 !important">{{{rounding_notes_html}}}</div>',
            '<div style="opacity: .0">{{{rounding_notes_html}}}</div>',
            '<span data-note="{{{rounding_notes_html}}}"></span>',
        ]
        for suffix in invalid:
            with self.subTest(suffix=suffix):
                preview = self.service.preview_documents(
                    card,
                    selected_document_ids=["parts_sale"],
                    template_overrides={"parts_sale": table + suffix},
                )
                self.assertFalse(preview["money_validation"]["valid"])
        for content in [
            table + "{{{rounding_notes_html}}}",
            table + '<div style="opacity: 0.5">{{{rounding_notes_html}}}</div>',
            table + '<div style="opacity: .5">{{{rounding_notes_html}}}</div>',
            "{{#materials}}{{total_display}} Округление {{rounding_adjustment_display}}{{/materials}}",
        ]:
            with self.subTest(content=content):
                preview = self.service.preview_documents(
                    card,
                    selected_document_ids=["parts_sale"],
                    template_overrides={"parts_sale": content},
                )
                self.assertTrue(preview["money_validation"]["valid"])
                self.assertIn("+0,01", preview["documents"][0]["pages"][0]["html"])

    def test_settlement_note_must_be_visible_and_may_use_aggregate_partial(self):
        card = money_card(
            price=".03", quantity="1", payments=[{"amount": ".02", "payment_method": "cash"}]
        )
        base = "{{invoice.total_display}} {{invoice.prepayment_display}} {{invoice.amount_due_display}}"
        for suffix, expected in [
            ("", False),
            ("<!-- {{invoice.settlement_rounding_adjustment_display}} -->", False),
            (" {{invoice.settlement_rounding_adjustment_display}}", True),
            (" {{{rounding_notes_html}}}", True),
        ]:
            with self.subTest(suffix=suffix):
                result = self.service.preview_documents(
                    card,
                    selected_document_ids=["invoice"],
                    template_overrides={"invoice": base + suffix},
                )
                self.assertEqual(result["money_validation"]["valid"], expected)

    def test_calculation_acceptance_is_bound_to_source_revision_and_schema(self):
        from minimal_kanban.mcp.raw_gateway import virtual_api_argument_errors, virtual_api_schema

        self.manual_snapshot()
        changed = deepcopy(self.card)
        changed.repair_order.tax_label = "НДС 20%"
        stale = self.service.get_completion_act_form(changed)
        arguments = dict(
            form_data=stale["form"],
            expected_version=1,
            expected_source_fingerprint=stale["draft"]["current_source_fingerprint"],
            idempotency_key="revision-bound",
        )
        projection = self.service.save_completion_act_form(changed, **arguments, dry_run=True)[
            "dry_run"
        ]
        changed.repair_order.tax_label = "НДС 7%"
        latest = self.service.get_completion_act_form(changed)
        with self.assertRaises(PrintModuleError) as caught:
            self.service.save_completion_act_form(
                changed,
                **{
                    **arguments,
                    "expected_source_fingerprint": latest["draft"]["current_source_fingerprint"],
                },
                accepted_calculation_fingerprint=projection["calculation_fingerprint"],
            )
        self.assertEqual(caught.exception.code, "completion_act_recalculation_required")
        schema = virtual_api_schema("/api/save_completion_act_form")
        self.assertIn("accepted_calculation_fingerprint", schema["properties"])
        payload = {
            "card_id": self.card.id,
            "form": stale["form"],
            "expected_version": 1,
            "expected_source_fingerprint": latest["draft"]["current_source_fingerprint"],
            "idempotency_key": "schema",
            "accepted_calculation_fingerprint": "a" * 64,
        }
        self.assertEqual(virtual_api_argument_errors("/api/save_completion_act_form", payload), [])
        payload["accepted_calculation_fingerprint"] = {}
        self.assertTrue(virtual_api_argument_errors("/api/save_completion_act_form", payload))
