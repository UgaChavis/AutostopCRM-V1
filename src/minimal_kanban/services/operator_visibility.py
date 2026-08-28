from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from ..models import AuditEvent
from ..operator_permissions import (
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    operator_has_permission,
)
from ..repair_order import RepairOrder, RepairOrderRow
from .errors import ServiceError
from .finance_read_core import CASHBOX_NOTIFICATION_SEEN_SETTING_KEY

EMPLOYEES_CASHBOXES_PRIVATE_SETTING_KEYS = frozenset(
    {
        "employees",
        "employee_shift_accruals",
        "employee_repair_order_accruals",
        "employee_salary_balance_resets",
    }
)

EMPLOYEES_CASHBOXES_PRIVATE_EVENT_ACTION_MARKERS = (
    "cash",
    "employee",
    "finance",
    "payment",
    "payroll",
    "salary",
)

EMPLOYEES_CASHBOXES_PRIVATE_EVENT_DETAIL_KEYS = frozenset(
    {
        "amount",
        "amount_minor",
        "cashbox_id",
        "cashbox_name",
        "employee_id",
        "employee_name",
        "payment",
        "payments",
        "payroll_postings",
        "salary",
    }
)

REPAIR_ORDER_PRIVATE_ROW_FIELDS = frozenset(
    {
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
        "work_salary_override_enabled",
        "work_salary_guarantee",
        "work_salary_percent_override",
        "work_salary_cost_price",
        "work_salary_note",
        "material_executor_id_snapshot",
        "material_executor_name_snapshot",
        "material_quantity_snapshot",
        "material_price_snapshot",
        "material_cost_price_snapshot",
        "material_percent_snapshot",
        "material_profit",
        "material_salary_amount",
        "material_salary_accrued_at",
    }
)

OPERATOR_PRIVATE_RESPONSE_FIELDS = REPAIR_ORDER_PRIVATE_ROW_FIELDS | {
    "payroll_postings",
    "payroll_reversals",
}

_REPAIR_ORDER_VISIBLE_ROW_FIELDS = (
    "name",
    "catalog_number",
    "quantity",
    "cost_price",
    "price",
    "total",
    "executor_id",
    "executor_name",
    "inventory_item_id",
    "inventory_movement_id",
    "inventory_unit",
)

_SAFE_REPAIR_ORDER_EVENT_DETAIL_KEYS = frozenset(
    {
        "number",
        "status",
        "works",
        "materials",
    }
)


def operator_can_access_employees_cashboxes(payload: dict[str, Any] | None) -> bool:
    operator_session = (payload or {}).get("_operator_session")
    return operator_session_can_access_employees_cashboxes(operator_session)


def operator_session_can_access_employees_cashboxes(operator_session: object) -> bool:
    return not isinstance(operator_session, dict) or operator_has_permission(
        operator_session,
        EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    )


def is_private_employee_cashbox_event(event: AuditEvent) -> bool:
    action = str(event.action or "").casefold()
    if any(marker in action for marker in EMPLOYEES_CASHBOXES_PRIVATE_EVENT_ACTION_MARKERS):
        return True
    details = event.details if isinstance(event.details, dict) else {}
    return any(key in details for key in EMPLOYEES_CASHBOXES_PRIVATE_EVENT_DETAIL_KEYS)


def visible_audit_events(
    payload: dict[str, Any] | None,
    events: Iterable[AuditEvent],
) -> list[AuditEvent]:
    source_events = list(events)
    if operator_can_access_employees_cashboxes(payload):
        return source_events
    visible: list[AuditEvent] = []
    for event in source_events:
        if str(event.action or "").casefold() == "repair_order_updated":
            safe_event = deepcopy(event)
            details = event.details if isinstance(event.details, dict) else {}
            safe_event.details = {
                key: deepcopy(details[key])
                for key in _SAFE_REPAIR_ORDER_EVENT_DETAIL_KEYS
                if key in details
            }
            visible.append(safe_event)
            continue
        if is_private_employee_cashbox_event(event):
            continue
        visible.append(event)
    return visible


def public_snapshot_settings(
    settings: dict[str, Any],
    *,
    include_employees_cashboxes: bool = True,
) -> dict[str, Any]:
    excluded_keys = {CASHBOX_NOTIFICATION_SEEN_SETTING_KEY}
    if not include_employees_cashboxes:
        excluded_keys.update(EMPLOYEES_CASHBOXES_PRIVATE_SETTING_KEYS)
    return {key: value for key, value in settings.items() if key not in excluded_keys}


def project_operator_result(payload: dict[str, Any] | None, result: Any) -> Any:
    if operator_can_access_employees_cashboxes(payload):
        return result
    return _project_operator_value(result)


def _project_operator_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _project_operator_value(item)
            for key, item in value.items()
            if key not in OPERATOR_PRIVATE_RESPONSE_FIELDS
        }
    if isinstance(value, list):
        return [_project_operator_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_project_operator_value(item) for item in value)
    return value


def preserve_restricted_repair_order_private_fields(
    operator_session: object,
    previous_order: RepairOrder,
    next_order: RepairOrder,
) -> RepairOrder:
    if operator_session_can_access_employees_cashboxes(operator_session):
        return next_order
    _preserve_restricted_rows(previous_order.works, next_order.works, section="works")
    _preserve_restricted_rows(previous_order.materials, next_order.materials, section="materials")
    next_order.payroll_postings = [dict(item) for item in previous_order.payroll_postings]
    return next_order


def _preserve_restricted_rows(
    previous_rows: Iterable[RepairOrderRow],
    next_rows: Iterable[RepairOrderRow],
    *,
    section: str,
) -> None:
    previous = list(previous_rows)
    current = list(next_rows)
    previous_by_id = {row.id: row for row in previous if row.id}
    matched_previous_ids: set[int] = set()
    unmatched_current: list[RepairOrderRow] = []

    for row in current:
        matched = previous_by_id.get(row.id) if row.id else None
        if matched is None or id(matched) in matched_previous_ids:
            unmatched_current.append(row)
            continue
        _restore_restricted_row(matched, row)
        matched_previous_ids.add(id(matched))

    unmatched_previous = [row for row in previous if id(row) not in matched_previous_ids]
    previous_by_fingerprint: dict[tuple[str, ...], list[RepairOrderRow]] = {}
    current_by_fingerprint: dict[tuple[str, ...], list[RepairOrderRow]] = {}
    for row in unmatched_previous:
        previous_by_fingerprint.setdefault(_row_fingerprint(row), []).append(row)
    for row in unmatched_current:
        current_by_fingerprint.setdefault(_row_fingerprint(row), []).append(row)

    restored_current_ids: set[int] = set()
    for fingerprint, rows in current_by_fingerprint.items():
        candidates = previous_by_fingerprint.get(fingerprint, [])
        if len(rows) != 1 or len(candidates) != 1:
            continue
        _restore_restricted_row(candidates[0], rows[0])
        matched_previous_ids.add(id(candidates[0]))
        restored_current_ids.add(id(rows[0]))

    remaining_previous = [row for row in previous if id(row) not in matched_previous_ids]
    remaining_current = [row for row in unmatched_current if id(row) not in restored_current_ids]
    if remaining_previous and remaining_current:
        raise ServiceError(
            "validation_error",
            "Не удалось безопасно сопоставить строки заказ-наряда. Обновите данные и повторите.",
            details={
                "field": f"repair_order.{section}",
                "reason": "row_identity_required",
            },
        )
    for row in remaining_current:
        _clear_restricted_row(row)


def _row_fingerprint(row: RepairOrderRow) -> tuple[str, ...]:
    return tuple(
        str(getattr(row, field_name) or "") for field_name in _REPAIR_ORDER_VISIBLE_ROW_FIELDS
    )


def _restore_restricted_row(previous: RepairOrderRow, current: RepairOrderRow) -> None:
    current.id = previous.id
    for field_name in REPAIR_ORDER_PRIVATE_ROW_FIELDS:
        setattr(current, field_name, getattr(previous, field_name))


def _clear_restricted_row(row: RepairOrderRow) -> None:
    for field_name in REPAIR_ORDER_PRIVATE_ROW_FIELDS:
        setattr(row, field_name, "")
