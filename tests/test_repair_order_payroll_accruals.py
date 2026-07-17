from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderPayrollAccrualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.logger = logging.getLogger(f"test.order-payroll.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.store = JsonStore(
            state_file=Path(self.temp_dir.name) / "state.json", logger=self.logger
        )
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _employee(self, name: str, **terms: str) -> dict:
        return self.service.save_employee(
            {
                "name": name,
                "salary_mode": terms.pop("salary_mode", "none"),
                "base_salary": terms.pop("base_salary", "0"),
                "work_percent": terms.pop("work_percent", "0"),
                "material_percent": terms.pop("material_percent", "0"),
                "repair_order_percent": terms.pop("repair_order_percent", "0"),
                **terms,
            }
        )["employee"]

    def _close_cashless_order(self, *, worker_id: str = "") -> dict:
        cashbox = self.service.create_cashbox({"name": "Безналичный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Toyota", "title": "Зарплата от ЗН", "deadline": {"hours": 2}}
        )["card"]
        work = {"name": "Работа", "quantity": "1", "price": "1000"}
        if worker_id:
            work["executor_id"] = worker_id
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [work],
                    "materials": [{"name": "Материал", "quantity": "1", "price": "500"}],
                    "payments": [
                        {
                            "amount": "1764.71",
                            "paid_at": "17.07.2026 12:00",
                            "payment_method": "cashless",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )
        return self.service.set_repair_order_status(
            {"card_id": card["id"], "status": "closed", "actor_name": "ADMIN"}
        )["card"]

    def test_two_independent_four_percent_accruals_exclude_cashless_fees(self) -> None:
        sergey = self._employee("Сергей Гелингер", repair_order_percent="4")
        alexey = self._employee("Алексей Мацурко", repair_order_percent="4")
        worker = self._employee("Исполнитель", salary_mode="percent_only", work_percent="50")

        card = self._close_cashless_order(worker_id=worker["id"])
        order = card["repair_order"]
        self.assertEqual(order["subtotal_total"], "1500")
        self.assertGreater(float(order["taxes_total"]), 0)
        self.assertEqual(order["payments"][0]["payment_method"], "cashless")
        self.assertEqual(order["works"][0]["salary_amount"], "500")

        report = self.service.get_payroll_report({"month": "2026-07"})
        order_rows = [
            row for row in report["detail_rows"] if row["row_type"] == "repair_order_accrual"
        ]
        self.assertEqual({row["employee_id"] for row in order_rows}, {sergey["id"], alexey["id"]})
        self.assertEqual({row["base_amount"] for row in order_rows}, {"1500"})
        self.assertEqual({row["repair_order_percent"] for row in order_rows}, {"4"})
        self.assertEqual({row["salary_amount"] for row in order_rows}, {"60"})
        for employee in (sergey, alexey):
            summary = next(
                item for item in report["summary"] if item["employee_id"] == employee["id"]
            )
            self.assertEqual(summary["repair_order_accruals_count"], 1)
            self.assertEqual(summary["repair_order_accrued_total"], "60")
            self.assertEqual(summary["accrued_total"], "60")
        salary_report = self.service.get_employee_salary_report(
            {"employee_id": sergey["id"], "month": "2026-07"}
        )
        self.assertEqual(salary_report["totals"]["repair_order_accrual_count"], 1)
        self.assertEqual(salary_report["totals"]["repair_order_accrual_total"], "60")
        self.assertIn("4% от стоимости заказ-наряда за наличный расчёт", salary_report["text"])
        reconciliation = self.service.get_employee_salary_reconciliation(
            {
                "employee_id": sergey["id"],
                "date_from": "2026-07-01",
                "date_to": "2026-07-31",
            }
        )
        order_reconciliation = next(
            row for row in reconciliation["rows"] if row["kind"] == "repair_order_accrual"
        )
        self.assertEqual(
            order_reconciliation["scheme"],
            "4% от стоимости заказ-наряда за наличный расчёт",
        )
        self.assertIn(
            "Стоимость заказ-наряда за наличный расчёт",
            order_reconciliation["calculation_base"],
        )
        self.assertIn("1 500,00", order_reconciliation["calculation_base"])
        self.assertEqual(order_reconciliation["accrued"], "60")

        self.service.update_repair_order(
            {"card_id": card["id"], "repair_order": {"comment": "Повторное сохранение"}}
        )
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

    def test_four_percent_rounds_each_employee_half_up_to_kopecks(self) -> None:
        self._employee("Сергей Гелингер", repair_order_percent="4")
        self._employee("Алексей Мацурко", repair_order_percent="4")
        card = self.service.create_card(
            {"vehicle": "Lada", "title": "Округление 4%", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работа", "quantity": "1", "price": "312.63"}],
                    "payments": [{"amount": "312.63", "paid_at": "17.07.2026 12:00"}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})

        rows = [
            row
            for row in self.service.get_payroll_report({"month": "2026-07"})["detail_rows"]
            if row["row_type"] == "repair_order_accrual"
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["base_amount"] for row in rows}, {"312.63"})
        self.assertEqual({row["salary_amount"] for row in rows}, {"12.51"})

    def test_reopen_reverses_and_reclose_creates_new_accrual(self) -> None:
        employee = self._employee("Сергей Гелингер", repair_order_percent="4")
        card = self._close_cashless_order()

        self.service.set_repair_order_status({"card_id": card["id"], "status": "open"})
        reversed_ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(reversed_ledger["accrued_total"], "0")
        self.assertEqual(
            {row["kind"] for row in reversed_ledger["journal_rows"]},
            {"repair_order_accrual", "repair_order_accrual_reversal"},
        )

        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        reclosed = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(reclosed["accrued_total"], "60")
        self.assertEqual(
            sum(row["kind"] == "repair_order_accrual" for row in reclosed["journal_rows"]),
            2,
        )

    def test_stale_active_percent_is_reversed_without_reopening_order(self) -> None:
        employee = self._employee("Сергей Гелингер", repair_order_percent="3")
        card = self._close_cashless_order()
        initial = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(initial["accrued_total"], "45")

        self.service.save_employee(
            {
                "employee_id": employee["id"],
                "name": employee["name"],
                "salary_mode": "none",
                "base_salary": "0",
                "work_percent": "0",
                "material_percent": "0",
                "repair_order_percent": "4",
                "payroll_effective_from": "2026-07-13T00:00:00+07:00",
            }
        )
        self.service.update_repair_order(
            {"card_id": card["id"], "repair_order": {"comment": "Сверка процента"}}
        )

        corrected = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(corrected["accrued_total"], "60")
        journal = corrected["journal_rows"]
        self.assertEqual(
            sum(row["kind"] == "repair_order_accrual" for row in journal),
            2,
        )
        self.assertEqual(
            sum(row["kind"] == "repair_order_accrual_reversal" for row in journal),
            1,
        )
        self.assertEqual(sorted(row["percent"] for row in journal), ["3", "3", "4"])

    def test_lost_full_payment_reverses_order_accrual(self) -> None:
        employee = self._employee("Алексей Мацурко", repair_order_percent="4")
        card = self._close_cashless_order()

        payment = card["repair_order"]["payments"][0]
        self.service.cancel_cash_transaction(
            {
                "cashbox_id": payment["cashbox_id"],
                "transaction_id": payment["cash_transaction_id"],
                "reason": "Клиент отменил полную оплату заказ-наряда",
            }
        )
        updated = self.service.get_card({"card_id": card["id"]})["card"]["repair_order"]
        self.assertEqual(updated["status"], "open")
        self.assertFalse(updated["is_paid"])
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger["accrued_total"], "0")
        self.assertEqual(
            {row["kind"] for row in ledger["journal_rows"]},
            {"repair_order_accrual", "repair_order_accrual_reversal"},
        )

    def test_individual_work_formula_has_priority_over_employee_fifty_percent(self) -> None:
        worker = self._employee("Исполнитель", salary_mode="percent_only", work_percent="50")
        card = self.service.create_card(
            {"vehicle": "Honda", "title": "Индивидуальная формула", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [
                        {
                            "name": "Работа",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": worker["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "100",
                            "work_salary_percent_override": "25",
                        }
                    ],
                    "payments": [{"amount": "1000", "paid_at": "17.07.2026 12:00"}],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})[
            "card"
        ]["repair_order"]
        self.assertEqual(closed["works"][0]["work_percent_snapshot"], "25")
        self.assertEqual(closed["works"][0]["salary_amount"], "325")


if __name__ == "__main__":
    unittest.main()
