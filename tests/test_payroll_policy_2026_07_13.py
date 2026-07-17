from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.card_service_payroll import (
    PAYROLL_POLICY_2026_07_13_ORDER_PERCENT_NAMES,
    PAYROLL_POLICY_2026_07_13_WORK_NAMES,
)
from minimal_kanban.storage.json_store import JsonStore


class PayrollPolicyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.payroll-policy.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)
        self.employees: dict[str, dict] = {}
        for name in (
            *PAYROLL_POLICY_2026_07_13_WORK_NAMES,
            *PAYROLL_POLICY_2026_07_13_ORDER_PERCENT_NAMES,
        ):
            payload = {
                "name": name,
                "created_at": "2026-01-01T00:00:00+07:00",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "45",
                "material_percent": "10",
            }
            if name == "Сергей Гелингер":
                payload.update(
                    {
                        "salary_mode": "salary_plus_percent",
                        "base_salary": "30000",
                        "work_percent": "45",
                    }
                )
            self.employees[name] = self.service.save_employee(payload)["employee"]
        self.expected_ids = {name: employee["id"] for name, employee in self.employees.items()}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _qualified_order(self) -> str:
        worker = self.employees["Александр Баландин"]
        sergey = self.employees["Сергей Гелингер"]
        card = self.service.create_card(
            {"vehicle": "Toyota", "title": "Миграция зарплаты", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [
                        {
                            "name": "Работа сотрудника",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": worker["id"],
                        },
                        {
                            "name": "Работа Сергея",
                            "quantity": "1",
                            "price": "500",
                            "executor_id": sergey["id"],
                        },
                    ],
                    "materials": [
                        {
                            "name": "Материал Сергея",
                            "quantity": "1",
                            "price": "500",
                            "cost_price": "300",
                            "executor_id": sergey["id"],
                        }
                    ],
                    "payments": [{"amount": "2000", "paid_at": "13.07.2026 12:00"}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        return card["id"]

    def test_dry_run_apply_and_second_apply_are_idempotent(self) -> None:
        card_id = self._qualified_order()
        before = self.state_file.read_bytes()
        dry_run = self.service.migrate_payroll_policy_2026_07_13(apply=False)
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(dry_run["mode"], "dry-run")
        self.assertEqual(dry_run["affected_repair_orders_count"], 1)

        applied = self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )
        self.assertEqual(applied["employees_checked"], 15)
        self.assertEqual(applied["affected_repair_orders_count"], 1)
        employees = {item["name"]: item for item in self.service.list_employees()["employees"]}
        for name in PAYROLL_POLICY_2026_07_13_WORK_NAMES:
            self.assertEqual(employees[name]["work_percent"], "50")
        for name in PAYROLL_POLICY_2026_07_13_ORDER_PERCENT_NAMES:
            employee = employees[name]
            self.assertEqual(employee["salary_mode"], "none")
            self.assertEqual(employee["base_salary"], "0")
            self.assertEqual(employee["work_percent"], "0")
            self.assertEqual(employee["material_percent"], "0")
            self.assertEqual(employee["repair_order_percent"], "4")
            self.assertEqual(
                employee["payroll_terms"][-1]["effective_from"],
                "2026-07-13T00:00:00+07:00",
            )
        sergey_terms = self.service._employee_weekly_base_salary_accruals(
            employees["Сергей Гелингер"],
            period_start=datetime.fromisoformat("2026-07-01T00:00:00+07:00"),
            period_end=datetime.fromisoformat("2026-08-01T00:00:00+07:00"),
            as_of=datetime.fromisoformat("2026-08-01T00:00:00+07:00"),
        )
        self.assertEqual(len(sergey_terms), 2)
        self.assertEqual(sum(item["amount"] for item in sergey_terms), 60000)

        order = self.service.get_card({"card_id": card_id})["card"]["repair_order"]
        self.assertEqual(order["works"][0]["work_percent_snapshot"], "50")
        self.assertEqual(order["works"][0]["salary_amount"], "500")
        self.assertEqual(order["works"][1]["salary_amount"], "")
        self.assertEqual(order["materials"][0]["material_salary_amount"], "")
        report = self.service.get_payroll_report({"month": "2026-07"})
        order_accruals = [
            row for row in report["detail_rows"] if row["row_type"] == "repair_order_accrual"
        ]
        self.assertEqual(len(order_accruals), 2)
        self.assertEqual({row["salary_amount"] for row in order_accruals}, {"80"})

        bundle = self.store.read_bundle()
        migrated_card = next(item for item in bundle["cards"] if item.id == card_id)
        migrated_card.repair_order.works[0].work_percent_snapshot = "50.0"
        self.service._save_bundle(
            bundle,
            columns=bundle["columns"],
            cards=bundle["cards"],
            events=bundle["events"],
        )

        second = self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )
        self.assertEqual(second["employees_changed"], 0)
        self.assertEqual(second["affected_repair_orders_count"], 0)
        repeated = self.service.get_payroll_report({"month": "2026-07"})
        self.assertEqual(
            len(
                [
                    row
                    for row in repeated["detail_rows"]
                    if row["row_type"] == "repair_order_accrual"
                ]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
