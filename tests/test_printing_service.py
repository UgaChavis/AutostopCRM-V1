from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card
from minimal_kanban.printing.pdf import PdfRenderError, render_html_to_pdf_bytes
from minimal_kanban.printing.printers import PrinterBackendError, print_html
from minimal_kanban.printing.service import PrintModuleService


def build_card() -> Card:
    return Card.from_dict(
        {
            "id": "card-print-1",
            "vehicle": "Toyota Camry XV70",
            "title": "ТО АКПП",
            "description": "Клиент приехал на обслуживание коробки передач.",
            "column": "inbox",
            "archived": False,
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:30:00+00:00",
            "deadline_timestamp": "2026-04-07T10:30:00+00:00",
            "repair_order": {
                "number": "12",
                "date": "06.04.2026 17:30",
                "opened_at": "06.04.2026 17:30",
                "client": "Иван Иванов",
                "phone": "+7 900 123-45-67",
                "vehicle": "Toyota Camry XV70",
                "license_plate": "А123АА124",
                "vin": "JTNB11HK103456789",
                "mileage": "165000",
                "reason": "Плановое обслуживание АКПП",
                "comment": "Проверили коробку, заменили масло и фильтр, рекомендовали контроль через 1000 км.",
                "note": "Следов критического износа не обнаружено.",
                "works": [
                    {"name": "Диагностика АКПП", "quantity": "1", "price": "2500", "total": ""},
                    {"name": "Замена масла АКПП", "quantity": "1", "price": "3500", "total": ""},
                ],
                "materials": [
                    {"name": "ATF", "quantity": "6", "price": "950", "total": ""},
                    {"name": "Фильтр АКПП", "quantity": "1", "price": "2100", "total": ""},
                ],
            },
        }
    )


class PrintingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = PrintModuleService(Path(self.temp_dir.name))
        self.card = build_card()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_workspace_exposes_documents_templates_and_settings(self) -> None:
        workspace = self.service.workspace(self.card)

        self.assertEqual(workspace["card_id"], self.card.id)
        self.assertEqual(len(workspace["documents"]), 5)
        self.assertEqual(workspace["documents"][0]["id"], "repair_order")
        self.assertTrue(workspace["documents"][0]["selected_template_id"].startswith("builtin:repair_order"))
        self.assertIn("repair_order", workspace["templates"])
        self.assertEqual(workspace["settings"]["service_profile"]["company_name"], "AutoStop CRM")

    def test_preview_returns_selected_documents_and_missing_fields(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["repair_order", "invoice"],
            active_document_id="invoice",
        )

        self.assertEqual(preview["active_document_id"], "invoice")
        self.assertEqual([item["id"] for item in preview["documents"]], ["repair_order", "invoice"])
        self.assertGreaterEqual(preview["documents"][0]["page_count"], 1)
        self.assertIn("Заказ-наряд", preview["documents"][0]["pages"][0]["html"])
        self.assertEqual(preview["documents"][0]["missing_fields"], [])

    def test_template_crud_duplicate_default_and_delete(self) -> None:
        saved = self.service.save_template(
            document_type="repair_order",
            name="Мой шаблон",
            content="<div class=\"document-page\"><h1>{{client.name_display}}</h1></div>",
        )
        template_id = saved["template"]["id"]
        self.assertTrue(template_id.startswith("custom:repair_order:"))

        duplicated = self.service.duplicate_template(template_id=template_id)
        duplicated_id = duplicated["template"]["id"]
        self.assertNotEqual(template_id, duplicated_id)

        defaulted = self.service.set_default_template(document_type="repair_order", template_id=duplicated_id)
        self.assertEqual(defaulted["template_id"], duplicated_id)
        self.assertTrue(any(item["is_default"] for item in defaulted["templates"]))

        deleted = self.service.delete_template(template_id=template_id)
        self.assertTrue(deleted["deleted"])

    def test_export_and_print_use_pdf_and_printer_backends(self) -> None:
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes", return_value=b"%PDF-1.4 test") as render_pdf:
            pdf_bytes, file_name, meta = self.service.export_documents_pdf(self.card, selected_document_ids=["repair_order"])
        self.assertTrue(pdf_bytes.startswith(b"%PDF-1.4"))
        self.assertTrue(file_name.endswith(".pdf"))
        self.assertEqual(meta["documents"][0]["id"], "repair_order")
        render_pdf.assert_called_once()

        with patch("minimal_kanban.printing.service.print_html") as print_backend:
            result = self.service.print_documents(
                self.card,
                selected_document_ids=["repair_order", "invoice"],
                printer_name="Office Printer",
                print_settings={"default_printer": "Office Printer", "copies": 2},
            )
        self.assertEqual(result["printer_name"], "Office Printer")
        self.assertEqual(result["copies"], 2)
        print_backend.assert_called_once()

    def test_pdf_generation_refuses_background_threads(self) -> None:
        errors: list[str] = []

        def worker() -> None:
            try:
                render_html_to_pdf_bytes("<html><body>test</body></html>")
            except PdfRenderError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=worker, name="printing-test-worker")
        thread.start()
        thread.join(timeout=5)

        self.assertEqual(errors, ["PDF generation is only available from the main desktop thread."])

    def test_print_backend_refuses_background_threads(self) -> None:
        errors: list[str] = []

        def worker() -> None:
            try:
                print_html("<html><body>test</body></html>", printer_name="Office Printer")
            except PrinterBackendError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=worker, name="printing-test-worker")
        thread.start()
        thread.join(timeout=5)

        self.assertEqual(errors, ["Qt printing is only available from the main desktop thread."])


if __name__ == "__main__":
    unittest.main()
