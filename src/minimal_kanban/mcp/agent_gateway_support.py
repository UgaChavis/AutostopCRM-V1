from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import BaseModel

from ..deployment_security import (
    load_agent_gateway_security_policy,
    release_smoke_proof,
    release_smoke_proof_matches,
)
from .gateway_contract import (
    CARD_FIELD_ALLOWLIST,
    DEFAULT_CARD_FIELDS,
    FINANCE_WORKFLOW_OPERATIONS,
)
from .raw_gateway import DESTRUCTIVE_CAPABILITY_MARKERS, DESTRUCTIVE_CAPABILITY_NAMES

AGENT_GATEWAY_FORMAT = "agent_envelope_v2"
STORE_VIN_PHOTO_PREVIEW_OPERATION = "download_store_quote_vin_photo"
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
MANAGER_GATEWAY_DEPENDENCY_NAMES = frozenset(
    MANAGER_WORKFLOW_TOOL_NAMES
    | {
        "agent_bootstrap",
        "store_runtime_status",
        "store_digest",
        "store_search",
        "store_entity_context",
        "store_management_action",
        "download_store_quote_vin_photo",
        "store_owner_api",
    }
)
DIAGNOSTIC_TOOL_NAMES = frozenset(
    {"ping_connector", "get_connector_identity", "get_runtime_status"}
)
PERMANENT_AGENT_GATEWAY_TOOL_NAMES = frozenset(
    AGENT_GATEWAY_TOOL_NAMES | MANAGER_WORKFLOW_TOOL_NAMES | DIAGNOSTIC_TOOL_NAMES
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

WORKFLOW_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
RELEASE_SMOKE_REVISION_PATTERN = re.compile(r"[0-9a-f]{40,64}")
RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID = "gateway-release-smoke"
OWNER_CORRELATION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,159}")
OWNER_CONTRACT_ID_PATTERN = re.compile(r"ac_[0-9a-f]{20}")
HEX_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAINTENANCE_TECHNICAL_RAW_CAPABILITIES = frozenset(
    {
        "api:/api/change_feed/bootstrap",
        "api:/api/change_feed/ack",
        "store_owner_api",
    }
)
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


def _release_smoke_proof(token: str, revision: str) -> str:
    return release_smoke_proof(token, revision)


def _maintenance_technical_write_allowed(
    *,
    capability: str,
    arguments: Mapping[str, Any],
    revision: str,
    proof: str,
    agent_bearer_token: str | None,
) -> bool:
    normalized_revision = str(revision or "").strip().casefold()
    if (
        capability not in MAINTENANCE_TECHNICAL_RAW_CAPABILITIES
        or RELEASE_SMOKE_REVISION_PATTERN.fullmatch(normalized_revision) is None
        or not agent_bearer_token
    ):
        return False
    if capability == "store_owner_api" and str(arguments.get("mode") or "").casefold() != "dry_run":
        return False
    if (
        capability
        in {
            "api:/api/change_feed/bootstrap",
            "api:/api/change_feed/ack",
        }
        and arguments.get("consumer_id") != RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID
    ):
        return False
    return release_smoke_proof_matches(agent_bearer_token, normalized_revision, proof)


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
    if isinstance(value, CallToolResult):
        structured = value.structuredContent
        if isinstance(structured, dict):
            return dict(structured)
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
    max_depth: int = 5,
    _budget: list[int] | None = None,
) -> Any:
    budget = _budget if _budget is not None else [100_000]
    if budget[0] <= 0:
        return "<payload-budget-exhausted>"
    if depth >= max_depth:
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
                max_depth=max_depth,
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
                max_depth=max_depth,
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
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                _subset_matches(expected_item, actual_item)
                for expected_item, actual_item in zip(expected, actual, strict=True)
            )
        )
    return expected == actual


def _error_code(value: Any) -> str:
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        return str(error.get("code") or "")
    return str(error or "")


def _store_owner_prepare_binding(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    prepared_for_mode: str,
) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    expected_revision_hash = meta.get("expected_revision_sha256")
    binding = {
        "contract_id": str(meta.get("contract_id") or "").strip(),
        "operation_id": str(meta.get("operation_id") or "").strip(),
        "request_sha256": str(meta.get("request_sha256") or "").strip(),
        "schema_hash": str(meta.get("schema_hash") or "").strip(),
        "verification_class": str(meta.get("verification_class") or "").strip(),
        "correlation_id": str(meta.get("correlation_id") or "").strip(),
        "target_ref_sha256": str(meta.get("target_ref_sha256") or "").strip(),
        "expected_revision_sha256": (
            str(expected_revision_hash).strip() if expected_revision_hash is not None else None
        ),
    }
    revision_hash_valid = binding["expected_revision_sha256"] is None or bool(
        HEX_SHA256_PATTERN.fullmatch(str(binding["expected_revision_sha256"]))
    )
    if (
        result.get("ok") is not True
        or result.get("status") != "validated"
        or summary.get("request_dispatched") is not False
        or summary.get("prepared_for_mode") != prepared_for_mode
        or meta.get("request_dispatched") is not False
        or meta.get("domain_handler_executed") is not False
        or OWNER_CONTRACT_ID_PATTERN.fullmatch(binding["contract_id"]) is None
        or binding["operation_id"] != str(arguments.get("operation_id") or "").strip()
        or HEX_SHA256_PATTERN.fullmatch(binding["request_sha256"]) is None
        or HEX_SHA256_PATTERN.fullmatch(binding["schema_hash"]) is None
        or HEX_SHA256_PATTERN.fullmatch(binding["target_ref_sha256"]) is None
        or binding["correlation_id"] != str(arguments.get("correlation_id") or "").strip()
        or binding["verification_class"]
        not in {
            "absence_plus_audit",
            "collection_membership",
            "exact_entity",
            "operation_specific_state",
        }
        or not revision_hash_valid
    ):
        return None
    if (
        binding["verification_class"] == "collection_membership"
        and binding["expected_revision_sha256"] is not None
    ):
        return None
    return binding


def _store_owner_transport_matches_binding(
    result: Mapping[str, Any], binding: Mapping[str, Any]
) -> bool:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    for key in (
        "contract_id",
        "operation_id",
        "request_sha256",
        "schema_hash",
        "verification_class",
        "correlation_id",
        "target_ref_sha256",
    ):
        if str(meta.get(key) or "").strip() != str(binding.get(key) or "").strip():
            return False
    actual_revision_hash = meta.get("expected_revision_sha256")
    expected_revision_hash = binding.get("expected_revision_sha256")
    return (actual_revision_hash is None and expected_revision_hash is None) or str(
        actual_revision_hash or ""
    ).strip() == str(expected_revision_hash or "").strip()


def _store_owner_request_error(
    name: str,
    arguments: Mapping[str, Any],
    *,
    owner_mode: str,
    owner_correlation_id: str,
) -> str | None:
    if name != "store_owner_api":
        return None
    if owner_mode not in {"read", "revision", "prepare", "dry_run", "apply"}:
        return "store_owner_mode_invalid"
    if OWNER_CORRELATION_PATTERN.fullmatch(owner_correlation_id) is None:
        return "store_owner_correlation_id_required_or_invalid"
    if owner_mode in {"dry_run", "apply"} and not str(arguments.get("target_id") or "").strip():
        return "store_owner_exact_target_id_required"
    return None


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _decimal_text(value: Decimal) -> str:
    normalized = format(value.quantize(Decimal("0.01")), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


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
        or normalized in DESTRUCTIVE_CAPABILITY_NAMES
        or any(marker in normalized for marker in DESTRUCTIVE_CAPABILITY_MARKERS)
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


__all__ = [
    "AGENT_GATEWAY_FORMAT",
    "AGENT_GATEWAY_TOOL_NAMES",
    "MAIL_CAPABILITY_NAMES",
    "MANAGER_WORKFLOW_TOOL_NAMES",
    "MANAGER_GATEWAY_DEPENDENCY_NAMES",
    "PERMANENT_AGENT_GATEWAY_TOOL_NAMES",
]
