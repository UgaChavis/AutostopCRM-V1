"""Build the full employee list from a detached, consistent store snapshot."""

from __future__ import annotations

from typing import Any

from ..models import Card


def build_full_employee_list(service: Any, snapshot: dict[str, Any], month: str) -> dict:
    settings = snapshot["settings"]
    cards = snapshot["cards"]
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

    # Group ledger inputs once instead of scanning every operation per employee.
    employee_ids = set(employees_by_id)
    cards_by_employee: dict[str, list[Card]] = {key: [] for key in employee_ids}
    for card in cards:
        order = card.repair_order
        if order.payroll_postings:
            card_employee_ids = {posting.get("employee_id") for posting in order.payroll_postings}
        else:
            card_employee_ids = {
                service._work_salary_employee_id(row)
                for row in order.works
                if row.salary_accrued_at
            }
            card_employee_ids.update(
                service._material_salary_employee_id(row)
                for row in order.materials
                if row.material_salary_accrued_at
            )
        for employee_id in card_employee_ids & employee_ids:
            cards_by_employee[employee_id].append(card)

    def by_employee(rows: list[Any], employee_id_for_row) -> dict[str, list[Any]]:
        grouped: dict[str, list[Any]] = {key: [] for key in employee_ids}
        for row in rows:
            employee_id = employee_id_for_row(row)
            if employee_id in grouped:
                grouped[employee_id].append(row)
        return grouped

    legacy_order_accruals = service._legacy_overall_accruals_without_postings(
        cards, repair_order_accruals
    )
    shifts_by_employee = by_employee(shift_accruals, lambda row: row.get("employee_id"))
    orders_by_employee = by_employee(legacy_order_accruals, lambda row: row.get("employee_id"))
    resets_by_employee = by_employee(salary_balance_resets, lambda row: row.get("employee_id"))
    transactions_by_employee = by_employee(
        snapshot["cash_transactions"], lambda row: row.employee_id
    )
    employee_balances = {}
    for employee in employees:
        employee_id = employee["id"]
        employee_balances[employee_id] = service._build_employee_salary_balance_summary(
            cards_by_employee[employee_id],
            snapshot["cashboxes"],
            transactions_by_employee[employee_id],
            employee,
            shift_accruals=shifts_by_employee[employee_id],
            repair_order_accruals=orders_by_employee[employee_id],
            salary_balance_resets=resets_by_employee[employee_id],
            months=6,
        )["balance_total"]
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
