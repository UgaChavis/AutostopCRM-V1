from __future__ import annotations

EMPLOYEE_SHIFT_ACCRUAL_NOTE = "Выплата за смены за текущую неделю"
EMPLOYEES_MAX_COUNT = 30

PAYROLL_MODE_NONE = "none"
PAYROLL_MODE_SALARY_ONLY = "salary_only"
PAYROLL_MODE_PERCENT_ONLY = "percent_only"
PAYROLL_MODE_SALARY_PLUS_PERCENT = "salary_plus_percent"
PAYROLL_ALLOWED_MODES = {
    PAYROLL_MODE_NONE,
    PAYROLL_MODE_SALARY_ONLY,
    PAYROLL_MODE_PERCENT_ONLY,
    PAYROLL_MODE_SALARY_PLUS_PERCENT,
}


def repair_order_payroll_scheme(percent: object) -> str:
    return f"{percent}% от стоимости заказ-наряда за наличный расчёт"
