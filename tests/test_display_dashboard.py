from __future__ import annotations

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

    def test_dashboard_uses_current_payroll_week_ranking_and_explicit_visibility(self) -> None:
        fixed_now = datetime(2026, 7, 24, 21, 0, tzinfo=business_timezone())
        employees = [
            {
                "name": "Борис Мастер",
                "position": "Механик",
                "salary_mode": "salary_only",
                "base_salary": "1000",
                "created_at": "2026-07-01T09:00:00+07:00",
            },
            {
                "name": "Анна Администратор",
                "position": "Администратор",
                "salary_mode": "none",
            },
            {
                "name": "Вера Офис",
                "position": "Администратор",
                "salary_mode": "none",
                "dashboard_visible": True,
            },
            {
                "name": "Глеб Скрытый",
                "position": "Механик",
                "salary_mode": "none",
                "dashboard_visible": False,
            },
            {
                "name": "Денис Бывший",
                "position": "Механик",
                "salary_mode": "none",
                "is_active": False,
            },
        ]
        with patch("minimal_kanban.models.utc_now", return_value=fixed_now):
            saved = [self.service.save_employee(item)["employee"] for item in employees]
            self.service.create_employee_shift_accrual(
                {"employee_id": saved[2]["id"], "amount": "3000"}
            )
            dashboard = self.service.get_display_dashboard()

        self.assertFalse(saved[1]["dashboard_visible"])
        self.assertTrue(saved[2]["dashboard_visible"])
        self.assertFalse(saved[3]["dashboard_visible"])
        self.assertEqual(dashboard["schema_version"], "display_dashboard.v2")
        self.assertEqual(
            dashboard["salary_period"],
            {
                "date_from": "2026-07-20",
                "date_to": "2026-07-26",
                "starts_at": "2026-07-20T00:00:00+07:00",
                "ends_at": "2026-07-27T00:00:00+07:00",
                "label": "20.07–26.07",
                "is_open": True,
            },
        )
        self.assertEqual(
            [item["name"] for item in dashboard["employees"]],
            ["Вера Офис", "Борис Мастер"],
        )
        self.assertEqual(
            [item["salary"] for item in dashboard["employees"]],
            ["3000", "1000"],
        )
        self.assertEqual(dashboard["timezone"], "Asia/Krasnoyarsk")
        self.assertEqual(len(dashboard["weeks"]), 4)

    def test_salary_period_rolls_over_at_midnight_after_sunday(self) -> None:
        timezone = business_timezone()
        sunday = self.service._display_dashboard_salary_period(
            now=datetime(2026, 7, 26, 23, 59, 59, tzinfo=timezone)
        )
        monday = self.service._display_dashboard_salary_period(
            now=datetime(2026, 7, 27, 0, 0, 0, tzinfo=timezone)
        )

        self.assertEqual(sunday["starts_at"].isoformat(), "2026-07-20T00:00:00+07:00")
        self.assertEqual(sunday["ends_at"].isoformat(), "2026-07-27T00:00:00+07:00")
        self.assertEqual(monday["starts_at"].isoformat(), "2026-07-27T00:00:00+07:00")
        self.assertEqual(monday["ends_at"].isoformat(), "2026-08-03T00:00:00+07:00")

    def test_salary_amount_resets_when_monday_starts(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Недельный Мастер",
                "position": "Механик",
                "salary_mode": "none",
                "created_at": "2026-07-20T00:00:00+07:00",
            }
        )["employee"]
        self.service.create_employee_shift_accrual(
            {
                "employee_id": employee["id"],
                "amount": "1000",
                "created_at": "2026-07-26T23:59:59+07:00",
            }
        )
        timezone = business_timezone()
        with patch(
            "minimal_kanban.models.utc_now",
            return_value=datetime(2026, 7, 26, 23, 59, 59, tzinfo=timezone),
        ):
            sunday = self.service.get_display_dashboard()
        with patch(
            "minimal_kanban.models.utc_now",
            return_value=datetime(2026, 7, 27, 0, 0, 0, tzinfo=timezone),
        ):
            monday = self.service.get_display_dashboard()

        self.assertEqual(sunday["employees"][0]["salary"], "1000")
        self.assertEqual(monday["employees"][0]["salary"], "0")

    def test_dashboard_money_is_rounded_up_to_whole_rubles(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Копеечный Мастер",
                "position": "Механик",
                "salary_mode": "none",
                "created_at": "2026-07-20T00:00:00+07:00",
            }
        )["employee"]
        self.service.create_employee_shift_accrual(
            {
                "employee_id": employee["id"],
                "amount": "1500.01",
                "created_at": "2026-07-20T10:00:00+07:00",
            }
        )
        dashboard_cards = [
            self._card(
                status="closed",
                closed_at="2026-07-20T12:00:00+07:00",
                price="1000.01",
            )
        ]
        now = datetime(2026, 7, 20, 12, 30, tzinfo=business_timezone())

        with patch("minimal_kanban.models.utc_now", return_value=now):
            dashboard = self.service.get_display_dashboard()
        weeks = self.service._display_dashboard_week_buckets(dashboard_cards, now=now)

        self.assertEqual(dashboard["employees"][0]["salary"], "1501")
        self.assertEqual(weeks[-1]["amount"], "1001")
        self.assertTrue(dashboard["completed_week_average"].isdigit())

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
                "salary_period",
                "employees",
                "weeks",
                "completed_week_average",
            },
        )
        self.assertEqual(set(payload["employees"][0]), {"name", "position", "salary"})
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
        self.assertEqual(dashboard["schema_version"], "display_dashboard.v2")
        self.assertEqual(len(dashboard["weeks"]), 4)
        self.assertIn("salary_period", dashboard)


class DisplayDashboardWebContractTests(unittest.TestCase):
    def test_scale_settings_open_named_dashboard_window(self) -> None:
        self.assertIn('id="openDisplayDashboardButton"', BOARD_WEB_APP_HTML)
        self.assertIn("window.open('/dashboard', 'autostop-display-dashboard')", BOARD_WEB_APP_HTML)
        self.assertIn("openDisplayDashboardButton?.addEventListener", BOARD_WEB_APP_HTML)

    def test_dashboard_has_required_content_polling_and_recovery_contract(self) -> None:
        self.assertIn("Результаты автосервиса", DISPLAY_DASHBOARD_HTML)
        self.assertIn("Начислено за текущую неделю", DISPLAY_DASHBOARD_HTML)
        self.assertIn("Валовая выручка · 4 недели", DISPLAY_DASHBOARD_HTML)
        self.assertIn('class="salary-row"', DISPLAY_DASHBOARD_HTML)
        self.assertIn("maximum > 0 && amount > 0", DISPLAY_DASHBOARD_HTML)
        self.assertIn("function wholeRubles(value)", DISPLAY_DASHBOARD_HTML)
        self.assertIn("maximumFractionDigits: 0", DISPLAY_DASHBOARD_HTML)
        self.assertIn("REFRESH_INTERVAL_MS = 45000", DISPLAY_DASHBOARD_HTML)
        self.assertIn("НЕТ ОБНОВЛЕНИЯ", DISPLAY_DASHBOARD_HTML)
        self.assertIn("window.__AUTOSTOP_DISPLAY_DASHBOARD__", DISPLAY_DASHBOARD_HTML)
        self.assertIn("beforeunload", DISPLAY_DASHBOARD_HTML)
        self.assertNotIn("chart.js", DISPLAY_DASHBOARD_HTML.lower())


if __name__ == "__main__":
    unittest.main()
