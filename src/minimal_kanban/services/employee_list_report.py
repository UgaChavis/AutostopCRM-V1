"""Build the full employee list while the caller holds the service lock."""

from __future__ import annotations

from typing import Any


def build_full_employee_list(service: Any, bundle: dict[str, Any], month: str) -> dict:
    settings = bundle["settings"]
    cards = bundle["cards"]
    employees = service._employees_from_settings(settings)
    employees_by_id = {item["id"]: item for item in employees}
    shift_accruals = service._employee_shift_accruals_from_settings(
        settings, employees_by_id=employees_by_id
    )
    repair_order_accruals = service._employee_repair_order_accruals_from_settings(
        settings, employees_by_id=employees_by_id
    )
    salary_balance_resets = service._employee_salary_balance_resets_from_settings(
        settings, employees_by_id=employees_by_id
    )
    report = service._build_payroll_report(
        cards,
        employees,
        shift_accruals=shift_accruals,
        repair_order_accruals=repair_order_accruals,
        month=month,
    )

    employee_balances = {
        employee["id"]: service._build_employee_salary_balance_summary(
            cards,
            bundle["cashboxes"],
            bundle["cash_transactions"],
            employee,
            shift_accruals=shift_accruals,
            repair_order_accruals=repair_order_accruals,
            salary_balance_resets=salary_balance_resets,
            months=6,
        )["balance_total"]
        for employee in employees
    }
    employees = [
        service._employee_with_current_payroll_term(
            {**employee, "balance_total": employee_balances.get(employee["id"], "0")}
        )
        for employee in employees
    ]
    return {
        "employees": employees,
        "month": month,
        "summary": report["summary"],
        "detail_rows": report["detail_rows"],
    }
