"""Vehicle acceptance act sources shared by preview, PDF export, and print."""

from __future__ import annotations

import html
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_repository_root_path, ensure_source_path
else:
    from source_path_support import ensure_repository_root_path, ensure_source_path

ensure_repository_root_path()
ensure_source_path()

from minimal_kanban.models import Card
from minimal_kanban.printing import service as printing_service_module
from minimal_kanban.printing.service import PrintModuleService
from tests.test_printing_service import build_card


class VehicleAcceptanceActTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = PrintModuleService(Path(self.temp_dir.name))
        self.card = build_card()

    def test_acceptance_act_renders_legal_terms_and_photo_fixation(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["vehicle_acceptance_act"],
            active_document_id="vehicle_acceptance_act",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Акт приема-передачи автомобиля в работу", html)
        self.assertIn("Фотофиксация состояния автомобиля", html)
        self.assertIn("150 рублей в сутки", html)
        self.assertIn(
            "претензии по повреждениям после выезда автомобиля из сервиса не принимаются", html
        )
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_acceptance_act_uses_card_short_essence_in_preview_export_and_print(self) -> None:
        title = 'Ремонт АКПП <контроль> & "проверка"'
        self.card.title = title
        self.card.description = "DESCRIPTION_MUST_NOT_REPLACE_SHORT_ESSENCE"
        for reason in ("", "Причина обращения из заказ-наряда"):
            with self.subTest(reason=reason):
                self.card.repair_order.reason = reason
                for entry_point, rendered_html in self._render_acceptance_act(self.card).items():
                    with self.subTest(entry_point=entry_point):
                        self.assertIn(
                            '<div class="doc-note">' + html.escape(title) + "</div>",
                            rendered_html,
                        )
                        self.assertNotIn(self.card.description, rendered_html)
                        if reason:
                            self.assertNotIn(reason, rendered_html)
                self.assertEqual(self.card.repair_order.reason, reason)

        self.card.title = "Диагностика после ремонта АКПП"
        refreshed = self.service.preview_documents(
            self.card, selected_document_ids=["vehicle_acceptance_act"]
        )["documents"][0]["pages"][0]["html"]
        self.assertIn('<div class="doc-note">' + self.card.title + "</div>", refreshed)
        self.assertNotIn(html.escape(title), refreshed)
        repair_order = self.service.preview_documents(
            self.card, selected_document_ids=["repair_order"]
        )["documents"][0]["pages"][0]["html"]
        self.assertIn(
            '<div class="doc-note">' + self.card.repair_order.reason + "</div>", repair_order
        )

    def _render_acceptance_act(self, card: Card) -> dict[str, str]:
        with (
            patch.object(
                printing_service_module,
                "render_html_to_pdf_bytes",
                return_value=b"%PDF-1.4 synthetic-acceptance-act",
            ) as render_pdf,
            patch.object(printing_service_module, "print_html") as print_backend,
        ):
            preview = self.service.preview_documents(
                card, selected_document_ids=["vehicle_acceptance_act"]
            )
            self.service.export_documents_pdf(
                card, selected_document_ids=["vehicle_acceptance_act"]
            )
            self.service.print_documents(
                card, selected_document_ids=["vehicle_acceptance_act"], printer_name="Test Printer"
            )
        return {
            "preview": "".join(page["html"] for page in preview["documents"][0]["pages"]),
            "export": render_pdf.call_args.args[0],
            "print": print_backend.call_args.args[0],
        }

    def test_acceptance_act_manual_document_preserves_multiline_reason(self) -> None:
        for reason in ('Ручная диагностика <узла> & "согласовано"\nРемонт АКПП', ""):
            with self.subTest(reason=reason):
                profile = self.service.manual_document_profile({"reason": reason})
                expected = "<br>".join(html.escape(line) for line in reason.split("\n")) or "—"
                for entry_point, rendered_html in self._render_acceptance_act(profile.card).items():
                    with self.subTest(entry_point=entry_point):
                        self.assertTrue(
                            '<div class="doc-note">' + expected + "</div>" in rendered_html,
                            "Manual repair reason was not rendered in the acceptance act",
                        )
                        self.assertNotIn(profile.card.title, rendered_html)
                self.assertEqual(profile.card.repair_order.reason, reason)

    def test_acceptance_act_empty_card_short_essence_does_not_use_other_fields(self) -> None:
        self.card.title = ""
        self.card.repair_order.reason = "ORDER_REASON_MUST_NOT_REPLACE_SHORT_ESSENCE"
        self.card.description = "DESCRIPTION_MUST_NOT_REPLACE_SHORT_ESSENCE"
        for entry_point, rendered_html in self._render_acceptance_act(self.card).items():
            with self.subTest(entry_point=entry_point):
                self.assertIn('<div class="doc-note">—</div>', rendered_html)
                self.assertNotIn(self.card.repair_order.reason, rendered_html)
                self.assertNotIn(self.card.description, rendered_html)

    def test_acceptance_act_custom_template_keeps_its_source_fields(self) -> None:
        content = "<section>{{card.title}} / {{{repair_order.reason_html}}}</section>"
        saved = self.service.save_template(
            document_type="vehicle_acceptance_act", name="Custom source fields", content=content
        )
        self.service.set_default_template(
            document_type="vehicle_acceptance_act", template_id=saved["template"]["id"]
        )
        for card in (
            self.card,
            self.service.manual_document_profile({"reason": "Ручная причина"}).card,
        ):
            with self.subTest(card_id=card.id):
                rendered_html = self.service.preview_documents(
                    card, selected_document_ids=["vehicle_acceptance_act"]
                )["documents"][0]["pages"][0]["html"]
                self.assertIn(html.escape(card.title), rendered_html)
                self.assertIn(card.repair_order.reason, rendered_html)
        self.assertEqual(self.service._read_custom_templates()[0].content, content)


if __name__ == "__main__":
    unittest.main()
