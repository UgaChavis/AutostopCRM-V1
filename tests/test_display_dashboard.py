from __future__ import annotations

import base64
import json
import logging
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.models import Card, business_timezone
from minimal_kanban.operator_activity import OperatorActivityService
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.services.shared_files_service import SharedFilesService
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.web_assets import (
    BOARD_WEB_APP_CONTRACT_TEXT as BOARD_WEB_APP_HTML,
)
from minimal_kanban.web_assets import DISPLAY_DASHBOARD_HTML


class DisplayDashboardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.display_dashboard.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.store = JsonStore(state_file=self.base_dir / "state.json", logger=self.logger)
        self.service = CardService(
            self.store,
            self.logger,
            attachments_dir=self.base_dir / "attachments",
            repair_orders_dir=self.base_dir / "repair-orders",
        )
        self.shared_files = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
        )
        self.service.configure_display_dashboard_shared_file_resolver(
            lambda file_id: (
                self.shared_files.get_shared_file_info({"file_id": file_id})["file"]
                if any(
                    item["id"] == file_id
                    for item in self.shared_files.list_shared_files({})["files"]
                )
                else None
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _card(
        self,
        *,
        status: str,
        closed_at: str,
        price: str = "1000",
        cashless: bool = False,
    ) -> Card:
        payments = (
            [{"amount": price, "payment_method": "cashless", "paid_at": closed_at}]
            if cashless
            else []
        )
        return Card.from_dict(
            {
                "title": "Dashboard aggregate fixture",
                "repair_order": {
                    "status": status,
                    "closed_at": closed_at,
                    "works": [{"name": "Работа", "quantity": "1", "price": price}],
                    "payments": payments,
                },
            }
        )

    def test_week_buckets_use_krasnoyarsk_monday_boundaries_and_grand_total(self) -> None:
        timezone = business_timezone()
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone)
        cashless = self._card(
            status="closed",
            closed_at="2026-06-30T10:00:00+07:00",
            cashless=True,
        )
        cards = [
            cashless,
            self._card(status="closed", closed_at="2026-07-08T10:00:00+07:00"),
            self._card(status="closed", closed_at="2026-07-13T23:59:59+07:00"),
            self._card(status="closed", closed_at="2026-07-14T10:00:00+07:00"),
            self._card(status="closed", closed_at="2026-07-20T00:00:00+07:00"),
            self._card(status="open", closed_at="2026-07-20T10:00:00+07:00"),
            self._card(status="ready", closed_at="2026-07-20T10:00:00+07:00"),
            self._card(status="open", closed_at="2026-07-15T10:00:00+07:00"),
            self._card(status="closed", closed_at="2026-07-20T13:00:00+07:00"),
        ]

        weeks = self.service._display_dashboard_week_buckets(cards, now=now)

        self.assertEqual(
            [item["date_from"] for item in weeks],
            [
                "2026-06-29",
                "2026-07-06",
                "2026-07-13",
                "2026-07-20",
            ],
        )
        self.assertEqual([item["orders_count"] for item in weeks], [1, 1, 2, 1])
        self.assertEqual(weeks[0]["amount"], cashless.repair_order.grand_total_amount())
        self.assertNotEqual(weeks[0]["amount"], cashless.repair_order.subtotal_amount())
        self.assertEqual([item["is_current"] for item in weeks], [False, False, False, True])
        self.assertEqual(weeks[-1]["date_to"], "2026-07-20")

    def test_dashboard_v3_contains_default_message_board_and_no_payroll_data(self) -> None:
        fixed_now = datetime(2026, 7, 24, 21, 0, tzinfo=business_timezone())
        self.service.save_employee(
            {
                "name": "Скрытый в v3 сотрудник",
                "position": "Механик",
                "salary_mode": "salary_only",
                "base_salary": "1000",
            }
        )

        with patch("minimal_kanban.models.utc_now", return_value=fixed_now):
            dashboard = self.service.get_display_dashboard()

        self.assertEqual(dashboard["schema_version"], "display_dashboard.v3")
        self.assertEqual(dashboard["timezone"], "Asia/Krasnoyarsk")
        self.assertEqual(len(dashboard["weeks"]), 4)
        self.assertEqual(
            set(dashboard["message_board"]),
            {
                "schema_version",
                "body_html",
                "image_file_ids",
                "updated_at",
                "updated_by",
                "revision",
            },
        )
        self.assertEqual(dashboard["message_board"]["body_html"], "")
        self.assertEqual(dashboard["message_board"]["image_file_ids"], [])
        self.assertNotIn("employees", dashboard)
        self.assertNotIn("salary_period", dashboard)

    def test_message_update_sanitizes_html_validates_images_and_uses_revision(self) -> None:
        image = self.shared_files.upload_shared_file(
            {
                "file_name": "dashboard.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"),
            }
        )["file"]
        current = self.service.get_display_dashboard()["message_board"]
        updated = self.service.update_board_settings(
            {
                "expected_revision": current["revision"],
                "actor_name": "МАСТЕР",
                "display_dashboard_message": {
                    "body_html": (
                        '<h2 onclick="alert(1)">План</h2>'
                        '<p><b>Подъёмник 2</b> <font size="7" color="red">срочно</font>'
                        '<a href="https://example.test">ссылка</a></p>'
                        "<script>secret()</script><style>body{display:none}</style>"
                        '<img src="https://example.test/x.png">'
                    ),
                    "image_file_ids": [image["id"]],
                },
            }
        )

        message = updated["settings"]["display_dashboard_message"]
        self.assertEqual(message["image_file_ids"], [image["id"]])
        self.assertIn("<h2>План</h2>", message["body_html"])
        self.assertIn('<font size="7">срочно</font>', message["body_html"])
        self.assertIn("ссылка", message["body_html"])
        for forbidden in ("onclick", "script", "style", "href", "<img", "secret"):
            self.assertNotIn(forbidden, message["body_html"].casefold())
        self.assertNotEqual(message["revision"], current["revision"])

        events = self.store.read_bundle()["events"]
        audit_event = next(
            event for event in events if event.action == "display_dashboard_message_updated"
        )
        audit_payload = json.dumps(audit_event.to_dict(), ensure_ascii=False)
        self.assertNotIn("Подъёмник", audit_payload)
        self.assertNotIn(message["body_html"], audit_payload)

        with self.assertRaises(ServiceError) as stale:
            self.service.update_board_settings(
                {
                    "expected_revision": current["revision"],
                    "display_dashboard_message": {"body_html": "<p>Устарело</p>"},
                }
            )
        self.assertEqual(stale.exception.code, "revision_conflict")
        self.assertEqual(stale.exception.status_code, 409)

    def test_message_dry_run_validates_without_writing(self) -> None:
        before = self.service.get_display_dashboard()["message_board"]
        preview = self.service.update_board_settings(
            {
                "expected_revision": before["revision"],
                "dry_run": True,
                "display_dashboard_message": {"body_html": "<p><u>Предпросмотр</u></p>"},
            }
        )
        after = self.service.get_display_dashboard()["message_board"]

        self.assertTrue(preview["meta"]["dry_run"])
        self.assertTrue(preview["meta"]["display_dashboard_message_changed"])
        self.assertEqual(
            preview["settings"]["display_dashboard_message"]["body_html"],
            "<p><u>Предпросмотр</u></p>",
        )
        self.assertEqual(after, before)
        self.assertFalse(
            any(
                event.action == "display_dashboard_message_updated"
                for event in self.store.read_bundle()["events"]
            )
        )

    def test_message_rejects_non_image_missing_and_oversized_content(self) -> None:
        text_file = self.shared_files.upload_shared_file(
            {
                "file_name": "dashboard.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"text").decode("ascii"),
            }
        )["file"]
        revision = self.service.get_display_dashboard()["message_board"]["revision"]
        cases = (
            {"body_html": "<p>Файл</p>", "image_file_ids": [text_file["id"]]},
            {"body_html": "<p>Файл</p>", "image_file_ids": ["missing-image"]},
            {"body_html": "<p>" + ("x" * 12_001) + "</p>", "image_file_ids": []},
            {
                "body_html": "<p>Много</p>",
                "image_file_ids": [text_file["id"]] * 9,
            },
        )
        for message in cases:
            with self.subTest(message_size=len(message["body_html"])):
                with self.assertRaises(ServiceError) as invalid:
                    self.service.update_board_settings(
                        {
                            "expected_revision": revision,
                            "display_dashboard_message": message,
                        }
                    )
                self.assertEqual(invalid.exception.code, "validation_error")

    def test_dashboard_money_is_rounded_up_to_whole_rubles(self) -> None:
        dashboard_cards = [
            self._card(
                status="closed",
                closed_at="2026-07-20T12:00:00+07:00",
                price="1000.01",
            )
        ]
        now = datetime(2026, 7, 20, 12, 30, tzinfo=business_timezone())

        weeks = self.service._display_dashboard_week_buckets(dashboard_cards, now=now)

        self.assertEqual(weeks[-1]["amount"], "1001")
        self.assertEqual(self.service._format_display_dashboard_rubles("1500.01"), "1501")

    def test_non_admin_session_cannot_change_dashboard_visibility(self) -> None:
        employee = self.service.save_employee({"name": "Мастер", "position": "Механик"})["employee"]
        with self.assertRaisesRegex(Exception, "дашборде"):
            self.service.save_employee(
                {
                    "employee_id": employee["id"],
                    "name": employee["name"],
                    "position": employee["position"],
                    "dashboard_visible": False,
                    "_operator_session": {"is_admin": False},
                }
            )

    def test_compact_payload_contains_no_order_employee_ids_or_pii_fields(self) -> None:
        self.service.save_employee({"name": "Мастер", "position": "Механик"})
        payload = self.service.get_display_dashboard()
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "generated_at",
                "timezone",
                "message_board",
                "weeks",
                "completed_week_average",
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False)
        forbidden_fields = (
            '"employee_id"',
            '"card_id"',
            '"client"',
            '"phone"',
            '"vin"',
            '"payments"',
            '"works"',
            '"materials"',
            '"salary_mode"',
            '"work_percent"',
            '"employee"',
            '"salary"',
        )
        for field in forbidden_fields:
            self.assertNotIn(field, encoded)
        self.assertNotIn('"salary_month"', encoded)


class DisplayDashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base_dir = Path(self.temp_dir.name)
        logger = logging.getLogger(f"test.display_dashboard.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        store = JsonStore(state_file=base_dir / "state.json", logger=logger)
        service = CardService(store, logger, attachments_dir=base_dir / "attachments")
        service.save_employee({"name": "API Мастер", "position": "Механик"})
        operator_service = OperatorAuthService(
            store,
            service,
            users_file=base_dir / "users.json",
            activity_service=OperatorActivityService(
                activity_dir=base_dir / "operator-activity", logger=logger
            ),
            logger=logger,
        )
        self.server = ApiServer(
            service,
            logger,
            operator_service=operator_service,
            start_port=0,
            fallback_limit=10,
            bearer_token="",
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            self.server.base_url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def test_page_is_static_but_aggregate_api_requires_operator_session(self) -> None:
        page_status, page = self._request("/dashboard")
        anonymous_status, anonymous = self._request("/api/get_display_dashboard")
        login_status, login = self._request(
            "/api/login_operator",
            method="POST",
            payload={"username": "admin", "password": "admin"},
        )
        token = json.loads(login)["data"]["session"]["token"]
        allowed_status, allowed = self._request(
            "/api/get_display_dashboard",
            headers={"X-Operator-Session": token},
        )

        self.assertEqual(page_status, 200)
        self.assertIn(b"display_dashboard", page)
        self.assertEqual(anonymous_status, 401)
        self.assertEqual(json.loads(anonymous)["error"]["details"]["auth_type"], "operator_session")
        self.assertEqual(login_status, 200)
        self.assertEqual(allowed_status, 200)
        dashboard = json.loads(allowed)["data"]
        self.assertEqual(dashboard["schema_version"], "display_dashboard.v3")
        self.assertEqual(len(dashboard["weeks"]), 4)
        self.assertIn("message_board", dashboard)
        self.assertNotIn("employees", dashboard)
        self.assertNotIn("salary_period", dashboard)


class DisplayDashboardWebContractTests(unittest.TestCase):
    def test_scale_settings_open_named_dashboard_window(self) -> None:
        self.assertIn('id="openDisplayDashboardButton"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="editDisplayDashboardMessageButton" type="button" '
            'title="НАСТРОИТЬ СООБЩЕНИЕ ДАШБОРДА"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'id="openModuleMapSettingsButton" type="button"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("ОТКРЫТЬ СТРУКТУРУ IT", BOARD_WEB_APP_HTML)
        self.assertNotIn("РЕДАКТИРОВАТЬ ДОСКУ СООБЩЕНИЙ", BOARD_WEB_APP_HTML)
        self.assertIn("window.open('/dashboard', 'autostop-display-dashboard')", BOARD_WEB_APP_HTML)
        self.assertIn("window.open('/module-map', 'autostop-module-map')", BOARD_WEB_APP_HTML)
        self.assertIn("openDisplayDashboardButton?.addEventListener", BOARD_WEB_APP_HTML)
        self.assertIn("openModuleMapSettingsButton?.addEventListener", BOARD_WEB_APP_HTML)

    def test_dashboard_has_required_content_polling_and_recovery_contract(self) -> None:
        self.assertIn("Результаты автосервиса", DISPLAY_DASHBOARD_HTML)
        self.assertIn("Доска механиков", DISPLAY_DASHBOARD_HTML)
        self.assertIn("Валовая выручка · 4 недели", DISPLAY_DASHBOARD_HTML)
        self.assertIn('class="message-board__content"', DISPLAY_DASHBOARD_HTML)
        self.assertIn("renderMessageBoard(data.message_board)", DISPLAY_DASHBOARD_HTML)
        self.assertIn("/api/shared_file?file_id=", DISPLAY_DASHBOARD_HTML)
        self.assertIn("URL.revokeObjectURL", DISPLAY_DASHBOARD_HTML)
        self.assertIn("headers: requestHeaders()", DISPLAY_DASHBOARD_HTML)
        self.assertIn("function wholeRubles(value)", DISPLAY_DASHBOARD_HTML)
        self.assertIn("maximumFractionDigits: 0", DISPLAY_DASHBOARD_HTML)
        self.assertIn("REFRESH_INTERVAL_MS = 45000", DISPLAY_DASHBOARD_HTML)
        self.assertIn("НЕТ ОБНОВЛЕНИЯ", DISPLAY_DASHBOARD_HTML)
        self.assertIn("window.__AUTOSTOP_DISPLAY_DASHBOARD__", DISPLAY_DASHBOARD_HTML)
        self.assertIn("beforeunload", DISPLAY_DASHBOARD_HTML)
        self.assertNotIn("chart.js", DISPLAY_DASHBOARD_HTML.lower())


if __name__ == "__main__":
    unittest.main()
