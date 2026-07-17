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

from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.card_service_payroll import PAYROLL_POLICY_2026_07_13_TERMS
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
        for name in PAYROLL_POLICY_2026_07_13_TERMS:
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
        self.unlisted_employee = self.service.save_employee(
            {
                "name": "Сергей Зазнобин",
                "created_at": "2026-01-01T00:00:00+07:00",
                "salary_mode": "none",
                "base_salary": "0",
                "work_percent": "0",
                "material_percent": "10",
                "repair_order_percent": "0",
            }
        )["employee"]
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

    def test_qualification_boundary_uses_later_of_full_payment_and_closure(self) -> None:
        def qualified_at(closed_at: str, paid_at: str) -> datetime:
            order = RepairOrder.from_dict(
                {
                    "status": "closed",
                    "closed_at": closed_at,
                    "works": [{"name": "Работа", "quantity": "1", "price": "100"}],
                    "payments": [{"amount": "100", "paid_at": paid_at}],
                }
            )
            result = self.service._repair_order_payroll_qualified_at(order)
            self.assertIsNotNone(result)
            return result

        before = qualified_at(
            "2026-07-12T23:59:59+07:00",
            "2026-07-12T23:59:59+07:00",
        )
        paid_after_closure = qualified_at(
            "2026-07-12T23:00:00+07:00",
            "2026-07-13T00:00:00+07:00",
        )
        closed_after_payment = qualified_at(
            "2026-07-13T00:00:00+07:00",
            "2026-07-12T23:00:00+07:00",
        )
        self.assertEqual(before, datetime.fromisoformat("2026-07-12T23:59:59+07:00"))
        self.assertEqual(paid_after_closure, datetime.fromisoformat("2026-07-13T00:00:00+07:00"))
        self.assertEqual(closed_after_payment, datetime.fromisoformat("2026-07-13T00:00:00+07:00"))

        self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )
        employee = next(
            item
            for item in self.service.list_employees()["employees"]
            if item["name"] == "Александр Баландин"
        )
        self.assertEqual(
            self.service._employee_payroll_term_at(employee, before)["work_percent"], "45"
        )
        self.assertEqual(
            self.service._employee_payroll_term_at(employee, paid_after_closure)["work_percent"],
            "50",
        )

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
        self.assertEqual(applied["employees_checked"], 18)
        self.assertEqual(applied["affected_repair_orders_count"], 1)
        employees = {item["name"]: item for item in self.service.list_employees()["employees"]}
        for name, expected in PAYROLL_POLICY_2026_07_13_TERMS.items():
            employee = employees[name]
            for key, value in expected.items():
                self.assertEqual(employee[key], value, f"{name}: {key}")
            self.assertEqual(
                employee["payroll_terms"][-1]["effective_from"],
                "2026-07-13T00:00:00+07:00",
            )
            for key, value in expected.items():
                self.assertEqual(employee["current_payroll_term"][key], value, f"{name}: {key}")
        unlisted = employees["Сергей Зазнобин"]
        for key in (
            "salary_mode",
            "base_salary",
            "work_percent",
            "material_percent",
            "repair_order_percent",
        ):
            self.assertEqual(unlisted[key], self.unlisted_employee[key], key)
        self.assertEqual(unlisted["payroll_terms"], self.unlisted_employee["payroll_terms"])
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

    def test_exact_matrix_recalculates_historical_rows_from_cutoff(self) -> None:
        card = self.service.create_card(
            {"vehicle": "Mazda", "title": "Точная матрица", "deadline": {"hours": 2}}
        )["card"]
        work_prices = {
            "Алексей Чупров": "1000",
            "Иван Сысоев": "2000",
            "Дмитрий Ляхов": "300",
            "Екатерина Игнатьева": "400",
            "Мария Чупрова": "500",
        }
        material_values = {
            "Алексей Чупров": ("500", "300"),
            "Иван Сысоев": ("1000", "500"),
            "Дмитрий Ляхов": ("200", "100"),
            "Екатерина Игнатьева": ("600", "400"),
            "Мария Чупрова": ("800", "500"),
        }
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [
                        {
                            "name": f"Работа: {name}",
                            "quantity": "1",
                            "price": price,
                            "executor_id": self.employees[name]["id"],
                        }
                        for name, price in work_prices.items()
                    ],
                    "materials": [
                        {
                            "name": f"Материал: {name}",
                            "quantity": "1",
                            "price": price,
                            "cost_price": cost,
                            "executor_id": self.employees[name]["id"],
                        }
                        for name, (price, cost) in material_values.items()
                    ],
                    "payments": [{"amount": "7300", "paid_at": "13.07.2026 12:00"}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})

        applied = self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )

        self.assertEqual(applied["employees_changed"], 18)
        order = self.service.get_card({"card_id": card["id"]})["card"]["repair_order"]
        works = {row["executor_name"]: row for row in order["works"]}
        materials = {row["executor_name"]: row for row in order["materials"]}
        self.assertEqual(works["Алексей Чупров"]["work_percent_snapshot"], "100")
        self.assertEqual(works["Алексей Чупров"]["salary_amount"], "1000")
        self.assertEqual(works["Иван Сысоев"]["work_percent_snapshot"], "100")
        self.assertEqual(works["Иван Сысоев"]["salary_amount"], "2000")
        self.assertEqual(materials["Алексей Чупров"]["material_percent_snapshot"], "10")
        self.assertEqual(materials["Алексей Чупров"]["material_salary_amount"], "20")
        self.assertEqual(materials["Иван Сысоев"]["material_percent_snapshot"], "10")
        self.assertEqual(materials["Иван Сысоев"]["material_salary_amount"], "50")
        self.assertEqual(works["Дмитрий Ляхов"]["salary_amount"], "")
        self.assertEqual(materials["Дмитрий Ляхов"]["material_salary_amount"], "")
        for name, expected_material_amount in (
            ("Екатерина Игнатьева", "20"),
            ("Мария Чупрова", "30"),
        ):
            self.assertEqual(works[name]["salary_amount"], "")
            self.assertEqual(materials[name]["material_percent_snapshot"], "10")
            self.assertEqual(materials[name]["material_salary_amount"], expected_material_amount)

    def test_reapply_replaces_later_overrides_and_recalculates_week(self) -> None:
        self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )
        maxim = self.employees["Максим Андрианов"]
        alexey = self.employees["Алексей Мацурко"]
        self.service.save_employee(
            {
                "employee_id": maxim["id"],
                "name": maxim["name"],
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "45",
                "material_percent": "0",
                "repair_order_percent": "0",
                "payroll_effective_from": "2026-07-14T00:00:00+07:00",
            }
        )
        self.service.save_employee(
            {
                "employee_id": alexey["id"],
                "name": alexey["name"],
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "45",
                "material_percent": "10",
                "repair_order_percent": "0",
                "payroll_effective_from": "2026-07-17T00:00:00+07:00",
            }
        )
        card = self.service.create_card(
            {"vehicle": "Lada", "title": "Позднее переопределение", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [
                        {
                            "name": "Работа Максима",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": maxim["id"],
                        },
                        {
                            "name": "Работа Алексея",
                            "quantity": "1",
                            "price": "500",
                            "executor_id": alexey["id"],
                        },
                    ],
                    "materials": [
                        {
                            "name": "Материал Алексея",
                            "quantity": "1",
                            "price": "1000",
                            "cost_price": "500",
                            "executor_id": alexey["id"],
                        }
                    ],
                    "payments": [{"amount": "2500", "paid_at": "17.07.2026 12:00"}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        before = self.service.get_card({"card_id": card["id"]})["card"]["repair_order"]
        self.assertEqual(before["works"][0]["work_percent_snapshot"], "45")
        self.assertEqual(before["works"][1]["salary_amount"], "225")
        self.assertEqual(before["materials"][0]["material_salary_amount"], "50")

        preview = self.service.migrate_payroll_policy_2026_07_13(apply=False)
        self.assertEqual(preview["employees_changed"], 2)
        self.assertEqual(preview["affected_repair_orders_count"], 1)
        applied = self.service.migrate_payroll_policy_2026_07_13(
            apply=True, expected_employee_ids=self.expected_ids
        )
        self.assertEqual(applied["employees_changed"], 2)
        self.assertEqual(applied["affected_repair_orders_count"], 1)

        employees = {item["name"]: item for item in self.service.list_employees()["employees"]}
        self.assertEqual(
            employees["Максим Андрианов"]["current_payroll_term"]["work_percent"], "50"
        )
        self.assertEqual(
            employees["Алексей Мацурко"]["current_payroll_term"]["repair_order_percent"],
            "4",
        )
        cutoff = datetime.fromisoformat("2026-07-13T00:00:00+07:00")
        for name in ("Максим Андрианов", "Алексей Мацурко"):
            terms = employees[name]["payroll_terms"]
            self.assertEqual(
                len(
                    [
                        term
                        for term in terms
                        if datetime.fromisoformat(term["effective_from"]) >= cutoff
                    ]
                ),
                1,
            )

        after = self.service.get_card({"card_id": card["id"]})["card"]["repair_order"]
        self.assertEqual(after["works"][0]["work_percent_snapshot"], "50")
        self.assertEqual(after["works"][0]["salary_amount"], "500")
        self.assertEqual(after["works"][1]["salary_amount"], "")
        self.assertEqual(after["materials"][0]["material_salary_amount"], "")
        report = self.service.get_payroll_report({"month": "2026-07"})
        alexey_order_accruals = [
            row
            for row in report["detail_rows"]
            if row["employee_id"] == alexey["id"] and row["row_type"] == "repair_order_accrual"
        ]
        self.assertEqual([row["salary_amount"] for row in alexey_order_accruals], ["100"])

        final_preview = self.service.migrate_payroll_policy_2026_07_13(apply=False)
        self.assertEqual(final_preview["employees_changed"], 0)
        self.assertEqual(final_preview["affected_repair_orders_count"], 0)
        self.assertEqual(final_preview["financial_effect_minor"], 0)


if __name__ == "__main__":
    unittest.main()
