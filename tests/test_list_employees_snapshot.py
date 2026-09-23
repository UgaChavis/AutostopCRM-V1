from __future__ import annotations

import logging
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ListEmployeesSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        logger = logging.getLogger(f"test.list_employees_snapshot.{self._testMethodName}")
        logger.addHandler(logging.NullHandler())
        self.store = JsonStore(state_file=Path(self.temp_dir.name) / "state.json", logger=logger)
        self.service = CardService(self.store, logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _old_full_result(self, month: str) -> dict:
        """Reference implementation of the former all-under-lock list path."""
        bundle = self.store.read_bundle()
        employees = self.service._employees_from_settings(bundle["settings"])
        employees_by_id = {item["id"]: item for item in employees}
        shifts = self.service._employee_shift_accruals_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        orders = self.service._employee_repair_order_accruals_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        resets = self.service._employee_salary_balance_resets_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        report = self.service._build_payroll_report(
            bundle["cards"],
            employees,
            shift_accruals=shifts,
            repair_order_accruals=orders,
            month=month,
        )
        balances = {
            item["id"]: self.service._build_employee_salary_balance_summary(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                item,
                shift_accruals=shifts,
                repair_order_accruals=orders,
                salary_balance_resets=resets,
                months=6,
            )["balance_total"]
            for item in employees
        }
        return {
            "employees": [
                self.service._employee_with_current_payroll_term(
                    {**item, "balance_total": balances.get(item["id"], "0")}
                )
                for item in employees
            ],
            "month": month,
            "summary": report["summary"],
            "detail_rows": report["detail_rows"],
        }

    def test_grouped_snapshot_matches_original_full_response(self) -> None:
        first = self.service.save_employee(
            {"name": "Механик Один", "salary_mode": "percent_only", "work_percent": "30"}
        )["employee"]
        second = self.service.save_employee({"name": "Механик Два", "salary_mode": "none"})[
            "employee"
        ]
        cashbox = self.service.create_cashbox({"name": "Зарплатная касса"})["cashbox"]
        card = self.service.create_card(
            {"vehicle": "Синтетический автомобиль", "title": "Зарплата", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "SALARY-TEST",
                    "status": "open",
                    "client": "Синтетический клиент",
                    "vehicle": "Синтетический автомобиль",
                    "payments": [
                        {"amount": "10000", "paid_at": "23.09.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Работа",
                            "quantity": "1",
                            "price": "10000",
                            "executor_id": first["id"],
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.service.create_employee_shift_accrual(
            {"employee_id": second["id"], "amount_minor": 13579, "note": "Смена"}
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": first["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 4200,
                "cashbox_id": cashbox["id"],
            }
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": second["id"],
                "transaction_kind": "salary_advance",
                "amount_minor": 2500,
                "cashbox_id": cashbox["id"],
            }
        )
        bundle = self.store.read_bundle()
        settings = dict(bundle["settings"])
        created_at = datetime.now(UTC).isoformat()
        legacy_accrual = {
            "id": "synthetic-order-accrual",
            "kind": "accrual",
            "employee_id": second["id"],
            "card_id": card["id"],
            "repair_order_number": "SALARY-TEST",
            "amount_minor": 6300,
            "base_amount_minor": 210000,
            "percent": "3",
            "created_at": created_at,
        }
        settings["employee_repair_order_accruals"] = [
            legacy_accrual,
            {
                **legacy_accrual,
                "id": "synthetic-order-reversal",
                "kind": "reversal",
                "amount_minor": 1100,
                "related_accrual_id": legacy_accrual["id"],
            },
        ]
        self.store.write_bundle(**{**bundle, "settings": settings})
        month = "2026-09"
        self.assertEqual(
            self.service.list_employees({"month": month}), self._old_full_result(month)
        )

    def test_full_report_releases_lock_and_uses_one_consistent_snapshot(self) -> None:
        employee = self.service.save_employee({"name": "Сотрудник снимка", "salary_mode": "none"})[
            "employee"
        ]
        report_started = threading.Event()
        allow_report = threading.Event()
        build_report = self.service._build_payroll_report

        def delayed_report(*args, **kwargs):
            report_started.set()
            if not allow_report.wait(5):
                raise AssertionError("report did not resume")
            return build_report(*args, **kwargs)

        with patch.object(self.service, "_build_payroll_report", side_effect=delayed_report):
            with ThreadPoolExecutor(max_workers=2) as executor:
                full = executor.submit(self.service.list_employees, {"month": "2026-09"})
                self.assertTrue(report_started.wait(5))
                try:
                    reference = executor.submit(
                        self.service.list_employees,
                        {"references_only": True, "month": "2026-09"},
                    )
                    self.assertEqual(
                        reference.result(timeout=2)["employees"][0]["id"], employee["id"]
                    )
                    self.service.create_employee_shift_accrual(
                        {"employee_id": employee["id"], "amount_minor": 12345}
                    )
                finally:
                    allow_report.set()
                before_write = full.result(timeout=5)

        self.assertEqual(before_write["employees"][0]["balance_total"], "0")
        after_write = self.service.list_employees({"month": "2026-09"})
        self.assertEqual(after_write["employees"][0]["balance_total"], "123.45")


if __name__ == "__main__":
    unittest.main()
