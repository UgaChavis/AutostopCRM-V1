from __future__ import annotations

# ruff: noqa: E402
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card
from minimal_kanban.printing.models import SUPPORTED_PRINT_DOCUMENT_TYPES
from minimal_kanban.printing.pdf import render_html_to_pdf_bytes
from minimal_kanban.printing.service import PrintModuleError, PrintModuleService
from minimal_kanban.printing.template_engine import render_template


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
                "payment_method": "cashless",
                "prepayment": "1000",
                "reason": "Плановое обслуживание АКПП",
                "comment": "Проверили коробку, заменили масло и фильтр, рекомендовали контроль через 1000 км.",
                "note": "Следов критического износа не обнаружено.",
                "works": [
                    {"name": "Диагностика АКПП", "quantity": "1", "price": "2500", "total": ""},
                    {"name": "Замена масла АКПП", "quantity": "1", "price": "3500", "total": ""},
                ],
                "materials": [
                    {
                        "name": "ATF",
                        "catalog_number": "08886-81210",
                        "quantity": "6",
                        "price": "950",
                        "total": "",
                    },
                    {"name": "Фильтр АКПП", "quantity": "1", "price": "2100", "total": ""},
                ],
            },
        }
    )


def build_payment_card(*, payment_method: str, payments: list[dict[str, str]]) -> Card:
    return Card.from_dict(
        {
            "id": "card-print-payments",
            "vehicle": "Toyota Camry XV70",
            "title": "РўРћ РђРљРџРџ",
            "description": "РљР»РёРµРЅС‚ РїСЂРёРµС…Р°Р» РЅР° РѕР±СЃР»СѓР¶РёРІР°РЅРёРµ РєРѕСЂРѕР±РєРё РїРµСЂРµРґР°С‡.",
            "column": "inbox",
            "archived": False,
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:30:00+00:00",
            "deadline_timestamp": "2026-04-07T10:30:00+00:00",
            "repair_order": {
                "number": "13",
                "date": "06.04.2026 17:30",
                "opened_at": "06.04.2026 17:30",
                "client": "РРІР°РЅ РРІР°РЅРѕРІ",
                "phone": "+7 900 123-45-67",
                "vehicle": "Toyota Camry XV70",
                "license_plate": "Рђ123РђРђ124",
                "vin": "JTNB11HK103456789",
                "mileage": "165000",
                "payment_method": payment_method,
                "payments": payments,
                "reason": "РџР»Р°РЅРѕРІРѕРµ РѕР±СЃР»СѓР¶РёРІР°РЅРёРµ РђРљРџРџ",
                "comment": "РџСЂРѕРІРµСЂРёР»Рё РєРѕСЂРѕР±РєСѓ, Р·Р°РјРµРЅРёР»Рё РјР°СЃР»Рѕ Рё С„РёР»СЊС‚СЂ.",
                "note": "РЎР»РµРґРѕРІ РєСЂРёС‚РёС‡РµСЃРєРѕРіРѕ РёР·РЅРѕСЃР° РЅРµ РѕР±РЅР°СЂСѓР¶РµРЅРѕ.",
                "works": [
                    {"name": "Р Р°Р±РѕС‚Р° 1", "quantity": "1", "price": "10000", "total": ""},
                ],
                "materials": [
                    {"name": "РњР°С‚РµСЂРёР°Р» 1", "quantity": "1", "price": "10000", "total": ""},
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
        self.assertEqual(len(workspace["documents"]), len(SUPPORTED_PRINT_DOCUMENT_TYPES))
        self.assertEqual(workspace["documents"][0]["id"], "repair_order")
        self.assertTrue(
            workspace["documents"][0]["selected_template_id"].startswith("builtin:repair_order")
        )
        self.assertIn("repair_order", workspace["templates"])
        self.assertEqual(workspace["settings"]["service_profile"]["company_name"], "Auto Stop")
        self.assertEqual(
            workspace["settings"]["service_profile"]["legal_name"],
            "ИП Гришкявичус Константин Владиславович",
        )
        self.assertEqual(workspace["settings"]["service_profile"]["reception_phone"], "288-14-15")
        inspection_document = next(
            item for item in workspace["documents"] if item["id"] == "inspection_sheet"
        )
        self.assertTrue(inspection_document["supports_form_fill"])

    def test_workspace_prefills_service_profile_when_settings_are_blank(self) -> None:
        self.service._settings_path.write_text(
            (
                '{"service_profile":{"company_name":"","legal_name":"","address":"","phone":"",'
                '"reception_phone":"","spare_parts_phone":"","email":"","website":"",'
                '"work_hours":"","inn":"","kpp":"","ogrn":"","bank_name":"","bik":"",'
                '"settlement_account":"","correspondent_account":"","tax_label":"",'
                '"payment_purpose":""}}'
            ),
            encoding="utf-8",
        )

        workspace = self.service.workspace(self.card)
        profile = workspace["settings"]["service_profile"]

        self.assertEqual(profile["company_name"], "Auto Stop")
        self.assertEqual(profile["legal_name"], "ИП Гришкявичус Константин Владиславович")
        self.assertEqual(profile["address"], "660012, г. Красноярск, ул. Семафорная, 80, стр. 4")
        self.assertEqual(profile["reception_phone"], "288-14-15")
        self.assertEqual(profile["spare_parts_phone"], "+7 (963) 184-76-76")
        self.assertEqual(profile["website"], "autostop124.ru")
        self.assertEqual(profile["tax_label"], "Без НДС")

    def test_repair_order_context_includes_brand_logo_asset(self) -> None:
        context = self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("repair_order"),
            settings=self.service._read_settings(),
        )
        self.assertTrue(
            context["service"]["brand_logo_data_uri"].startswith("data:image/png;base64,")
        )

    def test_preview_returns_selected_documents_and_missing_fields(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["repair_order", "invoice"],
            active_document_id="invoice",
        )

        self.assertEqual(preview["active_document_id"], "invoice")
        self.assertEqual([item["id"] for item in preview["documents"]], ["repair_order", "invoice"])
        self.assertIn('class="doc-brand-mark"', preview["documents"][0]["pages"][0]["html"])
        self.assertIn("data:image/png;base64,", preview["documents"][0]["pages"][0]["html"])
        self.assertGreaterEqual(preview["documents"][0]["page_count"], 1)
        self.assertIn("Заказ-наряд", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("Налоги и сборы", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("Предоплата", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("К доплате", preview["documents"][0]["pages"][0]["html"])
        self.assertTrue(
            any("Гарантийные условия" in page["html"] for page in preview["documents"][0]["pages"])
        )
        self.assertIn(
            "<strong>30 дней:</strong> гарантия на выполненные работы и замененные запасные части.",
            "".join(page["html"] for page in preview["documents"][0]["pages"]),
        )
        self.assertIn("Всего к оплате", preview["documents"][1]["pages"][0]["html"])
        self.assertIn("Сумма прописью", preview["documents"][1]["pages"][0]["html"])
        self.assertEqual(preview["documents"][0]["missing_fields"], [])

    def test_repair_order_template_renders_reception_phone_and_signatures(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["repair_order"],
            active_document_id="repair_order",
            print_settings={
                "service_profile": {
                    "company_name": "AutoStop",
                    "phone": "288-14-15",
                    "reception_phone": "288-14-15",
                }
            },
        )

        document = preview["documents"][0]
        self.assertEqual(document["page_count"], 2)
        self.assertIn("288-14-15", document["pages"][0]["html"])
        self.assertIn("Автомобиль получил, претензий не имею", document["pages"][1]["html"])

    def test_invoice_template_renders_brand_header_and_banking_block(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Счет на оплату", html)
        self.assertIn('class="doc-brand-mark"', html)
        self.assertIn("БИК", html)
        self.assertIn("Тел. 288-14-15", html)
        self.assertIn("Внимание! Оплата данного счета", html)
        self.assertIn("Ед. изм.", html)
        self.assertIn("Сумма прописью", html)
        self.assertIn("Всего к оплате", html)
        self.assertIn("Руководитель", html)
        self.assertIn("Бухгалтер", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_invoice_factura_template_renders_brand_header_and_totals(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice_factura"],
            active_document_id="invoice_factura",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Счет-фактура", html)
        self.assertIn('class="doc-brand-mark"', html)
        self.assertIn("Бухгалтерский документ", html)
        self.assertIn("ОГРН", html)
        self.assertIn("Налоговый режим", html)
        self.assertIn("Подписи", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_inspection_sheet_template_renders_brand_header_and_confirmation(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["inspection_sheet"],
            active_document_id="inspection_sheet",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Дефектовочная ведомость", html)
        self.assertIn('class="doc-brand-mark"', html)
        self.assertIn("Диагностика и дефектовка", html)
        self.assertIn("Сведения по заказу", html)
        self.assertIn("Подтверждение", html)
        self.assertIn("Мастер-приемщик", html)
        self.assertIn("С результатами ознакомлен", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_completion_act_template_renders_brand_header_and_signature_block(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["completion_act"],
            active_document_id="completion_act",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Акт выполненных работ", html)
        self.assertIn('class="doc-brand-mark"', html)
        self.assertIn("Телефон ресепшена", html)
        self.assertIn("Сумма прописью", html)
        self.assertIn("Подписи сторон", html)
        self.assertIn("Работы принял, претензий не имею", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_preview_supports_all_builtin_document_types(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=list(SUPPORTED_PRINT_DOCUMENT_TYPES),
            active_document_id="repair_order",
        )

        self.assertEqual(
            [item["id"] for item in preview["documents"]], list(SUPPORTED_PRINT_DOCUMENT_TYPES)
        )
        for document in preview["documents"]:
            self.assertGreaterEqual(document["page_count"], 1)
            self.assertIn("<!doctype html>", document["pages"][0]["html"].lower())
            self.assertIn(document["label"], document["pages"][0]["html"])

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

    def test_parts_sale_document_uses_material_rows_without_vehicle_requirement(self) -> None:
        card = build_card()
        card.repair_order.vehicle = ""
        card.repair_order.vin = ""
        card.repair_order.license_plate = ""

        preview = self.service.preview_documents(
            card,
            selected_document_ids=["parts_sale"],
            active_document_id="parts_sale",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Продажа запчастей", html)
        self.assertIn("ATF", html)
        self.assertIn("Фильтр АКПП", html)
        self.assertNotIn("vehicle", document["missing_fields"])
        self.assertNotIn("vin", document["missing_fields"])
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_print_context_omits_material_catalog_number(self) -> None:
        context = self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("repair_order"),
            settings=self.service._read_settings(),
        )
        self.assertNotIn("catalog_number", context["repair_order"]["materials"][0])

        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["repair_order"],
            active_document_id="repair_order",
        )
        self.assertNotIn("08886-81210", preview["documents"][0]["pages"][0]["html"])

    def test_print_context_uses_payment_summary_for_payment_amounts(self) -> None:
        scenarios = [
            (
                "cash",
                [{"amount": "10000", "payment_method": "cash"}],
                {
                    "subtotal": Decimal("20000"),
                    "taxes": Decimal("0"),
                    "grand": Decimal("20000"),
                    "prepayment": Decimal("10000"),
                    "due": Decimal("10000"),
                },
            ),
            (
                "cashless",
                [{"amount": "10000", "payment_method": "cashless"}],
                {
                    "subtotal": Decimal("20000"),
                    "taxes": Decimal("1500"),
                    "grand": Decimal("21500"),
                    "prepayment": Decimal("10000"),
                    "due": Decimal("11500"),
                },
            ),
            (
                "mixed",
                [
                    {"amount": "5000", "payment_method": "cash"},
                    {"amount": "7000", "payment_method": "cashless"},
                ],
                {
                    "subtotal": Decimal("20000"),
                    "taxes": Decimal("1050"),
                    "grand": Decimal("21050"),
                    "prepayment": Decimal("12000"),
                    "due": Decimal("9200"),
                },
            ),
        ]

        for label, payments, expected in scenarios:
            with self.subTest(label=label):
                card = build_payment_card(
                    payment_method="cashless" if label != "cash" else "cash", payments=payments
                )
                context = self.service._build_document_context(
                    card,
                    card.repair_order,
                    document=self.service._document_definition("repair_order"),
                    settings=self.service._read_settings(),
                )
                totals = context["totals"]

                self.assertEqual(totals["subtotal"], expected["subtotal"])
                self.assertEqual(totals["taxes"], expected["taxes"])
                self.assertEqual(totals["grand"], expected["grand"])
                self.assertEqual(totals["prepayment"], expected["prepayment"])
                self.assertEqual(totals["due"], expected["due"])
                self.assertEqual(
                    context["repair_order"]["prepayment_display"], totals["prepayment_display"]
                )
                self.assertEqual(totals["base_total_display"], totals["subtotal_display"])
                self.assertEqual(totals["total_paid_display"], totals["prepayment_display"])

    def test_inspection_sheet_form_roundtrip_updates_preview(self) -> None:
        initial = self.service.get_inspection_sheet_form(self.card)
        self.assertIn("planned_works", initial["form"])
        self.assertIn("planned_work_rows", initial["form"])
        self.assertIn("planned_material_rows", initial["form"])

        saved = self.service.save_inspection_sheet_form(
            self.card,
            form_data={
                "client": "New client",
                "vehicle": "Mazda CX-3",
                "vin_or_plate": "DK5FW106086 ? A123AA124",
                "complaint_summary": "Suspension noise",
                "findings": "Stabilizer link play\nDamper leak",
                "recommendations": "Replace links\nCheck bushings",
                "planned_works": "Replace stabilizer links",
                "planned_materials": "Stabilizer link",
                "planned_work_rows": [
                    {"name": "Replace stabilizer links", "quantity": "1"},
                    {"name": "Check bushings", "quantity": "1"},
                ],
                "planned_material_rows": [
                    {"name": "Stabilizer link", "quantity": "2"},
                ],
                "master_comment": "Confirm estimate after inspection",
            },
            filled_by="admin",
            source="manual",
        )
        self.assertEqual(saved["form"]["client"], "New client")
        self.assertEqual(saved["meta"]["source"], "manual")
        self.assertEqual(saved["form"]["planned_work_rows"][0]["quantity"], "1")
        self.assertEqual(saved["form"]["planned_material_rows"][0]["name"], "Stabilizer link")

        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["inspection_sheet"],
            active_document_id="inspection_sheet",
        )
        html = preview["documents"][0]["pages"][0]["html"]
        self.assertIn("New client", html)
        self.assertIn("Mazda CX-3", html)
        self.assertIn("Suspension noise", html)
        self.assertIn("Stabilizer link play", html)
        self.assertIn("Replace stabilizer links", html)
        self.assertIn("Check bushings", html)
        self.assertIn("Stabilizer link", html)

    def test_template_engine_renders_list_item_fields_inside_sections(self) -> None:
        rendered = render_template(
            "{{#rows}}<li>{{text}}</li>{{/rows}}",
            {"rows": [{"text": "один"}, {"text": "два"}]},
        )
        self.assertEqual(rendered, "<li>один</li><li>два</li>")

    def test_template_crud_duplicate_default_and_delete(self) -> None:
        saved = self.service.save_template(
            document_type="repair_order",
            name="Мой шаблон",
            content='<div class="document-page"><h1>{{client.name_display}}</h1></div>',
        )
        template_id = saved["template"]["id"]
        self.assertTrue(template_id.startswith("custom:repair_order:"))

        duplicated = self.service.duplicate_template(template_id=template_id)
        duplicated_id = duplicated["template"]["id"]
        self.assertNotEqual(template_id, duplicated_id)

        defaulted = self.service.set_default_template(
            document_type="repair_order", template_id=duplicated_id
        )
        self.assertEqual(defaulted["template_id"], duplicated_id)
        self.assertTrue(any(item["is_default"] for item in defaulted["templates"]))

        deleted = self.service.delete_template(template_id=template_id)
        self.assertTrue(deleted["deleted"])

    def test_export_and_print_use_pdf_and_printer_backends(self) -> None:
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ) as render_pdf:
            pdf_bytes, file_name, meta = self.service.export_documents_pdf(
                self.card, selected_document_ids=["repair_order"]
            )
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

    def test_print_requires_printer_selection_when_direct_print_requested(self) -> None:
        with self.assertRaises(PrintModuleError) as context:
            self.service.print_documents(
                self.card,
                selected_document_ids=["repair_order"],
                printer_name="",
            )

        self.assertEqual(context.exception.code, "validation_error")
        self.assertIn("Не выбран принтер", context.exception.message)

    def test_pdf_renderer_falls_back_safely_from_worker_thread(self) -> None:
        result: dict[str, bytes] = {}

        def run() -> None:
            result["pdf"] = render_html_to_pdf_bytes("<h1>Worker thread</h1>")

        thread = threading.Thread(target=run, name="pdf-worker-test")
        thread.start()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertIn("pdf", result)
        self.assertTrue(result["pdf"].startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
