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


async def verify_virtual_api_write_readback(
    operation: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    invoke: VirtualInvoker,
) -> dict[str, Any] | None:
    """Return exact verification for virtual writes that have a stable readback."""

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
