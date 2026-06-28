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
    _clear_cash_transactions(sanitized)
    _clear_card_payroll_history(sanitized)
    _clear_cashbox_statistics(sanitized)
    _clear_financial_events(sanitized)
    return sanitized


def _clear_cash_transactions(state: dict[str, Any]) -> None:
    if isinstance(state.get("cash_transactions"), list):
        state["cash_transactions"] = []


def _clear_card_payroll_history(state: dict[str, Any]) -> None:
    cards = state.get("cards")
    if not isinstance(cards, list):
        return
    for card in cards:
        if not isinstance(card, dict):
            continue
        repair_order = card.get("repair_order")
        if isinstance(repair_order, dict):
            _clear_repair_order_payroll_fields(repair_order)


def _clear_cashbox_statistics(state: dict[str, Any]) -> None:
    cashboxes = state.get("cashboxes")
    if not isinstance(cashboxes, list):
        return
    for cashbox in cashboxes:
        if not isinstance(cashbox, dict):
            continue
        statistics = cashbox.get("statistics")
        if not isinstance(statistics, dict):
            continue
        statistics["balance_minor"] = 0
        statistics["transactions_total"] = 0
        statistics["income_total_minor"] = 0
        statistics["expense_total_minor"] = 0


def _clear_financial_events(state: dict[str, Any]) -> None:
    events = state.get("events")
    if not isinstance(events, list):
        return
    state["events"] = [
        event
        for event in events
        if not (
            isinstance(event, dict)
            and str(event.get("action") or "").strip() in FINANCIAL_EVENT_ACTIONS
        )
    ]


def _clear_repair_order_payroll_fields(repair_order: dict[str, Any]) -> None:
    for row_key in ("works", "materials"):
        rows = repair_order.get(row_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                _clear_repair_order_payroll_row(row)

    for payment_key in ("payments", "payment_history"):
        payments = repair_order.get(payment_key)
        if not isinstance(payments, list):
            continue
        for payment in payments:
            if isinstance(payment, dict):
                _clear_repair_order_payment_history(payment)


def _clear_repair_order_payroll_row(row: dict[str, Any]) -> None:
    for field_name in (
        "executor_id",
        "executor_name",
        "work_executor_id_snapshot",
        "work_executor_name_snapshot",
        "work_quantity_snapshot",
        "work_price_snapshot",
        "work_total_snapshot",
        "salary_mode_snapshot",
        "base_salary_snapshot",
        "work_percent_snapshot",
        "salary_amount",
        "salary_accrued_at",
    ):
        if field_name in row:
            row[field_name] = ""


def _clear_repair_order_payment_history(payment: dict[str, Any]) -> None:
    if "cash_transaction_id" in payment:
        payment["cash_transaction_id"] = ""
