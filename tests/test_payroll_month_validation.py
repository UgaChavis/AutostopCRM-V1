from __future__ import annotations

# The fixture import sets up the source path before application imports.
# ruff: noqa: I001
import unittest
from datetime import datetime
from unittest.mock import patch

from tests import test_api as api_test_module
from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError

FROZEN_NOW = datetime.fromisoformat("2026-07-18T12:00:00+07:00")
INVALID_CALENDAR_MONTHS = ("2026-00", "2026-13", "0000-01", "9999-12")


class PayrollMonthValidationTests(CardServiceCase):
    def test_calendar_month_errors_are_validation_errors_and_do_not_write(self) -> None:
        employee = self.service.save_employee({"name": "Calendar boundary"})["employee"]
        before = self.state_file.read_bytes()
        for method_name in (
            "get_payroll_report",
            "list_employees",
            "get_employee_salary_report",
        ):
            for month in INVALID_CALENDAR_MONTHS:
                with self.subTest(method=method_name, month=month):
                    with self.assertRaises(ServiceError) as error:
                        getattr(self.service, method_name)(
                            {"employee_id": employee["id"], "month": month}
                        )
                    self.assertEqual(error.exception.code, "validation_error")
                    self.assertEqual(error.exception.status_code, 400)
                    self.assertEqual(error.exception.details["field"], "month")
                    self.assertEqual(self.state_file.read_bytes(), before)

    @patch("minimal_kanban.models.utc_now", return_value=FROZEN_NOW)
    def test_legacy_missing_and_malformed_months_use_current_business_month(self, _clock) -> None:
        for month in (None, "", "invalid", "2026-1"):
            with self.subTest(month=month):
                self.assertEqual(
                    self.service.get_payroll_report({"month": month})["month"], "2026-07"
                )

    def test_valid_month_and_existing_prefix_normalization_are_preserved(self) -> None:
        for month, expected in (("2026-02", "2026-02"), ("2026-10suffix", "2026-10")):
            with self.subTest(month=month):
                self.assertEqual(
                    self.service.get_payroll_report({"month": month})["month"], expected
                )

    def test_unicode_decimal_digits_use_the_same_canonical_month_for_reports(self) -> None:
        employee = self.service.save_employee({"name": "Calendar digits"})["employee"]
        for month in ("２０２６-１０", "٢٠٢٦-١٠"):
            with self.subTest(month=month):
                self.assertEqual(
                    self.service.get_payroll_report({"month": month})["month"], "2026-10"
                )
                self.service.get_employee_salary_report(
                    {"employee_id": employee["id"], "month": month}
                )


class PayrollMonthValidationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = api_test_module.ApiServerTests()
        self.addCleanup(self._cleanup_api)
        self.api.setUp()

    def _cleanup_api(self) -> None:
        self.assertTrue(self.api.doCleanups(), "API fixture cleanup failed")

    def test_invalid_calendar_months_return_400_on_payroll_reads(self) -> None:
        employee = self.api.service.save_employee({"name": "HTTP calendar boundary"})["employee"]
        for operation, month in (
            ("get_payroll_report", "2026-13"),
            ("list_employees", "2026-00"),
            ("get_employee_salary_report", "9999-12"),
        ):
            with self.subTest(operation=operation, month=month):
                status, response = self.api.request(
                    f"/api/{operation}", {"employee_id": employee["id"], "month": month}
                )
                self.assertEqual(status, 400)
                self.assertEqual(response["error"]["code"], "validation_error")
                self.assertEqual(response["error"]["details"]["field"], "month")
