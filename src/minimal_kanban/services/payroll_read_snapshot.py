"""Detach the inputs consumed by payroll reads, under the service lock.

Card activity, attachments and unrelated settings are deliberately outside this
read model. Repair orders and salary sources remain complete independent copies;
the existing calculators continue to own every financial rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any

from ..models import Card, CashBox, CashTransaction
from ..repair_order import RepairOrder, RepairOrderPayment, RepairOrderRow, RepairOrderTag

PAYROLL_READ_SETTING_KEYS = (
    "employees",
    "employee_shift_accruals",
    "employee_repair_order_accruals",
    "employee_salary_balance_resets",
)


@dataclass(frozen=True, slots=True)
class PayrollReadCard:
    id: str
    vehicle: str
    repair_order: RepairOrder


@dataclass(frozen=True, slots=True)
class SalaryReportCard:
    id: str
    number: str
    closed_at: str
    vehicle: str
    license_plate: str
    registration_plate: str
    works: list[dict[str, Any]]
    materials: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SalaryLedgerOrder:
    number: str
    status: str
    closed_at: str
    vehicle: str
    license_plate: str
    works: list[dict[str, Any]]
    materials: list[dict[str, Any]]
    payroll_postings: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SalaryLedgerCard:
    id: str
    vehicle: str
    display_vehicle: str
    registration_plate: str
    repair_order: SalaryLedgerOrder


@dataclass(frozen=True, slots=True)
class SalaryBalanceReadTransaction:
    employee_id: str
    transaction_kind: str
    amount_minor: int
    created_at: str


_READ_MODELS = (
    PayrollReadCard,
    SalaryBalanceReadTransaction,
    RepairOrder,
    RepairOrderRow,
    RepairOrderPayment,
    RepairOrderTag,
    CashBox,
    CashTransaction,
)
_IMMUTABLE_TYPES = (str, int, float, bool, bytes, type(None))


@lru_cache(maxsize=16)
def _model_field_names(model_type: type) -> tuple[str, ...]:
    """Cache class structure only, never employee data or calculation results."""
    return tuple(field.name for field in fields(model_type))


def _detach(value: Any, memo: dict[int, Any]) -> Any:
    """Detach the known read graph without re-normalizing any financial field."""
    value_type = type(value)
    if value_type in _IMMUTABLE_TYPES:
        return value
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if value_type is dict:
        result = {}
        memo[identity] = result
        for key, item in value.items():
            key = key if type(key) in _IMMUTABLE_TYPES else _detach(key, memo)
            result[key] = item if type(item) in _IMMUTABLE_TYPES else _detach(item, memo)
        return result
    if value_type is list:
        result = []
        memo[identity] = result
        result.extend(
            item if type(item) in _IMMUTABLE_TYPES else _detach(item, memo) for item in value
        )
        return result
    if value_type in _READ_MODELS:
        result = object.__new__(value_type)
        memo[identity] = result
        names = vars(value) if hasattr(value, "__dict__") else _model_field_names(value_type)
        for name in names:
            item = getattr(value, name)
            detached = item if type(item) in _IMMUTABLE_TYPES else _detach(item, memo)
            object.__setattr__(result, name, detached)
        return result
    return deepcopy(value, memo)


def detach_employee_list_inputs(bundle: dict[str, Any]) -> dict[str, Any]:
    """Copy one consistent payroll projection; the caller must hold its store lock."""
    return _detach(
        {
            "settings": {key: bundle["settings"].get(key) for key in PAYROLL_READ_SETTING_KEYS},
            "cards": [
                PayrollReadCard(card.id, card.vehicle, card.repair_order)
                for card in bundle["cards"]
            ],
            # The balance-only calculator omits cashbox labels and receipt revisions.
            # Keep every transaction and every amount; only unused presentation fields
            # are excluded. Do not use this projection for the detailed salary ledger.
            "cashboxes": [],
            "cash_transactions": [
                SalaryBalanceReadTransaction(
                    item.employee_id, item.transaction_kind, item.amount_minor, item.created_at
                )
                for item in bundle["cash_transactions"]
            ],
        },
        {},
    )


def detach_salary_ledger_inputs(
    bundle: dict[str, Any],
    *,
    employee_id: str,
    revision_employee: dict[str, Any] | None = None,
    revision_shifts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Keep every order source used by the ledger and its revision."""
    snapshot = _detach(
        {
            "cashboxes": bundle["cashboxes"],
            "cash_transactions": [
                item for item in bundle["cash_transactions"] if item.employee_id == employee_id
            ],
            "revision_employee": revision_employee,
            "revision_shifts": revision_shifts,
        },
        {},
    )
    snapshot["cards"] = detach_salary_ledger_cards(bundle["cards"])
    return snapshot


def detach_salary_ledger_cards(cards: Iterable[Card]) -> list[SalaryLedgerCard]:
    """Copy order rows, postings and vehicle labels for payroll calculations."""
    results = []
    for card in cards:
        order = card.repair_order
        postings = _detach(order.payroll_postings, {})
        results.append(
            SalaryLedgerCard(
                id=card.id,
                vehicle=card.vehicle,
                display_vehicle=card.vehicle_display(),
                registration_plate=card.vehicle_profile.registration_plate,
                repair_order=SalaryLedgerOrder(
                    number=order.number,
                    status=order.status,
                    closed_at=order.closed_at,
                    vehicle=order.vehicle,
                    license_plate=order.license_plate,
                    works=[] if postings else _salary_rows_snapshot(order.works),
                    materials=[] if postings else _salary_rows_snapshot(order.materials),
                    payroll_postings=postings,
                ),
            )
        )
    return results


def detach_salary_report_cards(cards: Iterable[Card]) -> list[SalaryReportCard]:
    """Detach the orders and vehicle labels required by one monthly report."""
    results = []
    for card in cards:
        order = card.repair_order
        results.append(
            SalaryReportCard(
                id=card.id,
                number=order.number,
                closed_at=order.closed_at,
                vehicle=order.vehicle or card.vehicle_display(),
                license_plate=order.license_plate,
                registration_plate=card.vehicle_profile.registration_plate,
                works=_salary_rows_snapshot(order.works),
                materials=_salary_rows_snapshot(order.materials),
            )
        )
    return results


def _salary_rows_snapshot(rows: Iterable[RepairOrderRow]) -> list[dict[str, Any]]:
    return [row.to_dict() if isinstance(row, RepairOrderRow) else _detach(row, {}) for row in rows]
