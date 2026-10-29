"""Compatibility and data-binding checks for the approved repair-order layout."""

from __future__ import annotations

import hashlib
import html
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.models import PrintModuleSettings  # noqa: E402
from minimal_kanban.printing.repair_order_v6 import REPAIR_ORDER_V6_TEMPLATE  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from tests.test_printing_service import build_card  # noqa: E402


class _Terms(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.items: list[str] = []
        self.paragraphs: list[int] = []
        self.fragments: list[str] | None = None
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "li":
            self.fragments = []
            self.paragraphs.append(0)
        elif tag == "p" and self.fragments is not None:
            self.paragraphs[-1] += 1

    def handle_data(self, data: str) -> None:
        if self.fragments is not None:
            self.fragments.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self.fragments is not None:
            self.items.append(" ".join(" ".join(self.fragments).split()))
            self.fragments = None


class RepairOrderV6Tests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.service = PrintModuleService(self.root)
        self.card = build_card()

    def context(self, settings: PrintModuleSettings | None = None) -> dict:
        return self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("repair_order"),
            settings=settings or PrintModuleSettings(),
        )

    def preview(self, service: PrintModuleService | None = None, **kwargs) -> dict:
        return (service or self.service).preview_documents(
            self.card, selected_document_ids=["repair_order"], **kwargs
        )["documents"][0]

    def test_standard_id_stays_stable_and_exposes_both_logical_pages(self) -> None:
        document = self.preview()
        self.assertEqual(document["template"]["id"], "builtin:repair_order:standard")
        self.assertEqual(document["page_count"], 2)
        self.assertEqual([page["number"] for page in document["pages"]], [1, 2])
        first, second = [page["html"] for page in document["pages"]]
        self.assertIn('<div class="document-page repair-order-v6">', first)
        self.assertIn('class="ro-v6-totals"', first)
        self.assertIn('class="document-page repair-order-v6 ro-v6-terms-page"', second)
        self.assertIn('class="ro-v6-stamp-space">М. П.</div>', second)
        self.assertNotIn('class="ro-v6-stamp-space">', first)
        self.assertEqual(len(_Terms(second).items), 8)
        self.assertTrue(document["money_validation"]["valid"])

    def test_reception_phone_falls_back_after_settings_normalize_whitespace(self) -> None:
        for reception, general, expected in (
            ("  288-14-15  ", "  111-22-33 ", "288-14-15"),
            (" \t\n ", " 111-22-33 ", "111-22-33"),
            ("", "111-22-33", "111-22-33"),
            (" \t ", " \n ", "—"),
        ):
            with self.subTest(reception=reception, general=general):
                payload = {"service_profile": {"reception_phone": reception, "phone": general}}
                settings = PrintModuleSettings.from_dict(payload)
                context = self.context(settings)
                self.assertEqual(context["service"]["reception_phone_display"], expected)
                self.assertEqual(context["service"]["reception_phone"], reception.strip() or "—")
                first = self.preview(print_settings=payload)["pages"][0]["html"]
                self.assertIn(f"Телефон приёмки<strong>{expected}</strong>", first)
                self.service.save_settings(payload)
                restored = self.preview(PrintModuleService(self.root))["pages"][0]["html"]
                self.assertIn(f"Телефон приёмки<strong>{expected}</strong>", restored)

        untouched = PrintModuleSettings.from_dict({"service_profile": {}}).service_profile
        defaults = PrintModuleSettings().service_profile
        self.assertEqual(untouched.reception_phone, defaults.reception_phone)
        self.assertEqual(untouched.phone, defaults.phone)

    def test_custom_default_and_content_survive_service_reload(self) -> None:
        content = (
            '<div class="document-page"><h1>Пользовательский заказ</h1>'
            "<p>{{client.name_display}}</p></div>"
        )
        custom = self.service.save_template(
            document_type="repair_order", name="Сохранённая форма", content=content
        )["template"]
        self.service.set_default_template(document_type="repair_order", template_id=custom["id"])
        original_settings = self.service._settings_path.read_bytes()
        original_templates = self.service._templates_path.read_bytes()

        reloaded = PrintModuleService(self.root)
        selected = self.preview(reloaded)
        self.assertEqual(selected["template"]["id"], custom["id"])
        self.assertTrue(selected["template"]["is_default"])
        self.assertEqual(selected["template"]["content"], content)
        self.assertIn("Пользовательский заказ", selected["pages"][0]["html"])
        explicit = self.preview(
            reloaded, selected_template_ids={"repair_order": "builtin:repair_order:standard"}
        )
        self.assertEqual(explicit["template"]["id"], "builtin:repair_order:standard")
        self.assertEqual(explicit["page_count"], 2)
        self.assertIn('<div class="document-page repair-order-v6">', explicit["pages"][0]["html"])
        self.assertEqual(reloaded._settings_path.read_bytes(), original_settings)
        self.assertEqual(reloaded._templates_path.read_bytes(), original_templates)

    def test_legacy_terms_html_is_byte_identical_to_the_pre_v6_contract(self) -> None:
        legacy = self.context()["repair_order"]["warranty_terms_html"]
        # Captured from the standard template's shared formatter before the v6 refactor.
        self.assertEqual(
            hashlib.sha256(legacy.encode("utf-8")).hexdigest(),
            "378cd4e6cbdef238b3337b9d271997600387fb00d3306166cf5cc1ecf7f7c591",
        )

    def test_all_eight_terms_keep_their_wording_and_only_gain_paragraphs(self) -> None:
        context = self.context()["repair_order"]
        legacy = _Terms(context["warranty_terms_html"])
        v6 = _Terms(context["warranty_terms_v6_html"])
        self.assertEqual(len(v6.items), 8)
        self.assertEqual(v6.items, legacy.items)
        self.assertEqual(v6.paragraphs, [1, 3, 1, 1, 4, 1, 1, 1])
        self.assertEqual(_Terms(self.preview()["pages"][1]["html"]).items, legacy.items)

    def test_v6_binds_shared_terms_instead_of_freezing_legal_text_in_the_template(self) -> None:
        self.assertIn("{{{repair_order.warranty_terms_v6_html}}}", REPAIR_ORDER_V6_TEMPLATE)
        self.assertNotIn("Гарантия сервиса:", REPAIR_ORDER_V6_TEMPLATE)
        with patch(
            "minimal_kanban.printing.repair_order_terms.WARRANTY_TERMS",
            (("Проверочный пункт:", ("Содержание единого источника.",)),),
        ):
            terms = _Terms(self.preview()["pages"][1]["html"])
        self.assertEqual(terms.items, ["Проверочный пункт: Содержание единого источника."])

    def test_empty_work_and_material_sections_render_clear_placeholders(self) -> None:
        self.card.repair_order.works = []
        self.card.repair_order.materials = []
        document = self.preview()
        first = document["pages"][0]["html"]
        self.assertIn("Работы не указаны", first)
        self.assertIn("Материалы не указаны", first)
        self.assertEqual(document["page_count"], 2)
        self.assertNotIn("{{", first)

    def test_long_client_and_row_text_remain_escaped_data(self) -> None:
        client = '<script>window.bad = 1</script> & "ООО" ' + "Длинное имя " * 12
        row = '<img src=x onerror="window.bad=1"> & Работа ' + "длинное описание " * 20
        self.card.repair_order.client = client
        self.card.repair_order.works[0].name = row
        pages = self.preview()["pages"]
        for page in pages:
            self.assertTrue(
                html.escape(client.strip()) in page["html"], "Client text lost or unescaped"
            )
            self.assertNotIn("<script>window.bad", page["html"])
        self.assertTrue(html.escape(row.strip()) in pages[0]["html"], "Row text lost or unescaped")
        self.assertNotIn("<img src=x onerror=", pages[0]["html"])
