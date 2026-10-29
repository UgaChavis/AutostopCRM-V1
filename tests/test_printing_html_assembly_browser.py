"""Exercise the emitted print assembler with a real browser DOM."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.document_rendering import wrap_document_html  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from minimal_kanban.printing.web_module import PRINTING_WEB_MODULE_SCRIPT  # noqa: E402
from tests.browser_ui_support import UiBrowserTestCase  # noqa: E402
from tests.test_printing_async_context import function_source  # noqa: E402
from tests.test_printing_service import build_card  # noqa: E402


def printable_page(label: str, *, head: str = "", body_style: str = "") -> dict[str, str]:
    return {
        "html": (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            "<title>Source page</title><style>"
            ".document-page + .document-page { break-before: page; }"
            "</style>" + head + '</head><body><div class="document-shell">'
            '<section class="document-page">'
            + body_style
            + "<p>"
            + label
            + "</p></section></div></body></html>"
        )
    }


class PrintingHtmlAssemblyBrowserTests(UiBrowserTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.page.set_content("<!doctype html><html><body></body></html>")
        viewport_constants = PRINTING_WEB_MODULE_SCRIPT.split(
            "    const REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS = ", 1
        )[1].split(";", 1)[0]
        self.page.add_script_tag(
            content="""
function escapeHtml(value) {
  const span = document.createElement('span');
  span.textContent = String(value ?? '');
  return span.innerHTML;
}
let testSelection = [];
const repairOrderPrintState = {previewByDocument: {}};
function repairOrderPrintSelectedIds() { return testSelection.slice(); }
let testPreview = null;
let testPageIndex = 0;
function repairOrderPrintCurrentPreview() { return testPreview; }
function repairOrderPrintCurrentPageIndex() { return testPageIndex; }
"""
            + "const REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS = "
            + viewport_constants
            + ";\n"
            + "\n".join(
                function_source(name)
                for name in (
                    "combinedPrintPagesHtml",
                    "repairOrderPrintCombinedHtml",
                    "completionActCombinedHtml",
                    "repairOrderPrintPreviewViewport",
                )
            )
        )

    def test_preview_viewport_detects_the_page_element_not_shared_styles(self) -> None:
        pages = [
            wrap_document_html(
                '<div class="document-page repair-order-v6">Заказ-наряд</div>', title="Заказ"
            ),
            wrap_document_html(
                '<div class="document-page repair-order-v6 ro-v6-terms-page">Условия</div>',
                title="Условия",
            ),
            wrap_document_html(
                '<div class="document-page regulated-page regulated-page--landscape">УПД</div>',
                title="УПД",
            ),
        ]
        # Every preview contains this selector in PRINT_BASE_STYLES, even portrait pages.
        self.assertTrue(all("regulated-page--landscape" in page for page in pages))
        result = self.page.evaluate(
            """pages => {
              testPreview = {pages: pages.map(html => ({html}))};
              return pages.map((_, index) => {
                testPageIndex = index;
                return repairOrderPrintPreviewViewport();
              });
            }""",
            pages,
        )
        self.assertEqual(
            result,
            [
                {"width": 920, "height": 1180},
                {"width": 920, "height": 1180},
                {"width": 1180, "height": 860},
            ],
        )
        empty = self.page.evaluate(
            "() => { testPreview = null; return repairOrderPrintPreviewViewport(); }"
        )
        self.assertEqual(empty, {"width": 920, "height": 1180})

    def test_only_selected_documents_are_assembled_in_selection_order(self) -> None:
        result = self.page.evaluate(
            """pages => {
              testSelection = ['invoice', 'repair_order'];
              repairOrderPrintState.previewByDocument = {
                repair_order: {pages: [pages[0], pages[1]]},
                invoice: {pages: [pages[2]]},
                completion_act: {pages: [pages[3]]},
              };
              return repairOrderPrintCombinedHtml();
            }""",
            [printable_page(label) for label in ("ORDER-1", "ORDER-2", "INVOICE", "STALE")],
        )
        self.assertNotIn("STALE", result)
        self.assertLess(result.index("INVOICE"), result.index("ORDER-1"))
        self.assertLess(result.index("ORDER-1"), result.index("ORDER-2"))
        self.assertEqual(result.count("<!doctype html>"), 1)
        self.assertEqual(result.count("<html"), 1)
        self.assertEqual(result.count("<head>"), 1)
        self.assertEqual(result.count("<body>"), 1)
        self.assertEqual(result.count('class="document-shell"'), 1)
        self.assertNotIn("page-break-after:always", result)

    def test_invoice_v5_styles_do_not_restyle_neighbor_documents(self) -> None:
        from minimal_kanban.printing.invoice_v5 import INVOICE_V5_STYLES

        neighbors = (
            '<div class="document-page repair-order-v6" id="neighbor-order">'
            '<h1 id="neighbor-title">Заказ-наряд</h1>'
            '<span class="label" id="neighbor-label">Нейтральная подпись</span>'
            '<div class="bank" id="neighbor-bank">Нейтральный блок</div></div>'
            '<div class="document-page regulated-page regulated-page--landscape" '
            'id="neighbor-regulated">Альбомный документ</div>'
        )
        baseline = wrap_document_html(neighbors, title="Соседние формы").replace(
            INVOICE_V5_STYLES, ""
        )
        self.page.set_content(baseline)
        self.page.emulate_media(media="print")
        snapshot = """() => ['neighbor-order', 'neighbor-title', 'neighbor-label',
          'neighbor-bank', 'neighbor-regulated'].map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return Object.fromEntries(['fontSize', 'fontFamily', 'fontWeight', 'color',
              'backgroundColor', 'paddingLeft', 'marginLeft', 'width', 'lineHeight',
              'page', 'boxSizing'].map(key => [key, style[key]]));
          })"""
        before = self.page.evaluate(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            invoice = service.preview_documents(build_card(), selected_document_ids=["invoice"])[
                "documents"
            ][0]
        # Restore the emitted assembler after set_content replaced its script context.
        self.page.add_script_tag(
            content="""function escapeHtml(value) {
              const span = document.createElement('span');
              span.textContent = String(value ?? ''); return span.innerHTML;
            }"""
            + function_source("combinedPrintPagesHtml")
        )
        combined = self.page.evaluate(
            "pages => combinedPrintPagesHtml(pages)", [{"html": baseline}, *invoice["pages"]]
        )
        self.page.set_content(combined)
        self.assertEqual(self.page.evaluate(snapshot), before)
        self.assertEqual(self.page.locator(".document-page.invoice-v5").count(), 1)
        self.assertEqual(self.page.locator(".document-shell > .document-page").count(), 3)
        self.assertEqual(self.page.locator("html").count(), 1)
        self.assertEqual(self.page.locator("body").count(), 1)

    def test_styles_survive_and_page_siblings_keep_their_print_break(self) -> None:
        head = "<style>.unique-head { color: rgb(1, 2, 3); }</style>"
        link = '<link rel="stylesheet" href="data:text/css,.linked%7Bcolor:red%7D">'
        pages = [
            printable_page(
                "ONE",
                head=head + link,
                body_style="<style>.body-style { font-weight: 700; }</style>",
            ),
            printable_page("TWO", head=head + link),
        ]
        result = self.page.evaluate("pages => combinedPrintPagesHtml(pages)", pages)
        self.page.set_content(result)
        self.page.emulate_media(media="print")
        self.assertEqual(self.page.locator("head style").count(), 2)
        self.assertEqual(self.page.locator('head link[rel="stylesheet"]').count(), 1)
        self.assertEqual(self.page.locator(".document-page style").count(), 1)
        self.assertEqual(self.page.locator(".document-shell > .document-page").count(), 2)
        self.assertEqual(self.page.locator(".document-shell > :not(.document-page)").count(), 0)
        self.assertEqual(
            self.page.locator(".document-page")
            .nth(1)
            .evaluate("element => getComputedStyle(element).breakBefore"),
            "page",
        )

    def test_repair_order_exact_numbers_fit_without_changing_normal_columns(self) -> None:
        # A4 minus the named page's 9 mm margins; the form adds its own 5 mm padding.
        self.page.set_viewport_size({"width": 726, "height": 1100})
        self.page.emulate_media(media="print")
        with tempfile.TemporaryDirectory() as directory:
            service = PrintModuleService(Path(directory))
            for precise in (False, True):
                with self.subTest(precise=precise):
                    card = build_card()
                    if precise:
                        for rows in (card.repair_order.works, card.repair_order.materials):
                            rows[0].quantity = "0.12345678901"
                            rows[0].price = "123.12345678901"
                            rows[0].total = ""
                    document = service.preview_documents(
                        card, selected_document_ids=["repair_order"]
                    )["documents"][0]
                    self.page.set_content(document["pages"][0]["html"])
                    tables = self.page.locator(".ro-v6-lines").evaluate_all(
                        """tables => tables.map(table => {
                          const width = table.getBoundingClientRect().width;
                          return Array.from(table.querySelector('tbody tr').cells).map(cell => {
                            const box = cell.getBoundingClientRect();
                            const range = document.createRange();
                            range.selectNodeContents(cell);
                            const text = range.getBoundingClientRect();
                            return {value: cell.textContent, percent: box.width / width * 100,
                              left: box.left, right: box.right,
                              textLeft: text.left, textRight: text.right};
                          });
                        })"""
                    )
                    self.assertEqual(len(tables), 2)
                    for cells in tables:
                        if precise:
                            self.assertEqual(cells[1]["value"], "0,12345678901")
                            self.assertEqual(cells[2]["value"], "123,12345678901")
                        else:
                            for cell, expected in zip(cells, (55, 11, 17, 17), strict=True):
                                self.assertAlmostEqual(cell["percent"], expected, delta=0.1)
                        for cell in cells[1:]:
                            self.assertGreaterEqual(cell["textLeft"], cell["left"] + 9.5)
                            self.assertLessEqual(cell["textRight"], cell["right"] - 9.5)

    def test_completion_act_uses_the_same_wrapper_and_handles_empty_preview(self) -> None:
        pages = [printable_page("ACT-1"), printable_page("ACT-2")]
        result = self.page.evaluate("pages => completionActCombinedHtml(pages)", pages)
        self.assertEqual(result.count('class="document-shell"'), 1)
        self.assertEqual(result.count('class="document-page"'), 2)
        self.assertIn("<title>Акт выполненных работ</title>", result)
        self.assertNotIn("page-break-after:always", result)
        empty = self.page.evaluate("() => completionActCombinedHtml([null, {html: ''}])")
        self.assertIn("Нет данных акта для печати.", empty)
        self.assertNotIn('class="document-page"', empty)

    def test_empty_selection_cannot_print_a_cached_document_and_escapes_labels(self) -> None:
        result = self.page.evaluate(
            """page => {
              testSelection = [];
              repairOrderPrintState.previewByDocument = {repair_order: {pages: [page]}};
              return repairOrderPrintCombinedHtml();
            }""",
            printable_page("STALE"),
        )
        self.assertNotIn("STALE", result)
        self.assertIn("Нет данных для печати.", result)
        escaped = self.page.evaluate("() => combinedPrintPagesHtml([], '<Title &>', '<Empty &>')")
        self.assertIn("<title>&lt;Title &amp;&gt;</title>", escaped)
        self.assertIn("&lt;Empty &amp;&gt;</main>", escaped)
