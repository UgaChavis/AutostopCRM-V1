from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime as dt
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore

FROZEN_PAYROLL_NOW = dt.fromisoformat("2026-07-18T12:00:00+07:00")


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

    @patch(
        "minimal_kanban.services.card_service.utc_now",
        return_value=FROZEN_PAYROLL_NOW,
    )
    @patch("minimal_kanban.models.utc_now", return_value=FROZEN_PAYROLL_NOW)
    def test_two_independent_four_percent_accruals_exclude_cashless_fees(
        self, _models_clock, _service_clock
    ) -> None:
        sergey = self._employee("Сергей Гелингер", repair_order_percent="4")
        alexey = self._employee("Алексей Мацурко", repair_order_percent="4")
        worker = self._employee("Исполнитель", salary_mode="percent_only", work_percent="50")

        card = self._close_cashless_order(worker_id=worker["id"])
        order = card["repair_order"]
        closed_datetime = dt.strptime(order["closed_at"], "%d.%m.%Y %H:%M")
        report_month = closed_datetime.strftime("%Y-%m")
        report_date = closed_datetime.strftime("%Y-%m-%d")
        self.assertEqual(order["subtotal_total"], "1500")
        self.assertGreater(float(order["taxes_total"]), 0)
        self.assertEqual(order["payments"][0]["payment_method"], "cashless")
        self.assertEqual(order["works"][0]["salary_amount"], "500")

        report = self.service.get_payroll_report({"month": report_month})
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
            {"employee_id": sergey["id"], "month": report_month}
        )
        self.assertEqual(salary_report["totals"]["repair_order_accrual_count"], 1)
        self.assertEqual(salary_report["totals"]["repair_order_accrual_total"], "60")
        self.assertIn("4% от стоимости заказ-наряда за наличный расчёт", salary_report["text"])
        reconciliation = self.service.get_employee_salary_reconciliation(
            {
                "employee_id": sergey["id"],
                "date_from": report_date,
                "date_to": report_date,
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

        with self.assertRaises(ServiceError) as blocked:
            self.service.update_repair_order(
                {"card_id": card["id"], "repair_order": {"comment": "Повторное сохранение"}}
            )
        self.assertEqual(blocked.exception.code, "repair_order_closed_read_only")
        repeated = self.service.get_payroll_report({"month": report_month})
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

    @patch(
        "minimal_kanban.services.card_service.utc_now",
        return_value=FROZEN_PAYROLL_NOW,
    )
    @patch("minimal_kanban.models.utc_now", return_value=FROZEN_PAYROLL_NOW)
    def test_four_percent_rounds_each_employee_half_up_to_kopecks(
        self, _models_clock, _service_clock
    ) -> None:
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
        closed = self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        report_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )

        rows = [
            row
            for row in self.service.get_payroll_report({"month": report_month})["detail_rows"]
            if row["row_type"] == "repair_order_accrual"
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["base_amount"] for row in rows}, {"312.63"})
        self.assertEqual({row["salary_amount"] for row in rows}, {"12.51"})

    def test_reopen_reverses_and_reclose_creates_new_accrual(self) -> None:
        employee = self._employee("Сергей Гелингер", repair_order_percent="4")
        card = self._close_cashless_order()

        current = self.service.get_card({"card_id": card["id"]})["card"]
        self.service.reopen_repair_order(
            {
                "card_id": card["id"],
                "expected_updated_at": current["updated_at"],
                "reason_code": "other",
                "reason_note": "Проверка повторного проведения",
                "idempotency_key": "payroll-reopen",
            }
        )
        reversed_ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(reversed_ledger["accrued_total"], "0")
        self.assertEqual(
            {row["kind"] for row in reversed_ledger["journal_rows"]},
            {"repair_order_accrual", "repair_order_accrual_reversal"},
        )

        current = self.service.get_card({"card_id": card["id"]})["card"]
        self.service.set_repair_order_status(
            {
                "card_id": card["id"],
                "status": "closed",
                "expected_updated_at": current["updated_at"],
                "idempotency_key": "payroll-reclose",
            }
        )
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
        with self.assertRaises(ServiceError) as blocked:
            self.service.update_repair_order(
                {"card_id": card["id"], "repair_order": {"comment": "Сверка процента"}}
            )
        self.assertEqual(blocked.exception.code, "repair_order_closed_read_only")

        corrected = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(corrected["accrued_total"], "45")
        journal = corrected["journal_rows"]
        self.assertEqual(
            sum(row["kind"] == "repair_order_accrual" for row in journal),
            1,
        )
        self.assertEqual(
            sum(row["kind"] == "repair_order_accrual_reversal" for row in journal),
            0,
        )
        self.assertEqual(sorted(row["percent"] for row in journal), ["3"])

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

    def test_repair_order_work_salary_override_accrues_guarantee_plus_percent(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Mercedes GLA",
                "title": "Индивидуальная зарплата по строке",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "77",
                    "status": "open",
                    "vehicle": "Mercedes GLA",
                    "license_plate": "К777КК124",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Ремонт модуля SCM",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_note": "Премия за сложную работу",
                        }
                    ],
                },
            }
        )
        stored_row = updated["card"]["repair_order"]["works"][0]
        self.assertEqual(stored_row["work_salary_override_enabled"], "true")
        self.assertEqual(stored_row["work_salary_guarantee"], "5000")
        self.assertEqual(stored_row["work_salary_percent_override"], "45")

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "45")
        self.assertEqual(closed_row["salary_amount"], "11750")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        detail_row = next(
            row for row in report["detail_rows"] if row["employee_id"] == employee["id"]
        )
        self.assertEqual(detail_row["salary_amount"], "11750")
        employee_report = self.service.get_employee_salary_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        self.assertIn("Выплата исполнителю", employee_report["text"])
        self.assertIn("45", employee_report["text"])

        reconciliation = self.service.get_employee_salary_reconciliation(
            {"employee_id": employee["id"]}
        )
        work_row = next(row for row in reconciliation["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "11750")
        self.assertIn("Выплата исполнителю 5 000,00 ₽ + 45%", work_row["scheme"])
        self.assertIn("Работа 20 000,00 ₽", work_row["calculation_base"])

    def test_repair_order_work_salary_cost_price_reduces_percent_base(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Мастер с себестоимостью",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Себестоимость работы",
                "deadline": {"hours": 2},
            }
        )
        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {
                    "number": "79",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Работа с подрядом",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_cost_price": "3000",
                        }
                    ],
                },
            }
        )
        stored_row = updated["card"]["repair_order"]["works"][0]
        self.assertEqual(stored_row["work_salary_cost_price"], "3000")

        closed = self.service.set_repair_order_status(
            {"card_id": created["card"]["id"], "status": "closed"}
        )
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "45")
        self.assertEqual(closed_row["salary_amount"], "10400")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        reconciliation = self.service.get_employee_salary_reconciliation(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        work_row = next(row for row in reconciliation["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "10400")
        self.assertIn("Работа 20 000,00 ₽", work_row["calculation_base"])
        self.assertIn("Себестоимость работы 3 000,00 ₽", work_row["calculation_base"])

    def test_repair_order_work_salary_cost_price_reduces_default_percent_accrual(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Процентный мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "Себестоимость без индивидуального процента",
                "deadline": {"hours": 2},
            }
        )
        self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {
                    "number": "80",
                    "status": "open",
                    "vehicle": "Nissan X-Trail",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Работа с сервисной себестоимостью",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                            "work_salary_cost_price": "1000",
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status(
            {"card_id": created["card"]["id"], "status": "closed"}
        )
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "30")
        self.assertEqual(closed_row["salary_amount"], "1200")

    def test_repair_order_work_salary_override_guarantee_above_total_uses_zero_base(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Премиальный Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        card = self.service.create_card(
            {
                "vehicle": "Honda Civic",
                "title": "Гарантия выше суммы",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "78",
                    "status": "open",
                    "vehicle": "Honda Civic",
                    "payments": [
                        {
                            "amount": "3000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Сложная диагностика",
                            "quantity": "1",
                            "price": "3000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "80",
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "5000")
        self.assertEqual(closed["repair_order"]["works"][0]["work_percent_snapshot"], "80")


if __name__ == "__main__":
    unittest.main()
