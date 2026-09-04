from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SALARY_BALANCE_RESET_PERMISSION = "salary_balance_reset"
EMPLOYEES_CASHBOXES_ACCESS_PERMISSION = "employees_cashboxes_access"
EMPLOYEES_READ_ACCESS_PERMISSION = "employees_read_access"
OPERATOR_PERMISSION_VALUES = frozenset(
    {
        EMPLOYEES_READ_ACCESS_PERMISSION,
        EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
        SALARY_BALANCE_RESET_PERMISSION,
    }
)


def normalize_operator_permissions(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    normalized = {
        str(item or "").strip().casefold()
        for item in value
        if isinstance(item, str) and str(item or "").strip()
    }
    return sorted(normalized & OPERATOR_PERMISSION_VALUES)


def operator_has_permission(session: Any, permission: str) -> bool:
    if not isinstance(session, dict):
        return False
    return str(permission or "").strip().casefold() in normalize_operator_permissions(
        session.get("permissions")
    )


def unknown_operator_permissions(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    return sorted(
        {
            str(item or "").strip().casefold()
            for item in value
            if isinstance(item, str)
            and str(item or "").strip()
            and str(item or "").strip().casefold() not in OPERATOR_PERMISSION_VALUES
        }
    )
