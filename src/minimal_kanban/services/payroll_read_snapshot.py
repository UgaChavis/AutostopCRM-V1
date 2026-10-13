"""Detach the inputs consumed by employee-list payroll reads, under the service lock.

Card activity, attachments and unrelated settings are deliberately outside this
read model. Repair orders and salary sources remain complete independent copies;
the existing calculators continue to own every financial rule.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any

from ..models import CashBox, CashTransaction
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
