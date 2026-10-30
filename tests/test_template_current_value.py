from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.service import PrintModuleService
from minimal_kanban.printing.template_engine import render_template, template_value_paths
from tests.test_document_money_v2 import money_card


class TemplateCurrentValueTests(unittest.TestCase):
    def test_scalar_sections_render_current_value_and_trace_its_source(self):
        template = "{{#materials}}{{#price}}[{{.}}]{{/price}}{{/materials}}"
        context = {"materials": [{"price": "<1 & 2>"}]}
        self.assertEqual(render_template(template, context), "[&lt;1 &amp; 2&gt;]")
        self.assertEqual(list(template_value_paths(template, context)), ["materials.price"])
        self.assertEqual(
            render_template("{{#items}}{{{.}}}{{/items}}", {"items": ["<b>X</b>"]}), "<b>X</b>"
        )

    def test_current_warning_is_present_in_preview_pdf_and_print_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            card = money_card()
            options = {
                "selected_document_ids": ["inspection_sheet"],
                "template_overrides": {
                    "inspection_sheet": "<p>Warnings:{{#meta.warnings}}[{{.}}]{{/meta.warnings}}</p>"
                },
            }
            preview = service.preview_documents(card, **options)
            warnings = preview["documents"][0]["warnings"]
            self.assertTrue(warnings)
            html_values = [
                preview["documents"][0]["pages"][0]["html"],
                service.prepare_documents_pdf(card, **options).html,
                service.prepare_print_documents(card, printer_name="Synthetic", **options).html,
            ]
            for html in html_values:
                self.assertIn(warnings[0], html)
                self.assertNotIn("Warnings:[]", html)
