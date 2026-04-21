from __future__ import annotations

from copy import deepcopy
from typing import Any


FINANCIAL_EVENT_ACTIONS = {
    "cash_transaction_created",
    "cash_transaction_deleted",
    "cashbox_transfer_created",
    "employee_salary_transaction_created",
}


def sanitize_financial_history_state(state: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(state)

    if isinstance(sanitized.get("cash_transactions"), list):
        sanitized["cash_transactions"] = []

    cards = sanitized.get("cards")
    if isinstance(cards, list):
        for card in cards:
            if not isinstance(card, dict):
                continue
            repair_order = card.get("repair_order")
            if not isinstance(repair_order, dict):
                continue
            _clear_repair_order_payroll_fields(repair_order)

    cashboxes = sanitized.get("cashboxes")
    if isinstance(cashboxes, list):
        for cashbox in cashboxes:
            if not isinstance(cashbox, dict):
                continue
            statistics = cashbox.get("statistics")
            if isinstance(statistics, dict):
                statistics["balance_minor"] = 0
                statistics["transactions_total"] = 0
                statistics["income_total_minor"] = 0
                statistics["expense_total_minor"] = 0

    events = sanitized.get("events")
    if isinstance(events, list):
        sanitized["events"] = [
            event
            for event in events
            if not (
                isinstance(event, dict)
                and str(event.get("action") or "").strip() in FINANCIAL_EVENT_ACTIONS
            )
        ]

    return sanitized


def _clear_repair_order_payroll_fields(repair_order: dict[str, Any]) -> None:
    for row_key in ("works", "materials"):
        rows = repair_order.get(row_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "executor_id" in row:
                row["executor_id"] = ""
            if "executor_name" in row:
                row["executor_name"] = ""
            if "salary_mode_snapshot" in row:
                row["salary_mode_snapshot"] = ""
            if "base_salary_snapshot" in row:
                row["base_salary_snapshot"] = ""
            if "work_percent_snapshot" in row:
                row["work_percent_snapshot"] = ""
            if "salary_amount" in row:
                row["salary_amount"] = ""
            if "salary_accrued_at" in row:
                row["salary_accrued_at"] = ""

    for payment_key in ("payments", "payment_history"):
        payments = repair_order.get(payment_key)
        if not isinstance(payments, list):
            continue
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            if "cash_transaction_id" in payment:
                payment["cash_transaction_id"] = ""
