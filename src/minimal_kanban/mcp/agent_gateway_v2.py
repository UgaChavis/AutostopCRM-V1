from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel

from ..api.route_registry import PROXIED_WRITE_ROUTES
from ..deployment_security import load_agent_gateway_security_policy
from ..repair_order import repair_order_payment_method_from_cashbox_name
from .client import BoardApiClient

AGENT_GATEWAY_FORMAT = "agent_envelope_v2"
AGENT_GATEWAY_TOOL_NAMES = frozenset(
    {
        "agent_bootstrap",
        "agent_board_digest",
        "agent_search",
        "agent_entity_context",
        "agent_board_workflow",
        "agent_finance_workflow",
        "agent_inventory_workflow",
        "agent_document_workflow",
        "discover_raw_capabilities",
        "get_raw_capability_schema",
        "call_raw_capability",
    }
)

MANAGER_WORKFLOW_TOOL_NAMES = frozenset(
    {
        "list_agent_workflows",
        "prepare_action_contract",
        "start_workflow",
        "workflow_status",
        "workflow_transition",
        "workflow_checkpoint",
        "workflow_wait_for_external",
        "complete_external_step",
        "workflow_resume",
        "workflow_cancel",
    }
)

DIAGNOSTIC_TOOL_NAMES = frozenset(
    {"ping_connector", "get_connector_identity", "get_runtime_status"}
)

PERMANENT_AGENT_GATEWAY_TOOL_NAMES = frozenset(
    AGENT_GATEWAY_TOOL_NAMES | MANAGER_WORKFLOW_TOOL_NAMES | DIAGNOSTIC_TOOL_NAMES
)

DEFAULT_CARD_FIELDS = (
    "id",
    "short_id",
    "vehicle",
    "title",
    "column",
    "column_label",
    "tags",
    "status",
    "indicator",
    "remaining_seconds",
    "deadline_timestamp",
    "client_id",
    "board_summary",
    "updated_at",
)

CARD_FIELD_ALLOWLIST = frozenset(
    {
        *DEFAULT_CARD_FIELDS,
        "archived",
        "description_preview",
        "vehicle_profile_compact",
        "attachment_count",
        "events_count",
        "is_unread",
        "has_unseen_update",
        "board_summary_stale",
    }
)

BOARD_WORKFLOW_OPERATIONS = frozenset(
    {
        "manager_board_scan",
        "list_ready_unpaid_cards",
        "triage_inbox_cards",
        "list_cards_missing_manager_data",
        "audit_repair_order_consistency",
        "audit_client_links",
        "bulk_set_deadline_if_below",
        "bulk_refresh_board_summaries",
        "cleanup_card",
        "apply_ready_unpaid_followups",
    }
)

FINANCE_WORKFLOW_OPERATIONS = frozenset(
    {
        "list_cashboxes",
        "get_cashbox",
        "get_cash_journal",
        "create_cashbox",
        "delete_cashbox",
        "create_cash_transaction",
        "get_repair_order",
        "update_repair_order",
        "set_repair_order_status",
        "record_repair_order_payment",
        "reorder_cashboxes",
        "create_cashbox_transfer",
        "create_employee_salary_transaction",
        "create_employee_shift_accrual",
        "cancel_cash_transaction",
        "cancel_last_cash_transaction",
        "apply_finance_audit_safe_fixes",
    }
)

FINANCE_VIRTUAL_OPERATIONS = {
    "reorder_cashboxes": "/api/reorder_cashboxes",
    "create_cashbox_transfer": "/api/create_cashbox_transfer",
    "create_employee_salary_transaction": "/api/create_employee_salary_transaction",
    "create_employee_shift_accrual": "/api/create_employee_shift_accrual",
    "cancel_cash_transaction": "/api/cancel_cash_transaction",
    "cancel_last_cash_transaction": "/api/cancel_last_cash_transaction",
    "apply_finance_audit_safe_fixes": "/api/finance_audit/apply_safe_fixes",
}

INVENTORY_WORKFLOW_OPERATIONS = frozenset(
    {
        "list_inventory_items",
        "search_inventory_items",
        "get_inventory_item",
        "list_inventory_movements",
        "save_inventory_item",
        "replenish_inventory_item",
        "write_off_inventory_item",
        "return_inventory_movement",
    }
)

DOCUMENT_WORKFLOW_OPERATIONS = frozenset(
    {
        "download_repair_order_print_pdf",
        "create_document_without_card_pdf",
        "list_shared_files",
        "get_shared_file_info",
        "download_shared_file",
        "upload_shared_file",
        "delete_shared_file",
    }
)

FINANCE_TOOL_NAMES = frozenset(FINANCE_WORKFLOW_OPERATIONS)
DESTRUCTIVE_TOOL_NAMES = frozenset(
    {
        "archive_card",
        "delete_cashbox",
        "delete_client",
        "delete_client_vehicle",
        "delete_column",
        "delete_shared_file",
        "delete_sticky",
    }
)

RAW_API_PREFIX = "api:"
RAW_API_WRITE_ROUTES = frozenset(
    route for route in PROXIED_WRITE_ROUTES if route != "/api/get_repair_order"
)
RAW_API_READ_ROUTES = frozenset(
    {
        "/api/agent_actions",
        "/api/agent_scheduled_tasks",
        "/api/agent_status",
        "/api/agent_tasks",
        "/api/export_operator_activity",
        "/api/finance_audit",
        "/api/get_employee_salary_ledger",
        "/api/get_employee_salary_reconciliation",
        "/api/get_employee_salary_report",
        "/api/get_operator_activity_aggregates",
        "/api/get_operator_activity_details",
        "/api/get_operator_profile",
        "/api/get_operator_user_report",
        "/api/get_payroll_report",
        "/api/list_operator_activity",
        "/api/list_operator_users",
        "/api/repair_order_number_audit",
    }
)
RAW_API_ROUTES = RAW_API_READ_ROUTES | RAW_API_WRITE_ROUTES
WORKFLOW_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

FINANCE_SENSITIVE_KEYS = frozenset(
    {
        "advance_payment",
        "cashbox_id",
        "cash_transaction_id",
        "noncash_due",
        "paid_amount",
        "payment",
        "payment_history",
        "payment_method",
        "payments",
        "payroll",
        "prepayment",
        "salary",
        "shift_accrual",
    }
)

MAIL_CAPABILITY_NAMES = frozenset(
    {
        "workflow_wait_for_external",
        "complete_external_step",
    }
)

OPTIMISTIC_WRITE_NAMES = frozenset(
    {
        "update_card",
        "update_repair_order",
        "set_repair_order_status",
        "api:/api/update_card",
        "api:/api/update_repair_order",
        "api:/api/set_repair_order_status",
        "api:/api/replace_repair_order_works",
        "api:/api/replace_repair_order_materials",
    }
)


def _read_annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _write_annotations(title: str, *, destructive: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=destructive,
        idempotentHint=True,
        openWorldHint=False,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return dict(value)
    return {"ok": False, "error": {"code": "unexpected_result", "message": str(value)}}


def _envelope(
    *,
    ok: bool,
    status: str = "completed",
    summary: dict[str, Any] | None = None,
    data: Any = None,
    run_id: int | None = None,
    changes: list[dict[str, Any]] | None = None,
    verification: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    next_actions: list[str] | None = None,
    page: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": bool(ok),
        "format": AGENT_GATEWAY_FORMAT,
        "run_id": run_id,
        "status": status,
        "summary": summary or {},
        "data": data,
        "changes": changes or [],
        "verification": verification or {},
        "warnings": list(dict.fromkeys(warnings or [])),
        "next_actions": next_actions or [],
        "page": page or {},
        "meta": {"response_mode": "agent_compact", **(meta or {})},
    }
    payload["meta"]["payload_bytes"] = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )
    return payload


def _tool_result(payload: dict[str, Any], *, label: str) -> CallToolResult:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    text_payload = {
        "ok": bool(payload.get("ok")),
        "tool": label,
        "status": payload.get("status"),
        "run_id": payload.get("run_id"),
        "summary": summary,
        "warnings": payload.get("warnings") or [],
        "next_actions": payload.get("next_actions") or [],
    }
    text = json.dumps(text_payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > 1000:
        text = text[:997] + "..."
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=not bool(payload.get("ok")),
    )


def _response_data(response: Any) -> tuple[bool, Any, dict[str, Any], Any]:
    payload = _as_dict(response)
    return (
        bool(payload.get("ok")),
        payload.get("data"),
        dict(payload.get("meta") or {}) if isinstance(payload.get("meta"), dict) else {},
        payload.get("error"),
    )


def _items_from_data(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _normalize_limit(value: Any, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(1, min(normalized, maximum))


def _cursor_offset(cursor: str | None) -> int:
    try:
        return max(0, int(str(cursor or "0")))
    except (TypeError, ValueError, OverflowError):
        return 0


def _selected_fields(fields: list[str] | None) -> tuple[str, ...]:
    if not fields:
        return DEFAULT_CARD_FIELDS
    selected = tuple(
        dict.fromkeys(
            str(field).strip()
            for field in fields
            if str(field or "").strip() in CARD_FIELD_ALLOWLIST
        )
    )
    return selected or DEFAULT_CARD_FIELDS


def _slim_card(card: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: card.get(field) for field in fields if field in card}


def _compact_object(
    value: Any,
    *,
    depth: int = 0,
    item_limit: int = 25,
    key_limit: int = 100,
    _budget: list[int] | None = None,
) -> Any:
    budget = _budget if _budget is not None else [100_000]
    if budget[0] <= 0:
        return "<payload-budget-exhausted>"
    if depth >= 5:
        return "<max-depth>"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        safe_items = [
            (key, item)
            for key, item in value.items()
            if str(key) not in {"content_base64", "pdf_base64", "raw_body", "body_html"}
        ]
        compact: dict[str, Any] = {}
        for key, item in safe_items[:key_limit]:
            if budget[0] <= 0:
                break
            normalized_key = str(key)
            budget[0] -= len(normalized_key)
            compact[normalized_key] = _compact_object(
                item,
                depth=depth + 1,
                item_limit=item_limit,
                key_limit=key_limit,
                _budget=budget,
            )
        if len(safe_items) > key_limit:
            compact["truncated_keys"] = len(safe_items) - key_limit
        elif budget[0] <= 0:
            compact["payload_truncated"] = True
        return compact
    if isinstance(value, list):
        compact = [
            _compact_object(
                item,
                depth=depth + 1,
                item_limit=item_limit,
                key_limit=key_limit,
                _budget=budget,
            )
            for item in value[:item_limit]
            if budget[0] > 0
        ]
        if len(value) > item_limit:
            compact.append({"truncated_items": len(value) - item_limit})
        return compact
    if isinstance(value, str):
        allowed = max(0, min(4000, budget[0]))
        budget[0] -= min(len(value), allowed)
        if len(value) > allowed:
            return value[:allowed] + "...<truncated>"
        return value
    budget[0] -= min(len(str(value)), 64)
    return value


def _find_value(value: Any, keys: frozenset[str], *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return candidate
        for candidate in value.values():
            found = _find_value(candidate, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for candidate in value[:25]:
            found = _find_value(candidate, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _contains_value(value: Any, key: str, expected: Any, *, depth: int = 0) -> bool:
    if depth > 7:
        return False
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        if key in value and str(value.get(key)) == str(expected):
            return True
        return any(_contains_value(item, key, expected, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, key, expected, depth=depth + 1) for item in value[:200])
    return False


def _find_mapping(value: Any, key: str, expected: Any, *, depth: int = 0) -> dict[str, Any] | None:
    if depth > 7:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
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


def _subset_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, BaseModel):
        expected = expected.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(actual, BaseModel):
        actual = actual.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset_matches(value, actual.get(key))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and expected == actual
    return expected == actual


def _error_code(value: Any) -> str:
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        return str(error.get("code") or "")
    return str(error or "")


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _decimal_text(value: Decimal) -> str:
    normalized = format(value.quantize(Decimal("0.01")), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _schema_hash(schema: Mapping[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _virtual_api_schema(route: str) -> dict[str, Any]:
    """Bind raw-schema confirmation to one exact internal API route."""

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


def _request_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workflow_state_version(value: Mapping[str, Any] | None) -> int | None:
    if not isinstance(value, Mapping):
        return None
    candidate = value.get("state_version")
    if not isinstance(candidate, int):
        summary = value.get("summary")
        candidate = summary.get("state_version") if isinstance(summary, Mapping) else None
    return candidate if isinstance(candidate, int) and candidate > 0 else None


def _virtual_api_route(name: str) -> str | None:
    normalized = str(name or "").strip()
    if not normalized.startswith(RAW_API_PREFIX):
        return None
    route = normalized.removeprefix(RAW_API_PREFIX)
    return route if route in RAW_API_ROUTES else None


def _virtual_api_risk(route: str, name: str) -> str:
    if route in RAW_API_READ_ROUTES:
        return "read"
    return "destructive" if _is_destructive_capability(name, "write") else "write"


def _virtual_api_name(route: str) -> str:
    return f"{RAW_API_PREFIX}{route}"


def _contains_sensitive_key(
    value: Any,
    keys: frozenset[str],
    *,
    depth: int = 0,
) -> bool:
    if depth > 7:
        return False
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        normalized_keys = {str(key).strip().casefold().replace("-", "_") for key in value}
        if any(
            key in keys
            or any(
                marker in key
                for marker in ("payment", "cashbox", "salary", "payroll", "shift_accrual")
            )
            for key in normalized_keys
        ):
            return True
        return any(_contains_sensitive_key(item, keys, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item, keys, depth=depth + 1) for item in value[:200])
    return False


def _is_finance_capability(name: str, arguments: Mapping[str, Any] | None = None) -> bool:
    normalized = str(name or "").casefold()
    if name in FINANCE_TOOL_NAMES or any(
        marker in normalized
        for marker in (
            "cashbox",
            "cash_transaction",
            "finance_audit",
            "payroll",
            "save_employee",
            "toggle_employee",
            "delete_employee",
            "salary_transaction",
            "shift_accrual",
            "repair_order_payment",
            "update_repair_order",
            "set_repair_order_status",
            "replace_repair_order_",
        )
    ):
        return True
    return _contains_sensitive_key(arguments or {}, FINANCE_SENSITIVE_KEYS)


def _is_mail_capability(name: str) -> bool:
    normalized = str(name or "").casefold()
    return name in MAIL_CAPABILITY_NAMES or any(
        marker in normalized for marker in ("gmail", "mail_", "email_")
    )


def _is_destructive_capability(name: str, risk: str) -> bool:
    normalized = str(name or "").casefold()
    return (
        risk == "destructive"
        or name in DESTRUCTIVE_TOOL_NAMES
        or any(marker in normalized for marker in ("delete_", "cancel_", "archive_", "remove_"))
    )


def _tool_risk(tool: Any) -> str:
    annotations = getattr(tool, "annotations", None)
    if bool(getattr(annotations, "destructiveHint", False)):
        return "destructive"
    if bool(getattr(annotations, "readOnlyHint", False)):
        return "read"
    return "write"


def _policy_error(
    *,
    tool_name: str,
    risk: str,
    arguments: Mapping[str, Any] | None = None,
) -> str | None:
    policy = load_agent_gateway_security_policy()
    if not policy.gateway_enabled:
        return "agent_gateway_disabled"
    if risk != "read" and not policy.writes_enabled:
        return "agent_gateway_writes_disabled"
    if _is_finance_capability(tool_name, arguments) and not policy.finance_enabled:
        return "agent_gateway_finance_disabled"
    if _is_mail_capability(tool_name) and not policy.mail_enabled:
        return "agent_gateway_mail_disabled"
    if _is_destructive_capability(tool_name, risk) and not policy.destructive_enabled:
        return "agent_gateway_destructive_disabled"
    return None


def register_agent_gateway_v2(
    server: FastMCP,
    board_api: BoardApiClient,
    *,
    connector_identity: Mapping[str, Any],
    agent_bearer_token: str | None = None,
) -> set[str]:
    """Register the compact Codex-first surface and hide raw tools behind discovery."""

    policy = load_agent_gateway_security_policy()
    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return set()
    if not policy.gateway_enabled:
        if policy.production:
            for name in list(tools):
                if name not in DIAGNOSTIC_TOOL_NAMES:
                    tool_manager.remove_tool(name)
            return set(tools)
        return set()

    raw_tools = dict(tools)
    manager_bootstrap_tool = raw_tools.get("agent_bootstrap")
    if "agent_bootstrap" in tools:
        tool_manager.remove_tool("agent_bootstrap")

    async def _invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        virtual_route = _virtual_api_route(name)
        if virtual_route is not None:
            payload = dict(arguments)
            service_identity = load_agent_gateway_security_policy().service_identity
            payload["source"] = "mcp_agent_gateway_v2"
            payload["actor_name"] = service_identity
            try:
                return _as_dict(
                    board_api._request(
                        virtual_route,
                        payload,
                        method="POST",
                        extra_headers={
                            "X-Autostop-Agent-Identity": service_identity,
                            "X-Autostop-Agent-Token": str(agent_bearer_token or ""),
                        },
                    )
                )
            except Exception as exc:  # pragma: no cover - transport integration failure
                return {
                    "ok": False,
                    "error": {
                        "code": "capability_failed",
                        "message": str(exc),
                        "tool": name,
                    },
                }
        tool = raw_tools.get(name)
        if tool is None:
            return {"ok": False, "error": {"code": "capability_not_found", "message": name}}
        try:
            effective_arguments = dict(arguments)
            if _tool_risk(tool) != "read":
                properties = getattr(tool, "parameters", {}).get("properties", {})
                if "actor_name" in properties:
                    effective_arguments["actor_name"] = policy.service_identity
                if "source" in properties:
                    effective_arguments["source"] = "mcp_agent_gateway_v2"
            return _as_dict(await tool.run(effective_arguments, convert_result=False))
        except Exception as exc:  # pragma: no cover - exercised through integration failures
            return {
                "ok": False,
                "error": {"code": "capability_failed", "message": str(exc), "tool": name},
            }

    async def _start_idempotent_workflow(
        *, workflow_id: str, intent: str, idempotency_key: str, payload: dict[str, Any]
    ) -> tuple[int | None, dict[str, Any], bool]:
        if "start_workflow" not in raw_tools:
            return (
                None,
                {
                    "ok": False,
                    "status": "blocked",
                    "warnings": ["durable_workflow_ledger_unavailable"],
                },
                False,
            )
        started = await _invoke(
            "start_workflow",
            {
                "workflow_id": workflow_id,
                "intent": intent,
                "idempotency_key": idempotency_key,
                "query": intent,
                "actor": load_agent_gateway_security_policy().service_identity,
                "scope": {
                    "operation": payload.get("operation"),
                    "request_fingerprint": payload.get("request_fingerprint"),
                },
                "metadata": {"gateway": "v2"},
            },
        )
        run_id = started.get("run_id")
        summary = started.get("summary") if isinstance(started.get("summary"), dict) else {}
        if not isinstance(run_id, int):
            candidate = summary.get("id") or summary.get("run_id")
            run_id = candidate if isinstance(candidate, int) else None
        deduplicated = bool(summary.get("deduplicated"))
        return run_id, started, deduplicated

    async def _transition(
        run_id: int | None,
        status: str,
        *,
        expected_state_version: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if run_id is None or "workflow_transition" not in raw_tools:
            return {
                "ok": False,
                "status": "blocked",
                "warnings": ["durable_workflow_transition_unavailable"],
            }
        arguments = {"run_id": run_id, "status": status, **extra}
        if expected_state_version is not None:
            arguments["expected_state_version"] = expected_state_version
        return await _invoke("workflow_transition", arguments)

    def _deduplicated_workflow_result(
        *,
        label: str,
        operation: str,
        run_id: int | None,
        started: dict[str, Any],
    ) -> CallToolResult:
        prior_status = str(started.get("status") or "planned")
        prior_completed = prior_status == "completed"
        warning = (
            "idempotency_reused_completed_result"
            if prior_completed
            else "prior_idempotent_attempt_failed"
            if prior_status in {"failed", "cancelled"}
            else "idempotent_attempt_requires_status_reconciliation"
        )
        return _tool_result(
            _envelope(
                ok=prior_completed,
                status=prior_status,
                run_id=run_id,
                summary={
                    "workflow_id": label,
                    "operation": operation,
                    "deduplicated": True,
                },
                data=_compact_object(started, item_limit=10),
                verification={
                    "idempotency_reused": True,
                    "prior_terminal_state": prior_status in WORKFLOW_TERMINAL_STATES,
                },
                warnings=[warning],
                next_actions=[]
                if prior_completed
                else [f"workflow_status(run_id={run_id}) before any retry"],
            ),
            label=label,
        )

    async def _record_repair_order_payment(
        payload: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        card_id = str(payload.get("card_id") or "").strip()
        cashbox_id = str(payload.get("cashbox_id") or "").strip()
        expected_updated_at = str(payload.get("expected_updated_at") or "").strip()
        payment_method = str(payload.get("payment_method") or "").strip().casefold()
        amount = _positive_decimal(payload.get("amount"))
        if amount is None and payload.get("amount_minor") is not None:
            minor = _positive_decimal(payload.get("amount_minor"))
            amount = minor / Decimal(100) if minor is not None else None
        missing = [
            name
            for name, value in (
                ("card_id", card_id),
                ("cashbox_id", cashbox_id),
                ("expected_updated_at", expected_updated_at),
                ("payment_method", payment_method),
                ("amount", amount),
            )
            if not value
        ]
        if missing or payment_method not in {"cash", "cashless", "card"}:
            return {
                "ok": False,
                "error": {
                    "code": "payment_preflight_failed",
                    "message": "missing_or_invalid_payment_fields",
                    "fields": missing
                    + (
                        [] if payment_method in {"cash", "cashless", "card"} else ["payment_method"]
                    ),
                },
            }

        order_read = await _invoke("get_repair_order", {"card_id": card_id})
        cashbox_read = await _invoke(
            "get_cashbox", {"cashbox_id": cashbox_id, "transaction_limit": 10}
        )
        if not bool(order_read.get("ok")) or not bool(cashbox_read.get("ok")):
            return {
                "ok": False,
                "error": {
                    "code": "payment_preflight_read_failed",
                    "order_ok": bool(order_read.get("ok")),
                    "cashbox_ok": bool(cashbox_read.get("ok")),
                },
            }
        order_data = order_read.get("data") if isinstance(order_read.get("data"), dict) else {}
        repair_order = (
            order_data.get("repair_order")
            if isinstance(order_data.get("repair_order"), dict)
            else {}
        )
        card = order_data.get("card") if isinstance(order_data.get("card"), dict) else {}
        cashbox_data = (
            cashbox_read.get("data") if isinstance(cashbox_read.get("data"), dict) else {}
        )
        cashbox = (
            cashbox_data.get("cashbox") if isinstance(cashbox_data.get("cashbox"), dict) else {}
        )
        resolved_cashbox_method = repair_order_payment_method_from_cashbox_name(
            cashbox.get("name"),
            default=payment_method,
        )
        if resolved_cashbox_method != payment_method:
            return {
                "ok": False,
                "error": {
                    "code": "payment_cashbox_method_mismatch",
                    "message": "select_a_cashbox_matching_the_requested_payment_method",
                    "requested_payment_method": payment_method,
                    "cashbox_payment_method": resolved_cashbox_method,
                },
            }
        current_updated_at = str(card.get("updated_at") or "").strip()
        if not current_updated_at or current_updated_at != expected_updated_at:
            return {
                "ok": False,
                "error": {
                    "code": "payment_revision_conflict",
                    "message": "reread_the_repair_order_before_retry",
                },
            }
        payment_summary = (
            repair_order.get("payment_summary")
            if isinstance(repair_order.get("payment_summary"), dict)
            else {}
        )
        due_key = "noncash_due" if payment_method == "cashless" else "cash_due"
        outstanding = _positive_decimal(payment_summary.get(due_key))
        if outstanding is None:
            return {
                "ok": False,
                "error": {"code": "payment_debt_unavailable", "field": due_key},
            }
        if amount > outstanding and not bool(payload.get("allow_overpayment")):
            return {
                "ok": False,
                "error": {
                    "code": "payment_overpayment_blocked",
                    "amount": _decimal_text(amount),
                    "outstanding": _decimal_text(outstanding),
                },
            }

        existing_payments = repair_order.get("payments")
        payments = (
            [dict(item) for item in existing_payments if isinstance(item, dict)]
            if isinstance(existing_payments, list)
            else []
        )
        payment_id = f"agent-payment-{_request_fingerprint({'key': idempotency_key})[:16]}"
        payment: dict[str, Any] = {
            "id": payment_id,
            "amount": _decimal_text(amount),
            "note": str(payload.get("note") or "").strip(),
            "payment_method": payment_method,
            "cashbox_id": cashbox_id,
        }
        paid_at = str(payload.get("paid_at") or "").strip()
        if paid_at:
            payment["paid_at"] = paid_at
        payments.append(payment)
        write_result = await _invoke(
            "update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {"payments": payments},
                "expected_updated_at": expected_updated_at,
                "actor_name": load_agent_gateway_security_policy().service_identity,
            },
        )
        if not bool(write_result.get("ok")):
            return write_result

        order_readback = await _invoke("get_repair_order", {"card_id": card_id})
        recorded_payment = _find_mapping(order_readback, "id", payment_id)
        transaction_id = str((recorded_payment or {}).get("cash_transaction_id") or "").strip()
        final_cashbox_id = str((recorded_payment or {}).get("cashbox_id") or "").strip()
        final_payment_method = (
            str((recorded_payment or {}).get("payment_method") or "").strip().casefold()
        )
        cashbox_readback = await _invoke(
            "get_cashbox", {"cashbox_id": cashbox_id, "transaction_limit": 50}
        )
        checks = {
            "repair_order_reread": bool(order_readback.get("ok")),
            "payment_id_present": recorded_payment is not None,
            "amount_exact": str((recorded_payment or {}).get("amount") or "")
            == _decimal_text(amount),
            "payment_method_exact": final_payment_method == payment_method,
            "cashbox_exact": final_cashbox_id == cashbox_id,
            "cash_transaction_linked": bool(transaction_id),
            "cash_journal_entry_present": bool(transaction_id)
            and _contains_value(cashbox_readback, "id", transaction_id),
        }
        return {
            "ok": all(checks.values()),
            "executor_applied": True,
            "data": {
                "card_id": card_id,
                "payment_id": payment_id,
                "cash_transaction_id": transaction_id or None,
                "amount": _decimal_text(amount),
                "payment_method": payment_method,
                "cashbox_id": cashbox_id,
                "outstanding_before": _decimal_text(outstanding),
            },
            "verification": checks,
            "error": None if all(checks.values()) else {"code": "payment_readback_failed"},
        }

    async def _verify_operation(
        operation: str, arguments: dict[str, Any], result: dict[str, Any], risk: str
    ) -> dict[str, Any]:
        if risk == "read":
            return {"required": False, "passed": bool(result.get("ok"))}
        if operation == "record_repair_order_payment":
            checks = (
                result.get("verification") if isinstance(result.get("verification"), dict) else {}
            )
            return {
                "required": True,
                "passed": bool(result.get("ok")) and all(bool(value) for value in checks.values()),
                "check": "repair_order_payment_and_cash_journal_readback",
                "evidence": checks,
            }
        read_tool = ""
        read_arguments: dict[str, Any] = {}
        if operation in {"create_cash_transaction", "create_cashbox"}:
            cashbox_id = arguments.get("cashbox_id") or _find_value(
                result, frozenset({"cashbox_id"})
            )
            if operation == "create_cashbox":
                result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
                created_cashbox = (
                    result_data.get("cashbox")
                    if isinstance(result_data.get("cashbox"), dict)
                    else {}
                )
                cashbox_id = created_cashbox.get("id") or cashbox_id
            if cashbox_id:
                read_tool = "get_cashbox"
                read_arguments = {"cashbox_id": str(cashbox_id), "transaction_limit": 10}
        elif operation in {"update_repair_order", "set_repair_order_status"}:
            card_id = arguments.get("card_id") or _find_value(result, frozenset({"card_id"}))
            if card_id:
                read_tool = "get_repair_order"
                read_arguments = {"card_id": str(card_id)}
        elif operation in {
            "save_inventory_item",
            "replenish_inventory_item",
            "write_off_inventory_item",
            "return_inventory_movement",
        }:
            item_id = arguments.get("item_id") or _find_value(result, frozenset({"item_id"}))
            if item_id:
                read_tool = "get_inventory_item"
                read_arguments = {"item_id": str(item_id)}
        elif operation == "upload_shared_file":
            file_id = arguments.get("file_id") or _find_value(result, frozenset({"file_id", "id"}))
            if file_id:
                read_tool = "get_shared_file_info"
                read_arguments = {"file_id": str(file_id)}
        elif operation == "delete_shared_file":
            file_id = arguments.get("file_id")
            if file_id:
                readback = await _invoke("get_shared_file_info", {"file_id": str(file_id)})
                return {
                    "required": True,
                    "passed": not bool(readback.get("ok"))
                    and _error_code(readback) in {"not_found", "shared_file_not_found"},
                    "check": "get_shared_file_info_not_found",
                    "evidence": _compact_object(readback, item_limit=5),
                }
        elif operation == "delete_cashbox":
            cashbox_id = arguments.get("cashbox_id")
            if cashbox_id:
                readback = await _invoke(
                    "get_cashbox", {"cashbox_id": str(cashbox_id), "transaction_limit": 1}
                )
                return {
                    "required": True,
                    "passed": not bool(readback.get("ok"))
                    and _error_code(readback) in {"not_found", "cashbox_not_found"},
                    "check": "get_cashbox_not_found",
                    "evidence": _compact_object(readback, item_limit=5),
                }
        elif operation in {"create_document_without_card_pdf", "download_repair_order_print_pdf"}:
            has_document = bool(
                _find_value(
                    result,
                    frozenset(
                        {
                            "content_base64",
                            "pdf_base64",
                            "content_bytes",
                            "file_name",
                            "mime_type",
                        }
                    ),
                )
            )
            return {
                "required": True,
                "passed": bool(result.get("ok")) and has_document,
                "check": "document_artifact_present",
            }
        elif operation.startswith("bulk_") or operation in BOARD_WORKFLOW_OPERATIONS:
            backend_verification = _find_value(result, frozenset({"verification"}))
            if isinstance(backend_verification, dict):
                return {
                    "required": True,
                    "passed": bool(backend_verification.get("passed", result.get("ok"))),
                    "check": "backend_manager_verification",
                    "evidence": _compact_object(backend_verification),
                }
        if not read_tool:
            return {
                "required": True,
                "passed": bool(result.get("ok")),
                "check": "executor_contract_only",
                "warning": "focused_readback_not_available",
            }
        readback = await _invoke(read_tool, read_arguments)
        passed = bool(readback.get("ok"))
        if passed and operation == "create_cash_transaction":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            transaction = (
                result_data.get("transaction")
                if isinstance(result_data.get("transaction"), dict)
                else {}
            )
            transaction_id = transaction.get("id")
            passed = bool(transaction_id) and _contains_value(readback, "id", transaction_id)
        elif passed and operation == "create_cashbox":
            passed = bool(read_arguments.get("cashbox_id")) and _contains_value(
                readback, "id", read_arguments.get("cashbox_id")
            )
        elif passed and operation == "update_repair_order":
            read_data = readback.get("data") if isinstance(readback.get("data"), dict) else {}
            actual_order = read_data.get("repair_order", read_data)
            passed = _subset_matches(arguments.get("repair_order") or {}, actual_order)
        elif passed and operation == "set_repair_order_status":
            passed = _contains_value(readback, "status", arguments.get("status"))
        elif passed and operation == "upload_shared_file":
            passed = bool(read_arguments.get("file_id")) and _contains_value(
                readback, "id", read_arguments.get("file_id")
            )
        elif passed and operation in {
            "save_inventory_item",
            "replenish_inventory_item",
            "write_off_inventory_item",
            "return_inventory_movement",
        }:
            passed = bool(read_arguments.get("item_id")) and _contains_value(
                readback, "id", read_arguments.get("item_id")
            )
        return {
            "required": True,
            "passed": passed,
            "check": read_tool,
            "evidence": _compact_object(readback, item_limit=10),
        }

    async def _execute_workflow(
        *,
        workflow_id: str,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str,
        allowed: frozenset[str],
        mode: str | None = None,
        allow_large_output: bool = False,
    ) -> CallToolResult:
        if operation not in allowed:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="failed",
                    warnings=["operation_not_allowed_for_workflow"],
                    summary={"workflow_id": workflow_id, "operation": operation},
                ),
                label=workflow_id,
            )
        if not idempotency_key:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["idempotency_key_required"]),
                label=workflow_id,
            )
        if (
            operation in {"update_repair_order", "set_repair_order_status"}
            and not str(payload.get("expected_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["expected_updated_at_required_reread_exact_card_first"],
                    summary={"workflow_id": workflow_id, "operation": operation},
                    next_actions=["agent_entity_context for the exact repair order"],
                ),
                label=workflow_id,
            )
        logical_payment = workflow_id == "finance" and operation == "record_repair_order_payment"
        if workflow_id == "board":
            target_tool = "run_manager_operation"
        elif workflow_id == "finance" and operation in FINANCE_VIRTUAL_OPERATIONS:
            target_tool = _virtual_api_name(FINANCE_VIRTUAL_OPERATIONS[operation])
        else:
            target_tool = operation
        tool = raw_tools.get(target_tool)
        virtual_route = _virtual_api_route(target_tool)
        if tool is None and virtual_route is None and not logical_payment:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["executor_capability_missing"]),
                label=workflow_id,
            )
        risk = (
            "write"
            if logical_payment
            else "destructive"
            if virtual_route is not None and _is_destructive_capability(target_tool, "write")
            else "write"
            if virtual_route is not None
            else _tool_risk(tool)
        )
        policy_error = _policy_error(tool_name=operation, risk=risk, arguments=payload)
        if policy_error:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=[policy_error]), label=workflow_id
            )
        request_fingerprint = _request_fingerprint(
            {"workflow_id": workflow_id, "operation": operation, "mode": mode, "payload": payload}
        )
        run_id, started, deduplicated = await _start_idempotent_workflow(
            workflow_id=f"{workflow_id}:{operation}",
            intent=f"{workflow_id}_{operation}",
            idempotency_key=idempotency_key,
            payload={
                "operation": operation,
                "request_fingerprint": request_fingerprint,
            },
        )
        if started and not bool(started.get("ok")):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="failed",
                    run_id=run_id,
                    warnings=list(started.get("warnings") or ["workflow_start_failed"]),
                    summary={"workflow_id": workflow_id, "operation": operation},
                ),
                label=workflow_id,
            )
        if deduplicated:
            return _deduplicated_workflow_result(
                label=workflow_id,
                operation=operation,
                run_id=run_id,
                started=started,
            )
        if run_id is None:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["durable_workflow_run_id_unavailable"],
                ),
                label=workflow_id,
            )
        executing = await _transition(
            run_id,
            "executing",
            expected_state_version=_workflow_state_version(started),
            message=f"execute {operation}",
        )
        if not bool(executing.get("ok")):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    run_id=run_id,
                    data=_compact_object(executing),
                    warnings=["workflow_enter_executing_failed"],
                ),
                label=workflow_id,
            )
        arguments = dict(payload)
        if workflow_id == "board":
            arguments = {
                "operation": operation,
                "payload": payload,
                "mode": mode or str(payload.get("mode") or "dry_run"),
                "actor_name": str(
                    payload.get("actor_name")
                    or load_agent_gateway_security_policy().service_identity
                ),
            }
        elif (
            risk != "read"
            and tool is not None
            and "actor_name" in getattr(tool, "parameters", {}).get("properties", {})
        ):
            arguments.setdefault(
                "actor_name", load_agent_gateway_security_policy().service_identity
            )
        result = (
            await _record_repair_order_payment(arguments, idempotency_key=idempotency_key)
            if logical_payment
            else await _invoke(target_tool, arguments)
        )
        verification = await _verify_operation(operation, arguments, result, risk)
        executor_ok = bool(result.get("ok")) or bool(result.get("executor_applied"))
        verification_passed = bool(verification.get("passed"))
        result_ok = executor_ok and verification_passed
        ledger_closed = False
        ledger_error: dict[str, Any] | None = None
        workflow_status = "failed"
        executing_version = _workflow_state_version(executing)
        if result_ok:
            verifying = await _transition(
                run_id,
                "verifying",
                expected_state_version=executing_version,
                message=f"verify {operation}",
            )
            if bool(verifying.get("ok")):
                completed = await _transition(
                    run_id,
                    "completed",
                    expected_state_version=_workflow_state_version(verifying),
                    message=f"completed {operation}",
                    verification={"executor_ok": True, **verification},
                    summary=f"{workflow_id}:{operation}",
                )
                ledger_closed = (
                    bool(completed.get("ok")) and str(completed.get("status")) == "completed"
                )
                workflow_status = "completed" if ledger_closed else "verifying"
                if not ledger_closed:
                    ledger_error = completed
            else:
                ledger_error = verifying
                compensation = await _transition(
                    run_id,
                    "compensating",
                    expected_state_version=executing_version,
                    message=f"ledger close reconciliation required for {operation}",
                )
                if bool(compensation.get("ok")):
                    workflow_status = "compensating"
                else:
                    ledger_error = {"verifying": verifying, "compensating": compensation}
        elif executor_ok:
            compensation = await _transition(
                run_id,
                "compensating",
                expected_state_version=executing_version,
                message=f"verification failed after executor applied {operation}",
                verification={"executor_ok": True, **verification},
            )
            workflow_status = "compensating" if bool(compensation.get("ok")) else "executing"
            if not bool(compensation.get("ok")):
                ledger_error = compensation
        else:
            failed = await _transition(
                run_id,
                "failed",
                expected_state_version=executing_version,
                message=f"failed {operation}",
            )
            ledger_closed = bool(failed.get("ok")) and str(failed.get("status")) == "failed"
            workflow_status = "failed"
            if not ledger_closed:
                ledger_error = failed
        overall_ok = result_ok and ledger_closed
        safe_result = result if allow_large_output else _compact_object(result)
        return _tool_result(
            _envelope(
                ok=overall_ok,
                status=workflow_status,
                run_id=run_id,
                summary={
                    "workflow_id": workflow_id,
                    "operation": operation,
                    "executor": target_tool,
                    "risk": risk,
                },
                data=safe_result,
                verification={
                    "executor_ok": executor_ok,
                    "ledger_closed": ledger_closed,
                    **verification,
                },
                warnings=(
                    []
                    if overall_ok
                    else ["verification_failed_compensation_required"]
                    if executor_ok and not verification_passed
                    else ["workflow_ledger_close_failed"]
                    if result_ok
                    else ["executor_failed"]
                ),
                next_actions=[]
                if overall_ok
                else [f"workflow_status(run_id={run_id}) and reconcile exact target"],
                meta={"ledger_error": _compact_object(ledger_error) if ledger_error else None},
            ),
            label=workflow_id,
        )

    @server.tool(
        name="agent_bootstrap",
        description="Return one compact Codex startup package: manager route, CRM board digest, security policy, and unfinished workflows.",
        annotations=_read_annotations("Agent Bootstrap v2"),
    )
    async def agent_bootstrap(
        query: str = "", intent: str | None = None, sample_limit: int = 8
    ) -> CallToolResult:
        manager_payload: dict[str, Any] = {}
        if manager_bootstrap_tool is not None:
            try:
                manager_payload = _as_dict(
                    await manager_bootstrap_tool.run(
                        {"query": query, "intent": intent, "limit": 8}, convert_result=False
                    )
                )
            except Exception as exc:  # pragma: no cover
                manager_payload = {"ok": False, "error": str(exc)}
        context_ok, context_data, _context_meta, context_error = _response_data(
            board_api.get_board_context()
        )
        cards_ok, cards_data, _cards_meta, cards_error = _response_data(
            board_api.get_cards(include_archived=False, compact=True)
        )
        cards = _items_from_data(cards_data, "cards")
        sample = [
            _slim_card(card, DEFAULT_CARD_FIELDS)
            for card in cards[: _normalize_limit(sample_limit, default=8, maximum=20)]
        ]
        context = (
            context_data.get("context", context_data) if isinstance(context_data, dict) else {}
        )
        ok = context_ok and cards_ok
        payload = _envelope(
            ok=ok,
            status="ready" if ok else "degraded",
            summary={
                "connector": dict(connector_identity),
                "board": {
                    "columns": context.get("columns_total"),
                    "active_cards": context.get("active_cards_total", len(cards)),
                    "archived_cards": context.get("archived_cards_total"),
                    "stickies": context.get("stickies_total"),
                },
                "manager": manager_payload.get("summary", manager_payload),
                "security_policy": load_agent_gateway_security_policy().public_dict(),
                "card_sample": sample,
            },
            warnings=[] if ok else [str(context_error or cards_error or "bootstrap_degraded")],
            next_actions=["agent_board_digest or agent_search", "use named workflow before raw"],
            meta={"tool_count": len(getattr(tool_manager, "_tools", {}))},
        )
        return _tool_result(payload, label="agent_bootstrap")

    @server.tool(
        name="agent_board_digest",
        description="Return a paginated field-selected digest of active or archived CRM cards without UI-only fields.",
        annotations=_read_annotations("Agent Board Digest"),
    )
    def agent_board_digest(
        include_archived: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        fields: list[str] | None = None,
    ) -> CallToolResult:
        ok, data, meta, error = _response_data(
            board_api.get_cards(include_archived=include_archived, compact=True)
        )
        cards = _items_from_data(data, "cards")
        offset = _cursor_offset(cursor)
        effective_limit = _normalize_limit(limit, default=50, maximum=100)
        selected = _selected_fields(fields)
        page_items = [
            _slim_card(card, selected) for card in cards[offset : offset + effective_limit]
        ]
        next_offset = offset + len(page_items)
        has_more = next_offset < len(cards)
        payload = _envelope(
            ok=ok,
            status="completed" if ok else "failed",
            summary={"total": len(cards), "returned": len(page_items), "fields": list(selected)},
            data={"cards": page_items},
            warnings=[] if ok else [str(error or "board_digest_failed")],
            page={
                "cursor": str(offset),
                "next_cursor": str(next_offset) if has_more else None,
                "limit": effective_limit,
                "has_more": has_more,
            },
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_board_digest")

    @server.tool(
        name="agent_search",
        description="Search CRM cards, clients, repair orders, inventory, cashboxes, or shared files and return compact results.",
        annotations=_read_annotations("Agent Search"),
    )
    def agent_search(
        entity: Literal["card", "client", "repair_order", "inventory", "cashbox", "file"],
        query: str = "",
        include_archived: bool = False,
        limit: int = 20,
    ) -> CallToolResult:
        effective_limit = _normalize_limit(limit, default=20, maximum=50)
        if entity == "card":
            response = board_api.search_cards(
                query=query, include_archived=include_archived, limit=effective_limit
            )
            keys = ("cards", "items", "results")
        elif entity == "client":
            response = board_api.search_clients(query=query, limit=effective_limit)
            keys = ("clients", "items", "results")
        elif entity == "repair_order":
            response = board_api.list_repair_orders(
                query=query, limit=effective_limit, compact=True, redact_private=True
            )
            keys = ("repair_orders", "items", "results")
        elif entity == "inventory":
            response = board_api.search_inventory_items(query=query, limit=effective_limit)
            keys = ("items", "inventory_items", "results")
        elif entity == "cashbox":
            response = board_api.list_cashboxes(limit=effective_limit)
            keys = ("cashboxes", "items", "results")
        else:
            response = board_api.list_shared_files()
            keys = ("files", "items", "results")
        ok, data, meta, error = _response_data(response)
        items = _items_from_data(data, *keys)[:effective_limit]
        if entity == "card":
            items = [_slim_card(item, DEFAULT_CARD_FIELDS) for item in items]
        else:
            items = _compact_object(items, item_limit=effective_limit)
        payload = _envelope(
            ok=ok,
            summary={"entity": entity, "query": query, "returned": len(items)},
            data={"items": items},
            warnings=[] if ok else [str(error or "search_failed")],
            page={"limit": effective_limit, "has_more": False},
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_search")

    @server.tool(
        name="agent_entity_context",
        description="Read focused context for one exact CRM card, client, repair order, cashbox, inventory item, or shared file.",
        annotations=_read_annotations("Agent Entity Context"),
    )
    def agent_entity_context(
        entity: Literal["card", "client", "repair_order", "cashbox", "inventory", "file"],
        entity_id: str,
        detail: Literal["summary", "full"] = "summary",
    ) -> CallToolResult:
        if entity == "card":
            response = (
                board_api.get_card_context(
                    entity_id, event_limit=10, include_repair_order_text=False
                )
                if detail == "full"
                else board_api.get_card(entity_id)
            )
        elif entity == "client":
            response = board_api.get_client(entity_id, order_limit=20 if detail == "full" else 5)
        elif entity == "repair_order":
            response = board_api.get_repair_order(entity_id)
        elif entity == "cashbox":
            response = board_api.get_cashbox(
                entity_id, transaction_limit=50 if detail == "full" else 10
            )
        elif entity == "inventory":
            response = board_api.get_inventory_item(entity_id)
        else:
            response = board_api.get_shared_file_info(entity_id)
        ok, data, meta, error = _response_data(response)
        payload = _envelope(
            ok=ok,
            summary={"entity": entity, "entity_id": entity_id, "detail": detail},
            data=_compact_object(data, item_limit=50 if detail == "full" else 15),
            warnings=[] if ok else [str(error or "entity_read_failed")],
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_entity_context")

    @server.tool(
        name="agent_board_workflow",
        description="Execute one named board manager operation with durable idempotency and automatic ledger transitions.",
        annotations=_write_annotations("Agent Board Workflow"),
    )
    async def agent_board_workflow(
        operation: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
        mode: Literal["dry_run", "apply"] = "dry_run",
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="board",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=BOARD_WORKFLOW_OPERATIONS,
            mode=mode,
        )

    @server.tool(
        name="agent_finance_workflow",
        description="Execute a finance/cashbox/repair-order operation with idempotency, policy gates, and compact verification evidence.",
        annotations=_write_annotations("Agent Finance Workflow", destructive=True),
    )
    async def agent_finance_workflow(
        operation: str, payload: dict[str, Any] | None, idempotency_key: str
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="finance",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=FINANCE_WORKFLOW_OPERATIONS,
        )

    @server.tool(
        name="agent_inventory_workflow",
        description="Execute an inventory operation with idempotency, policy gates, and compact result evidence.",
        annotations=_write_annotations("Agent Inventory Workflow"),
    )
    async def agent_inventory_workflow(
        operation: str, payload: dict[str, Any] | None, idempotency_key: str
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="inventory",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=INVENTORY_WORKFLOW_OPERATIONS,
        )

    @server.tool(
        name="agent_document_workflow",
        description="Execute a CRM print/file operation; binary payloads are returned only when allow_large_output is explicit.",
        annotations=_write_annotations("Agent Document Workflow"),
    )
    async def agent_document_workflow(
        operation: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
        allow_large_output: bool = False,
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="document",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=DOCUMENT_WORKFLOW_OPERATIONS,
            allow_large_output=allow_large_output,
        )

    @server.tool(
        name="discover_raw_capabilities",
        description="Search hidden raw CRM/manager capabilities by name or description before using the raw escape hatch.",
        annotations=_read_annotations("Discover Raw Capabilities"),
    )
    def discover_raw_capabilities(query: str = "", limit: int = 25) -> CallToolResult:
        effective_limit = _normalize_limit(limit, default=25, maximum=100)
        normalized_query = str(query or "").strip().casefold()
        items: list[dict[str, Any]] = []
        for name, tool in sorted(raw_tools.items()):
            if name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
                continue
            description = str(getattr(tool, "description", "") or "")
            if normalized_query and normalized_query not in f"{name} {description}".casefold():
                continue
            schema = getattr(tool, "parameters", {}) or {}
            items.append(
                {
                    "name": name,
                    "description": description[:300],
                    "risk": _tool_risk(tool),
                    "schema_hash": _schema_hash(schema),
                }
            )
            if len(items) >= effective_limit:
                break
        if len(items) < effective_limit:
            for route in sorted(RAW_API_ROUTES):
                name = _virtual_api_name(route)
                description = (
                    f"Guarded internal CRM fallback for {route}; use only when no focused "
                    "named workflow or MCP capability covers the exact action."
                )
                if normalized_query and normalized_query not in f"{name} {description}".casefold():
                    continue
                risk = _virtual_api_risk(route, name)
                items.append(
                    {
                        "name": name,
                        "description": description,
                        "risk": risk,
                        "schema_hash": _schema_hash(_virtual_api_schema(route)),
                    }
                )
                if len(items) >= effective_limit:
                    break
        payload = _envelope(
            ok=True,
            summary={"query": query, "returned": len(items)},
            data={"capabilities": items},
            page={"limit": effective_limit, "has_more": len(items) == effective_limit},
        )
        return _tool_result(payload, label="discover_raw_capabilities")

    @server.tool(
        name="get_raw_capability_schema",
        description="Return the current input schema and schema hash for one hidden raw capability.",
        annotations=_read_annotations("Raw Capability Schema"),
    )
    def get_raw_capability_schema(name: str) -> CallToolResult:
        tool = raw_tools.get(str(name or "").strip())
        virtual_route = _virtual_api_route(name)
        if (tool is None and virtual_route is None) or name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["capability_not_found"]),
                label="get_raw_capability_schema",
            )
        schema = (
            _virtual_api_schema(virtual_route)
            if virtual_route is not None
            else getattr(tool, "parameters", {}) or {}
        )
        risk = (
            _virtual_api_risk(virtual_route, name)
            if virtual_route is not None
            else _tool_risk(tool)
        )
        payload = _envelope(
            ok=True,
            summary={"name": name, "risk": risk, "schema_hash": _schema_hash(schema)},
            data={"input_schema": schema},
        )
        return _tool_result(payload, label="get_raw_capability_schema")

    @server.tool(
        name="call_raw_capability",
        description="Invoke one discovered hidden capability after validating its current schema hash and security policy.",
        annotations=_write_annotations("Call Raw Capability", destructive=True),
    )
    async def call_raw_capability(
        name: str,
        arguments: dict[str, Any] | None,
        schema_hash: str,
        idempotency_key: str = "",
        allow_large_output: bool = False,
    ) -> CallToolResult:
        policy_now = load_agent_gateway_security_policy()
        if not policy_now.raw_enabled:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=["agent_gateway_raw_disabled"]),
                label="call_raw_capability",
            )
        tool = raw_tools.get(str(name or "").strip())
        virtual_route = _virtual_api_route(name)
        if (tool is None and virtual_route is None) or name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["capability_not_found"]),
                label="call_raw_capability",
            )
        current_schema = (
            _virtual_api_schema(virtual_route)
            if virtual_route is not None
            else getattr(tool, "parameters", {}) or {}
        )
        current_hash = _schema_hash(current_schema)
        if str(schema_hash or "") != current_hash:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    summary={"name": name, "current_schema_hash": current_hash},
                    warnings=["schema_hash_mismatch_rediscover_capability"],
                ),
                label="call_raw_capability",
            )
        risk = (
            _virtual_api_risk(virtual_route, name)
            if virtual_route is not None
            else _tool_risk(tool)
        )
        policy_error = _policy_error(
            tool_name=name,
            risk=risk,
            arguments=arguments or {},
        )
        if policy_error:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=[policy_error]),
                label="call_raw_capability",
            )
        if (
            name in OPTIMISTIC_WRITE_NAMES
            and not str((arguments or {}).get("expected_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["expected_updated_at_required_reread_exact_card_first"],
                ),
                label="call_raw_capability",
            )
        run_id: int | None = None
        if risk != "read":
            if not str(idempotency_key or "").strip():
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=["idempotency_key_required_for_raw_write"],
                    ),
                    label="call_raw_capability",
                )
            request_fingerprint = _request_fingerprint(
                {"capability": name, "arguments": arguments or {}}
            )
            run_id, started, deduplicated = await _start_idempotent_workflow(
                workflow_id=f"raw:{name}",
                intent=f"raw_{name}",
                idempotency_key=idempotency_key,
                payload={
                    "operation": name,
                    "request_fingerprint": request_fingerprint,
                },
            )
            if started and not bool(started.get("ok")):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="failed",
                        run_id=run_id,
                        warnings=list(started.get("warnings") or ["workflow_start_failed"]),
                    ),
                    label="call_raw_capability",
                )
            if deduplicated:
                return _deduplicated_workflow_result(
                    label="call_raw_capability",
                    operation=name,
                    run_id=run_id,
                    started=started,
                )
            if run_id is None:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=["durable_workflow_run_id_unavailable"],
                    ),
                    label="call_raw_capability",
                )
            executing = await _transition(
                run_id,
                "executing",
                expected_state_version=_workflow_state_version(started),
                message=f"raw execute {name}",
            )
            if not bool(executing.get("ok")):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        run_id=run_id,
                        data=_compact_object(executing),
                        warnings=["workflow_enter_executing_failed"],
                    ),
                    label="call_raw_capability",
                )
        effective_arguments = dict(arguments or {})
        result = await _invoke(name, effective_arguments)
        verification = await _verify_operation(name, effective_arguments, result, risk)
        executor_ok = bool(result.get("ok")) or bool(result.get("executor_applied"))
        verification_passed = bool(verification.get("passed"))
        ok = executor_ok and verification_passed
        ledger_closed = risk == "read"
        ledger_error: dict[str, Any] | None = None
        workflow_status = "completed" if ok and risk == "read" else "failed"
        if run_id is not None:
            executing_version = _workflow_state_version(executing)
            if ok:
                verifying = await _transition(
                    run_id,
                    "verifying",
                    expected_state_version=executing_version,
                    message=f"raw verify {name}",
                )
                if bool(verifying.get("ok")):
                    completed = await _transition(
                        run_id,
                        "completed",
                        expected_state_version=_workflow_state_version(verifying),
                        message=f"raw completed {name}",
                        verification={
                            "executor_ok": True,
                            "schema_hash_verified": True,
                            **verification,
                        },
                        summary=f"raw:{name}",
                    )
                    ledger_closed = (
                        bool(completed.get("ok")) and str(completed.get("status")) == "completed"
                    )
                    workflow_status = "completed" if ledger_closed else "verifying"
                    if not ledger_closed:
                        ledger_error = completed
                else:
                    ledger_error = verifying
                    compensation = await _transition(
                        run_id,
                        "compensating",
                        expected_state_version=executing_version,
                        message=f"raw ledger close reconciliation required for {name}",
                    )
                    if bool(compensation.get("ok")):
                        workflow_status = "compensating"
                    else:
                        ledger_error = {"verifying": verifying, "compensating": compensation}
            elif executor_ok:
                compensation = await _transition(
                    run_id,
                    "compensating",
                    expected_state_version=executing_version,
                    message=f"raw verification failed after executor applied {name}",
                    verification={
                        "executor_ok": True,
                        "schema_hash_verified": True,
                        **verification,
                    },
                )
                workflow_status = "compensating" if bool(compensation.get("ok")) else "executing"
                if not bool(compensation.get("ok")):
                    ledger_error = compensation
            else:
                failed = await _transition(
                    run_id,
                    "failed",
                    expected_state_version=executing_version,
                    message=f"raw failed {name}",
                )
                ledger_closed = bool(failed.get("ok")) and str(failed.get("status")) == "failed"
                workflow_status = "failed"
                if not ledger_closed:
                    ledger_error = failed
        overall_ok = ok and ledger_closed
        data = result if allow_large_output else _compact_object(result)
        payload = _envelope(
            ok=overall_ok,
            status=workflow_status,
            run_id=run_id,
            summary={"name": name, "risk": risk, "schema_hash": current_hash},
            data=data,
            warnings=(
                []
                if overall_ok
                else ["verification_failed_compensation_required"]
                if executor_ok and not verification_passed
                else ["workflow_ledger_close_failed"]
                if ok
                else ["raw_capability_failed"]
            ),
            verification={
                "schema_hash_verified": True,
                "executor_ok": executor_ok,
                "ledger_closed": ledger_closed,
                **verification,
            },
            next_actions=[]
            if overall_ok
            else [f"workflow_status(run_id={run_id}) and reconcile exact target"],
            meta={"ledger_error": _compact_object(ledger_error) if ledger_error else None},
        )
        return _tool_result(payload, label="call_raw_capability")

    keep = set(PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
    if not policy.mail_enabled:
        keep.difference_update(MAIL_CAPABILITY_NAMES)
    for name in list(tools):
        if name not in keep:
            tool_manager.remove_tool(name)
    return set(tools)


__all__ = [
    "AGENT_GATEWAY_FORMAT",
    "AGENT_GATEWAY_TOOL_NAMES",
    "MANAGER_WORKFLOW_TOOL_NAMES",
    "PERMANENT_AGENT_GATEWAY_TOOL_NAMES",
    "register_agent_gateway_v2",
]
