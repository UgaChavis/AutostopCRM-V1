"""Protect the physical scale of portrait forms in mixed A4 print packets."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import Card, ClientProfile  # noqa: E402
from minimal_kanban.printing.document_rendering import combined_document_html  # noqa: E402
from minimal_kanban.printing.pdf import render_html_to_pdf_bytes  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from scripts.run_invoice_v5_demo import demo_fixture  # noqa: E402
from tests.browser_ui_support import UiBrowserTestCase  # noqa: E402
from tests.test_printing_service import build_card  # noqa: E402


class PrintingMixedGeometryTests(UiBrowserTestCase):
    def setUp(self) -> None:
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.service = PrintModuleService(self.directory)
        self.page.emulate_media(media="print")

    def document_html(self, document_ids: list[str]) -> str:
        preview = self.service.preview_documents(build_card(), selected_document_ids=document_ids)
        return combined_document_html(
            [
                {"document_html": page["html"]}
                for document in preview["documents"]
                for page in document["pages"]
            ]
        )

    def test_regulated_requisites_fit_print_area_and_keep_column_proportions(self) -> None:
        # A4 landscape minus the named page's two 6 mm margins. Page padding
        # and the UPD status column must also come out of this available width.
        self.page.set_viewport_size({"width": round(285 * 96 / 25.4), "height": 794})
        for document_id, weights in (
            ("invoice_factura", (47, 105, 8, 39, 75, 8)),
            ("upd", (42, 95, 6, 39, 72, 6)),
        ):
            with self.subTest(document=document_id):
                self.page.set_content(self.document_html([document_id]))
                geometry = self.page.locator(".regulated-requisites-table").evaluate(
                    """table => {
                      const page = table.closest('.document-page');
                      const box = page.getBoundingClientRect();
                      const style = getComputedStyle(page);
                      const rect = table.getBoundingClientRect();
                      return {
                        pageLeft: box.left + parseFloat(style.paddingLeft),
                        pageRight: box.right - parseFloat(style.paddingRight),
                        tableLeft: rect.left, tableRight: rect.right,
                        columns: Array.from(table.rows[0].cells,
                          cell => cell.getBoundingClientRect().width / rect.width)
                      };
                    }"""
                )
                self.assertGreaterEqual(geometry["tableLeft"], geometry["pageLeft"] - 0.5)
                self.assertLessEqual(geometry["tableRight"], geometry["pageRight"] + 0.5)
                self.assertEqual(len(geometry["columns"]), len(weights))
                for actual, weight in zip(geometry["columns"], weights, strict=True):
                    self.assertAlmostEqual(actual, weight / sum(weights), delta=0.001)

    def html_pdf_pages(
        self, html: str, *, engine: str = "chromium", orientation: str = "portrait"
    ) -> list[ET.Element]:
        if shutil.which("pdftotext") is None:
            self.skipTest("Poppler pdftotext is required for physical PDF geometry")
        pdf_path = self.directory / "packet.pdf"
        if engine == "qt":
            # Exercise the same isolated Qt subprocess as a request served by
            # the CRM, without creating a QApplication in the test runner.
            with ThreadPoolExecutor(max_workers=1) as executor:
                data = executor.submit(
                    render_html_to_pdf_bytes, html, orientation=orientation
                ).result(timeout=90)
            pdf_path.write_bytes(data)
        else:
            self.page.set_content(html)
            self.page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True)
        result = subprocess.run(
            ["pdftotext", "-bbox", str(pdf_path), "-"],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return ET.fromstring(result.stdout).findall(".//{*}page")

    def pdf_pages(self, document_ids: list[str]) -> list[ET.Element]:
        return self.html_pdf_pages(self.document_html(document_ids))

    @staticmethod
    def page_text(page: ET.Element) -> str:
        return " ".join(word.text or "" for word in page.findall(".//{*}word"))

    def assert_completion_act_packet_numbers(self, engine: str) -> None:
        for sequence in (
            ["completion_act"],
            ["invoice_factura", "invoice", "completion_act"],
            ["completion_act", "invoice", "invoice_factura"],
        ):
            with self.subTest(engine=engine, sequence=sequence):
                pages = self.html_pdf_pages(
                    self.document_html(sequence),
                    engine=engine,
                    orientation="landscape" if "invoice_factura" in sequence else "portrait",
                )
                self.assertEqual(len(pages), len(sequence))
                act_pages = []
                for index, page in enumerate(pages, 1):
                    text = self.page_text(page)
                    # All three forms must share the physical packet counter.
                    footer = " ".join(
                        word.text or ""
                        for word in page.findall(".//{*}word")
                        if float(word.attrib["yMin"]) > float(page.attrib["height"]) - 45
                    )
                    expected = (
                        f"{index} / {len(pages)}"
                        if "Счёт на оплату" in text
                        else f"Лист {index} из {len(pages)}"
                    )
                    self.assertEqual(footer, expected)
                    if "страница 1 из 1" in text:
                        act_pages.append(page)
                self.assertEqual(len(act_pages), 1, "The act's local page counter must remain")

    def test_completion_act_has_global_and_local_numbers_in_chromium(self) -> None:
        self.assert_completion_act_packet_numbers("chromium")

    def test_completion_act_has_global_and_local_numbers_in_qt(self) -> None:
        self.assert_completion_act_packet_numbers("qt")

    def assert_upd_boundary_pages(self, engine: str) -> None:
        # These lengths straddle physical page boundaries in the two engines.
        # The reference footer used to move alone, padding made a blank leaf,
        # and individual descriptions could split across physical pages.
        for count in (11, 12, 30, 48, 80):
            with self.subTest(engine=engine, rows=count):
                fixture = demo_fixture()
                fixture["card"]["repair_order"].update(
                    works=[
                        {
                            "name": (
                                f"Проверка {index:02d}: диагностика автомобиля, обслуживание "
                                "и проверка технического состояния узла"
                            ),
                            "quantity": "1.25",
                            "price": "1000",
                        }
                        for index in range(1, count + 1)
                    ],
                    materials=[],
                    payments=[],
                )
                preview = self.service.preview_documents(
                    Card.from_dict(fixture["card"]),
                    client=ClientProfile.from_dict(fixture["client"]),
                    print_settings={"service_profile": fixture["service_profile"]},
                    selected_document_ids=["upd"],
                )
                self.assertTrue(preview["money_validation"]["valid"])
                html = combined_document_html(
                    [{"document_html": page["html"]} for page in preview["documents"][0]["pages"]]
                )
                pages = self.html_pdf_pages(html, engine=engine, orientation="landscape")
                texts = [self.page_text(page) for page in pages]
                full_text = " ".join(texts)
                for index in range(1, count + 1):
                    self.assertEqual(full_text.count(f"Проверка {index:02d}:"), 1)
                reference_pages = [text for text in texts if "УПД №" in text]
                self.assertEqual(len(reference_pages), 2)
                self.assertIn("Итого", reference_pages[0])
                self.assertIn("Основание передачи", reference_pages[1])
                for index, (page, text) in enumerate(zip(pages, texts, strict=True), 1):
                    words = [word.text for word in page.findall(".//{*}word")]
                    self.assertEqual(
                        words.count("Проверка"),
                        words.count("узла"),
                        f"An item's description splits across physical page {index}",
                    )
                    self.assertTrue(
                        any(label in text for label in ("Проверка", "Итого", "Основание передачи")),
                        f"Physical page {index} contains only a footer or table heading: {text}",
                    )
                    self.assertGreater(float(page.attrib["width"]), float(page.attrib["height"]))
                    self.assertIn(f"Лист {index} из {len(pages)}", text)
                    for word in page.findall(".//{*}word"):
                        self.assertGreaterEqual(float(word.attrib["xMin"]), 0)
                        self.assertGreaterEqual(float(word.attrib["yMin"]), 0)
                        self.assertLessEqual(
                            float(word.attrib["xMax"]), float(page.attrib["width"])
                        )
                        self.assertLessEqual(
                            float(word.attrib["yMax"]), float(page.attrib["height"])
                        )

    def test_upd_keeps_totals_with_reference_at_page_boundaries_in_chromium(self) -> None:
        self.assert_upd_boundary_pages("chromium")

    def test_upd_keeps_totals_with_reference_at_page_boundaries_in_qt(self) -> None:
        self.assert_upd_boundary_pages("qt")

    def title_box(self, pages: list[ET.Element], title: str) -> tuple[float, float]:
        # Compact running headers can repeat the title. The tallest matching
        # word is the document's actual heading, measured from the emitted PDF.
        boxes = [
            (
                float(word.attrib["xMax"]) - float(word.attrib["xMin"]),
                float(word.attrib["yMax"]) - float(word.attrib["yMin"]),
            )
            for page in pages
            for word in page.findall(".//{*}word")
            if (word.text or "").casefold() == title.casefold()
        ]
        self.assertTrue(boxes, f"PDF is missing title {title!r}")
        return max(boxes, key=lambda box: box[1])

    def test_mixed_pdf_keeps_portrait_text_scale_and_named_page_orientation(self) -> None:
        if shutil.which("pdftotext") is None:
            self.skipTest("Poppler pdftotext is required for physical PDF geometry")
        titles = {"invoice": "Счёт", "repair_order": "Заказ-наряд"}
        baselines = {
            document_id: self.title_box(self.pdf_pages([document_id]), title)
            for document_id, title in titles.items()
        }
        for sequence in (
            ["repair_order", "invoice", "invoice_factura", "upd"],
            ["upd", "invoice_factura", "invoice", "repair_order"],
        ):
            with self.subTest(sequence=sequence):
                pages = self.pdf_pages(sequence)
                orientations = []
                for page in pages:
                    width, height = float(page.attrib["width"]), float(page.attrib["height"])
                    self.assertAlmostEqual(min(width, height), 210 * 72 / 25.4, delta=1)
                    self.assertAlmostEqual(max(width, height), 297 * 72 / 25.4, delta=1)
                    orientations.append("portrait" if width < height else "landscape")
                self.assertEqual(orientations.count("portrait"), 3)
                self.assertEqual(orientations.count("landscape"), 3)
                for document_id, title in titles.items():
                    actual = self.title_box(pages, title)
                    for dimension, expected in zip(actual, baselines[document_id], strict=True):
                        self.assertAlmostEqual(dimension, expected, delta=0.05)
