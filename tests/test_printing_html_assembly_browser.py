"""Exercise the emitted print assembler with a real browser DOM."""

from __future__ import annotations

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.document_rendering import wrap_document_html  # noqa: E402
from minimal_kanban.printing.web_module import PRINTING_WEB_MODULE_SCRIPT  # noqa: E402
from tests.browser_ui_support import UiBrowserTestCase  # noqa: E402
from tests.test_printing_async_context import function_source  # noqa: E402


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
