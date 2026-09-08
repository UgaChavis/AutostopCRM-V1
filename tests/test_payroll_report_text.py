from __future__ import annotations

# ruff: noqa: E402
import sys
import unittest
from decimal import Decimal
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import format_money_minor
from minimal_kanban.services.card_service_payroll import CardServicePayrollMixin
from minimal_kanban.services.payroll_report_text import (
    _material_lines,
    _work_lines,
    employee_salary_report_text,
)


class PayrollReportTextTests(unittest.TestCase):
    def test_empty_report_preserves_owner_facing_message(self) -> None:
        self.assertEqual(
            employee_salary_report_text(
                employee={}, period={"label": "Сентябрь 2026"}, totals={}, days=[]
            ),
            "ОТЧЕТ ПО НАЧИСЛЕНИЯМ\n\nСотрудник: Сотрудник\nПериод: Сентябрь 2026\n\n"
            "За выбранный период начислений по закрытым заказ-нарядам нет.",
        )

    def test_totals_money_columns_round_once_and_preserve_legacy_override(self) -> None:
        service = CardServicePayrollMixin()
        amounts = {
            "base_salary_total": Decimal("1.005"),
            "shift_accrual_total": Decimal("2.005"),
            "work_total": Decimal("100.005"),
            "work_accrued_total": Decimal("3.005"),
            "material_total": Decimal("20.005"),
            "material_cost_total": Decimal("21.005"),
            "material_profit_total": Decimal("-1.005"),
            "material_accrued_total": Decimal("-0.005"),
            "repair_order_accrual_total": Decimal("4.005"),
        }
        totals = service._employee_salary_report_totals_payload(
            repair_order_count=2, work_count=3, **amounts
        )
        expected_minor = [101, 201, 10001, 301, 2001, 2101, -101, -1, 401]
        for name, minor in zip(amounts, expected_minor, strict=True):
            self.assertEqual(totals[f"{name}_minor"], minor)
            self.assertEqual(totals[f"{name}_display"], format_money_minor(minor))
            self.assertEqual(Decimal(totals[name]), Decimal(minor) / 100)
        self.assertEqual(totals["accrued_total_minor"], 1002)
        legacy = service._employee_salary_report_totals_payload(
            repair_order_count=0,
            work_count=0,
            work_total=Decimal("0"),
            accrued_total=Decimal("7.005"),
        )
        self.assertEqual(legacy["work_accrued_total_minor"], 701)
        self.assertEqual(legacy["accrued_total_minor"], 701)

    def test_item_lines_keep_optional_quantity_and_scheme_rules(self) -> None:
        work = {
            "name": "Диагностика",
            "quantity": "",
            "price": "",
            "price_display": "",
            "total_display": "0",
            "accrued_display": "0",
        }
        self.assertEqual(
            _work_lines(work), ["  - Диагностика", "    Стоимость: 0", "    Начислено: 0"]
        )
        work.update(quantity="1", price_display="100", scheme="50%")
        self.assertEqual(
            _work_lines(work),
            [
                "  - Диагностика",
                "    Кол-во: 1 | Цена: 100",
                "    Стоимость: 0",
                "    Схема: 50%",
                "    Начислено: 0",
            ],
        )
        material = dict(
            work, name="Фильтр", cost_price_display="", cost_total_display="0", profit_display="0"
        )
        self.assertIn("    Кол-во: 1 | Цена: 100 | Закупка: -", _material_lines(material))
        material.update(quantity="", price="")
        self.assertEqual(
            _material_lines(material),
            [
                "  - Материал: Фильтр",
                "    Продажа: 0",
                "    Закупка всего: 0",
                "    Прибыль: 0",
                "    Начислено: 0",
            ],
        )

    def test_full_report_keeps_salary_shift_order_reversal_and_day_order(self) -> None:
        totals = CardServicePayrollMixin()._employee_salary_report_totals_payload(
            repair_order_count=1, work_count=0, work_total=Decimal("0")
        )
        accrual = {
            "repair_order_number": "42",
            "base_amount_display": "100",
            "scheme": "4%",
            "amount_display": "4",
        }
        day = {
            "label": "08.09.2026",
            "totals": totals,
            "base_salary_accruals": [{"created_at": "08.09.2026 10:00", "amount_display": "100"}],
            "shift_accruals": [
                {"created_at": "08.09.2026 11:00", "note": "Смена", "amount_display": "200"}
            ],
            "repair_order_accruals": [accrual, dict(accrual, kind="reversal", amount_display="-4")],
            "repair_orders": [
                dict(
                    totals,
                    repair_order_number="42",
                    vehicle="Toyota",
                    license_plate="А123АА",
                    works=[],
                    materials=[],
                )
            ],
        }
        text = employee_salary_report_text(
            employee={"name": "Мастер"},
            period={"label": "Сентябрь 2026"},
            totals=totals,
            days=[day],
        )
        expected = [
            "ОТЧЕТ ПО НАЧИСЛЕНИЯМ",
            "Сотрудник: Мастер",
            "ИТОГО",
            "08.09.2026",
            "Оклад | 08.09.2026 10:00 | начислено: 100",
            "Смены | 08.09.2026 11:00 | Смена | начислено: 200",
            "Начисление от ЗН 42 | база: 100 | схема: 4% | начислено: 4",
            "Отмена от ЗН 42 | база: 100 | схема: 4% | начислено: -4",
            "ЗН 42 | Toyota | госномер: А123АА",
            "Итого за день:",
        ]
        positions = [text.index(fragment) for fragment in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertFalse(text.endswith("\n"))
