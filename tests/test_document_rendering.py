from __future__ import annotations

# ruff: noqa: E402
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.printing.defaults import PRINT_BASE_STYLES
from minimal_kanban.printing.document_rendering import (
    PAGE_BREAK_MARKER,
    combined_document_html,
    preview_document_payload,
    wrap_document_html,
)
from minimal_kanban.printing.models import (
    PrintDocumentDefinition,
    PrintModuleSettings,
    PrintTemplateRecord,
)
from minimal_kanban.printing.service import PrintModuleError, PrintModuleService
from tests.test_printing_service import build_business_client, build_card


class _ScopedText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"meta", "img", "br", "hr", "col", "link", "input"}:
            self.stack.append((tag, set(dict(attrs).get("class", "").split())))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.fragments.append((data, set().union(*(classes for _, classes in self.stack))))


class DocumentRenderingTests(unittest.TestCase):
    def test_print_rules_keep_logical_pages_headings_and_totals_together(self) -> None:
        import re

        expected = {
            ".document-page + .document-page": ("break-before: page", "page-break-before: always"),
            ".doc-section__title": ("break-after: avoid", "page-break-after: avoid"),
            ".doc-totals-table": ("break-inside: avoid", "page-break-inside: avoid"),
            ".doc-invoice-final": ("break-inside: avoid", "page-break-inside: avoid"),
            ".doc-bank-table__account": ("white-space: nowrap", "word-break: normal"),
        }
        for selector, declarations in expected.items():
            match = re.search(
                r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]+)\}", PRINT_BASE_STYLES
            )
            self.assertIsNotNone(match, selector)
            for declaration in declarations:
                self.assertIn(declaration, match.group(1), selector)

    def test_invoice_final_block_contains_totals_words_and_signatures_and_accounts_do_not_wrap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            preview = service.preview_documents(
                build_card(), client=build_business_client(), selected_document_ids=["invoice"]
            )
        parser = _ScopedText()
        parser.feed(preview["documents"][0]["pages"][0]["html"])
        final_text = "".join(
            text for text, classes in parser.fragments if "doc-invoice-final" in classes
        )
        for label in (
            "Итого",
            "В том числе НДС (5%)",
            "Всего к оплате",
            "Сумма прописью",
            "Подписи",
            "Руководитель",
            "Бухгалтер",
        ):
            self.assertIn(label, final_text)
        accounts = [
            text
            for text, classes in parser.fragments
            if "doc-bank-table__account" in classes and text.strip()
        ]
        self.assertEqual(len(accounts), 4)
        self.assertTrue(all(len(account) == 20 and account.isdigit() for account in accounts))

    def test_shell_escapes_only_title_and_keeps_styles_and_nested_body(self) -> None:
        body = "<section><div>Авто &amp; сервис</div></section>"
        rendered = wrap_document_html(body, title='Акт <1> "&"')
        self.assertIn("<title>Акт &lt;1&gt; &quot;&amp;&quot;</title>", rendered)
        self.assertIn(f"<style>{PRINT_BASE_STYLES}</style>", rendered)
        self.assertIn(f'<div class="document-shell">{body}</div>', rendered)

    def test_combined_html_unwraps_once_and_removes_only_page_markers(self) -> None:
        body = "<section><div>Один</div></section>"
        result = combined_document_html(
            [
                {"document_html": wrap_document_html(body + PAGE_BREAK_MARKER + "Два", title="А")},
                {"document_html": '<BODY class="legacy">Три</BODY>'},
                {"document_html": "<p>Четыре</p>"},
            ]
        )
        self.assertEqual(
            result,
            wrap_document_html(
                body + "Два\nТри\n<p>Четыре</p>", title="Печать документов AutoStop CRM"
            ),
        )

    def test_preview_keeps_projection_and_splits_pages_in_order(self) -> None:
        document = PrintDocumentDefinition("repair_order", "Заказ-наряд", "", "template")
        template = PrintTemplateRecord("template", document.id, "Шаблон", "", "", "")
        settings = PrintModuleSettings(default_template_ids={document.id: template.id})
        rendered = {
            "document": document,
            "template": template,
            "document_html": wrap_document_html(
                " Первый " + PAGE_BREAK_MARKER + " Второй ", title=document.label
            ),
            "warnings": ["Предупреждение"],
            "missing_fields": ["client"],
            "computed_totals": {"total": "10.00"},
            "computed_items": [{"name": "Работа"}],
        }
        preview = preview_document_payload(rendered, settings=settings)
        self.assertEqual(preview["id"], document.id)
        self.assertEqual(preview["page_count"], 2)
        self.assertEqual(
            preview["pages"],
            [
                {"number": 1, "html": wrap_document_html("Первый", title=document.label)},
                {"number": 2, "html": wrap_document_html("Второй", title=document.label)},
            ],
        )
        self.assertTrue(preview["template"]["is_default"])
        for key in ("warnings", "missing_fields", "computed_totals", "computed_items"):
            self.assertEqual(preview[key], rendered[key])
        rendered["document_html"] = ""
        settings.default_template_ids.clear()
        empty = preview_document_payload(rendered, settings=settings)
        self.assertEqual(empty["page_count"], 1)
        self.assertFalse(empty["template"]["is_default"])

    def test_preview_export_and_print_share_normalization_and_rendered_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            card = build_card()
            arguments = {
                "selected_document_ids": [
                    "repair_order",
                    "unknown",
                    "technical_repair_order",
                    "repair_order",
                ],
                "selected_template_ids": {"unknown": "ignored"},
                "template_overrides": {"unknown": "ignored"},
                "document_overrides": {},
            }
            with (
                patch.object(
                    service, "_render_document_batch", wraps=service._render_document_batch
                ) as batch,
                patch(
                    "minimal_kanban.printing.service.render_html_to_pdf_bytes",
                    return_value=b"%PDF-test",
                ) as export,
                patch("minimal_kanban.printing.service.print_html") as printer,
            ):
                preview = service.preview_documents(card, **arguments)
                pdf, _, meta = service.export_documents_pdf(card, **arguments)
                printed = service.print_documents(card, printer_name="Test printer", **arguments)
            self.assertEqual(pdf, b"%PDF-test")
            self.assertEqual(batch.call_count, 3)
            self.assertEqual(batch.call_args_list[0], batch.call_args_list[1])
            self.assertEqual(batch.call_args_list[1], batch.call_args_list[2])
            self.assertEqual(export.call_args.args[0], printer.call_args.args[0])
            self.assertEqual(
                [item["id"] for item in preview["documents"]],
                ["repair_order", "technical_repair_order"],
            )
            self.assertEqual(len(meta["documents"]), 2)
            self.assertEqual(len(printed["documents"]), 2)
            with patch.object(service, "_render_document_batch") as batch:
                with self.assertRaisesRegex(PrintModuleError, "Не выбран принтер"):
                    service.print_documents(card)
                batch.assert_not_called()
