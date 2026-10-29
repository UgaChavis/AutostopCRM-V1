from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError  # noqa: E402
from minimal_kanban.printing.money_validation import rounding_notes_html  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from minimal_kanban.printing.template_engine import (  # noqa: E402
    TemplateRenderError,
    render_template,
    template_value_paths,
)
from tests import test_api as api_fixtures  # noqa: E402
from tests.test_document_money_v2 import money_card  # noqa: E402


class TemplateRenderLimitTests(unittest.TestCase):
    def test_nested_empty_loops_consume_one_shared_work_budget(self):
        template = "{{#rows}}" * 7 + "{{/rows}}" * 7
        context = {"rows": [{} for _ in range(8)]}
        for operation in (
            lambda: render_template(template, context),
            lambda: list(template_value_paths(template, context)),
        ):
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(TemplateRenderError, "предел обработки секций"),
            ):
                operation()

    def test_expansion_is_bounded_before_joining_large_result(self):
        context = {"rows": [{"text": "x" * 100_000} for _ in range(100)]}
        for content in (
            "{{#rows}}{{text}}{{/rows}}",
            "{{#rows}}" + "x" * 100_000 + "{{/rows}}",
        ):
            with (
                self.subTest(content=content[:50]),
                self.assertRaisesRegex(TemplateRenderError, "размер документа"),
            ):
                render_template(content, context)

    def test_repeated_long_field_names_also_consume_work_budget(self):
        content = "{{#rows}}" * 3 + "{{" + "x" * 100_000 + "}}" + "{{/rows}}" * 3
        with self.assertRaisesRegex(TemplateRenderError, "предел обработки секций"):
            list(template_value_paths(content, {"rows": [{} for _ in range(8)]}))

    def test_large_flat_document_and_safe_nesting_keep_exact_contents(self):
        context = {"rows": [{"text": "я" * 3000} for _ in range(1000)], "enabled": True}
        template = "{{#enabled}}" * 20 + "{{#rows}}{{text}}{{/rows}}" + "{{/enabled}}" * 20
        self.assertEqual(render_template(template, context), "я" * 3_000_000)
        self.assertEqual(render_template("{{#rows}}{{name}}{{/rows}}", {"rows": []}), "")

    def test_money_note_trace_budget_failure_is_a_controlled_template_error(self):
        card = money_card(price="1", quantity="3", total="3.01")
        content = (
            "{{#materials}}{{quantity_display}} * {{price_display}} = {{total_display}}"
            "{{/materials}} {{{rounding_notes_html}}}"
        )
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            document = service._document_definition("parts_sale")
            context = service._build_document_context(
                card,
                card.repair_order,
                document=document,
                settings=service._read_settings(),
                client=None,
            )
            context["rounding_notes_html"] = rounding_notes_html(document, context)
            untraced = render_template(content, context)
            arguments = {
                "selected_document_ids": ["parts_sale"],
                "template_overrides": {"parts_sale": content},
            }
            self.assertTrue(
                service.preview_documents(card, **arguments)["money_validation"]["valid"]
            )
            with (
                patch(
                    "minimal_kanban.printing.template_engine._MAX_RENDER_CHARS", len(untraced) + 1
                ),
                patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf,
                patch("minimal_kanban.printing.service.print_html") as printer,
            ):
                for operation in (
                    lambda: service.preview_documents(card, **arguments),
                    lambda: service.export_documents_pdf(card, **arguments),
                    lambda: service.print_documents(card, printer_name="Synthetic", **arguments),
                ):
                    with self.assertRaises(PrintModuleError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, "template_error")
                pdf.assert_not_called()
                printer.assert_not_called()


class TemplateRenderLimitApiTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def test_small_override_cannot_block_crm_by_expanding_empty_sections(self):
        order = {
            "tax_label": "Без НДС",
            "works": [
                {"name": f"Synthetic {index}", "quantity": "1", "price": "1"} for index in range(8)
            ],
        }
        before = copy.deepcopy(self.store.read_bundle())
        templates = (
            "{{#works}}" * 7 + "{{/works}}" * 7,
            "{{#works}}" * 4 + "x" * 3000 + "{{/works}}" * 4,
        )
        with (
            patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for content in templates:
                for route in (
                    "preview_repair_order_print_documents",
                    "export_repair_order_print_pdf",
                    "print_repair_order_documents",
                ):
                    with self.subTest(route=route, template_chars=len(content)):
                        status, response = self.request(
                            f"/api/{route}",
                            {
                                "document_without_card": True,
                                "manual_document": order,
                                "selected_document_ids": ["invoice"],
                                "template_overrides": {"invoice": content},
                                "printer_name": "Synthetic",
                            },
                        )
                        self.assertEqual(status, 400, response)
                        self.assertEqual(response["error"]["code"], "template_error")
            pdf.assert_not_called()
            printer.assert_not_called()
        self.assertEqual(self.request("/api/get_cards", {})[0], 200)
        self.assertEqual(self.store.read_bundle(), before)
