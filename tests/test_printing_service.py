from __future__ import annotations

# ruff: noqa: E402
import html
import json
import sys
import tempfile
import threading
import unittest
from decimal import Decimal

# ruff: noqa: E402
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card, ClientProfile
from minimal_kanban.printing import service as printing_service_module
from minimal_kanban.printing.defaults import PRINT_BASE_STYLES
from minimal_kanban.printing.models import SUPPORTED_PRINT_DOCUMENT_TYPES, PrintModuleSettings
from minimal_kanban.printing.pdf import (
    PdfRenderError,
    _ensure_qt_webengine_chromium_flags,
    _html_to_plain_text,
    _parse_json_object,
    _read_generated_pdf_bytes,
    _read_pdf_cli_stdin,
    render_html_to_pdf_bytes,
)
from minimal_kanban.printing.printers import _normalize_copy_count
from minimal_kanban.printing.service import (
    PrintModuleError,
    PrintModuleService,
    _balance_regulated_line_totals,
    _money_display,
    _money_words_display,
)
from minimal_kanban.printing.template_engine import TemplateRenderError, render_template


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


def build_cashless_prepayment_example_card() -> Card:
    return Card.from_dict(
        {
            "id": "card-print-payments-example",
            "vehicle": "Toyota Land Cruiser 200",
            "title": "Ремонт АКПП",
            "description": "Эталонный пример расчета предоплаты по безналу.",
            "column": "inbox",
            "archived": False,
            "created_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:30:00+00:00",
            "repair_order": {
                "number": "312",
                "date": "28.05.2026 10:00",
                "opened_at": "28.05.2026 10:00",
                "client": "ООО СК Бастион",
                "phone": "89048952630",
                "vehicle": "Toyota Land Cruiser 200",
                "license_plate": "М763НН124",
                "vin": "JTMHV05J504018876",
                "mileage": "",
                "payment_method": "cashless",
                "payments": [{"amount": "170000", "payment_method": "cashless"}],
                "reason": "Ремонт АКПП",
                "comment": "Проверка итоговой таблицы заказ-наряда.",
                "works": [
                    {"name": "Итого работы", "quantity": "1", "price": "197000", "total": ""},
                ],
                "materials": [
                    {"name": "Итого материалы", "quantity": "1", "price": "137545", "total": ""},
                ],
            },
        }
    )


def build_business_client() -> ClientProfile:
    return ClientProfile.from_dict(
        {
            "id": "client-print-ooo",
            "client_type": "ooo",
            "display_name": "ООО Контрагент",
            "legal_name": "ООО Контрагент",
            "short_name": "Контрагент",
            "phone": "+7 900 000-00-01",
            "email": "info@example.com",
            "inn": "2468000000",
            "kpp": "246801001",
            "ogrn": "1234567890123",
            "checking_account": "40702810900000000001",
            "bank_name": "Тест Банк",
            "bik": "044525225",
            "correspondent_account": "30101810400000000225",
            "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
            "actual_address": "660000, г. Красноярск, ул. Тестовая, 2",
            "contact_person": "Иванов Иван",
            "contact_position": "Директор",
        }
    )


def build_client_profile(
    *,
    legal_name: str,
    inn: str,
    kpp: str,
    legal_address: str,
) -> ClientProfile:
    return ClientProfile.from_dict(
        {
            "id": f"client-{inn}",
            "client_type": "company",
            "display_name": legal_name,
            "legal_name": legal_name,
            "short_name": legal_name,
            "inn": inn,
            "kpp": kpp,
            "legal_address": legal_address,
            "actual_address": legal_address,
        }
    )


def build_vat_5_regression_card() -> Card:
    return Card.from_dict(
        {
            "id": "synthetic-vat-5-regression",
            "vehicle": "Synthetic vehicle",
            "title": "Synthetic VAT 5% fixture",
            "description": "Synthetic multi-row VAT fixture.",
            "column": "inbox",
            "archived": False,
            "created_at": "2026-08-19T10:00:00+00:00",
            "updated_at": "2026-08-19T10:00:00+00:00",
            "repair_order": {
                "number": "SYN-VAT-5",
                "date": "19.08.2026",
                "payment_method": "cashless",
                "tax_label": "НДС (5%)",
                "works": [
                    {"name": "Synthetic row A", "quantity": "1", "price": "200000"},
                    {"name": "Synthetic row B", "quantity": "1", "price": "35700"},
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

    def test_print_settings_copies_reject_bool_fractional_and_non_finite_values(self) -> None:
        bad_values = (True, False, 1.5, float("inf"))

        for value in bad_values:
            with self.subTest(value=value):
                settings = PrintModuleSettings.from_dict({"copies": value})
                self.assertEqual(settings.copies, 1)

        self.assertEqual(PrintModuleSettings.from_dict({"copies": "3"}).copies, 3)
        self.assertEqual(PrintModuleSettings.from_dict({"copies": "9" * 80}).copies, 1)
        self.assertEqual(PrintModuleSettings.from_dict({"copies": "30"}).copies, 20)

    def test_printer_backend_copy_count_rejects_invalid_values(self) -> None:
        for value in (True, 1.5, float("inf"), "bad"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_copy_count(value), 1)
        self.assertEqual(_normalize_copy_count("9" * 80), 1)
        self.assertEqual(_normalize_copy_count("3"), 3)
        self.assertEqual(_normalize_copy_count("30"), 20)

    def test_manual_document_profile_uses_builtin_templates_without_card(self) -> None:
        profile = self.service.manual_document_profile(
            {
                "document_number": "INV-77",
                "document_date": "15.06.2026",
                "client": {
                    "client_type": "ooo",
                    "display_name": "ООО Ручной Клиент",
                    "legal_name": "ООО Ручной Клиент",
                    "inn": "2468000000",
                    "kpp": "246801001",
                    "checking_account": "40702810900000000001",
                    "bank_name": "Тест Банк",
                    "bik": "044525225",
                    "correspondent_account": "30101810400000000225",
                    "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
                    "contact_person": "Петров Петр",
                    "contact_position": "Директор",
                },
                "vehicle": {
                    "name": "Toyota Land Cruiser 200",
                    "vin": "JTMHV05J604123456",
                    "license_plate": "А777АА124",
                    "mileage": "188000",
                },
                "works": [
                    {"name": "Диагностика подвески", "quantity": "1", "price": "2500"},
                ],
                "materials": [
                    {"name": "Фильтр салона", "quantity": "2", "price": "900"},
                ],
                "payments": [
                    {
                        "amount": "1000",
                        "paid_at": "15.06.2026",
                        "payment_method": "cash",
                        "note": "Аванс",
                    }
                ],
                "reason": "Ручное оформление без карточки",
                "comment": "Комментарий клиента",
                "note": "Комментарий мастера",
            }
        )

        workspace = self.service.workspace(
            profile.card,
            repair_order=profile.card.repair_order,
        )
        preview = self.service.preview_documents(
            profile.card,
            repair_order=profile.card.repair_order,
            client=profile.client,
            selected_document_ids=[
                "repair_order",
                "vehicle_acceptance_act",
                "invoice",
                "invoice_factura",
                "upd",
                "inspection_sheet",
                "completion_act",
                "parts_sale",
            ],
            active_document_id="invoice",
        )

        self.assertTrue(workspace["meta"]["document_without_card"])
        self.assertEqual(
            workspace["meta"]["supported_document_types"], list(SUPPORTED_PRINT_DOCUMENT_TYPES)
        )
        self.assertEqual(preview["active_document_id"], "invoice")
        self.assertEqual(len(preview["documents"]), len(SUPPORTED_PRINT_DOCUMENT_TYPES))
        invoice_html = preview["documents"][2]["pages"][0]["html"]
        repair_order_html = preview["documents"][0]["pages"][0]["html"]
        self.assertIn("Счет на оплату", invoice_html)
        self.assertIn("ООО Ручной Клиент", invoice_html)
        self.assertIn("2468000000", invoice_html)
        self.assertIn("Toyota Land Cruiser 200", repair_order_html)
        self.assertIn("Диагностика подвески", repair_order_html)
        self.assertIn("Фильтр салона", repair_order_html)
        self.assertIn("5 058,82", invoice_html)
        self.assertIn("1 176,47", invoice_html)
        self.assertIn("3 882,35", invoice_html)
        self.assertEqual(preview["documents"][0]["template"]["source"], "builtin")

    def test_manual_inspection_sheet_drafts_are_isolated_by_document_identity(self) -> None:
        first = self.service.manual_document_profile(
            {
                "document_number": "MAN-1",
                "client": {"display_name": "Первый клиент"},
                "vehicle": {"name": "Toyota Camry"},
                "works": [{"name": "Первая работа", "quantity": "1", "price": "1000"}],
            }
        )
        second = self.service.manual_document_profile(
            {
                "document_number": "MAN-2",
                "client": {"display_name": "Второй клиент"},
                "vehicle": {"name": "Nissan X-Trail"},
                "works": [{"name": "Вторая работа", "quantity": "1", "price": "2000"}],
            }
        )

        self.service.save_inspection_sheet_form(
            first.card,
            repair_order=first.card.repair_order,
            form_data={"findings": "Утечка из первого документа"},
            filled_by="tester",
        )
        loaded = self.service.get_inspection_sheet_form(
            second.card,
            repair_order=second.card.repair_order,
        )

        self.assertEqual(loaded["form"]["client"], "Второй клиент")
        self.assertEqual(loaded["form"]["vehicle"], "Nissan X-Trail")
        self.assertNotIn("Утечка из первого документа", loaded["form"]["findings"])

    def test_manual_document_profile_parses_text_request_sections(self) -> None:
        profile = self.service.manual_document_profile(
            request_text=(
                "Счет № TXT-500 от 15.06.2026\n"
                "Клиент: ООО Текстовый Клиент\n"
                "Телефон: +7 391 200-00-00\n"
                "ИНН: 2468123456\n"
                "КПП: 246801001\n"
                "Банк: Текст Банк\n"
                "БИК: 040407777\n"
                "Р/с: 40702810900000000999\n"
                "К/с: 30101810400000000777\n"
                "НДС: Без НДС\n"
                "Адрес: 660000, Красноярск, ул. Ручная, 5\n"
                "Автомобиль: Lexus RX200t\n"
                "Госномер: Т555ТТ124\n"
                "VIN: JTJBARBZ502123456\n"
                "Пробег: 123000\n"
                "Работы:\n"
                "Диагностика 1 x 2500\n"
                "Замена масла 1 x 1200\n"
                "Материалы:\n"
                "Масло 5 x 900\n"
                "Оплаты:\n"
                "1000 | 15.06.2026 | cash | Аванс\n"
                "Комментарий: оформить без карточки CRM."
            )
        )

        order = profile.card.repair_order
        self.assertEqual(order.number, "TXT-500")
        self.assertEqual(order.date, "15.06.2026")
        self.assertEqual(order.tax_label, "Без НДС")
        self.assertEqual(order.client, "ООО Текстовый Клиент")
        self.assertEqual(order.phone, "+7 391 200-00-00")
        self.assertEqual(order.vehicle, "Lexus RX200t")
        self.assertEqual(order.license_plate, "т555тт124")
        self.assertEqual(order.vin, "JTJBARBZ502123456")
        self.assertEqual(order.mileage, "123000")
        self.assertEqual([row.name for row in order.works], ["Диагностика", "Замена масла"])
        self.assertEqual([row.name for row in order.materials], ["Масло"])
        self.assertEqual(order.payments[0].amount, "1000")
        self.assertEqual(profile.client.inn if profile.client else "", "2468123456")
        self.assertEqual(profile.client.bank_name if profile.client else "", "Текст Банк")

    def test_manual_document_profile_keeps_text_request_when_ui_form_is_blank(self) -> None:
        profile = self.service.manual_document_profile(
            {
                "client": {"display_name": "", "inn": ""},
                "vehicle": {"name": "", "vin": ""},
                "works": [],
                "materials": [],
                "payments": [],
                "comment": "",
            },
            request_text=(
                "Клиент: ООО Из Текста\n"
                "ИНН: 2468555444\n"
                "Автомобиль: Subaru Forester\n"
                "VIN: JF1SJ5LC5FG123456\n"
                "Работы:\n"
                "Диагностика 1 x 3000\n"
            ),
        )

        self.assertEqual(profile.card.repair_order.client, "ООО Из Текста")
        self.assertEqual(profile.card.repair_order.vehicle, "Subaru Forester")
        self.assertEqual(profile.card.repair_order.vin, "JF1SJ5LC5FG123456")
        self.assertEqual([row.name for row in profile.card.repair_order.works], ["Диагностика"])
        self.assertEqual(profile.client.inn if profile.client else "", "2468555444")

    def test_manual_document_profile_preserves_explicit_zero_line_item_values(self) -> None:
        profile = self.service.manual_document_profile(
            {
                "client": {"display_name": "ООО Нулевые Значения"},
                "works": [{"name": "Гарантийная проверка", "quantity": 0, "price": 0, "total": 0}],
                "materials": [{"name": "Крепеж", "quantity": 0, "price": 0}],
            }
        )

        work = profile.card.repair_order.works[0]
        material = profile.card.repair_order.materials[0]

        self.assertEqual(work.quantity, "0")
        self.assertEqual(work.price, "0")
        self.assertEqual(work.total, "0")
        self.assertEqual(material.quantity, "0")
        self.assertEqual(material.price, "0")

    def test_manual_invoice_can_render_without_vat(self) -> None:
        profile = self.service.manual_document_profile(
            {
                "document_number": "NO-VAT-1",
                "document_date": "15.06.2026",
                "tax_label": "Без НДС",
                "client": {"display_name": "ООО Без НДС", "inn": "2468000000"},
                "vehicle": {"name": "Toyota Camry", "vin": "JTNB11HK203123456"},
                "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
            }
        )

        preview = self.service.preview_documents(
            profile.card,
            repair_order=profile.card.repair_order,
            client=profile.client,
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]
        context = self.service._build_document_context(
            profile.card,
            profile.card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=self.service._read_settings(),
            client=profile.client,
        )

        self.assertIn("<td>Налоговый режим</td><td>Без НДС</td>", html)
        self.assertNotIn("В том числе НДС (5%)", html)
        self.assertEqual(context["invoice"]["vat"], Decimal("0.00"))
        self.assertFalse(context["invoice"]["has_vat"])

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

    def test_brand_logo_reader_ignores_oversized_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logo_path = Path(temp_dir) / "logo.png"
            logo_path.write_bytes(b"x" * 16)

            printing_service_module._brand_logo_data_uri.cache_clear()
            with (
                patch.object(printing_service_module, "_BRAND_LOGO_PATH", logo_path),
                patch.object(printing_service_module, "PRINT_BRAND_LOGO_MAX_BYTES", 8),
            ):
                self.assertEqual(printing_service_module._brand_logo_data_uri(), "")
            printing_service_module._brand_logo_data_uri.cache_clear()

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
        self.assertIn("налоги и сборы 15%", preview["documents"][0]["pages"][0]["html"])
        self.assertIn(
            "Стоимость заказ-наряда по безналичному расчету",
            preview["documents"][0]["pages"][0]["html"],
        )
        self.assertIn("13 800 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("16 235,29 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("Предоплата по безналу", preview["documents"][0]["pages"][0]["html"])
        self.assertIn("1 000 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertIn(
            "Доплата по безналичному расчету",
            preview["documents"][0]["pages"][0]["html"],
        )
        self.assertIn("15 235,29 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertIn(
            "Доплата по наличному расчету",
            preview["documents"][0]["pages"][0]["html"],
        )
        self.assertIn("12 950 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertNotIn("13 800,00 ₽", preview["documents"][0]["pages"][0]["html"])
        self.assertNotIn("Итого по заказ-наряду", preview["documents"][0]["pages"][0]["html"])
        self.assertTrue(
            any(
                "Гарантийные и важные условия" in page["html"]
                for page in preview["documents"][0]["pages"]
            )
        )
        self.assertIn(
            "<strong>Хранение и выдача:</strong> после уведомления о готовности первые 2 дня хранения бесплатные, далее стоимость хранения составляет 150 рублей в сутки.",
            "".join(page["html"] for page in preview["documents"][0]["pages"]),
        )
        self.assertIn(
            "6 месяцев", "".join(page["html"] for page in preview["documents"][0]["pages"])
        )
        self.assertIn("1000 км", "".join(page["html"] for page in preview["documents"][0]["pages"]))
        self.assertIn(
            "20-30 тыс. км", "".join(page["html"] for page in preview["documents"][0]["pages"])
        )
        self.assertIn(
            "контрактные", "".join(page["html"] for page in preview["documents"][0]["pages"])
        )
        self.assertIn("Всего к оплате", preview["documents"][1]["pages"][0]["html"])
        self.assertIn("Предоплата", preview["documents"][1]["pages"][0]["html"])
        self.assertIn("15 235,29", preview["documents"][1]["pages"][0]["html"])
        self.assertIn("Сумма прописью", preview["documents"][1]["pages"][0]["html"])
        self.assertEqual(preview["documents"][0]["missing_fields"], [])

    def test_preview_accepts_scalar_document_id_and_ignores_malformed_template_maps(
        self,
    ) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids="invoice",
            active_document_id="invoice",
            selected_template_ids=["not-a-map"],
            template_overrides=["not-a-map"],
        )

        self.assertEqual(preview["active_document_id"], "invoice")
        self.assertEqual([item["id"] for item in preview["documents"]], ["invoice"])
        self.assertIn("Счет на оплату", preview["documents"][0]["pages"][0]["html"])

    def test_template_overrides_are_filtered_by_supported_document_type(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice"],
            active_document_id="invoice",
            template_overrides={
                "invoice": '<div class="document-page"><h1>Черновой шаблон</h1></div>',
                "unknown": '<div class="document-page"><h1>Wrong</h1></div>',
            },
        )

        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Черновой шаблон", html)
        self.assertNotIn("Wrong", html)

    def test_deeply_nested_template_override_returns_template_error(self) -> None:
        deep_template = "{{#card}}" * 100 + "x" + "{{/card}}" * 100

        with self.assertRaises(PrintModuleError) as context:
            self.service.preview_documents(
                self.card,
                selected_document_ids=["repair_order"],
                active_document_id="repair_order",
                template_overrides={"repair_order": deep_template},
            )

        self.assertEqual(context.exception.code, "template_error")
        self.assertIn("Слишком глубокая вложенность", context.exception.message)

    def test_oversized_template_override_is_rejected_without_truncation(self) -> None:
        with patch("minimal_kanban.printing.service.PRINT_TEMPLATE_CONTENT_MAX_CHARS", 32):
            with self.assertRaises(PrintModuleError) as context:
                self.service.preview_documents(
                    self.card,
                    selected_document_ids=["invoice"],
                    active_document_id="invoice",
                    template_overrides={"invoice": "<div>" + ("x" * 64) + "</div>"},
                )

        self.assertEqual(context.exception.code, "validation_error")
        self.assertEqual(context.exception.details["max_size_chars"], 32)

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
        self.assertIn("Администратор", document["pages"][1]["html"])
        self.assertIn("Клиент", document["pages"][1]["html"])

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
        self.assertIn("В том числе НДС (5%)", html)
        self.assertIn("Сумма прописью", html)
        self.assertIn("Всего к оплате", html)
        self.assertIn("Предоплата", html)
        self.assertIn("2 941,18", html)
        self.assertIn("16 235,29", html)
        self.assertIn("1 000,00", html)
        self.assertIn("15 235,29", html)
        self.assertIn("773,11", html)
        self.assertNotIn("13 800,00", html)
        self.assertIn("Руководитель", html)
        self.assertIn("Бухгалтер", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_invoice_context_uses_cashless_prices_and_included_vat(self) -> None:
        context = self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=self.service._read_settings(),
        )

        invoice = context["invoice"]
        invoice_items = invoice["line_items"]

        self.assertEqual(invoice["total"], Decimal("16235.29"))
        self.assertEqual(invoice["total_display"], "16 235,29")
        self.assertEqual(invoice["vat"], Decimal("773.11"))
        self.assertEqual(invoice["vat_display"], "773,11")
        self.assertEqual(invoice["subtotal"], Decimal("16235.29"))
        self.assertEqual(invoice["prepayment"], Decimal("1000"))
        self.assertEqual(invoice["prepayment_display"], "1 000,00")
        self.assertEqual(invoice["amount_due"], Decimal("15235.29"))
        self.assertEqual(invoice["amount_due_display"], "15 235,29")
        self.assertTrue(invoice["has_prepayment"])
        self.assertEqual(context["line_items"], invoice_items)
        self.assertEqual(invoice_items[0]["name"], "Диагностика АКПП")
        self.assertEqual(invoice_items[0]["price"], Decimal("2941.18"))
        self.assertEqual(invoice_items[0]["total"], Decimal("2941.18"))
        self.assertEqual(invoice_items[0]["price_display"], "2 941,18")
        self.assertEqual(invoice_items[0]["total_display"], "2 941,18")
        self.assertEqual(invoice_items[2]["name"], "ATF")
        self.assertEqual(invoice_items[2]["price"], Decimal("1117.65"))
        self.assertEqual(invoice_items[2]["total"], Decimal("6705.88"))
        self.assertEqual(invoice_items[2]["price_display"], "1 117,65")
        self.assertEqual(invoice_items[2]["total_display"], "6 705,88")

    def test_vat_5_regression_reconciles_invoice_and_regulated_documents(self) -> None:
        card = build_vat_5_regression_card()
        settings = self.service._read_settings()
        expected_total = Decimal("277294.12")
        expected_subtotal = Decimal("264089.64")
        expected_vat = Decimal("13204.48")

        invoice_context = self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=settings,
        )["invoice"]
        self.assertEqual(invoice_context["total"], expected_total)
        self.assertEqual(invoice_context["vat"], expected_vat)
        self.assertEqual(invoice_context["total"] - invoice_context["vat"], expected_subtotal)

        for document_id in ("invoice_factura", "upd"):
            context = self.service._build_document_context(
                card,
                card.repair_order,
                document=self.service._document_definition(document_id),
                settings=settings,
            )["regulated"]
            self.assertEqual(context["subtotal"], expected_subtotal)
            self.assertEqual(context["vat"], expected_vat)
            self.assertEqual(context["total_with_tax"], expected_total)
            self.assertEqual(context["subtotal"] + context["vat"], context["total_with_tax"])
            self.assertEqual(
                [(row["subtotal"], row["vat"], row["total_with_tax"]) for row in context["rows"]],
                [
                    (Decimal("224089.64"), Decimal("11204.48"), Decimal("235294.12")),
                    (Decimal("40000.00"), Decimal("2000.00"), Decimal("42000.00")),
                ],
            )
            self.assertEqual(
                [row["price"] for row in context["rows"]],
                [Decimal("224089.64"), Decimal("40000.00")],
            )
            self.assertEqual([row["quantity"] for row in context["rows"]], ["1", "1"])

        for document_id in ("invoice", "invoice_factura", "upd"):
            preview = self.service.preview_documents(
                card,
                selected_document_ids=[document_id],
                active_document_id=document_id,
            )
            html_text = "".join(page["html"] for page in preview["documents"][0]["pages"])
            self.assertIn("13 204,48", html_text)
            self.assertNotIn("13 864,71", html_text)
            self.assertNotIn("13 864,72", html_text)
            self.assertIn("277 294,12", html_text)

        rendered_html: list[str] = []

        def capture_render(html_text: str, **_kwargs: object) -> bytes:
            rendered_html.append(html_text)
            return b"%PDF-1.4 synthetic-vat-5"

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            side_effect=capture_render,
        ):
            self.service.export_documents_pdf(
                card,
                selected_document_ids=["invoice", "invoice_factura", "upd"],
            )
        self.assertEqual(len(rendered_html), 1)
        self.assertIn("13 204,48", rendered_html[0])
        self.assertNotIn("13 864,71", rendered_html[0])

    def test_vat_5_document_rounding_cent_is_balanced_to_invoice(self) -> None:
        settings = self.service._read_settings()
        invoice = self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=settings,
        )["invoice"]
        self.assertEqual(invoice["total"], Decimal("16235.29"))
        self.assertEqual(invoice["vat"], Decimal("773.11"))

        for document_id in ("invoice_factura", "upd"):
            regulated = self.service._build_document_context(
                self.card,
                self.card.repair_order,
                document=self.service._document_definition(document_id),
                settings=settings,
            )["regulated"]
            self.assertEqual(regulated["total_with_tax"], invoice["total"])
            self.assertEqual(regulated["vat"], invoice["vat"])
            self.assertEqual(regulated["subtotal"], Decimal("15462.18"))
            self.assertEqual(regulated["subtotal"] + regulated["vat"], regulated["total_with_tax"])
            self.assertEqual(
                sum((row["vat"] for row in regulated["rows"]), Decimal("0")),
                invoice["vat"],
            )
            self.assertEqual(regulated["rows"][-1]["subtotal"], Decimal("2352.94"))
            self.assertEqual(regulated["rows"][-1]["vat"], Decimal("117.64"))

    def test_regulated_vat_balancing_never_creates_negative_rows(self) -> None:
        rows = [
            {
                "name": f"row-{index}",
                "quantity": "1",
                "subtotal": Decimal("0.01"),
                "vat": Decimal("0.00"),
                "total_with_tax": Decimal("0.01"),
            }
            for index in range(150)
        ]

        balanced = _balance_regulated_line_totals(
            rows,
            target_total=Decimal("1.50"),
            target_vat=Decimal("0.07"),
            tax_rate=Decimal("0.05"),
        )

        self.assertEqual(
            sum((row["total_with_tax"] for row in balanced), Decimal("0")),
            Decimal("1.50"),
        )
        self.assertEqual(sum((row["vat"] for row in balanced), Decimal("0")), Decimal("0.07"))
        self.assertEqual(
            sum((row["subtotal"] for row in balanced), Decimal("0")),
            Decimal("1.43"),
        )
        self.assertTrue(
            all(
                row["subtotal"] >= Decimal("0")
                and row["vat"] >= Decimal("0")
                and row["subtotal"] + row["vat"] == row["total_with_tax"]
                and row["quantity"] == "1"
                for row in balanced
            )
        )

        reduced = _balance_regulated_line_totals(
            rows,
            target_total=Decimal("0.50"),
            target_vat=Decimal("0.02"),
            tax_rate=Decimal("0.05"),
        )
        self.assertEqual(
            sum((row["total_with_tax"] for row in reduced), Decimal("0")),
            Decimal("0.50"),
        )
        self.assertEqual(sum((row["vat"] for row in reduced), Decimal("0")), Decimal("0.02"))
        self.assertTrue(
            all(
                row["subtotal"] >= Decimal("0")
                and row["vat"] >= Decimal("0")
                and row["subtotal"] + row["vat"] == row["total_with_tax"]
                for row in reduced
            )
        )

    def test_regulated_vat_supports_single_empty_and_fractional_quantity_rows(self) -> None:
        settings = self.service._read_settings()
        cases = (
            ([{"name": "single", "quantity": "2", "price": "100"}], Decimal("235.29")),
            ([{"name": "fractional", "quantity": "0,5", "price": "100"}], Decimal("58.82")),
            ([], Decimal("0.00")),
        )
        for index, (works, expected_total) in enumerate(cases):
            with self.subTest(index=index):
                card = Card.from_dict(
                    {
                        "id": f"vat-quantity-{index}",
                        "title": "VAT quantity fixture",
                        "column": "inbox",
                        "repair_order": {
                            "number": f"VAT-QTY-{index}",
                            "payment_method": "cashless",
                            "tax_label": "НДС 5%",
                            "works": works,
                        },
                    }
                )
                regulated = self.service._build_document_context(
                    card,
                    card.repair_order,
                    document=self.service._document_definition("upd"),
                    settings=settings,
                )["regulated"]
                self.assertEqual(regulated["total_with_tax"], expected_total)
                self.assertEqual(regulated["subtotal"] + regulated["vat"], expected_total)
                self.assertEqual(
                    sum((row["vat"] for row in regulated["rows"]), Decimal("0")),
                    regulated["vat"],
                )
                self.assertEqual(
                    sum((row["subtotal"] for row in regulated["rows"]), Decimal("0")),
                    regulated["subtotal"],
                )

    def test_regulated_vat_handles_empty_label_no_vat_and_zero_rate(self) -> None:
        settings = self.service._read_settings()
        expected = {
            "": (True, Decimal("5.60")),
            "Без НДС": (False, Decimal("0.00")),
            "НДС 0%": (False, Decimal("0.00")),
        }
        for label, (has_vat, vat) in expected.items():
            with self.subTest(label=label):
                card = Card.from_dict(
                    {
                        "id": f"vat-label-{label}",
                        "title": "VAT label fixture",
                        "column": "inbox",
                        "repair_order": {
                            "number": "VAT-LABEL",
                            "payment_method": "cashless",
                            "tax_label": label,
                            "works": [{"name": "row", "quantity": "1", "price": "100"}],
                        },
                    }
                )
                context = self.service._build_document_context(
                    card,
                    card.repair_order,
                    document=self.service._document_definition("upd"),
                    settings=settings,
                )
                self.assertEqual(context["regulated"]["vat"], vat)
                self.assertEqual(context["regulated"]["has_vat"], has_vat)
                self.assertEqual(
                    context["regulated"]["subtotal"] + context["regulated"]["vat"],
                    context["regulated"]["total_with_tax"],
                )

    def test_invoice_template_renders_linked_client_requisites(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            client=build_business_client(),
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Реквизиты покупателя", html)
        self.assertIn("ООО Контрагент", html)
        self.assertIn("2468000000", html)
        self.assertIn("246801001", html)
        self.assertIn("40702810900000000001", html)
        self.assertIn("Тест Банк", html)
        self.assertIn("660000, г. Красноярск, ул. Тестовая, 1", html)
        self.assertIn("Иванов Иван", html)
        self.assertIn("Директор", html)
        self.assertIn("info@example.com", html)
        self.assertNotIn("Реквизиты клиента не указаны", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_invoice_factura_template_renders_regulated_header_and_totals(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice_factura"],
            active_document_id="invoice_factura",
        )

        document = preview["documents"][0]
        html = document["pages"][0]["html"]
        self.assertIn("Счет-фактура", html)
        self.assertIn("Приложение № 1 к постановлению Правительства Российской Федерации", html)
        self.assertIn("319246800097453, 05.08.2019", html)
        self.assertIn("Налоговая ставка", html)
        self.assertIn("5%", html)
        self.assertIn("(5б)", html)
        self.assertIn("15 462,18", html)
        self.assertIn("2 941,18", html)
        self.assertIn("773,11", html)
        self.assertIn("16 235,29", html)
        self.assertIn("Руководитель организации", html)
        self.assertIn("Индивидуальный предприниматель", html)
        self.assertNotIn("Бухгалтерский документ", html)
        self.assertNotIn("Номенклатура", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_invoice_factura_matches_regulated_sample_layout_and_requisites(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            client=build_business_client(),
            selected_document_ids=["invoice_factura"],
            active_document_id="invoice_factura",
        )

        html = preview["documents"][0]["pages"][0]["html"]
        self.assertIn("Приложение № 1 к постановлению Правительства Российской Федерации", html)
        self.assertIn("Счет-фактура №", html)
        self.assertIn("Исправление №", html)
        self.assertIn("Грузоотправитель и его адрес", html)
        self.assertIn("Грузополучатель и его адрес", html)
        self.assertIn("К платежно-расчетному документу", html)
        self.assertIn("Документ об отгрузке", html)
        self.assertIn("К счету-фактуре", html)
        self.assertIn('class="regulated-wide-line"', html)
        self.assertIn("ИНН/КПП покупателя", html)
        self.assertIn("ООО Контрагент", html)
        self.assertIn("2468000000 / 246801001", html)
        self.assertIn("246413435608", html)
        self.assertNotIn("не применяется для ИП", html)
        self.assertIn("КРАЙ КРАСНОЯРСКИЙ, ГОРОД КРАСНОЯРСК", html)
        self.assertIn("Российский рубль, 643", html)
        self.assertIn(
            "Наименование товара (описание выполненных работ, оказанных услуг), имущественного права",
            html,
        )
        self.assertIn("В том числе сумма акциза", html)
        self.assertIn("Налоговая ставка", html)
        self.assertIn("Сумма<br>налога, предъявля-<br>емая покупателю", html)
        self.assertIn(
            "Стоимость товаров (работ, услуг), имущественных прав с налогом - всего", html
        )
        self.assertIn("Страна происхождения товара", html)
        self.assertIn(
            "Регистрационный номер декларации на товары или регистрационный номер партии товара, подлежащего прослеживаемости",
            html,
        )
        self.assertIn(
            "Идентификатор государственного контракта, договора (соглашения) (при наличии)", html
        )
        self.assertIn("предъявля-", html)
        self.assertIn("цифро-", html)
        self.assertIn(
            "основной государственный регистрационный номер индивидуального предпринимателя и дата присвоения такого номера",
            html,
        )
        self.assertIn("Без акциза", html)
        self.assertIn(">н/ч<", html)
        self.assertIn('<td class="regulated-center">X</td>', html)
        self.assertIn("Итого", html)
        self.assertIn("Всего к оплате", html)
        self.assertIn(">он же<", html)
        self.assertIn("Индивидуальный предприниматель Гришкявичус Константин Владиславович", html)
        self.assertIn("319246800097453, 05.08.2019", html)
        self.assertNotIn("Бухгалтерский документ", html)
        self.assertNotIn("Номенклатура", html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_invoice_factura_can_match_uploaded_sample_values_from_repair_order_card(
        self,
    ) -> None:
        card = Card.from_dict(
            {
                "id": "card-schet-faktura-sample",
                "vehicle": "",
                "title": "Счет-фактура",
                "description": "",
                "column": "done",
                "archived": False,
                "created_at": "2026-05-20T12:00:00+00:00",
                "updated_at": "2026-05-20T12:00:00+00:00",
                "repair_order": {
                    "number": "268",
                    "date": "20.05.2026 12:00",
                    "opened_at": "20.05.2026 12:00",
                    "client": 'ООО "КРАМЗ"',
                    "payment_method": "cashless",
                    "tax_label": "НДС 5%",
                    "works": [
                        {
                            "name": "Замена заднего левого фонаря",
                            "quantity": "1",
                            "price": "1070",
                            "total": "",
                        },
                    ],
                    "materials": [
                        {
                            "name": "Фонарь задний левый",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "12384",
                            "total": "",
                        },
                    ],
                },
            }
        )
        client = build_client_profile(
            legal_name='ООО "КРАМЗ"',
            inn="2465043748",
            kpp="246501001",
            legal_address=(
                "660111, Красноярский край, Г.О. ГОРОД КРАСНОЯРСК, "
                "Г КРАСНОЯРСК, УЛ ПОГРАНИЧНИКОВ, ЗД. 42"
            ),
        )

        preview = self.service.preview_documents(
            card,
            client=client,
            selected_document_ids=["invoice_factura"],
            active_document_id="invoice_factura",
        )

        text = html.unescape(preview["documents"][0]["pages"][0]["html"])
        expected_fragments = [
            "Счет-фактура №",
            "268",
            "20 мая 2026 г.",
            'ООО "КРАМЗ"',
            "2465043748 / 246501001",
            "Замена заднего левого фонаря",
            "Фонарь задний левый",
            ">н/ч<",
            ">796<",
            ">шт<",
            "1 258,82",
            "14 569,42",
            "15 074,51",
            "59,94",
            "693,79",
            "753,73",
            "15 828,24",
            "regulated-req-payment-grid",
            "Счет-фактура № 268 от 20.05.2026 страница 1 из 1",
        ]
        for fragment in expected_fragments:
            self.assertIn(fragment, text)
        self.assertNotIn(
            '<td class="regulated-req-value">№ 268 от 20.05.2026</td>',
            preview["documents"][0]["pages"][0]["html"],
        )

    def test_upd_template_renders_two_page_regulated_sample_from_card_and_client(self) -> None:
        preview = self.service.preview_documents(
            self.card,
            client=build_business_client(),
            selected_document_ids=["upd"],
            active_document_id="upd",
        )

        document = preview["documents"][0]
        self.assertEqual(document["id"], "upd")
        self.assertEqual(document["page_count"], 2)
        first_page_html = document["pages"][0]["html"]
        second_page_html = document["pages"][1]["html"]
        combined_html = first_page_html + second_page_html
        self.assertIn("Универсальный передаточный документ", first_page_html)
        self.assertIn("Статус:", first_page_html)
        self.assertIn("1 - счет-фактура и передаточный документ (акт)", first_page_html)
        self.assertIn("Счет-фактура №", first_page_html)
        self.assertIn("Документ об отгрузке", first_page_html)
        self.assertIn('class="regulated-wide-line"', first_page_html)
        self.assertIn("ООО Контрагент", first_page_html)
        self.assertIn("2468000000 / 246801001", first_page_html)
        self.assertIn("Российский рубль, 643", first_page_html)
        self.assertIn("УПД № 12", second_page_html)
        self.assertIn("Документ составлен на", second_page_html)
        self.assertIn("Всего к оплате", second_page_html)
        self.assertIn("Основание передачи (сдачи) / получения (приемки)", second_page_html)
        self.assertIn("Счет на оплату №12", second_page_html)
        self.assertIn("[16]", second_page_html)
        self.assertIn("[19]", second_page_html)
        self.assertIn("ИНН 2468000000, КПП 246801001", second_page_html)
        self.assertIn("может не заполняться при проставлении печати", second_page_html)
        self.assertIn("(договор; доверенность и др.)", second_page_html)
        self.assertIn("транспортная накладная, поручение экспедитору", second_page_html)
        self.assertIn(
            "основной государственный регистрационный номер индивидуального предпринимателя и дата присвоения такого номера",
            second_page_html,
        )
        self.assertNotIn("319246800097453, 05.08.2019", second_page_html)
        self.assertIn("Индивидуальный предприниматель", second_page_html)
        self.assertIn(
            "Товар (груз) передал / услуги, результаты работ, права сдал", second_page_html
        )
        self.assertIn(
            "Товар (груз) получил / услуги, результаты работ, права принял", second_page_html
        )
        self.assertIn(
            "Ответственный за правильность оформления факта хозяйственной жизни", second_page_html
        )
        self.assertIn(
            "Наименование экономического субъекта составителя документа",
            second_page_html,
        )
        self.assertIn("М.П.", second_page_html)
        self.assertIn("Диагностика АКПП", combined_html)
        self.assertIn("ATF", combined_html)
        self.assertNotIn("продолжение передаточной части", second_page_html)
        self.assertNotIn("Передача на территории сервиса", combined_html)
        self.assertNotIn(
            '<td class="regulated-line-cell">Индивидуальный предприниматель или иное уполномоченное лицо</td>',
            second_page_html,
        )
        self.assertNotIn("undefined", combined_html)
        self.assertNotIn("NaN", combined_html)

    def test_upd_can_match_uploaded_sample_values_from_repair_order_card(self) -> None:
        card = Card.from_dict(
            {
                "id": "card-upd-sample",
                "vehicle": "",
                "title": "УПД",
                "description": "",
                "column": "done",
                "archived": False,
                "created_at": "2026-05-08T12:00:00+00:00",
                "updated_at": "2026-05-08T12:00:00+00:00",
                "repair_order": {
                    "number": "169",
                    "date": "08.05.2026 12:00",
                    "opened_at": "08.05.2026 12:00",
                    "client": 'ЗАО "ВЕАЛ"',
                    "payment_method": "cashless",
                    "tax_label": "НДС 5%",
                    "works": [
                        {
                            "name": "Замена коренного сальника",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "19714.29",
                            "total": "",
                        },
                        {
                            "name": "Диагностика автоэлектрика",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "2190.48",
                            "total": "",
                        },
                        {
                            "name": "Замена парктроника переднего левого",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "3285.71",
                            "total": "",
                        },
                        {
                            "name": "Доставка",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "1095.24",
                            "total": "",
                        },
                    ],
                    "materials": [
                        {
                            "name": "Сальник коленвала задний",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "8542.86",
                            "total": "",
                        },
                        {
                            "name": "Расходные материалы Motul 8100 X-Clean Gen2 5W40",
                            "inventory_unit": "л",
                            "quantity": "3",
                            "price": "1924.33",
                            "total": "5773",
                        },
                        {
                            "name": "Расходные материалы",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "2190.48",
                            "total": "",
                        },
                        {
                            "name": "Парктроник передний левый центральный",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "1642.86",
                            "total": "",
                        },
                    ],
                },
            }
        )
        client = build_client_profile(
            legal_name='ЗАО "ВЕАЛ"',
            inn="2466080950",
            kpp="246601001",
            legal_address="660049, КРАСНОЯРСКИЙ КРАЙ, Г. КРАСНОЯРСК, ПР-КТ МИРА, Д. 30, ОФИС 502",
        )

        preview = self.service.preview_documents(
            card,
            client=client,
            selected_document_ids=["upd"],
            active_document_id="upd",
        )

        text = html.unescape("".join(page["html"] for page in preview["documents"][0]["pages"]))
        expected_fragments = [
            "Универсальный передаточный документ",
            "Счет-фактура №",
            "169",
            "08 мая 2026 г.",
            'ЗАО "ВЕАЛ"',
            "2466080950 / 246601001",
            "Универсальный передаточный документ №169 от 08.05.2026",
            "regulated-req-payment-grid",
            "Замена коренного сальника",
            "Диагностика автоэлектрика",
            "Замена парктроника переднего левого",
            "Доставка",
            "Сальник коленвала задний",
            "Расходные материалы Motul 8100 X-Clean Gen2 5W40",
            "Расходные материалы",
            "Парктроник передний левый центральный",
            "23 193,28",
            "22 088,84",
            "1 104,44",
            "23 193,28",
            "2 577,04",
            "2 454,32",
            "122,72",
            "2 577,04",
            "3 865,54",
            "3 681,47",
            "184,07",
            "3 865,54",
            "1 288,52",
            "1 227,16",
            "61,36",
            "1 288,52",
            "10 050,42",
            "9 571,83",
            "478,59",
            "10 050,42",
            "2 156,11",
            "6 468,34",
            "323,42",
            "6 791,76",
            "2 577,04",
            "2 454,32",
            "122,72",
            "2 577,04",
            "1 932,78",
            "1 840,75",
            "92,03",
            "1 932,78",
            "49 787,03",
            "2 489,35",
            "52 276,38",
            "Счет на оплату №169 от 08.05.2026",
            "УПД № 169 от 08.05.2026 страница 2 из 2",
        ]
        for fragment in expected_fragments:
            self.assertIn(fragment, text)

    def test_regulated_work_unit_can_be_set_explicitly_for_upd_rows(self) -> None:
        card = Card.from_dict(
            {
                "id": "card-upd-work-unit",
                "vehicle": "",
                "title": "УПД",
                "description": "",
                "column": "done",
                "archived": False,
                "created_at": "2026-05-08T10:00:00+00:00",
                "updated_at": "2026-05-08T10:00:00+00:00",
                "repair_order": {
                    "number": "169",
                    "date": "08.05.2026 12:00",
                    "opened_at": "08.05.2026 12:00",
                    "client": 'ЗАО "ВЕАЛ"',
                    "payment_method": "cashless",
                    "tax_label": "НДС 5%",
                    "works": [
                        {
                            "name": "Замена коренного сальника",
                            "inventory_unit": "шт",
                            "quantity": "1",
                            "price": "19714.29",
                            "total": "",
                        },
                    ],
                    "materials": [],
                },
            }
        )
        preview = self.service.preview_documents(
            card,
            client=build_business_client(),
            selected_document_ids=["upd"],
            active_document_id="upd",
        )

        html = preview["documents"][0]["pages"][0]["html"]
        self.assertIn('<td class="regulated-center">796</td>', html)
        self.assertIn('<td class="regulated-center">шт</td>', html)

    def test_regulated_documents_preserve_explicit_line_total_for_rounding_kopeck(
        self,
    ) -> None:
        card = Card.from_dict(
            {
                "id": "card-regulated-rounded-total",
                "vehicle": "",
                "title": "УПД",
                "description": "",
                "column": "done",
                "archived": False,
                "created_at": "2026-05-08T10:00:00+00:00",
                "updated_at": "2026-05-08T10:00:00+00:00",
                "repair_order": {
                    "number": "169",
                    "date": "08.05.2026 12:00",
                    "opened_at": "08.05.2026 12:00",
                    "client": 'ЗАО "ВЕАЛ"',
                    "payment_method": "cashless",
                    "tax_label": "НДС 5%",
                    "works": [],
                    "materials": [
                        {
                            "name": "Расходные материалы Motul 8100 X-Clean Gen2 5W40",
                            "inventory_unit": "л",
                            "quantity": "3",
                            "price": "1924.33",
                            "total": "5773",
                        },
                    ],
                },
            }
        )
        self.assertEqual(card.repair_order.materials[0].total, "5773")

        preview = self.service.preview_documents(
            card,
            client=build_business_client(),
            selected_document_ids=["invoice_factura", "upd"],
            active_document_id="upd",
        )

        combined_html = "".join(
            page["html"] for document in preview["documents"] for page in document["pages"]
        )
        self.assertIn("Расходные материалы Motul 8100 X-Clean Gen2 5W40", combined_html)
        self.assertIn('<td class="regulated-center">112</td>', combined_html)
        self.assertIn('<td class="regulated-center">л</td>', combined_html)
        self.assertIn("2 156,11", combined_html)
        self.assertIn("6 791,76", combined_html)
        self.assertIn("323,42", combined_html)
        self.assertIn("6 791,76", combined_html)

    def test_regulated_documents_do_not_require_vehicle_contact_fields(self) -> None:
        card = build_card()
        card.repair_order.phone = ""
        card.repair_order.vehicle = ""
        card.repair_order.vin = ""
        card.vehicle = ""

        preview = self.service.preview_documents(
            card,
            client=build_business_client(),
            selected_document_ids=["invoice_factura", "upd"],
            active_document_id="invoice_factura",
        )

        for document in preview["documents"]:
            self.assertEqual(document["missing_fields"], [])
            self.assertEqual(document["warnings"], [])

    def test_regulated_document_overrides_print_fields_without_mutating_client(self) -> None:
        client = build_business_client()
        preview = self.service.preview_documents(
            self.card,
            client=client,
            selected_document_ids=["upd"],
            active_document_id="upd",
            document_overrides={
                "buyer_name": "ООО Ручной Получатель",
                "buyer_inn": "2455555555",
                "buyer_kpp": "245501001",
                "buyer_address": "660049, Красноярск, пр-т Мира, 1",
                "basis": "Договор ремонта № ABC-9 от 01.04.2026",
                "transport_details": "Самовывоз покупателем",
                "buyer_position": "Директор",
                "buyer_signer": "Петров П.П.",
                "seller_position": "ИП",
                "seller_signer": "Гришкявичус К.В.",
            },
        )

        html = "".join(page["html"] for page in preview["documents"][0]["pages"])
        self.assertIn("ООО Ручной Получатель", html)
        self.assertIn("2455555555 / 245501001", html)
        self.assertIn("660049, Красноярск, пр-т Мира, 1", html)
        self.assertIn("Договор ремонта № ABC-9 от 01.04.2026", html)
        self.assertIn("Самовывоз покупателем", html)
        self.assertIn("Петров П.П.", html)
        self.assertIn("Гришкявичус К.В.", html)
        self.assertEqual(client.legal_name, "ООО Контрагент")

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
        self.assertIn("Ключевые условия", html)
        self.assertIn("30 дней на работы", html)
        self.assertIn("6 месяцев", html)
        self.assertIn("1000 км", html)
        self.assertIn("150 рублей в сутки", html)
        self.assertIn("Фотофиксация", html)
        self.assertIn("Сумма прописью", html)
        self.assertIn("Стоимость заказ-наряда за наличный расчет", html)
        self.assertIn("Доплата по безналичному расчету", html)
        self.assertIn("Доплата по наличному расчету", html)
        self.assertNotIn("Налоги и сборы</td>", html)
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

    def test_print_context_omits_internal_material_fields(self) -> None:
        material = self.card.repair_order.materials[0]
        material.cost_price = "700"
        material.executor_id = "employee-1"
        material.executor_name = "Сергей Снабженец"
        material.material_percent_snapshot = "10"
        material.material_profit = "1500"
        material.material_salary_amount = "150"
        material.material_salary_accrued_at = "06.04.2026 18:00"

        context = self.service._build_document_context(
            self.card,
            self.card.repair_order,
            document=self.service._document_definition("repair_order"),
            settings=self.service._read_settings(),
        )
        private_fields = {
            "catalog_number",
            "cost_price",
            "executor_id",
            "executor_name",
            "material_percent_snapshot",
            "material_profit",
            "material_salary_amount",
            "material_salary_accrued_at",
        }
        self.assertTrue(private_fields.isdisjoint(context["repair_order"]["materials"][0]))

        preview = self.service.preview_documents(
            self.card,
            selected_document_ids=["repair_order"],
            active_document_id="repair_order",
        )
        html = preview["documents"][0]["pages"][0]["html"]
        self.assertNotIn("08886-81210", html)
        self.assertNotIn("Сергей Снабженец", html)
        self.assertNotIn("material_salary_amount", html)

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
                    "cash_like_prepayment": Decimal("10000"),
                    "due": Decimal("10000"),
                    "cash_due": Decimal("10000"),
                    "noncash_due": Decimal("11764.71"),
                    "due_label": "Доплата по наличному расчету",
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
                    "cash_like_prepayment": Decimal("0"),
                    "due": Decimal("13529.41"),
                    "cash_due": Decimal("11500"),
                    "noncash_due": Decimal("13529.41"),
                    "due_label": "Доплата по безналичному расчету",
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
                    "cash_like_prepayment": Decimal("5000"),
                    "due": Decimal("10647.06"),
                    "cash_due": Decimal("9050"),
                    "noncash_due": Decimal("10647.06"),
                    "due_label": "Доплата по безналичному расчету",
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
                self.assertEqual(totals["due_label"], expected["due_label"])
                self.assertEqual(totals["cash_due"], expected.get("cash_due", expected["due"]))
                self.assertEqual(totals["noncash_due"], expected["noncash_due"])
                self.assertEqual(totals["cash_total"], expected["subtotal"])
                self.assertEqual(totals["cash_like_prepayment"], expected["cash_like_prepayment"])
                self.assertEqual(totals["noncash_total"], Decimal("23529.41"))
                self.assertEqual(totals["noncash_taxes_and_fees"], Decimal("3529.41"))
                self.assertEqual(
                    context["repair_order"]["prepayment_display"], totals["prepayment_display"]
                )
                self.assertEqual(totals["base_total_display"], totals["subtotal_display"])
                self.assertEqual(totals["total_paid_display"], totals["prepayment_display"])

    def test_repair_order_print_totals_show_cash_and_cashless_without_duplicate_totals(
        self,
    ) -> None:
        card = build_payment_card(
            payment_method="cashless",
            payments=[
                {"amount": "3000", "payment_method": "cash"},
                {"amount": "2000", "payment_method": "card"},
            ],
        )

        preview = self.service.preview_documents(
            card,
            selected_document_ids=["repair_order"],
            active_document_id="repair_order",
        )
        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Стоимость заказ-наряда за наличный расчет</td><td>20 000 ₽", html)
        self.assertIn("Стоимость заказ-наряда по безналичному расчету", html)
        self.assertIn("включая налоги и сборы 15%", html)
        self.assertIn(">23 529,41 ₽</td>", html)
        self.assertIn("Предоплата за наличные</td><td>5 000 ₽", html)
        self.assertNotIn("Предоплата наличными</td>", html)
        self.assertNotIn("Предоплата на карту</td>", html)
        self.assertNotIn("Налоги и сборы</td>", html)
        self.assertIn("Доплата по безналичному расчету</td><td>17 647,06 ₽", html)
        self.assertIn("Доплата по наличному расчету</td><td>15 000 ₽", html)
        self.assertNotIn("20 000,00 ₽", html)
        self.assertNotIn("<tr><td>Итого работы</td>", html)
        self.assertNotIn("<tr><td>Итого материалы</td>", html)
        self.assertNotIn("Итого по заказ-наряду", html)

    def test_repair_order_print_totals_include_cashless_prepayment_fees(self) -> None:
        preview = self.service.preview_documents(
            build_cashless_prepayment_example_card(),
            selected_document_ids=["repair_order"],
            active_document_id="repair_order",
        )
        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Стоимость заказ-наряда за наличный расчет</td><td>334 545 ₽", html)
        self.assertIn(">393 582,35 ₽</td>", html)
        self.assertIn("Предоплата по безналу</td><td>170 000 ₽", html)
        self.assertNotIn("Налоги и сборы</td>", html)
        self.assertIn("Доплата по безналичному расчету</td><td>223 582,35 ₽", html)
        self.assertIn("Доплата по наличному расчету</td><td>190 045 ₽", html)
        self.assertNotIn("334 545,00 ₽", html)

    def test_invoice_print_subtracts_prepayment_from_cashless_total(self) -> None:
        preview = self.service.preview_documents(
            build_cashless_prepayment_example_card(),
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Итого</td><td>393 582,35", html)
        self.assertIn("В том числе НДС (5%)</td><td>18 742,02", html)
        self.assertIn("Предоплата</td><td>170 000,00", html)
        self.assertIn("Всего к оплате</td><td>223 582,35", html)
        self.assertIn("Двести двадцать три тысячи", html)
        self.assertNotIn("Всего к оплате</td><td>393 582,35", html)

    def test_invoice_print_grosses_cash_prepayment_as_cashless_equivalent(self) -> None:
        card = Card.from_dict(
            {
                "id": "card-invoice-cash-prepayment-391",
                "vehicle": "",
                "title": "Счет на оплату",
                "description": "",
                "column": "done",
                "archived": False,
                "created_at": "2026-06-03T10:00:00+00:00",
                "updated_at": "2026-06-03T10:00:00+00:00",
                "repair_order": {
                    "number": "391",
                    "date": "03.06.2026 10:00",
                    "opened_at": "03.06.2026 10:00",
                    "client": "Физическое лицо",
                    "payment_method": "cash",
                    "works": [{"name": "Ремонт", "quantity": "1", "price": "82310"}],
                    "payments": [
                        {
                            "amount": "40000",
                            "paid_at": "03.06.2026 10:00",
                            "payment_method": "cash",
                            "note": "Предоплата наличными",
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )

        preview = self.service.preview_documents(
            card,
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]
        context = self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=self.service._read_settings(),
        )
        invoice = context["invoice"]

        self.assertEqual(invoice["total"], Decimal("96835.29"))
        self.assertEqual(invoice["total_display"], "96 835,29")
        self.assertEqual(invoice["prepayment"], Decimal("47058.82"))
        self.assertEqual(invoice["prepayment_display"], "47 058,82")
        self.assertEqual(invoice["amount_due"], Decimal("49776.47"))
        self.assertEqual(invoice["amount_due_display"], "49 776,47")
        self.assertIn("Итого</td><td>96 835,29", html)
        self.assertIn("Предоплата</td><td>47 058,82", html)
        self.assertIn("Всего к оплате</td><td>49 776,47", html)
        self.assertNotIn("40 000,00", html)
        self.assertNotIn("Всего к оплате</td><td>96 835,29", html)

    def test_invoice_print_grosses_cash_and_keeps_cashless_prepayment(self) -> None:
        card = build_payment_card(
            payment_method="cashless",
            payments=[
                {
                    "amount": "5000",
                    "payment_method": "cash",
                    "paid_at": "06.04.2026 10:00",
                    "note": "Нал",
                    "actor_name": "ADMIN",
                },
                {
                    "amount": "7000",
                    "payment_method": "cashless",
                    "paid_at": "06.04.2026 10:10",
                    "note": "Безнал",
                    "actor_name": "ADMIN",
                },
            ],
        )

        preview = self.service.preview_documents(
            card,
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]
        context = self.service._build_document_context(
            card,
            card.repair_order,
            document=self.service._document_definition("invoice"),
            settings=self.service._read_settings(),
        )
        invoice = context["invoice"]

        self.assertEqual(invoice["total"], Decimal("23529.41"))
        self.assertEqual(invoice["prepayment"], Decimal("12882.35"))
        self.assertEqual(invoice["prepayment_display"], "12 882,35")
        self.assertEqual(invoice["amount_due"], Decimal("10647.06"))
        self.assertEqual(invoice["amount_due_display"], "10 647,06")
        self.assertIn("Итого</td><td>23 529,41", html)
        self.assertIn("Предоплата</td><td>12 882,35", html)
        self.assertIn("Всего к оплате</td><td>10 647,06", html)
        self.assertNotIn("Предоплата</td><td>12 000,00", html)

    def test_invoice_print_hides_prepayment_row_when_order_has_no_payments(self) -> None:
        preview = self.service.preview_documents(
            build_payment_card(payment_method="cashless", payments=[]),
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Итого</td><td>23 529,41", html)
        self.assertNotIn("Предоплата</td>", html)
        self.assertIn("Всего к оплате</td><td>23 529,41", html)

    def test_invoice_print_caps_amount_due_at_zero_after_overpayment(self) -> None:
        preview = self.service.preview_documents(
            build_payment_card(
                payment_method="cashless",
                payments=[{"amount": "30000", "payment_method": "cashless"}],
            ),
            selected_document_ids=["invoice"],
            active_document_id="invoice",
        )
        html = preview["documents"][0]["pages"][0]["html"]

        self.assertIn("Итого</td><td>23 529,41", html)
        self.assertIn("Предоплата</td><td>30 000,00", html)
        self.assertIn("Всего к оплате</td><td>0,00", html)
        self.assertNotIn("Всего к оплате</td><td>-", html)

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

    def test_template_engine_rejects_deeply_nested_sections(self) -> None:
        deep_template = "{{#a}}" * 100 + "x" + "{{/a}}" * 100

        with self.assertRaisesRegex(
            TemplateRenderError,
            "Слишком глубокая вложенность секций шаблона",
        ):
            render_template(deep_template, {"a": True})

    def test_money_formatting_rejects_non_finite_values_and_rounds_half_up(self) -> None:
        self.assertEqual(_money_display("1.005"), "1,01")
        self.assertEqual(_money_display("NaN"), "—")
        self.assertEqual(_money_display("Infinity"), "—")
        self.assertEqual(_money_display("1e999999"), "—")
        self.assertEqual(_money_words_display("NaN"), "—")
        self.assertEqual(_money_words_display("1e999999"), "—")

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

    def test_save_template_rejects_oversized_content_without_truncation(self) -> None:
        with patch("minimal_kanban.printing.service.PRINT_TEMPLATE_CONTENT_MAX_CHARS", 32):
            with self.assertRaises(PrintModuleError) as context:
                self.service.save_template(
                    document_type="repair_order",
                    name="Большой шаблон",
                    content="<section>" + ("x" * 64) + "</section>",
                )

        self.assertEqual(context.exception.code, "validation_error")
        self.assertEqual(context.exception.details["max_size_chars"], 32)
        self.assertEqual(self.service._read_custom_templates(), [])

    def test_custom_template_reader_skips_duplicate_ids_and_builtin_id_collisions(self) -> None:
        now = "2026-06-01T10:00:00+00:00"
        custom_template = {
            "id": "custom:repair_order:duplicate",
            "document_type": "repair_order",
            "name": "Дубликат",
            "content": '<div class="document-page">A</div>',
            "created_at": now,
            "updated_at": now,
            "source": "custom",
        }
        builtin_collision = {
            **custom_template,
            "id": "builtin:repair_order:standard",
            "name": "Подмена встроенного",
        }
        self.service._templates_path.write_text(
            json.dumps([builtin_collision, custom_template, custom_template], ensure_ascii=False),
            encoding="utf-8",
        )

        templates = self.service._read_custom_templates()

        self.assertEqual([item.id for item in templates], ["custom:repair_order:duplicate"])

    def test_custom_template_reader_rejects_nonstandard_json_constants(self) -> None:
        self.service._templates_path.write_text(
            '[{"id":"custom:repair_order:bad","document_type":"repair_order","score":NaN}]',
            encoding="utf-8",
        )

        self.assertEqual(self.service._read_custom_templates(), [])

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

    def test_regulated_documents_force_landscape_pdf_and_print_orientation(self) -> None:
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ) as render_pdf:
            _pdf_bytes, _file_name, meta = self.service.export_documents_pdf(
                self.card,
                selected_document_ids=["invoice_factura"],
                print_settings={"orientation": "portrait"},
            )

        self.assertEqual(meta["orientation"], "landscape")
        self.assertEqual(render_pdf.call_args.kwargs["orientation"], "landscape")

        with patch("minimal_kanban.printing.service.print_html") as print_backend:
            self.service.print_documents(
                self.card,
                selected_document_ids=["upd"],
                printer_name="Office Printer",
                print_settings={"default_printer": "Office Printer", "orientation": "portrait"},
            )

        self.assertEqual(print_backend.call_args.kwargs["orientation"], "landscape")

    def test_export_file_name_removes_path_and_windows_reserved_characters(self) -> None:
        self.card.repair_order.number = r"..\bad/name:*?<>|"

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ):
            _, file_name, _ = self.service.export_documents_pdf(
                self.card,
                selected_document_ids=["invoice"],
            )

        self.assertEqual(file_name, "autostopcrm-invoice-bad-name.pdf")
        self.assertNotRegex(file_name, r'[<>:"/\\|?*]')

    def test_json_write_sanitizes_non_finite_values_and_writes_valid_json(self) -> None:
        self.service._write_inspection_sheet_form_map(
            {"card-print-1": {"client": float("nan"), "planned_work_rows": [float("inf")]}}
        )

        raw = self.service._inspection_sheet_forms_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)

        self.assertNotIn("NaN", raw)
        self.assertNotIn("Infinity", raw)
        self.assertEqual(parsed["card-print-1"]["client"], 0.0)
        self.assertEqual(parsed["card-print-1"]["planned_work_rows"], [0.0])

    def test_template_write_rejects_payload_that_reader_would_ignore_as_oversized(self) -> None:
        self.service.save_template(
            document_type="repair_order",
            name="Small template",
            content="<section>OK</section>",
        )
        templates_path = self.service._templates_path
        original = templates_path.read_text(encoding="utf-8")

        with patch("minimal_kanban.printing.service.PRINT_JSON_FILE_MAX_BYTES", 512):
            with self.assertRaises(PrintModuleError) as context:
                self.service.save_template(
                    document_type="repair_order",
                    name="Huge template",
                    content="<section>" + ("x" * 2048) + "</section>",
                )

        self.assertEqual(context.exception.code, "validation_error")
        self.assertEqual(templates_path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(templates_path.parent.glob("*.tmp")), [])

    def test_inspection_sheet_form_reader_ignores_oversized_json_file(self) -> None:
        self.service._inspection_sheet_forms_path.write_text(
            json.dumps(
                {"card-print-1": {"client": "Oversized client", "padding": "x" * 64}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch("minimal_kanban.printing.service.PRINT_JSON_FILE_MAX_BYTES", 8):
            loaded = self.service.get_inspection_sheet_form(self.card)

        self.assertFalse(loaded["meta"]["has_saved_draft"])
        self.assertNotEqual(loaded["form"]["client"], "Oversized client")

    def test_inspection_sheet_form_reader_uses_bounded_binary_read(self) -> None:
        self.service._inspection_sheet_forms_path.write_text(
            json.dumps(
                {"card-print-1": {"client": "Saved client"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(Path, "read_text", side_effect=AssertionError("must not read text")):
            loaded = self.service.get_inspection_sheet_form(self.card)

        self.assertTrue(loaded["meta"]["has_saved_draft"])
        self.assertEqual(loaded["form"]["client"], "Saved client")

    def test_print_requires_printer_selection_when_direct_print_requested(self) -> None:
        with self.assertRaises(PrintModuleError) as context:
            self.service.print_documents(
                self.card,
                selected_document_ids=["repair_order"],
                printer_name="",
            )

        self.assertEqual(context.exception.code, "validation_error")
        self.assertIn("Не выбран принтер", context.exception.message)

    def test_plain_text_pdf_fallback_strips_head_style_and_script_blocks(self) -> None:
        text = _html_to_plain_text(
            """
            <!doctype html>
            <html>
              <head>
                <style>body { color: red; }</style>
                <script>alert('bad')</script>
              </head>
              <body><h1>Счет на оплату</h1><p>Владимир Регин</p></body>
            </html>
            """
        )

        self.assertIn("Счет на оплату", text)
        self.assertIn("Владимир Регин", text)
        self.assertNotIn("body { color", text)
        self.assertNotIn("alert", text)

    def test_print_styles_remove_preview_chrome_for_pdf_output(self) -> None:
        self.assertIn("@media print", PRINT_BASE_STYLES)
        self.assertIn("background: #ffffff;", PRINT_BASE_STYLES)
        self.assertIn("box-shadow: none;", PRINT_BASE_STYLES)
        self.assertIn("print-color-adjust: exact;", PRINT_BASE_STYLES)

    def test_print_styles_do_not_create_blank_pages_between_document_sections(self) -> None:
        self.assertIn("page-break-before: always;", PRINT_BASE_STYLES)
        self.assertIn("page-break-after: auto;", PRINT_BASE_STYLES)
        self.assertIn("break-before: page;", PRINT_BASE_STYLES)
        self.assertIn("break-after: auto;", PRINT_BASE_STYLES)

    def test_pdf_renderer_prefers_webengine_html_printing(self) -> None:
        with (
            patch("minimal_kanban.printing.pdf._should_use_qt_renderer", return_value=True),
            patch(
                "minimal_kanban.printing.pdf._should_use_qt_subprocess_renderer",
                return_value=False,
            ),
            patch(
                "minimal_kanban.printing.pdf._render_webengine_pdf_bytes",
                return_value=b"%PDF-1.4 webengine",
            ) as webengine,
            patch("minimal_kanban.printing.pdf._render_qt_pdf_bytes") as legacy_qt,
        ):
            pdf_bytes = render_html_to_pdf_bytes(
                "<!doctype html><html><body><h1>Заказ-наряд</h1></body></html>"
            )

        self.assertEqual(pdf_bytes, b"%PDF-1.4 webengine")
        webengine.assert_called_once()
        legacy_qt.assert_not_called()

    def test_pdf_renderer_adds_webengine_chromium_flags_without_overwriting(self) -> None:
        env = {"QTWEBENGINE_CHROMIUM_FLAGS": "--existing"}

        _ensure_qt_webengine_chromium_flags(env)

        self.assertIn("--existing", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--no-sandbox", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--disable-dev-shm-usage", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--disable-gpu", env["QTWEBENGINE_CHROMIUM_FLAGS"])

    def test_pdf_json_parser_rejects_nonstandard_constants(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
            _parse_json_object('{"content_base64": NaN}', label="Qt subprocess response")

    def test_pdf_json_parser_rejects_non_object_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "Qt subprocess response must be a JSON object"):
            _parse_json_object("[]", label="Qt subprocess response")

    def test_pdf_json_parser_uses_stable_message_for_invalid_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "Qt subprocess response must contain valid JSON"):
            _parse_json_object("{not-json", label="Qt subprocess response")

    def test_pdf_json_parser_rejects_deeply_nested_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "Qt subprocess response must contain valid JSON"):
            _parse_json_object("[" * 5000 + "]" * 5000, label="Qt subprocess response")

    def test_pdf_cli_stdin_reader_rejects_oversized_request(self) -> None:
        with patch("minimal_kanban.printing.pdf.PDF_CLI_STDIN_MAX_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "Qt subprocess request is too large"):
                _read_pdf_cli_stdin(BytesIO(b"12345"))

    def test_generated_pdf_reader_rejects_oversized_pdf_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "oversized.pdf"
            pdf_path.write_bytes(b"%PDF" + (b"x" * 16))

            with (
                patch("minimal_kanban.printing.pdf.PDF_OUTPUT_MAX_BYTES", 8),
                patch.object(Path, "read_bytes", side_effect=AssertionError("must not load file")),
            ):
                with self.assertRaisesRegex(PdfRenderError, "слишком большой PDF"):
                    _read_generated_pdf_bytes(pdf_path, label="Qt")

    def test_pdf_renderer_rejects_plain_text_fallback_by_default(self) -> None:
        with (
            patch("minimal_kanban.printing.pdf._should_use_qt_renderer", return_value=True),
            patch(
                "minimal_kanban.printing.pdf._should_use_qt_subprocess_renderer", return_value=False
            ),
            patch("minimal_kanban.printing.pdf.PDF_OUTPUT_MAX_BYTES", 4096),
            patch(
                "minimal_kanban.printing.pdf._render_webengine_pdf_bytes",
                return_value=b"%PDF" + (b"x" * 4096),
            ),
        ):
            with self.assertRaisesRegex(PdfRenderError, "HTML-рендер PDF недоступен"):
                render_html_to_pdf_bytes("<h1>Fallback</h1>")

    def test_pdf_renderer_can_use_explicit_plain_text_fallback(self) -> None:
        with (
            patch("minimal_kanban.printing.pdf._should_use_qt_renderer", return_value=True),
            patch(
                "minimal_kanban.printing.pdf._should_use_qt_subprocess_renderer", return_value=False
            ),
            patch("minimal_kanban.printing.pdf.PDF_OUTPUT_MAX_BYTES", 4096),
            patch(
                "minimal_kanban.printing.pdf._render_webengine_pdf_bytes",
                return_value=b"%PDF" + (b"x" * 4096),
            ),
        ):
            pdf_bytes = render_html_to_pdf_bytes(
                "<h1>Fallback</h1>", allow_plain_text_fallback=True
            )

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertLessEqual(len(pdf_bytes), 4096)

    def test_pdf_renderer_falls_back_safely_from_worker_thread(self) -> None:
        result: dict[str, bytes] = {}

        def run() -> None:
            result["pdf"] = render_html_to_pdf_bytes(
                """
                <!doctype html>
                <html>
                  <head><style>body { color: red; }</style></head>
                  <body><h1>Счет на оплату</h1><p>Владимир Регин</p></body>
                </html>
                """,
                allow_plain_text_fallback=True,
            )

        thread = threading.Thread(target=run, name="pdf-worker-test")
        thread.start()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertIn("pdf", result)
        self.assertTrue(result["pdf"].startswith(b"%PDF"))
        self.assertNotIn(b"(????", result["pdf"])
        self.assertNotIn(b"body { color", result["pdf"])


if __name__ == "__main__":
    unittest.main()
