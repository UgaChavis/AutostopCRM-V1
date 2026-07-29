from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..api.route_registry import PROXIED_WRITE_ROUTES
from ..storage.change_feed_store import (
    CHANGE_FEED_CONSUMER_MAX_LENGTH,
    CHANGE_FEED_PAGE_DEFAULT,
    CHANGE_FEED_PAGE_MAX,
    CHANGE_FEED_TOKEN_MAX_LENGTH,
)

RAW_API_PREFIX = "api:"
CHANGE_FEED_BOOTSTRAP_ROUTE = "/api/change_feed/bootstrap"
CHANGE_FEED_READ_ROUTE = "/api/change_feed/read"
CHANGE_FEED_ACK_ROUTE = "/api/change_feed/ack"
CHANGE_FEED_WRITE_ROUTES = frozenset({CHANGE_FEED_BOOTSTRAP_ROUTE, CHANGE_FEED_ACK_ROUTE})
RAW_API_WRITE_ROUTES = (
    frozenset(route for route in PROXIED_WRITE_ROUTES if route != "/api/get_repair_order")
    | CHANGE_FEED_WRITE_ROUTES
)
RAW_API_READ_ROUTES = frozenset(
    {
        "/api/agent_actions",
        "/api/agent_scheduled_tasks",
        "/api/agent_status",
        "/api/agent_tasks",
        "/api/export_operator_activity",
        "/api/finance_audit",
        CHANGE_FEED_READ_ROUTE,
        "/api/get_ai_chat_knowledge",
        "/api/get_board_revision",
        "/api/get_display_dashboard",
        "/api/get_employee_salary_ledger",
        "/api/get_employee_salary_reconciliation",
        "/api/get_employee_salary_report",
        "/api/get_inspection_sheet_form",
        "/api/get_operator_activity_aggregates",
        "/api/get_operator_activity_details",
        "/api/get_operator_user_report",
        "/api/get_payroll_report",
        "/api/get_repair_order_print_workspace",
        "/api/list_employees",
        "/api/list_operator_activity",
        "/api/list_operator_users",
        "/api/repair_order_number_audit",
    }
)
RAW_API_ROUTES = RAW_API_READ_ROUTES | RAW_API_WRITE_ROUTES

OPTIMISTIC_WRITE_NAMES = frozenset(
    {
        "update_card",
        "update_repair_order",
        "set_repair_order_status",
        "delete_shared_file",
        "api:/api/update_card",
        "api:/api/update_repair_order",
        "api:/api/set_repair_order_status",
        "api:/api/replace_repair_order_works",
        "api:/api/replace_repair_order_materials",
        "api:/api/set_card_ai_autofill",
        "api:/api/delete_gateway_attestation_payment_fixture",
    }
)
DESTRUCTIVE_CAPABILITY_MARKERS = ("delete_", "cancel_", "archive_", "remove_")
VirtualInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _change_feed_schema(route: str) -> dict[str, Any] | None:
    consumer = {
        "type": "string",
        "minLength": 1,
        "maxLength": CHANGE_FEED_CONSUMER_MAX_LENGTH,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$",
    }
    properties: dict[str, Any] = {"consumer_id": consumer}
    required = ["consumer_id"]
    if route == CHANGE_FEED_READ_ROUTE:
        properties.update(
            {
                "cursor": {
                    "anyOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": CHANGE_FEED_TOKEN_MAX_LENGTH,
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                    "description": "Opaque replay cursor returned by the preceding page.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": CHANGE_FEED_PAGE_MAX,
                    "default": CHANGE_FEED_PAGE_DEFAULT,
                },
            }
        )
    elif route == CHANGE_FEED_ACK_ROUTE:
        properties["ack"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": CHANGE_FEED_TOKEN_MAX_LENGTH,
            "description": "Opaque ACK token returned with one delivered page.",
        }
        required.append("ack")
    elif route != CHANGE_FEED_BOOTSTRAP_ROUTE:
        return None
    return {
        "$id": f"autostopcrm-agent-gateway:{route}",
        "title": route,
        "type": "object",
        "description": {
            CHANGE_FEED_BOOTSTRAP_ROUTE: (
                "Read the durable feed checkpoint without opening or acknowledging a delivery."
            ),
            CHANGE_FEED_READ_ROUTE: (
                "Read one replay-safe ordered CRM change-feed page without advancing ACK state."
            ),
            CHANGE_FEED_ACK_ROUTE: ("Explicitly acknowledge one contiguous CRM change-feed page."),
        }[route],
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _find_mapping(
    value: Any,
    key: str,
    expected: Any,
    *,
    depth: int = 0,
) -> dict[str, Any] | None:
    if depth > 7:
        return None
    if isinstance(value, Mapping):
        if key in value and str(value.get(key)) == str(expected):
            return dict(value)
        for item in value.values():
            found = _find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = _find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    return None


def _mapping_subset_matches(expected: Mapping[str, Any], actual: Any) -> bool:
    if not isinstance(actual, Mapping):
        return False
    return all(key in actual and actual.get(key) == value for key, value in expected.items())


def _find_mapping_matching(
    value: Any,
    predicate: Callable[[Mapping[str, Any]], bool],
    *,
    depth: int = 0,
) -> dict[str, Any] | None:
    if depth > 7:
        return None
    if isinstance(value, Mapping):
        if predicate(value):
            return dict(value)
        for item in value.values():
            found = _find_mapping_matching(item, predicate, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = _find_mapping_matching(item, predicate, depth=depth + 1)
            if found is not None:
                return found
    return None


def _cashbox_order_ids(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 7:
        return []
    if isinstance(value, Mapping):
        cashboxes = value.get("cashboxes")
        if isinstance(cashboxes, list):
            rows = [
                item
                for item in cashboxes
                if isinstance(item, Mapping)
                and str(item.get("id") or "").strip()
                and isinstance(item.get("order"), int)
            ]
            if len(rows) == len(cashboxes):
                return [
                    str(item["id"])
                    for item in sorted(
                        rows,
                        key=lambda item: (int(item["order"]), str(item["id"])),
                    )
                ]
        for item in value.values():
            found = _cashbox_order_ids(item, depth=depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = _cashbox_order_ids(item, depth=depth + 1)
            if found:
                return found
    return []


async def verify_virtual_api_write_readback(
    operation: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    invoke: VirtualInvoker,
) -> dict[str, Any] | None:
    """Return exact verification for virtual writes that have a stable readback."""

    if operation == "api:/api/save_employee":
        created = _find_mapping_matching(
            result,
            lambda item: bool(
                str(item.get("id") or "").strip()
                and str(item.get("name") or "").strip()
                and str(item.get("updated_at") or "").strip()
            ),
        )
        employee_id = str((created or {}).get("id") or "")
        readback = await invoke("api:/api/list_employees", {})
        employee = _find_mapping(readback, "id", employee_id) if employee_id else None
        passed = bool(
            result.get("ok")
            and readback.get("ok")
            and employee_id
            and isinstance(employee, dict)
            and str(employee.get("name") or "") == str(arguments.get("name") or "")
            and str(employee.get("updated_at") or "")
            == str((created or {}).get("updated_at") or "")
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_employee_list_readback",
            "evidence": {
                "employee_id": employee_id,
                "employee_present": isinstance(employee, dict),
                "name_exact": str((employee or {}).get("name") or "")
                == str(arguments.get("name") or ""),
                "revision_exact": str((employee or {}).get("updated_at") or "")
                == str((created or {}).get("updated_at") or ""),
                "readback_ok": bool(readback.get("ok")),
            },
        }

    if operation == "api:/api/delete_employee":
        employee_id = str(arguments.get("employee_id") or "").strip()
        readback = await invoke("api:/api/list_employees", {})
        employee = _find_mapping(readback, "id", employee_id) if employee_id else None
        requested_shift_accrual_ids = {
            str(item)
            for item in arguments.get("attestation_cleanup_shift_accrual_ids") or []
            if str(item)
        }
        cleanup_meta = _find_mapping_matching(
            result,
            lambda item: (
                "attestation_shift_cleanup" in item and "removed_shift_accrual_ids" in item
            ),
        )
        shift_cleanup_exact = not requested_shift_accrual_ids or bool(
            isinstance(cleanup_meta, dict)
            and cleanup_meta.get("attestation_shift_cleanup") is True
            and {
                str(item)
                for item in cleanup_meta.get("removed_shift_accrual_ids") or []
                if str(item)
            }
            == requested_shift_accrual_ids
        )
        return {
            "required": True,
            "passed": bool(
                result.get("ok")
                and readback.get("ok")
                and employee_id
                and employee is None
                and shift_cleanup_exact
            ),
            "check": "exact_employee_absence_readback",
            "evidence": {
                "employee_id": employee_id,
                "employee_absent": employee is None,
                "shift_cleanup_count": len(requested_shift_accrual_ids),
                "shift_cleanup_exact": shift_cleanup_exact,
                "readback_ok": bool(readback.get("ok")),
            },
        }

    if operation == "api:/api/delete_gateway_attestation_payment_fixture":
        card_id = str(arguments.get("card_id") or "").strip()
        cashbox_id = str(
            (
                _find_mapping_matching(
                    result,
                    lambda item: "removed_transaction_ids" in item and "cashbox_id" in item,
                )
                or {}
            ).get("cashbox_id")
            or ""
        )
        expected_transaction_ids = {
            str(item) for item in arguments.get("expected_transaction_ids") or [] if str(item)
        }
        result_meta = _find_mapping_matching(
            result,
            lambda item: (
                "balance_minor_before" in item
                and "balance_minor_after" in item
                and "removed_effect_minor" in item
                and "removed_transaction_ids" in item
            ),
        )
        card_readback = await invoke("get_card", {"card_id": card_id})
        cashbox_readback = await invoke(
            "get_cashbox",
            {"cashbox_id": cashbox_id, "transaction_limit": 100},
        )
        card = _find_mapping(card_readback, "id", card_id) if card_id else None
        cashbox = _find_mapping(cashbox_readback, "id", cashbox_id) if cashbox_id else None
        order = (
            card.get("repair_order")
            if isinstance(card, dict) and isinstance(card.get("repair_order"), Mapping)
            else {}
        )
        removed_absent = all(
            _find_mapping(cashbox_readback, "id", transaction_id) is None
            for transaction_id in expected_transaction_ids
        )
        balance_restored = bool(
            isinstance(result_meta, dict)
            and int(result_meta.get("removed_effect_minor") or 0) == 100
            and int(result_meta.get("balance_minor_before") or 0)
            - int(result_meta.get("balance_minor_after") or 0)
            == int(result_meta.get("removed_effect_minor") or 0)
            and int(result_meta.get("balance_minor_after") or 0)
            == (
                (cashbox.get("statistics") or {}).get("balance_minor")
                if isinstance(cashbox, dict)
                else None
            )
        )
        return {
            "required": True,
            "passed": bool(
                result.get("ok")
                and card_readback.get("ok")
                and cashbox_readback.get("ok")
                and isinstance(card, dict)
                and isinstance(cashbox, dict)
                and not order.get("works")
                and not order.get("materials")
                and not order.get("payments")
                and removed_absent
                and balance_restored
            ),
            "check": "exact_attestation_payment_fixture_absence_readback",
            "evidence": {
                "card_id": card_id,
                "cashbox_id": cashbox_id,
                "transaction_count": len(expected_transaction_ids),
                "repair_order_empty": bool(order)
                and not order.get("works")
                and not order.get("materials")
                and not order.get("payments"),
                "transactions_absent": removed_absent,
                "removed_effect_minor": int((result_meta or {}).get("removed_effect_minor") or 0),
                "balance_restored": balance_restored,
            },
        }

    if operation in {
        "create_employee_salary_transaction",
        "api:/api/create_employee_salary_transaction",
    }:
        employee_id = str(arguments.get("employee_id") or "").strip()
        cashbox_id = str(arguments.get("cashbox_id") or "").strip()
        expected_employee_updated_at = str(
            arguments.get("expected_employee_updated_at") or ""
        ).strip()
        expected_cashbox_updated_at = str(
            arguments.get("expected_cashbox_updated_at") or ""
        ).strip()
        transaction = _find_mapping_matching(
            result,
            lambda item: bool(
                str(item.get("id") or "").strip()
                and str(item.get("employee_id") or "") == employee_id
                and str(item.get("cashbox_id") or "") == cashbox_id
                and str(item.get("direction") or "") == "expense"
                and str(item.get("transaction_kind") or "") in {"salary_payout", "salary_advance"}
            ),
        )
        transaction_id = str((transaction or {}).get("id") or "")
        cashbox_readback = await invoke(
            "get_cashbox",
            {"cashbox_id": cashbox_id, "transaction_limit": 50},
        )
        employee_readback = await invoke("api:/api/list_employees", {})
        ledger_readback = await invoke(
            "api:/api/get_employee_salary_ledger",
            {"employee_id": employee_id, "months": 1},
        )
        cash_transaction = (
            _find_mapping(cashbox_readback, "id", transaction_id) if transaction_id else None
        )
        employee = _find_mapping(employee_readback, "id", employee_id) if employee_id else None
        ledger_row = (
            _find_mapping(ledger_readback, "transaction_id", transaction_id)
            if transaction_id
            else None
        )
        cashbox = _find_mapping(cashbox_readback, "id", cashbox_id) if cashbox_id else None
        amount_minor = int((transaction or {}).get("amount_minor") or 0)
        requested_amount_minor = int(arguments.get("amount_minor") or 0)
        passed = bool(
            result.get("ok")
            and cashbox_readback.get("ok")
            and employee_readback.get("ok")
            and ledger_readback.get("ok")
            and transaction_id
            and requested_amount_minor > 0
            and amount_minor == requested_amount_minor
            and isinstance(cash_transaction, dict)
            and int(cash_transaction.get("amount_minor") or 0) == requested_amount_minor
            and str(cash_transaction.get("employee_id") or "") == employee_id
            and isinstance(employee, dict)
            and str(employee.get("updated_at") or "") == expected_employee_updated_at
            and isinstance(cashbox, dict)
            and str(cashbox.get("updated_at") or "") != expected_cashbox_updated_at
            and isinstance(ledger_row, dict)
            and int(ledger_row.get("amount_minor") or 0) == requested_amount_minor
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_salary_cashbox_employee_and_ledger_readback",
            "evidence": {
                "transaction_id": transaction_id,
                "cashbox_id": cashbox_id,
                "employee_id": employee_id,
                "amount_exact": amount_minor == requested_amount_minor,
                "cash_transaction_present": isinstance(cash_transaction, dict),
                "employee_revision_exact": str((employee or {}).get("updated_at") or "")
                == expected_employee_updated_at,
                "cashbox_revision_changed": str((cashbox or {}).get("updated_at") or "")
                != expected_cashbox_updated_at,
                "ledger_row_present": isinstance(ledger_row, dict),
            },
        }

    if operation in {
        "create_employee_shift_accrual",
        "api:/api/create_employee_shift_accrual",
    }:
        employee_id = str(arguments.get("employee_id") or "").strip()
        expected_employee_updated_at = str(
            arguments.get("expected_employee_updated_at") or ""
        ).strip()
        requested_amount_minor = int(arguments.get("amount_minor") or 0)
        accrual = _find_mapping_matching(
            result,
            lambda item: bool(
                str(item.get("id") or "").strip()
                and str(item.get("employee_id") or "") == employee_id
                and int(item.get("amount_minor") or 0) == requested_amount_minor
            ),
        )
        accrual_id = str((accrual or {}).get("id") or "")
        employee_readback = await invoke("api:/api/list_employees", {})
        ledger_readback = await invoke(
            "api:/api/get_employee_salary_ledger",
            {"employee_id": employee_id, "months": 1},
        )
        employee = _find_mapping(employee_readback, "id", employee_id) if employee_id else None
        ledger_row = (
            _find_mapping(ledger_readback, "accrual_id", accrual_id) if accrual_id else None
        )
        passed = bool(
            result.get("ok")
            and employee_readback.get("ok")
            and ledger_readback.get("ok")
            and accrual_id
            and requested_amount_minor > 0
            and isinstance(employee, dict)
            and str(employee.get("updated_at") or "") == expected_employee_updated_at
            and isinstance(ledger_row, dict)
            and int(ledger_row.get("amount_minor") or 0) == requested_amount_minor
            and str(ledger_row.get("kind") or "") == "shift_accrual"
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_shift_accrual_employee_and_ledger_readback",
            "evidence": {
                "accrual_id": accrual_id,
                "employee_id": employee_id,
                "amount_exact": int((accrual or {}).get("amount_minor") or 0)
                == requested_amount_minor,
                "employee_revision_exact": str((employee or {}).get("updated_at") or "")
                == expected_employee_updated_at,
                "ledger_row_present": isinstance(ledger_row, dict),
            },
        }

    if operation in {
        "cancel_cash_transaction",
        "api:/api/cancel_cash_transaction",
    }:
        transaction_id = str(arguments.get("transaction_id") or "").strip()
        cashbox_id = str(arguments.get("cashbox_id") or "").strip()
        expected_cashbox_updated_at = str(
            arguments.get("expected_cashbox_updated_at") or ""
        ).strip()
        cancelled = _find_mapping(result, "id", transaction_id) if transaction_id else None
        cancellation = _find_mapping_matching(
            result,
            lambda item: bool(
                str(item.get("id") or "").strip()
                and str(item.get("transaction_kind") or "") == "cashbox_cancellation"
                and str(item.get("related_transaction_id") or "") == transaction_id
            ),
        )
        cancellation_id = str((cancellation or {}).get("id") or "")
        cashbox_readback = await invoke(
            "get_cashbox",
            {"cashbox_id": cashbox_id, "transaction_limit": 50},
        )
        cashbox = _find_mapping(cashbox_readback, "id", cashbox_id) if cashbox_id else None
        cancelled_readback = (
            _find_mapping(cashbox_readback, "id", transaction_id) if transaction_id else None
        )
        cancellation_readback = (
            _find_mapping(cashbox_readback, "id", cancellation_id) if cancellation_id else None
        )
        related_transaction_id = str(
            (
                _find_mapping_matching(
                    result,
                    lambda item: "related_transaction_id" in item and "related_cashbox_id" in item,
                )
                or {}
            ).get("related_transaction_id")
            or ""
        )
        related_cashbox_id = str(
            (
                _find_mapping_matching(
                    result,
                    lambda item: "related_transaction_id" in item and "related_cashbox_id" in item,
                )
                or {}
            ).get("related_cashbox_id")
            or ""
        )
        related_cancellation = _find_mapping_matching(
            result,
            lambda item: bool(
                related_transaction_id
                and str(item.get("id") or "").strip()
                and str(item.get("transaction_kind") or "") == "cashbox_cancellation"
                and str(item.get("related_transaction_id") or "") == related_transaction_id
            ),
        )
        related_cancellation_id = str((related_cancellation or {}).get("id") or "")
        related_readback_ok = True
        if related_transaction_id or related_cashbox_id:
            related_cashbox_readback = await invoke(
                "get_cashbox",
                {"cashbox_id": related_cashbox_id, "transaction_limit": 50},
            )
            related_cashbox = (
                _find_mapping(related_cashbox_readback, "id", related_cashbox_id)
                if related_cashbox_id
                else None
            )
            related_cancelled_readback = (
                _find_mapping(
                    related_cashbox_readback,
                    "id",
                    related_transaction_id,
                )
                if related_transaction_id
                else None
            )
            related_cancellation_readback = (
                _find_mapping(
                    related_cashbox_readback,
                    "id",
                    related_cancellation_id,
                )
                if related_cancellation_id
                else None
            )
            related_readback_ok = bool(
                related_cashbox_readback.get("ok")
                and isinstance(related_cashbox, dict)
                and str(related_cashbox.get("updated_at") or "")
                != str(arguments.get("expected_related_cashbox_updated_at") or "")
                and isinstance(related_cancelled_readback, dict)
                and str(related_cancelled_readback.get("transaction_kind") or "")
                == "cashbox_cancelled"
                and isinstance(related_cancellation_readback, dict)
                and str(related_cancellation_readback.get("related_transaction_id") or "")
                == related_transaction_id
            )
        payment_card_id = str(
            (
                _find_mapping_matching(
                    result,
                    lambda item: "repair_order_card_id" in item,
                )
                or {}
            ).get("repair_order_card_id")
            or ""
        )
        payment_readback_ok = True
        if payment_card_id:
            payment_readback = await invoke(
                "get_repair_order",
                {"card_id": payment_card_id},
            )
            payment_readback_ok = bool(
                payment_readback.get("ok")
                and _find_mapping(payment_readback, "cash_transaction_id", transaction_id) is None
            )
        passed = bool(
            result.get("ok")
            and cashbox_readback.get("ok")
            and isinstance(cancelled, dict)
            and str(cancelled.get("transaction_kind") or "") == "cashbox_cancelled"
            and isinstance(cancellation, dict)
            and isinstance(cancelled_readback, dict)
            and str(cancelled_readback.get("transaction_kind") or "") == "cashbox_cancelled"
            and isinstance(cancellation_readback, dict)
            and str(cancellation_readback.get("related_transaction_id") or "") == transaction_id
            and isinstance(cashbox, dict)
            and str(cashbox.get("updated_at") or "") != expected_cashbox_updated_at
            and payment_readback_ok
            and related_readback_ok
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_cash_cancellation_and_optional_payment_readback",
            "evidence": {
                "transaction_id": transaction_id,
                "cancellation_transaction_id": cancellation_id,
                "cashbox_id": cashbox_id,
                "cancelled_kind_exact": str(
                    (cancelled_readback or {}).get("transaction_kind") or ""
                )
                == "cashbox_cancelled",
                "cancellation_pair_exact": str(
                    (cancellation_readback or {}).get("related_transaction_id") or ""
                )
                == transaction_id,
                "cashbox_revision_changed": str((cashbox or {}).get("updated_at") or "")
                != expected_cashbox_updated_at,
                "payment_readback_ok": payment_readback_ok,
                "related_readback_ok": related_readback_ok,
            },
        }

    if operation in {
        "cancel_last_cash_transaction",
        "api:/api/cancel_last_cash_transaction",
    }:
        transaction_id = str(arguments.get("transaction_id") or "").strip()
        cashbox_id = str(arguments.get("cashbox_id") or "").strip()
        expected_cashbox_updated_at = str(
            arguments.get("expected_cashbox_updated_at") or ""
        ).strip()
        cancelled = _find_mapping(result, "id", transaction_id) if transaction_id else None
        cashbox_readback = await invoke(
            "get_cashbox",
            {"cashbox_id": cashbox_id, "transaction_limit": 50},
        )
        cashbox = _find_mapping(cashbox_readback, "id", cashbox_id) if cashbox_id else None
        removed_readback = (
            _find_mapping(cashbox_readback, "id", transaction_id) if transaction_id else None
        )
        payment_card_id = str(
            (
                _find_mapping_matching(
                    result,
                    lambda item: "repair_order_card_id" in item,
                )
                or {}
            ).get("repair_order_card_id")
            or ""
        )
        payment_readback_ok = True
        if payment_card_id:
            payment_readback = await invoke(
                "get_repair_order",
                {"card_id": payment_card_id},
            )
            payment_readback_ok = bool(
                payment_readback.get("ok")
                and _find_mapping(payment_readback, "cash_transaction_id", transaction_id) is None
            )
        passed = bool(
            result.get("ok")
            and cashbox_readback.get("ok")
            and isinstance(cancelled, dict)
            and removed_readback is None
            and isinstance(cashbox, dict)
            and str(cashbox.get("updated_at") or "") != expected_cashbox_updated_at
            and payment_readback_ok
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_cancelled_last_transaction_absence_readback",
            "evidence": {
                "transaction_id": transaction_id,
                "cashbox_id": cashbox_id,
                "transaction_absent": removed_readback is None,
                "cashbox_revision_changed": str((cashbox or {}).get("updated_at") or "")
                != expected_cashbox_updated_at,
                "payment_readback_ok": payment_readback_ok,
            },
        }

    if operation in {
        "apply_finance_audit_safe_fixes",
        "api:/api/finance_audit/apply_safe_fixes",
    }:
        selected_issue_ids = [
            str(item) for item in arguments.get("issue_ids", []) if isinstance(item, str) and item
        ]
        expected_issue_ids = [
            str(item)
            for item in arguments.get("expected_issue_ids", [])
            if isinstance(item, str) and item
        ]
        dry_run = bool(arguments.get("dry_run", True))
        audit_readback = await invoke("api:/api/finance_audit", {})
        actual_issue_ids = [
            str(item.get("id") or "")
            for item in (_find_mapping(audit_readback, "issues", None) or {}).get("issues", [])
            if isinstance(item, Mapping) and str(item.get("id") or "")
        ]
        if not actual_issue_ids:
            audit_payload = _find_mapping_matching(
                audit_readback,
                lambda item: isinstance(item.get("issues"), list),
            )
            actual_issue_ids = [
                str(item.get("id") or "")
                for item in (audit_payload or {}).get("issues", [])
                if isinstance(item, Mapping) and str(item.get("id") or "")
            ]
        safe_fix = _find_mapping_matching(
            result,
            lambda item: str(item.get("kind") or "") == "restore_missing_employee",
        )
        employee_id = str((safe_fix or {}).get("employee_id") or "")
        employee_readback = await invoke("api:/api/list_employees", {})
        employee = _find_mapping(employee_readback, "id", employee_id) if employee_id else None
        expected_after = (
            expected_issue_ids
            if dry_run
            else [
                issue_id
                for issue_id in expected_issue_ids
                if issue_id not in set(selected_issue_ids)
            ]
        )
        meta = _find_mapping(result, "dry_run", dry_run)
        passed = bool(
            result.get("ok")
            and audit_readback.get("ok")
            and employee_readback.get("ok")
            and selected_issue_ids
            and expected_issue_ids
            and isinstance(meta, dict)
            and bool(meta.get("dry_run")) is dry_run
            and actual_issue_ids == expected_after
            and (
                (dry_run and employee is None and not bool(meta.get("changed")))
                or (
                    not dry_run
                    and isinstance(employee, dict)
                    and not bool(employee.get("is_active"))
                    and bool(meta.get("changed"))
                )
            )
        )
        return {
            "required": True,
            "passed": passed,
            "check": (
                "finance_audit_dry_run_exact_no_change_readback"
                if dry_run
                else "finance_audit_selected_fix_exact_readback"
            ),
            "evidence": {
                "selected_count": len(selected_issue_ids),
                "issue_snapshot_exact": actual_issue_ids == expected_after,
                "employee_id": employee_id,
                "employee_present": isinstance(employee, dict),
                "dry_run": dry_run,
            },
        }

    if operation == "reorder_cashboxes":
        expected_before = [
            str(item)
            for item in arguments.get("expected_cashbox_ids", [])
            if isinstance(item, str) and item
        ]
        cashbox_id = str(arguments.get("cashbox_id") or "").strip()
        before_cashbox_id = str(
            arguments.get("before_cashbox_id")
            or arguments.get("before_id")
            or arguments.get("target_cashbox_id")
            or ""
        ).strip()
        expected_after = list(expected_before)
        if cashbox_id in expected_after:
            expected_after.remove(cashbox_id)
            if before_cashbox_id and before_cashbox_id in expected_after:
                expected_after.insert(expected_after.index(before_cashbox_id), cashbox_id)
            elif not before_cashbox_id:
                expected_after.append(cashbox_id)
            else:
                expected_after = []
        else:
            expected_after = []
        readback = await invoke(
            "list_cashboxes",
            {"limit": max(20, len(expected_before))},
        )
        result_order = _cashbox_order_ids(result)
        actual_order = _cashbox_order_ids(readback)
        passed = bool(
            result.get("ok")
            and readback.get("ok")
            and expected_after
            and result_order == expected_after
            and actual_order == expected_after
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_cashbox_order_readback",
            "evidence": {
                "cashbox_id": cashbox_id,
                "before_cashbox_id": before_cashbox_id,
                "expected_count": len(expected_after),
                "result_order_exact": result_order == expected_after,
                "readback_order_exact": actual_order == expected_after,
                "readback_ok": bool(readback.get("ok")),
            },
        }

    if operation == "create_cashbox_transfer":
        source_cashbox_id = str(
            arguments.get("from_cashbox_id") or arguments.get("cashbox_id") or ""
        ).strip()
        target_cashbox_id = str(
            arguments.get("to_cashbox_id") or arguments.get("target_cashbox_id") or ""
        ).strip()
        source_transaction = (
            _find_mapping(result, "cashbox_id", source_cashbox_id) if source_cashbox_id else None
        )
        target_transaction = (
            _find_mapping(result, "cashbox_id", target_cashbox_id) if target_cashbox_id else None
        )
        source_transaction_id = str((source_transaction or {}).get("id") or "")
        target_transaction_id = str((target_transaction or {}).get("id") or "")
        source_readback = (
            await invoke(
                "get_cashbox",
                {"cashbox_id": source_cashbox_id, "transaction_limit": 50},
            )
            if source_cashbox_id
            else {}
        )
        target_readback = (
            await invoke(
                "get_cashbox",
                {"cashbox_id": target_cashbox_id, "transaction_limit": 50},
            )
            if target_cashbox_id
            else {}
        )
        same_group = bool(
            (source_transaction or {}).get("transfer_group_id")
            and (source_transaction or {}).get("transfer_group_id")
            == (target_transaction or {}).get("transfer_group_id")
        )
        pair_linked = bool(
            source_transaction_id
            and target_transaction_id
            and str((source_transaction or {}).get("related_transaction_id") or "")
            == target_transaction_id
            and str((target_transaction or {}).get("related_transaction_id") or "")
            == source_transaction_id
        )
        passed = bool(
            result.get("ok")
            and source_readback.get("ok")
            and target_readback.get("ok")
            and str((source_transaction or {}).get("direction") or "") == "expense"
            and str((target_transaction or {}).get("direction") or "") == "income"
            and same_group
            and pair_linked
            and _find_mapping(source_readback, "id", source_transaction_id)
            and _find_mapping(target_readback, "id", target_transaction_id)
        )
        return {
            "required": True,
            "passed": passed,
            "check": "exact_cashbox_transfer_pair_readback",
            "evidence": {
                "source_cashbox_id": source_cashbox_id,
                "target_cashbox_id": target_cashbox_id,
                "source_transaction_id": source_transaction_id,
                "target_transaction_id": target_transaction_id,
                "same_group": same_group,
                "pair_linked": pair_linked,
                "source_readback_ok": bool(source_readback.get("ok")),
                "target_readback_ok": bool(target_readback.get("ok")),
            },
        }

    if operation == "update_display_dashboard_message":
        expected_revision = str(arguments.get("expected_revision") or "").strip()
        dry_run = arguments.get("dry_run") is True
        proposed = _find_mapping(
            result,
            "schema_version",
            "display_dashboard_message.v1",
        )
        readback = await invoke("api:/api/get_display_dashboard", {})
        actual = _find_mapping(
            readback,
            "schema_version",
            "display_dashboard_message.v1",
        )
        if dry_run:
            dry_run_receipt = _find_mapping(result, "dry_run", True)
            passed = bool(
                result.get("ok")
                and readback.get("ok")
                and dry_run_receipt
                and expected_revision
                and str((actual or {}).get("revision") or "") == expected_revision
            )
            check = "display_dashboard_message_dry_run_without_write"
        else:
            expected_state = {
                key: (proposed or {}).get(key)
                for key in ("revision", "body_html", "image_file_ids")
            }
            passed = bool(
                result.get("ok")
                and readback.get("ok")
                and proposed
                and expected_state.get("revision")
                and _mapping_subset_matches(expected_state, actual)
            )
            check = "exact_display_dashboard_message_readback"
        return {
            "required": True,
            "passed": passed,
            "check": check,
            "evidence": {
                "expected_revision": expected_revision,
                "proposed_revision": str((proposed or {}).get("revision") or ""),
                "actual_revision": str((actual or {}).get("revision") or ""),
                "image_count": len((actual or {}).get("image_file_ids") or []),
                "readback_ok": bool(readback.get("ok")),
                "dry_run": dry_run,
            },
        }

    if operation in {
        f"api:{CHANGE_FEED_BOOTSTRAP_ROUTE}",
        f"api:{CHANGE_FEED_ACK_ROUTE}",
    }:
        consumer_id = str(arguments.get("consumer_id") or "").strip()
        expected = _find_mapping(result, "consumer_id", consumer_id) if consumer_id else None
        readback = (
            await invoke(
                f"api:{CHANGE_FEED_BOOTSTRAP_ROUTE}",
                {"consumer_id": consumer_id},
            )
            if consumer_id
            else {}
        )
        actual = _find_mapping(readback, "consumer_id", consumer_id) if consumer_id else None
        expected_generation = str((expected or {}).get("generation") or "")
        expected_acked = (expected or {}).get("acked_sequence")
        passed = bool(
            result.get("ok")
            and readback.get("ok")
            and expected_generation
            and expected_acked is not None
            and str((actual or {}).get("generation") or "") == expected_generation
            and (actual or {}).get("acked_sequence") == expected_acked
        )
        return {
            "required": True,
            "passed": passed,
            "check": (
                "exact_change_feed_ack_checkpoint"
                if operation == f"api:{CHANGE_FEED_ACK_ROUTE}"
                else "exact_change_feed_bootstrap_checkpoint"
            ),
            "evidence": {
                "consumer_id": consumer_id,
                "generation": expected_generation,
                "acked_sequence": expected_acked,
                "readback_ok": bool(readback.get("ok")),
            },
        }

    card_id = str(arguments.get("card_id") or "").strip()
    if operation == "api:/api/set_card_ai_autofill":
        expected_card = _find_mapping(result, "id", card_id) if card_id else None
        readback = await invoke("get_card", {"card_id": card_id}) if card_id else {}
        actual_card = _find_mapping(readback, "id", card_id) if card_id else None
        state_fields = (
            "ai_autofill_active",
            "ai_autofill_until",
            "ai_next_run_at",
            "ai_autofill_prompt",
            "last_card_fingerprint",
            "ai_run_count",
            "updated_at",
        )
        expected_state = {
            field: expected_card[field]
            for field in state_fields
            if isinstance(expected_card, dict) and field in expected_card
        }
        return {
            "required": True,
            "passed": bool(
                result.get("ok")
                and readback.get("ok")
                and expected_state
                and _mapping_subset_matches(expected_state, actual_card)
            ),
            "check": "exact_card_ai_autofill_readback",
            "evidence": {
                "card_id": card_id,
                "state_fields": sorted(expected_state),
                "readback_ok": bool(readback.get("ok")),
            },
        }
    if operation == "api:/api/open_card":
        readback = (
            await invoke(
                "api:/api/list_operator_activity",
                {
                    "action": "card_opened",
                    "source": "mcp_agent_gateway_v2",
                    "query": card_id,
                    "limit": 10,
                },
            )
            if card_id
            else {}
        )
        activity = _find_mapping(readback, "object_id", card_id) if card_id else None
        return {
            "required": True,
            "passed": bool(
                result.get("ok")
                and readback.get("ok")
                and isinstance(activity, dict)
                and activity.get("action") == "card_opened"
                and activity.get("source") == "mcp_agent_gateway_v2"
            ),
            "check": "exact_operator_activity_readback",
            "evidence": {
                "card_id": card_id,
                "activity_id": str((activity or {}).get("id") or ""),
                "readback_ok": bool(readback.get("ok")),
            },
        }
    return None


def schema_hash(schema: Mapping[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def virtual_api_schema(route: str) -> dict[str, Any]:
    """Bind raw-schema confirmation to one exact internal API route."""

    change_feed_schema = _change_feed_schema(route)
    if change_feed_schema is not None:
        return change_feed_schema
    return {
        "$id": f"autostopcrm-agent-gateway:{route}",
        "title": route,
        "type": "object",
        "description": (
            f"Guarded JSON-object fallback for {route}. The hash is bound to this exact route; "
            "resolve target ids with focused reads and inspect the corresponding API contract "
            "before execution."
        ),
        "additionalProperties": True,
    }


def request_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def virtual_api_route(name: str) -> str | None:
    normalized = str(name or "").strip()
    if not normalized.startswith(RAW_API_PREFIX):
        return None
    route = normalized.removeprefix(RAW_API_PREFIX)
    return route if route in RAW_API_ROUTES else None


def virtual_api_risk(route: str, name: str) -> str:
    if route in RAW_API_READ_ROUTES:
        return "read"
    normalized = str(name or "").casefold()
    if any(marker in normalized for marker in DESTRUCTIVE_CAPABILITY_MARKERS):
        return "destructive"
    return "write"


def virtual_api_name(route: str) -> str:
    return f"{RAW_API_PREFIX}{route}"


__all__ = [
    "CHANGE_FEED_ACK_ROUTE",
    "CHANGE_FEED_BOOTSTRAP_ROUTE",
    "CHANGE_FEED_READ_ROUTE",
    "CHANGE_FEED_WRITE_ROUTES",
    "DESTRUCTIVE_CAPABILITY_MARKERS",
    "OPTIMISTIC_WRITE_NAMES",
    "RAW_API_READ_ROUTES",
    "RAW_API_ROUTES",
    "RAW_API_WRITE_ROUTES",
    "request_fingerprint",
    "schema_hash",
    "virtual_api_name",
    "virtual_api_risk",
    "virtual_api_route",
    "virtual_api_schema",
    "verify_virtual_api_write_readback",
]
