from __future__ import annotations

# ruff: noqa: E402
import base64
from copy import deepcopy
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.models import SUPPORTED_PRINT_DOCUMENT_TYPES
from minimal_kanban.repair_order import RepairOrder


class PrintingMoneyApiCases:
    def test_printing_http_rejects_extreme_quantity_and_unknown_custom_vat(self) -> None:
        status, created = self.request("/api/create_card", {"title": "Synthetic review boundaries"})
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        extreme_order = {
            "tax_label": "НДС 5%",
            "works": [
                {
                    "name": "Synthetic",
                    "quantity": "1E-100000",
                    "price": "1",
                    "total": "0.01",
                }
            ],
        }
        before = deepcopy(self.store.read_bundle())
        state_file = self.store.base_dir / "state.json"
        before_bytes = state_file.read_bytes()
        status, rejected = self.request(
            "/api/update_repair_order",
            {"card_id": card_id, "repair_order": extreme_order},
        )
        self.assertEqual(status, 400, rejected)
        self.assertEqual(rejected["error"]["code"], "validation_error")
        self.assertEqual(rejected["error"]["details"]["field"], "repair_order.works[0].quantity")
        self.assertEqual(state_file.read_bytes(), before_bytes)
        self.assertEqual(self.store.read_bundle(), before)

        # Old persisted orders still need the print guard even though new input
        # is now rejected. Seed only this isolated fixture through its store.
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.repair_order = RepairOrder.from_dict(extreme_order)
        self.store.write_bundle(**bundle)
        historical_bytes = state_file.read_bytes()
        arguments = {"card_id": card_id, "selected_document_ids": ["invoice"]}
        status, preview = self.request("/api/preview_repair_order_print_documents", arguments)
        self.assertEqual(status, 200)
        self.assertFalse(preview["data"]["money_validation"]["valid"])
        self.assertIn(
            "number_out_of_range",
            [issue["code"] for issue in preview["data"]["money_validation"]["errors"]],
        )
        with (
            patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as renderer,
            patch("minimal_kanban.printing.service.print_html") as printer,
        ):
            for route in ("export_repair_order_print_pdf", "print_repair_order_documents"):
                with self.subTest(route=route):
                    status, blocked = self.request(
                        f"/api/{route}", {**arguments, "printer_name": "Synthetic Printer"}
                    )
                    self.assertEqual(status, 422, blocked)
                    self.assertEqual(blocked["error"]["code"], "document_money_validation_failed")
            renderer.assert_not_called()
            printer.assert_not_called()
        self.assertEqual(state_file.read_bytes(), historical_bytes)
        status, updated = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "tax_label": "",
                    "works": [{"name": "Synthetic", "quantity": "1", "price": "1"}],
                },
            },
        )
        self.assertEqual(status, 200, updated)
        arguments.update(
            selected_document_ids=["inspection_sheet"],
            print_settings={"service_profile": {"tax_label": ""}},
            template_overrides={"inspection_sheet": "НДС {{invoice.vat_display}}"},
        )
        status, preview = self.request("/api/preview_repair_order_print_documents", arguments)
        self.assertEqual(status, 200)
        self.assertFalse(preview["data"]["money_validation"]["valid"])
        self.assertIn(
            "tax_status_unknown",
            [issue["code"] for issue in preview["data"]["money_validation"]["errors"]],
        )
        status, blocked = self.request("/api/export_repair_order_print_pdf", arguments)
        self.assertEqual(status, 422)
        self.assertEqual(blocked["error"]["code"], "document_money_validation_failed")

    def test_completion_act_http_recalculation_acceptance_and_invalid_mode(self) -> None:
        status, created = self.request("/api/create_card", {"title": "Synthetic reviewed act"})
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "tax_label": "НДС 5%",
                    "works": [{"name": "Synthetic", "quantity": "1", "price": "100"}],
                },
            },
        )
        _, loaded = self.request("/api/get_completion_act_form", {"card_id": card_id})
        form = loaded["data"]["form"]
        form["calculation_mode"] = "manual_net"
        form["items"] = [{"name": "Synthetic", "unit": "шт", "quantity": "1", "price": "100"}]
        base = {
            "card_id": card_id,
            "form": form,
            "expected_version": 0,
            "expected_source_fingerprint": loaded["data"]["draft"]["current_source_fingerprint"],
            "idempotency_key": "http-reviewed-initial",
        }
        status, saved = self.request("/api/save_completion_act_form", base)
        self.assertEqual(status, 200)
        form = saved["data"]["form"]
        self.request(
            "/api/update_repair_order",
            {"card_id": card_id, "repair_order": {"tax_label": "НДС 20%"}},
        )
        _, stale = self.request("/api/get_completion_act_form", {"card_id": card_id})
        base.update(
            form=form,
            expected_version=1,
            idempotency_key="http-reviewed-new",
            expected_source_fingerprint=stale["data"]["draft"]["current_source_fingerprint"],
        )
        status, blocked = self.request("/api/save_completion_act_form", base)
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], "completion_act_recalculation_required")
        status, proposed = self.request("/api/save_completion_act_form", {**base, "dry_run": True})
        self.assertEqual(status, 200)
        self.assertEqual(proposed["data"]["dry_run"]["projected_totals"]["gross"], "120.00")
        accepted = proposed["data"]["dry_run"]["calculation_fingerprint"]
        request = {**base, "accepted_calculation_fingerprint": accepted}
        status, result = self.request("/api/save_completion_act_form", request)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["totals"]["gross"], "120.00")
        status, replay = self.request("/api/save_completion_act_form", request)
        self.assertEqual(status, 200)
        self.assertTrue(replay["data"]["draft"]["idempotent_replay"])
        for mode in ({}, [], None):
            status, invalid = self.request(
                "/api/preview_repair_order_print_documents",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["completion_act"],
                    "document_overrides": {"completion_act": {**form, "calculation_mode": mode}},
                },
            )
            self.assertEqual(status, 400)
            self.assertEqual(invalid["error"]["code"], "validation_error")

    def test_document_money_http_422_and_explicit_order_tax_alias(self) -> None:
        status, created = self.request(
            "/api/create_card", {"title": "Synthetic tax", "deadline": {"hours": 1}}
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {"works": [{"name": "Synthetic", "quantity": "1", "price": "100"}]},
            },
        )
        self.assertEqual(status, 200)
        state_file = self.store.base_dir / "state.json"
        before_preview = state_file.read_bytes()
        request = {"card_id": card_id, "selected_document_ids": ["invoice_factura"]}
        status, preview = self.request("/api/preview_repair_order_print_documents", request)
        self.assertEqual(status, 200)
        self.assertFalse(preview["data"]["money_validation"]["valid"])
        self.assertIn(
            "tax_status_unknown",
            {issue["code"] for issue in preview["data"]["money_validation"]["errors"]},
        )
        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes") as pdf:
            status, failure = self.request("/api/export_repair_order_print_pdf", request)
        self.assertEqual(status, 422)
        self.assertEqual(failure["error"]["code"], "document_money_validation_failed")
        pdf.assert_not_called()
        invoice_request = {**request, "selected_document_ids": ["invoice"]}
        status, invoice = self.request("/api/preview_repair_order_print_documents", invoice_request)
        self.assertEqual(status, 200)
        self.assertTrue(invoice["data"]["money_validation"]["valid"])
        self.assertIn(
            "В том числе НДС 5%</td><td>5,60",
            invoice["data"]["documents"][0]["pages"][0]["html"],
        )
        self.assertEqual(state_file.read_bytes(), before_preview)
        before = self.store.read_bundle()
        status, updated = self.request(
            "/api/update_repair_order", {"card_id": card_id, "repair_order": {"taxLabel": "НДС 0%"}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["repair_order"]["tax_label"], "НДС 0%")
        after_update = state_file.read_bytes()
        status, preview = self.request("/api/preview_repair_order_print_documents", request)
        self.assertEqual(status, 200)
        self.assertTrue(preview["data"]["money_validation"]["valid"])
        self.assertIn(
            '<td class="regulated-center">0%</td>',
            preview["data"]["documents"][0]["pages"][0]["html"],
        )
        status, editor_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                **invoice_request,
                "repair_order": {"works": [{"name": "Editor", "quantity": "1", "price": "100"}]},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(editor_preview["data"]["money_validation"]["valid"])
        self.assertEqual(
            editor_preview["data"]["documents"][0]["document_guard"]["tax_status"], "НДС 5%"
        )
        invoice_html = editor_preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("В том числе НДС 5%</td><td>5,60", invoice_html)
        self.assertIn("К оплате</td><td>117,65", invoice_html)
        status, regulated_editor_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                **request,
                "repair_order": {"works": [{"name": "Editor", "quantity": "1", "price": "100"}]},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(regulated_editor_preview["data"]["money_validation"]["valid"])
        self.assertIn(
            '<td class="regulated-center">0%</td>',
            regulated_editor_preview["data"]["documents"][0]["pages"][0]["html"],
        )
        self.assertEqual(state_file.read_bytes(), after_update)
        after = self.store.read_bundle()
        source_order = next(card.repair_order for card in after["cards"] if card.id == card_id)
        self.assertEqual(source_order.tax_label, "НДС 0%")
        self.assertEqual(source_order.works[0].name, "Synthetic")
        for key in (
            "cashboxes",
            "cash_transactions",
            "employees",
            "inventory_items",
            "inventory_movements",
        ):
            self.assertEqual(after.get(key), before.get(key))

    def test_repair_order_print_module_accepts_manual_document_without_card(self) -> None:
        manual_document = {
            "document_number": "MAN-100",
            "tax_label": "НДС (5%)",
            "document_date": "15.06.2026",
            "client": {
                "client_type": "ooo",
                "display_name": "ООО Документ Без Карточки",
                "legal_name": "ООО Документ Без Карточки",
                "inn": "2468000000",
                "kpp": "246801001",
                "checking_account": "40702810900000000001",
                "bank_name": "Тест Банк",
                "bik": "044525225",
                "correspondent_account": "30101810400000000225",
                "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
            },
            "vehicle": {
                "name": "Nissan X-Trail T32",
                "vin": "Z8NTANT32ES123456",
                "license_plate": "К123КК124",
                "mileage": "92000",
            },
            "works": [{"name": "Замена масла", "quantity": "1", "price": "1500"}],
            "materials": [{"name": "Масло моторное", "quantity": "5", "price": "700"}],
            "payments": [{"amount": "2000", "paid_at": "15.06.2026", "payment_method": "cash"}],
            "reason": "Ручное оформление",
            "comment": "Данные введены оператором вручную",
            "note": "Проверить подписи",
        }

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace",
            {"document_without_card": True, "manual_document": manual_document},
        )
        self.assertEqual(status, 200)
        self.assertTrue(workspace["data"]["meta"]["document_without_card"])
        self.assertEqual(workspace["data"]["card_id"], "manual-document")
        self.assertEqual(
            [item["id"] for item in workspace["data"]["documents"]],
            list(SUPPORTED_PRINT_DOCUMENT_TYPES),
        )

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "manual_document": manual_document,
                "selected_document_ids": ["invoice", "completion_act", "repair_order"],
                "active_document_id": "completion_act",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["data"]["active_document_id"], "completion_act")
        invoice_html = preview["data"]["documents"][0]["pages"][0]["html"]
        repair_order_html = preview["data"]["documents"][2]["pages"][0]["html"]
        self.assertIn("ООО Документ Без Карточки", invoice_html)
        self.assertIn("2468000000", invoice_html)
        self.assertIn("246801001", invoice_html)
        self.assertIn("660000, г. Красноярск, ул. Тестовая, 1", invoice_html)
        self.assertNotIn("40702810900000000001", invoice_html)
        self.assertIn("5 882,35", invoice_html)
        self.assertIn("Nissan X-Trail T32", repair_order_html)

        status, upd_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "manual_document": {
                    **manual_document,
                    "tax_label": "НДС (5%)",
                    "regulated": {
                        "basis": "Договор № MANUAL-100",
                        "transport_details": "Передача на территории сервиса",
                    },
                },
                "selected_document_ids": ["upd"],
                "active_document_id": "upd",
            },
        )
        self.assertEqual(status, 200)
        upd_document = upd_preview["data"]["documents"][0]
        self.assertEqual(upd_document["id"], "upd")
        self.assertEqual(upd_document["page_count"], 2)
        upd_html = "".join(page["html"] for page in upd_document["pages"])
        self.assertIn("Универсальный передаточный документ", upd_html)
        self.assertIn("ООО Документ Без Карточки", upd_html)
        self.assertIn("Договор № MANUAL-100", upd_html)
        self.assertIn("Передача на территории сервиса", upd_html)

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 manual-document-route",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "document_without_card": True,
                    "manual_document": manual_document,
                    "selected_document_ids": ["invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF"))
        self.assertEqual(exported["data"]["meta"]["source"], "manual_document")
        self.assertEqual(
            exported["data"]["meta"]["documents"][0]["template_id"], "builtin:invoice:standard"
        )

    def test_repair_order_print_module_accepts_request_text_without_card(self) -> None:
        request_text = (
            "Счет № TEXT-77 от 15.06.2026\n"
            "НДС: 5%\n"
            "Клиент: ООО Текстовый Документ\n"
            "ИНН: 2468003333\n"
            "КПП: 246801001\n"
            "Банк: Тест Банк\n"
            "БИК: 044525225\n"
            "Р/с: 40702810900000000001\n"
            "К/с: 30101810400000000225\n"
            "Автомобиль: Toyota RAV4\n"
            "VIN: JTMBFREV10D123456\n"
            "Работы:\n"
            "Диагностика 1 x 2500\n"
            "Материалы:\n"
            "Фильтр салона 1 x 900\n"
            "Оплаты:\n"
            "1000 | 15.06.2026 | cash | Аванс"
        )

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "request_text": request_text,
                "selected_document_ids": ["invoice", "completion_act"],
                "active_document_id": "invoice",
            },
        )
        self.assertEqual(status, 200)
        invoice_html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("ООО Текстовый Документ", invoice_html)
        self.assertIn("2468003333", invoice_html)
        self.assertNotIn("40702810900000000001", invoice_html)
        self.assertIn("4 000,00", invoice_html)
        self.assertIn("В том числе НДС", invoice_html)

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 request-text-manual-document-route",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "document_without_card": True,
                    "request_text": request_text,
                    "selected_document_ids": ["invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF"))
        self.assertEqual(exported["data"]["meta"]["source"], "manual_document")
        self.assertEqual(
            exported["data"]["meta"]["documents"][0]["template_id"], "builtin:invoice:standard"
        )

    def test_completion_act_form_routes_save_preview_conflict_and_reset(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Completion act route", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "ACT-API-1",
                    "tax_label": "НДС (5%)",
                    "date": "20.08.2026",
                    "client": "API Client",
                    "works": [
                        {
                            "id": "work-api-1",
                            "name": "API work",
                            "quantity": "1",
                            "price": "372983",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        business_baseline = self.store.read_bundle()

        status, loaded = self.request("/api/get_completion_act_form", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(loaded["data"]["totals"]["vat"], "20895.41")
        form = loaded["data"]["form"]
        source_fingerprint = loaded["data"]["draft"]["current_source_fingerprint"]
        form["basis"] = "API manual basis"
        status, saved = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": card_id,
                "form": form,
                "expected_version": 0,
                "expected_source_fingerprint": source_fingerprint,
                "idempotency_key": "api-completion-save-1",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["draft"]["version"], 1)
        self.assertEqual(self.store.read_bundle(), business_baseline)

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "ACT-API-1",
                    "tax_label": "НДС (5%)",
                    "date": "20.08.2026",
                    "client": "API Client",
                    "works": [
                        {
                            "id": "work-api-1",
                            "name": "API work",
                            "quantity": "1",
                            "price": "400000",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        business_baseline = self.store.read_bundle()

        status, stale = self.request(
            "/api/get_completion_act_form",
            {"card_id": card_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(stale["data"]["draft"]["is_stale"])
        self.assertEqual(stale["data"]["form"]["basis"], "API manual basis")
        status, ignored_override = self.request(
            "/api/get_completion_act_form",
            {
                "card_id": card_id,
                "repair_order": {"works": [{} for _ in range(10_000)]},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            ignored_override["data"]["fresh_form"]["items"][0]["price"],
            "448179.28",
        )
        for selected_ids in (["completion_act"], "completion_act", [" completion_act "]):
            with self.subTest(selected_document_ids=selected_ids):
                status, persisted_source_preview = self.request(
                    "/api/preview_repair_order_print_documents",
                    {
                        "card_id": card_id,
                        "selected_document_ids": selected_ids,
                        "active_document_id": " completion_act ",
                        "repair_order": {
                            "works": [
                                {
                                    "id": "work-api-1",
                                    "name": "Unsaved conflicting work",
                                    "quantity": "1",
                                    "price": "999999",
                                }
                            ]
                        },
                    },
                )
                self.assertEqual(status, 200)
                persisted_item = persisted_source_preview["data"]["documents"][0]["computed_items"][
                    0
                ]
                self.assertEqual(persisted_item["name"], "API work")
                self.assertEqual(persisted_item["price_without_vat_display"], "417 908,12")
                self.assertFalse(persisted_source_preview["data"]["money_validation"]["valid"])

        preview_form = dict(form)
        preview_form["calculation_mode"] = "manual_net"
        preview_form["items"] = [
            {
                **form["items"][0],
                "quantity": "3",
                "price": "0.10",
            }
        ]
        status, reviewed = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["completion_act"],
                "document_overrides": {
                    "completion_act": preview_form,
                    "completion_act_recalculate": True,
                },
            },
        )
        self.assertEqual(status, 200)
        status, updated = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": card_id,
                "form": preview_form,
                "expected_version": 1,
                "expected_source_fingerprint": stale["data"]["draft"]["current_source_fingerprint"],
                "idempotency_key": "api-completion-update-calculation",
                "accepted_calculation_fingerprint": reviewed["data"]["documents"][0][
                    "calculation_fingerprint"
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["draft"]["version"], 2)
        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["completion_act"],
                "active_document_id": "completion_act",
                "document_overrides": {"completion_act": preview_form},
            },
        )
        self.assertEqual(status, 200)
        document = preview["data"]["documents"][0]
        self.assertEqual(document["computed_totals"]["base"], "0.30")
        self.assertEqual(document["computed_totals"]["vat"], "0.02")
        self.assertEqual(document["computed_items"][0]["sum_without_vat_display"], "0,30")

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4\ncompletion-act-test\n%%EOF",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["completion_act"],
                    "document_overrides": {"completion_act": preview_form},
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF"))

        form["basis"] = "Conflicting change"
        status, conflict = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": card_id,
                "form": form,
                "expected_version": 0,
                "expected_source_fingerprint": source_fingerprint,
                "idempotency_key": "api-completion-save-2",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["code"], "completion_act_version_conflict")

        status, reset = self.request(
            "/api/reset_completion_act_form",
            {
                "card_id": card_id,
                "expected_version": 2,
                "idempotency_key": "api-completion-reset-1",
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(reset["data"]["draft"]["exists"])
        self.assertEqual(reset["data"]["draft"]["version"], 3)
        self.assertEqual(self.store.read_bundle(), business_baseline)

        status, card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(card["data"]["card"]["repair_order"]["works"][0]["price"], "400000")

        status, empty_created = self.request(
            "/api/create_card",
            {"title": "Empty completion act", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        empty_card_id = empty_created["data"]["card"]["id"]
        status, empty = self.request("/api/get_completion_act_form", {"card_id": empty_card_id})
        self.assertEqual(status, 200)
        self.assertIn("items", empty["data"]["missing_fields"])
        empty_source_fingerprint = empty["data"]["draft"]["current_source_fingerprint"]
        status, empty_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": empty_card_id,
                "selected_document_ids": ["completion_act"],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(empty_preview["data"]["documents"][0]["warnings"])
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4\nempty-completion-act\n%%EOF",
        ):
            status, empty_export = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "card_id": empty_card_id,
                    "selected_document_ids": ["completion_act"],
                },
            )
        self.assertEqual(status, 422)
        self.assertEqual(empty_export["error"]["code"], "document_money_validation_failed")
        status, invalid_save = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": empty_card_id,
                "form": empty["data"]["form"],
                "expected_version": 0,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid_save["error"]["code"], "validation_error")

        status, invalid_card_id = self.request(
            "/api/get_completion_act_form",
            {"card_id": ["x"] * 10_000},
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid_card_id["error"]["details"]["field"], "card_id")
        status, invalid_request_text = self.request(
            "/api/get_completion_act_form",
            {"card_id": empty_card_id, "request_text": " " * 20_001},
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid_request_text["error"]["details"]["field"], "request_text")

        mutation_base = {
            "card_id": empty_card_id,
            "form": empty["data"]["form"],
            "expected_version": 0,
            "expected_source_fingerprint": empty_source_fingerprint,
            "idempotency_key": "completion-metadata-guard",
        }
        for override, field in (
            ({"form": []}, "form"),
            ({"expected_version": []}, "expected_version"),
            ({"expected_source_fingerprint": []}, "expected_source_fingerprint"),
            ({"idempotency_key": ["x"] * 10_000}, "idempotency_key"),
            ({"source": {"nested": ["x"] * 10_000}}, "source"),
            ({"actor_name": ["x"] * 10_000}, "actor_name"),
        ):
            with self.subTest(override=field):
                status, invalid_metadata = self.request(
                    "/api/save_completion_act_form",
                    {**mutation_base, **override},
                )
                self.assertEqual(status, 400)
                self.assertEqual(invalid_metadata["error"]["code"], "validation_error")
                self.assertEqual(invalid_metadata["error"]["details"]["field"], field)

        status, spoof_attempt = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": empty_card_id,
                "form": empty["data"]["form"],
                "expected_version": 0,
                "expected_source_fingerprint": empty_source_fingerprint,
                "idempotency_key": "completion-session-spoof-guard",
                "_operator_session": {"audit_actor_name": "SPOOFED-ACTOR"},
            },
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(spoof_attempt["data"]["draft"]["filled_by"], "SPOOFED-ACTOR")

    def test_completion_act_save_rejects_changed_source_snapshot(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Completion act source conflict", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        def update_price(price: str) -> None:
            update_status, _ = self.request(
                "/api/update_repair_order",
                {
                    "card_id": card_id,
                    "repair_order": {
                        "number": "ACT-SOURCE-1",
                        "works": [
                            {
                                "id": "source-work-1",
                                "name": "Source work",
                                "quantity": "1",
                                "price": price,
                            }
                        ],
                    },
                },
            )
            self.assertEqual(update_status, 200)

        update_price("100")
        status, initial = self.request(
            "/api/get_completion_act_form",
            {"card_id": card_id},
        )
        self.assertEqual(status, 200)
        stale_form = initial["data"]["form"]
        stale_form["basis"] = "Only this field was edited"
        expected_source = initial["data"]["draft"]["current_source_fingerprint"]
        update_price("200")
        business_baseline = self.store.read_bundle()

        status, conflict = self.request(
            "/api/save_completion_act_form",
            {
                "card_id": card_id,
                "form": stale_form,
                "expected_version": 0,
                "expected_source_fingerprint": expected_source,
                "idempotency_key": "api-completion-source-conflict",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["code"], "completion_act_source_conflict")
        self.assertEqual(self.store.read_bundle(), business_baseline)

        status, current = self.request(
            "/api/get_completion_act_form",
            {"card_id": card_id},
        )
        self.assertEqual(status, 200)
        self.assertFalse(current["data"]["draft"]["exists"])
        self.assertEqual(current["data"]["draft"]["version"], 0)
        self.assertEqual(current["data"]["fresh_form"]["items"][0]["price"], "235.29")
