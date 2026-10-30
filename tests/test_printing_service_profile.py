"""Preserve intentional clears without reinterpreting legacy print profiles."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.models import PrintServiceProfile  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from tests.test_printing_service import build_card  # noqa: E402


class PrintServiceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.service = PrintModuleService(self.root)
        self.card = build_card()

    def stored(self) -> dict:
        return json.loads(self.service._settings_path.read_text(encoding="utf-8"))

    def test_new_explicit_clears_survive_save_reload_and_invoice_preview(self) -> None:
        defaults = PrintServiceProfile().to_dict()
        cleared = set(defaults) - {"company_name", "tax_label"}
        self.service.save_settings(
            {"service_profile": {key: "Synthetic " + key for key in cleared}}
        )
        saved = self.service.save_settings({"service_profile": {key: " \t " for key in cleared}})[
            "settings"
        ]
        self.assertNotIn("_service_profile_explicit_empty_fields", saved)
        self.assertEqual(set(self.stored()["_service_profile_explicit_empty_fields"]), cleared)
        reloaded = PrintModuleService(self.root)
        profile = reloaded.workspace(self.card)["settings"]["service_profile"]
        for key in cleared:
            with self.subTest(field=key):
                self.assertEqual(saved["service_profile"][key], "")
                self.assertEqual(self.stored()["service_profile"][key], "")
                self.assertEqual(profile[key], "")
        document = reloaded.preview_documents(self.card, selected_document_ids=["invoice"])[
            "documents"
        ][0]
        rendered = document["pages"][0]["html"]
        for key in ("legal_name", "inn", "bik", "settlement_account", "payment_purpose"):
            self.assertNotIn(defaults[key], rendered)
        self.assertIn('<div class="inv-v5-label">Назначение платежа</div><p>—</p>', rendered)
        self.assertTrue(document["money_validation"]["valid"])

    def test_clears_survive_unrelated_save_and_custom_default_reload(self) -> None:
        self.service.save_settings({"service_profile": {"payment_purpose": "", "kpp": ""}})
        template = self.service.save_template(
            document_type="invoice",
            name="Synthetic profile check",
            content='<div class="document-page">PURPOSE[{{service.payment_purpose}}]'
            "KPP[{{service.kpp}}]</div>",
        )["template"]
        self.service.save_settings({"copies": 3, "service_profile": {"email": "demo@example.test"}})
        self.service.set_default_template(document_type="invoice", template_id=template["id"])
        reloaded = PrintModuleService(self.root)
        self.assertEqual(
            self.stored()["_service_profile_explicit_empty_fields"], ["kpp", "payment_purpose"]
        )
        self.assertEqual(reloaded._read_settings().copies, 3)
        preview = reloaded.preview_documents(self.card, selected_document_ids=["invoice"])[
            "documents"
        ][0]
        self.assertEqual(preview["template"]["id"], template["id"])
        self.assertIn("PURPOSE[—]KPP[—]", preview["pages"][0]["html"])
        self.service.save_settings({"service_profile": {"payment_purpose": "Оплата по договору"}})
        self.assertEqual(self.stored()["_service_profile_explicit_empty_fields"], ["kpp"])
        self.assertEqual(
            PrintModuleService(self.root)._read_settings().service_profile.payment_purpose,
            "Оплата по договору",
        )

    def test_temporary_clears_apply_to_preview_without_changing_saved_profile(self) -> None:
        self.service.save_settings(
            {"service_profile": {"payment_purpose": "Сохранённое назначение"}}
        )
        before = self.service._settings_path.read_bytes()
        temporary = self.service.preview_documents(
            self.card,
            selected_document_ids=["invoice"],
            print_settings={"service_profile": {"payment_purpose": "", "bik": ""}},
        )["documents"][0]["pages"][0]["html"]
        self.assertIn('<div class="inv-v5-label">Назначение платежа</div><p>—</p>', temporary)
        self.assertNotIn(PrintServiceProfile().bik, temporary)
        self.assertEqual(self.service._settings_path.read_bytes(), before)
        persisted = PrintModuleService(self.root).preview_documents(
            self.card, selected_document_ids=["invoice"]
        )["documents"][0]["pages"][0]["html"]
        self.assertIn("Сохранённое назначение", persisted)
        self.assertIn(PrintServiceProfile().bik, persisted)

    def test_legacy_blank_null_and_missing_fields_keep_their_previous_meaning(self) -> None:
        self.service._settings_path.write_text(
            json.dumps({"service_profile": {"payment_purpose": "", "inn": None, "phone": ""}}),
            encoding="utf-8",
        )
        before = self.service._settings_path.read_bytes()
        profile = self.service.workspace(self.card)["settings"]["service_profile"]
        defaults = PrintServiceProfile()
        self.assertEqual(profile["payment_purpose"], defaults.payment_purpose)
        self.assertEqual(profile["inn"], defaults.inn)
        self.assertEqual(profile["bank_name"], defaults.bank_name)
        self.assertEqual(profile["phone"], "")
        self.assertEqual(self.service._settings_path.read_bytes(), before)
        self.service.save_settings({"service_profile": {"payment_purpose": ""}})
        self.service.save_settings({"service_profile": {"payment_purpose": None}})
        self.assertEqual(
            self.stored()["service_profile"]["payment_purpose"], defaults.payment_purpose
        )
        self.assertNotIn("_service_profile_explicit_empty_fields", self.stored())

    def test_profile_clear_metadata_does_not_change_explicit_tax_selection(self) -> None:
        self.card.repair_order.tax_label = ""
        self.service.save_settings({"service_profile": {"tax_label": "НДС 7%"}})
        self.service.save_settings({"service_profile": {"payment_purpose": ""}})
        self.assertTrue(self.stored()["_tax_label_explicit"])
        self.assertEqual(self.stored()["service_profile"]["tax_label"], "НДС 7%")
        before = self.service._settings_path.read_bytes()
        for document_id in ("invoice", "invoice_factura", "upd"):
            with self.subTest(document_id=document_id, tax_label="НДС 7%"):
                preview = PrintModuleService(self.root).preview_documents(
                    self.card, selected_document_ids=[document_id]
                )
                self.assertTrue(preview["money_validation"]["valid"])
                self.assertIn("5%", preview["documents"][0]["pages"][0]["html"])
                self.assertEqual(self.service._settings_path.read_bytes(), before)
                if document_id == "invoice":
                    self.assertIn("В том числе НДС 5%", preview["documents"][0]["pages"][0]["html"])
        self.service.save_settings({"service_profile": {"tax_label": ""}})
        self.service.save_settings({"copies": 2})
        self.assertFalse(self.stored()["_tax_label_explicit"])
        self.assertEqual(self.stored()["service_profile"]["tax_label"], "")
        before = self.service._settings_path.read_bytes()
        for document_id in ("invoice", "invoice_factura", "upd"):
            with self.subTest(document_id=document_id, tax_label=""):
                preview = PrintModuleService(self.root).preview_documents(
                    self.card, selected_document_ids=[document_id]
                )
                self.assertTrue(preview["money_validation"]["valid"])
                self.assertIn("5%", preview["documents"][0]["pages"][0]["html"])
                self.assertEqual(self.service._settings_path.read_bytes(), before)
                if document_id == "invoice":
                    self.assertIn("В том числе НДС 5%", preview["documents"][0]["pages"][0]["html"])
