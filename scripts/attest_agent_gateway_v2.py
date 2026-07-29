from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_agent_gateway_v2 import (  # noqa: E402
    DEFAULT_TOKEN_ENV,
    EXPECTED_TOOL_NAMES,
    _anonymous_access_probe,
    _open_session,
    _serialized_size,
    _structured,
    _tool_ok,
)

from minimal_kanban.mcp.gateway_contract import (  # noqa: E402
    BOARD_WORKFLOW_OPERATIONS,
    DOCUMENT_WORKFLOW_OPERATIONS,
    FINANCE_WORKFLOW_OPERATIONS,
    INVENTORY_WORKFLOW_OPERATIONS,
)

ATTESTATION_FORMAT = "gateway_attestation_v1"
ATTESTATION_MANIFEST_FORMAT = "gateway_attestation_manifest_v1"
DEFAULT_MCP_URL = "http://127.0.0.1:8001/mcp"
DEFAULT_OUTPUT_ROOT = Path("/var/lib/autostop-manager/integration/gateway-attestation")
RUN_ID_RE = re.compile(r"^AST-GWAT-[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9]{4,16})?$")
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
MANAGER_RAW_CRM_CAPABILITIES = (
    "create_client",
    "create_card",
    "link_card_to_client",
)
STORE_ONLY_DOCUMENT_OPERATIONS = frozenset({"download_store_quote_vin_photo"})
CRM_DOCUMENT_OPERATIONS = frozenset(DOCUMENT_WORKFLOW_OPERATIONS) - STORE_ONLY_DOCUMENT_OPERATIONS
EXPECTED_CRM_OPERATION_COUNT = 43
PUBLIC_CASE_ORDER = (
    "ping_connector",
    "get_connector_identity",
    "get_runtime_status",
    "agent_bootstrap",
    "agent_board_digest",
    "agent_search",
    "agent_entity_context",
    "list_agent_workflows",
    "prepare_action_contract",
    "agent_board_workflow",
    "agent_finance_workflow",
    "agent_inventory_workflow",
    "agent_document_workflow",
    "discover_raw_capabilities",
    "get_raw_capability_schema",
    "call_raw_capability",
    "start_workflow",
    "workflow_status",
    "workflow_checkpoint",
    "workflow_transition",
    "workflow_wait_for_external",
    "complete_external_step",
    "workflow_resume",
    "workflow_cancel",
)
BOARD_OPERATION_ORDER = (
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
)
FINANCE_OPERATION_ORDER = (
    "list_cashboxes",
    "get_cashbox",
    "get_cash_journal",
    "get_repair_order",
    "create_cashbox",
    "create_cash_transaction",
    "create_cashbox_transfer",
    "record_repair_order_payment",
    "update_repair_order",
    "set_repair_order_status",
    "reorder_cashboxes",
    "create_employee_salary_transaction",
    "create_employee_shift_accrual",
    "cancel_cash_transaction",
    "cancel_last_cash_transaction",
    "apply_finance_audit_safe_fixes",
    "delete_cashbox",
)
INVENTORY_OPERATION_ORDER = (
    "list_inventory_items",
    "search_inventory_items",
    "get_inventory_item",
    "list_inventory_movements",
    "save_inventory_item",
    "replenish_inventory_item",
    "write_off_inventory_item",
    "return_inventory_movement",
)
DOCUMENT_OPERATION_ORDER = (
    "list_shared_files",
    "create_document_without_card_pdf",
    "upload_shared_file",
    "get_shared_file_info",
    "download_shared_file",
    "download_repair_order_print_pdf",
    "update_display_dashboard_message",
    "delete_shared_file",
)
READ_ONLY_OPERATIONS = frozenset(
    {
        "manager_board_scan",
        "list_ready_unpaid_cards",
        "triage_inbox_cards",
        "list_cards_missing_manager_data",
        "audit_repair_order_consistency",
        "audit_client_links",
        "list_cashboxes",
        "get_cashbox",
        "get_cash_journal",
        "get_repair_order",
        "list_inventory_items",
        "search_inventory_items",
        "get_inventory_item",
        "list_inventory_movements",
        "list_shared_files",
        "create_document_without_card_pdf",
        "get_shared_file_info",
        "download_shared_file",
        "download_repair_order_print_pdf",
    }
)


class AttestationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        classification: str = "verification",
        evidence: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.classification = classification
        self.evidence = list(evidence or [])


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    family: str
    target: str
    kind: str
    requires_apply: bool = False
    operation: str = ""
    workflow_tool: str = ""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _safe_code(value: object, *, fallback: str = "") -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if SAFE_CODE_RE.fullmatch(normalized) else fallback


def _result_error_code(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return _safe_code(error.get("code"), fallback="remote_error")
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            code = _safe_code(warning)
            if code:
                return code
    return "remote_error"


def _tool_input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    return schema if isinstance(schema, dict) else {}


def _tool_annotations(tool: Any) -> dict[str, Any]:
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return {}
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(mode="json", exclude_none=True)
    return annotations if isinstance(annotations, dict) else {}


def _compact_refs(value: Any) -> dict[str, str | int]:
    allowed = {
        "run_id",
        "state_version",
        "card_id",
        "client_id",
        "client_vehicle_id",
        "cashbox_id",
        "transaction_id",
        "movement_id",
        "item_id",
        "file_id",
        "step_id",
    }
    refs: dict[str, str | int] = {}
    pending = [value]
    while pending and len(refs) < 16:
        current = pending.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                normalized_key = str(key)
                if (
                    normalized_key in allowed
                    and isinstance(item, (str, int))
                    and not isinstance(item, bool)
                ):
                    text = str(item)
                    refs.setdefault(normalized_key, item if isinstance(item, int) else text[:160])
                elif isinstance(item, (dict, list)):
                    pending.append(item)
        elif isinstance(current, list):
            pending.extend(current[:20])
    return refs


def _call_evidence(
    *,
    name: str,
    arguments: dict[str, Any],
    result: Any,
    duration_ms: int,
) -> dict[str, Any]:
    structured = _structured(result)
    evidence: dict[str, Any] = {
        "tool": name,
        "request_bytes": len(_canonical_json(arguments)),
        "request_sha256": _sha256(arguments),
        "response_bytes": _serialized_size(result),
        "response_sha256": _sha256(structured),
        "duration_ms": duration_ms,
        "ok": _tool_ok(result),
        "is_error": bool(getattr(result, "isError", False)),
        "format": _safe_code(structured.get("format"), fallback=""),
        "status": _safe_code(structured.get("status"), fallback=""),
        "refs": _compact_refs(structured),
    }
    if not evidence["ok"]:
        evidence["error_code"] = _result_error_code(structured)
    return evidence


async def _attested_call(
    session: Any,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    expect_ok: bool = True,
) -> tuple[Any, dict[str, Any]]:
    effective_arguments = dict(arguments or {})
    started = time.monotonic()
    try:
        result = await session.call_tool(name, effective_arguments)
    except Exception as exc:
        raise AttestationError(
            f"transport_{name}_{type(exc).__name__.casefold()}",
            classification="transport_auth",
        ) from exc
    evidence = _call_evidence(
        name=name,
        arguments=effective_arguments,
        result=result,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    if bool(evidence["ok"]) is not expect_ok:
        suffix = (
            "unexpected_success"
            if evidence["ok"]
            else str(evidence.get("error_code") or "unexpected_failure")
        )
        raise AttestationError(f"{name}_{suffix}", evidence=[evidence])
    return result, evidence


def _operation_cases(
    family: str,
    workflow_tool: str,
    operations: tuple[str, ...],
) -> list[CaseSpec]:
    return [
        CaseSpec(
            case_id=f"operation:{family}:{operation}",
            family=family,
            target=operation,
            kind="operation",
            requires_apply=operation not in READ_ONLY_OPERATIONS,
            operation=operation,
            workflow_tool=workflow_tool,
        )
        for operation in operations
    ]


def build_case_specs() -> list[CaseSpec]:
    public = [
        CaseSpec(
            case_id=f"public:{name}",
            family="public",
            target=name,
            kind="public_tool",
        )
        for name in PUBLIC_CASE_ORDER
    ]
    operations = [
        *_operation_cases("board", "agent_board_workflow", BOARD_OPERATION_ORDER),
        *_operation_cases("inventory", "agent_inventory_workflow", INVENTORY_OPERATION_ORDER),
        *_operation_cases("document", "agent_document_workflow", DOCUMENT_OPERATION_ORDER),
        *_operation_cases("finance", "agent_finance_workflow", FINANCE_OPERATION_ORDER),
    ]
    raw = [
        CaseSpec(
            case_id=f"raw:{name}",
            family="raw",
            target=name,
            kind="raw_capability",
            requires_apply=True,
        )
        for name in MANAGER_RAW_CRM_CAPABILITIES
    ]
    return [*public, *operations, *raw]


def _validate_static_contract() -> None:
    if set(PUBLIC_CASE_ORDER) != set(EXPECTED_TOOL_NAMES) or len(PUBLIC_CASE_ORDER) != 24:
        raise AttestationError("public_case_manifest_drift", classification="schema")
    family_checks = (
        (set(BOARD_OPERATION_ORDER), set(BOARD_WORKFLOW_OPERATIONS), "board"),
        (set(FINANCE_OPERATION_ORDER), set(FINANCE_WORKFLOW_OPERATIONS), "finance"),
        (set(INVENTORY_OPERATION_ORDER), set(INVENTORY_WORKFLOW_OPERATIONS), "inventory"),
        (set(DOCUMENT_OPERATION_ORDER), set(CRM_DOCUMENT_OPERATIONS), "document"),
    )
    for actual, expected, family in family_checks:
        if actual != expected:
            raise AttestationError(f"{family}_operation_manifest_drift", classification="schema")
    operation_count = sum(len(actual) for actual, _expected, _family in family_checks)
    if operation_count != EXPECTED_CRM_OPERATION_COUNT:
        raise AttestationError("crm_operation_count_drift", classification="schema")


def _new_state(
    *,
    run_id: str,
    mcp_url: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    cases = [
        {
            **asdict(spec),
            "status": "pending",
            "attempts": 0,
            "last_evidence": [],
        }
        for spec in build_case_specs()
    ]
    return {
        "ok": True,
        "format": ATTESTATION_FORMAT,
        "run_id": run_id,
        "status": "ready",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "mcp_url_kind": "public" if mcp_url.startswith("https://") else "internal",
        "manifest_sha256": _sha256(manifest),
        "manifest": manifest,
        "cases": cases,
        "current_case_id": None,
        "blocked": None,
        "refs": {"synthetic_prefix": run_id},
        "cleanup": {"status": "not_started", "verified": False},
        "summary": {
            "total": len(cases),
            "passed": 0,
            "pending": len(cases),
            "blocked": 0,
        },
        "data_included": False,
    }


def _state_summary(state: dict[str, Any]) -> dict[str, int]:
    statuses = [str(item.get("status") or "") for item in state.get("cases", [])]
    return {
        "total": len(statuses),
        "passed": statuses.count("passed"),
        "pending": statuses.count("pending"),
        "blocked": statuses.count("blocked"),
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError("attestation_state_unreadable") from exc
    if payload.get("format") != ATTESTATION_FORMAT:
        raise AttestationError("attestation_state_format_invalid")
    return payload


async def _build_live_manifest(
    session: Any,
    *,
    anonymous_blocked: bool,
    anonymous_status: int,
) -> dict[str, Any]:
    listed = await session.list_tools()
    tools = list(getattr(listed, "tools", []) or [])
    tool_names = {str(tool.name) for tool in tools}
    if tool_names != set(EXPECTED_TOOL_NAMES):
        raise AttestationError("live_public_tool_manifest_drift", classification="schema")
    tool_contracts = []
    for tool in sorted(tools, key=lambda item: str(item.name)):
        schema = _tool_input_schema(tool)
        annotations = _tool_annotations(tool)
        tool_contracts.append(
            {
                "name": str(tool.name),
                "schema_sha256": _sha256(schema),
                "annotations_sha256": _sha256(annotations),
                "request_schema_bytes": len(_canonical_json(schema)),
            }
        )

    raw_contracts = []
    for name in MANAGER_RAW_CRM_CAPABILITIES:
        discovered, _ = await _attested_call(
            session,
            "discover_raw_capabilities",
            {"query": name, "limit": 10},
        )
        capabilities = (_structured(discovered).get("data") or {}).get("capabilities") or []
        exact = [
            item
            for item in capabilities
            if isinstance(item, dict) and str(item.get("name") or "") == name
        ]
        if len(exact) != 1 or str(exact[0].get("risk") or "") not in {"write", "destructive"}:
            raise AttestationError(f"raw_{name}_discovery_invalid", classification="routing")
        schema_result, _ = await _attested_call(
            session,
            "get_raw_capability_schema",
            {"name": name},
        )
        schema_payload = _structured(schema_result)
        summary = (
            schema_payload.get("summary") if isinstance(schema_payload.get("summary"), dict) else {}
        )
        data = schema_payload.get("data") if isinstance(schema_payload.get("data"), dict) else {}
        schema_hash = str(summary.get("schema_hash") or "")
        input_schema = data.get("input_schema")
        if not schema_hash or not isinstance(input_schema, dict):
            raise AttestationError(f"raw_{name}_schema_invalid", classification="schema")
        raw_contracts.append(
            {
                "name": name,
                "risk": str(summary.get("risk") or ""),
                "schema_hash": schema_hash,
                "schema_sha256": _sha256(input_schema),
            }
        )

    operations = {
        "board": list(BOARD_OPERATION_ORDER),
        "inventory": list(INVENTORY_OPERATION_ORDER),
        "document": list(DOCUMENT_OPERATION_ORDER),
        "finance": list(FINANCE_OPERATION_ORDER),
    }
    return {
        "format": ATTESTATION_MANIFEST_FORMAT,
        "generated_at": _utc_now(),
        "anonymous_access_blocked": anonymous_blocked,
        "anonymous_status_code": anonymous_status,
        "tool_count": len(tool_contracts),
        "tools": tool_contracts,
        "operation_count": sum(len(items) for items in operations.values()),
        "operations": operations,
        "manager_raw_crm_capabilities": raw_contracts,
        "case_ids": [spec.case_id for spec in build_case_specs()],
    }


async def _assert_frozen_manifest_live(
    session: Any,
    manifest: dict[str, Any],
) -> None:
    listed = await session.list_tools()
    tools = list(getattr(listed, "tools", []) or [])
    expected_tools = {
        str(item.get("name") or ""): item
        for item in manifest.get("tools", [])
        if isinstance(item, dict)
    }
    if {str(tool.name) for tool in tools} != set(expected_tools):
        raise AttestationError("live_public_tool_manifest_drift", classification="schema")
    for tool in tools:
        name = str(tool.name)
        expected = expected_tools[name]
        schema = _tool_input_schema(tool)
        annotations = _tool_annotations(tool)
        if (
            _sha256(schema) != expected.get("schema_sha256")
            or _sha256(annotations) != expected.get("annotations_sha256")
            or len(_canonical_json(schema)) != expected.get("request_schema_bytes")
        ):
            raise AttestationError(
                f"live_public_tool_schema_drift_{name}",
                classification="schema",
            )

    frozen_raw = {
        str(item.get("name") or ""): item
        for item in manifest.get("manager_raw_crm_capabilities", [])
        if isinstance(item, dict)
    }
    if set(frozen_raw) != set(MANAGER_RAW_CRM_CAPABILITIES):
        raise AttestationError("frozen_raw_capability_manifest_invalid", classification="schema")
    for name in MANAGER_RAW_CRM_CAPABILITIES:
        discovered, _ = await _attested_call(
            session,
            "discover_raw_capabilities",
            {"query": name, "limit": 10},
        )
        capabilities = (
            (_structured(discovered).get("data") or {}).get("capabilities")
            if isinstance(_structured(discovered).get("data"), dict)
            else []
        )
        exact = [
            item
            for item in capabilities or []
            if isinstance(item, dict) and str(item.get("name") or "") == name
        ]
        schema_result, _ = await _attested_call(
            session,
            "get_raw_capability_schema",
            {"name": name},
        )
        schema_payload = _structured(schema_result)
        summary = (
            schema_payload.get("summary") if isinstance(schema_payload.get("summary"), dict) else {}
        )
        input_schema = (
            (schema_payload.get("data") or {}).get("input_schema")
            if isinstance(schema_payload.get("data"), dict)
            else None
        )
        expected = frozen_raw[name]
        if (
            len(exact) != 1
            or str(exact[0].get("risk") or "") != expected.get("risk")
            or str(exact[0].get("schema_hash") or "") != expected.get("schema_hash")
            or str(summary.get("schema_hash") or "") != expected.get("schema_hash")
            or not isinstance(input_schema, dict)
            or _sha256(input_schema) != expected.get("schema_sha256")
        ):
            raise AttestationError(
                f"live_raw_capability_schema_drift_{name}",
                classification="schema",
            )


def _first_card_id(payload: dict[str, Any]) -> str:
    candidates = []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (payload.get("card_sample"), summary.get("card_sample"), data.get("cards")):
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    for item in candidates:
        card_id = str(item.get("id") or "").strip()
        if card_id:
            return card_id
    return ""


def _first_entity_id(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    pending = [payload]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key in keys:
                value = current.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            pending.extend(value for value in current.values() if isinstance(value, (dict, list)))
        elif isinstance(current, list):
            pending.extend(current[:30])
    return ""


async def _public_case(
    session: Any,
    *,
    target: str,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    refs = state["refs"]
    prefix = str(refs["synthetic_prefix"])
    evidence: list[dict[str, Any]] = []

    async def call(
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        expect_ok: bool = True,
    ) -> Any:
        result, item = await _attested_call(
            session,
            name,
            arguments,
            expect_ok=expect_ok,
        )
        evidence.append(item)
        return result

    if target in {"ping_connector", "get_connector_identity", "get_runtime_status"}:
        await call(target)
    elif target == "agent_bootstrap":
        result = await call(
            target,
            {
                "query": f"{prefix} gateway attestation",
                "intent": "crm_agent_integration_audit",
                "sample_limit": 1,
            },
        )
        card_id = _first_card_id(_structured(result))
        if not card_id:
            raise AttestationError("bootstrap_missing_compact_card_ref")
        refs["read_card_id"] = card_id
    elif target == "agent_board_digest":
        result = await call(
            target,
            {
                "scope": "crm",
                "limit": 1,
                "fields": ["id", "short_id", "vehicle", "title", "updated_at"],
            },
        )
        card_id = _first_card_id(_structured(result))
        if card_id:
            refs["read_card_id"] = card_id
    elif target == "agent_search":
        card_id = str(refs.get("read_card_id") or "")
        if not card_id:
            raise AttestationError("read_card_ref_missing")
        await call(
            target,
            {"entity": "card", "query": card_id, "limit": 1, "include_archived": False},
        )
        await call(
            target,
            {"entity": "card", "query": f"{prefix}-not-found", "limit": 1},
        )
    elif target == "agent_entity_context":
        card_id = str(refs.get("read_card_id") or "")
        if not card_id:
            raise AttestationError("read_card_ref_missing")
        await call(
            target,
            {"entity": "card", "entity_id": card_id, "detail": "summary"},
        )
        await call(
            target,
            {"entity": "card", "entity_id": f"{prefix}-missing", "detail": "summary"},
            expect_ok=False,
        )
    elif target == "list_agent_workflows":
        await call(target, {"query": "CRM", "intent": "crm_agent_integration_audit", "limit": 50})
    elif target == "prepare_action_contract":
        await call(
            target,
            {
                "domain": "inventory",
                "action": "adjust",
                "target_id": f"{prefix}-inventory",
                "planned_changes": {
                    "movement_type": "write_off",
                    "quantity": 1,
                    "card_id": f"{prefix}-card",
                },
                "owner_intent": "synthetic gateway attestation only",
                "expected_revision": f"{prefix}@0",
                "idempotency_key": f"{prefix}-contract",
                "dry_run": True,
            },
        )
    elif target == "agent_board_workflow":
        await call(
            target,
            {
                "operation": "manager_board_scan",
                "payload": {"limit": 1},
                "idempotency_key": f"{prefix}-public-board",
                "mode": "dry_run",
            },
        )
        await call(
            target,
            {
                "operation": "create_card",
                "payload": {},
                "idempotency_key": f"{prefix}-public-board-invalid",
                "mode": "dry_run",
            },
            expect_ok=False,
        )
    elif target == "agent_finance_workflow":
        await call(
            target,
            {
                "operation": "list_cashboxes",
                "payload": {"limit": 1},
                "idempotency_key": f"{prefix}-public-finance",
            },
        )
        await call(
            target,
            {
                "operation": "create_card",
                "payload": {},
                "idempotency_key": f"{prefix}-public-finance-invalid",
            },
            expect_ok=False,
        )
    elif target == "agent_inventory_workflow":
        await call(
            target,
            {
                "operation": "list_inventory_items",
                "payload": {"limit": 1},
                "idempotency_key": f"{prefix}-public-inventory",
            },
        )
    elif target == "agent_document_workflow":
        await call(
            target,
            {
                "operation": "list_shared_files",
                "payload": {},
                "idempotency_key": f"{prefix}-public-document",
                "allow_large_output": False,
            },
        )
    elif target == "discover_raw_capabilities":
        await call(target, {"query": "get_cards", "limit": 5})
        await call(target, {"query": "create_card", "limit": 5})
    elif target == "get_raw_capability_schema":
        result = await call(target, {"name": "get_cards"})
        summary = _structured(result).get("summary")
        schema_hash = str(summary.get("schema_hash") or "") if isinstance(summary, dict) else ""
        if not schema_hash:
            raise AttestationError("get_cards_schema_hash_missing", classification="schema")
        refs["get_cards_schema_hash"] = schema_hash
        await call(target, {"name": f"{prefix}-missing-capability"}, expect_ok=False)
    elif target == "call_raw_capability":
        schema_hash = str(refs.get("get_cards_schema_hash") or "")
        if not schema_hash:
            raise AttestationError("get_cards_schema_hash_missing", classification="schema")
        await call(
            target,
            {
                "name": "get_cards",
                "arguments": {"include_archived": False, "compact": True, "limit": 1},
                "schema_hash": schema_hash,
                "allow_large_output": False,
            },
        )
        await call(
            target,
            {
                "name": "get_cards",
                "arguments": {"include_archived": False, "compact": True, "limit": 1},
                "schema_hash": "0" * 16,
                "allow_large_output": False,
            },
            expect_ok=False,
        )
    elif target == "start_workflow":
        result = await call(
            target,
            {
                "workflow_id": "gateway_v2_attestation",
                "intent": "crm_agent_integration_audit",
                "idempotency_key": f"{prefix}-lifecycle",
                "query": "safe synthetic lifecycle attestation",
                "dry_run": True,
                "source": "gateway-attestation",
                "metadata": {"synthetic": True, "attestation_run_id": prefix},
            },
        )
        structured = _structured(result)
        run_id = structured.get("run_id")
        if not isinstance(run_id, int):
            raise AttestationError("synthetic_workflow_run_id_missing")
        refs["workflow_run_id"] = run_id
        summary = structured.get("summary") if isinstance(structured.get("summary"), dict) else {}
        version = summary.get("state_version")
        if isinstance(version, int):
            refs["workflow_state_version"] = version
    elif target == "workflow_status":
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "include_events": False,
                "include_external_steps": True,
            },
        )
        summary = _structured(result).get("summary")
        version = summary.get("state_version") if isinstance(summary, dict) else None
        if not isinstance(version, int):
            raise AttestationError("workflow_state_version_missing")
        refs["workflow_state_version"] = version
    elif target == "workflow_checkpoint":
        version = int(refs["workflow_state_version"])
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "checkpoint": {"phase": "attestation", "next_action": "execute"},
                "message": "synthetic attestation checkpoint",
                "expected_state_version": version,
            },
        )
        refs["workflow_state_version"] = int(
            (_structured(result).get("summary") or {})["state_version"]
        )
        await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "checkpoint": {"phase": "stale"},
                "expected_state_version": version,
            },
            expect_ok=False,
        )
    elif target == "workflow_transition":
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "status": "executing",
                "message": "synthetic attestation execution",
                "expected_state_version": int(refs["workflow_state_version"]),
            },
        )
        refs["workflow_state_version"] = int(
            (_structured(result).get("summary") or {})["state_version"]
        )
    elif target == "workflow_wait_for_external":
        step_id = f"{prefix}-external"
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "step_id": step_id,
                "connector": "gateway-attestation",
                "action": "refs-only-probe",
                "request_refs": {"thread_id": step_id},
                "expected_state_version": int(refs["workflow_state_version"]),
            },
        )
        refs["workflow_step_id"] = step_id
        refs["workflow_state_version"] = int(
            (_structured(result).get("summary") or {})["state_version"]
        )
    elif target == "complete_external_step":
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "step_id": str(refs["workflow_step_id"]),
                "result_refs": {"message_id": str(refs["workflow_step_id"])},
                "expected_state_version": int(refs["workflow_state_version"]),
            },
        )
        refs["workflow_state_version"] = int(
            (_structured(result).get("summary") or {})["state_version"]
        )
    elif target == "workflow_resume":
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "expected_state_version": int(refs["workflow_state_version"]),
            },
        )
        refs["workflow_state_version"] = int(
            (_structured(result).get("summary") or {})["state_version"]
        )
    elif target == "workflow_cancel":
        result = await call(
            target,
            {
                "run_id": int(refs["workflow_run_id"]),
                "reason": "synthetic gateway attestation completed",
                "expected_state_version": int(refs["workflow_state_version"]),
            },
        )
        if _structured(result).get("status") != "cancelled":
            raise AttestationError("synthetic_workflow_not_cancelled")
    else:
        raise AttestationError(f"public_case_not_implemented_{target}")
    return evidence


def _read_operation_payload(operation: str, refs: dict[str, Any]) -> dict[str, Any]:
    if operation in {
        "manager_board_scan",
        "list_ready_unpaid_cards",
        "triage_inbox_cards",
        "list_cards_missing_manager_data",
        "audit_repair_order_consistency",
        "audit_client_links",
    }:
        return {"limit": 1}
    if operation == "list_cashboxes":
        return {"limit": 1}
    if operation == "get_cash_journal":
        return {
            "months": 1,
            "limit": 1,
            "include_markdown": False,
            "compact_groups": True,
        }
    if operation == "get_cashbox":
        cashbox_id = str(refs.get("read_cashbox_id") or "")
        if not cashbox_id:
            raise AttestationError("read_cashbox_ref_missing")
        return {"cashbox_id": cashbox_id, "transaction_limit": 1}
    if operation == "get_repair_order":
        card_id = str(refs.get("read_card_id") or "")
        if not card_id:
            raise AttestationError("read_card_ref_missing")
        return {"card_id": card_id}
    if operation == "list_inventory_items":
        return {"limit": 1}
    if operation == "search_inventory_items":
        return {"query": str(refs["synthetic_prefix"]), "limit": 1}
    if operation == "get_inventory_item":
        item_id = str(refs.get("read_inventory_item_id") or "")
        if not item_id:
            raise AttestationError("read_inventory_item_ref_missing")
        return {"item_id": item_id}
    if operation == "list_inventory_movements":
        item_id = str(refs.get("read_inventory_item_id") or "")
        return {"item_id": item_id or None, "limit": 1}
    if operation == "list_shared_files":
        return {}
    if operation == "create_document_without_card_pdf":
        return {
            "request_text": (
                "Заказ-наряд. Тест аттестации Gateway. Автомобиль: TEST. "
                "Работа: диагностическая проверка, 1 шт, 0 руб."
            ),
            "document_type": "repair_order",
        }
    if operation in {"get_shared_file_info", "download_shared_file"}:
        file_id = str(refs.get("synthetic_file_id") or "")
        if not file_id:
            raise AttestationError("synthetic_file_ref_missing")
        payload: dict[str, Any] = {"file_id": file_id}
        if operation == "download_shared_file":
            payload.update({"include_base64": False, "max_base64_bytes": 1})
        return payload
    if operation == "download_repair_order_print_pdf":
        card_id = str(refs.get("synthetic_card_id") or "")
        if not card_id:
            raise AttestationError("synthetic_card_ref_missing")
        return {"card_id": card_id, "selected_document_ids": ["repair_order"]}
    raise AttestationError(f"read_operation_payload_missing_{operation}")


def _walk_mappings(value: Any, *, limit: int = 500) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    pending = [value]
    while pending and len(mappings) < limit:
        current = pending.pop()
        if isinstance(current, dict):
            mappings.append(current)
            pending.extend(item for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            pending.extend(current[:100])
    return mappings


def _mapping_for_entity(value: Any, entity_id: str) -> dict[str, Any] | None:
    mappings = _walk_mappings(value)
    for mapping in mappings:
        if str(mapping.get("id") or "") == entity_id:
            return mapping
    for mapping in mappings:
        for key in ("card_id", "client_id", "item_id", "file_id", "cashbox_id"):
            if str(mapping.get(key) or "") == entity_id:
                return mapping
    return None


def _first_value(value: Any, keys: tuple[str, ...]) -> Any:
    for mapping in _walk_mappings(value):
        for key in keys:
            if key in mapping and mapping[key] not in (None, ""):
                return mapping[key]
    return None


def _attempt_number(state: dict[str, Any], case_id: str) -> int:
    item = _find_case(state, case_id)
    return max(1, int(item.get("attempts") or 1))


def _synthetic_entities(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    defaults = {
        "cards": [],
        "clients": [],
        "inventory_items": [],
        "files": [],
        "cashboxes": [],
        "cash_transactions": [],
        "employees": [],
        "shift_accruals": [],
    }
    registry = state.setdefault("synthetic_entities", defaults)
    for key in defaults:
        registry.setdefault(key, [])
    return registry


async def _raw_contract(
    session: Any,
    *,
    name: str,
    evidence: list[dict[str, Any]],
) -> tuple[str, str]:
    discovered, discovered_evidence = await _attested_call(
        session,
        "discover_raw_capabilities",
        {"query": name, "limit": 10},
    )
    evidence.append(discovered_evidence)
    discovered_payload = _structured(discovered)
    capabilities = (
        (discovered_payload.get("data") or {}).get("capabilities")
        if isinstance(discovered_payload.get("data"), dict)
        else []
    )
    exact = [
        item
        for item in capabilities or []
        if isinstance(item, dict) and str(item.get("name") or "") == name
    ]
    if len(exact) != 1:
        raise AttestationError(
            f"raw_{name}_discovery_invalid",
            classification="routing",
            evidence=evidence,
        )
    discovered_hash = str(exact[0].get("schema_hash") or "")
    risk = str(exact[0].get("risk") or "")

    schema_result, schema_evidence = await _attested_call(
        session,
        "get_raw_capability_schema",
        {"name": name},
    )
    evidence.append(schema_evidence)
    schema_payload = _structured(schema_result)
    summary = (
        schema_payload.get("summary") if isinstance(schema_payload.get("summary"), dict) else {}
    )
    schema_hash = str(summary.get("schema_hash") or "")
    input_schema = (
        (schema_payload.get("data") or {}).get("input_schema")
        if isinstance(schema_payload.get("data"), dict)
        else None
    )
    if (
        not schema_hash
        or schema_hash != discovered_hash
        or not isinstance(input_schema, dict)
        or risk not in {"read", "write", "destructive"}
    ):
        raise AttestationError(
            f"raw_{name}_schema_invalid",
            classification="schema",
            evidence=evidence,
        )
    return schema_hash, risk


async def _raw_invoke(
    session: Any,
    *,
    name: str,
    arguments: dict[str, Any],
    idempotency_key: str,
    evidence: list[dict[str, Any]],
    allow_large_output: bool = False,
) -> dict[str, Any]:
    schema_hash, risk = await _raw_contract(session, name=name, evidence=evidence)
    result, call_evidence = await _attested_call(
        session,
        "call_raw_capability",
        {
            "name": name,
            "arguments": arguments,
            "schema_hash": schema_hash,
            "idempotency_key": idempotency_key if risk != "read" else "",
            "allow_large_output": allow_large_output,
        },
    )
    evidence.append(call_evidence)
    structured = _structured(result)
    verification = (
        structured.get("verification") if isinstance(structured.get("verification"), dict) else {}
    )
    if (
        verification.get("schema_hash_verified") is not True
        or verification.get("executor_ok") is not True
        or verification.get("ledger_closed") is not True
    ):
        raise AttestationError(
            f"raw_{name}_gateway_verification_invalid",
            classification="verification",
            evidence=evidence,
        )
    return structured


async def _card_context(
    session: Any,
    *,
    card_id: str,
    evidence: list[dict[str, Any]],
    include_archived: bool | None = False,
    detail: str = "summary",
) -> dict[str, Any]:
    result, read_evidence = await _attested_call(
        session,
        "agent_entity_context",
        {"entity": "card", "entity_id": card_id, "detail": detail},
    )
    evidence.append(read_evidence)
    structured = _structured(result)
    card = _mapping_for_entity(structured, card_id)
    if not isinstance(card, dict):
        raise AttestationError(
            "synthetic_card_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    archived = bool(card.get("archived"))
    if include_archived is not None and archived is not include_archived:
        raise AttestationError(
            "synthetic_card_archive_state_invalid",
            classification="verification",
            evidence=evidence,
        )
    return card


async def _create_synthetic_board_card(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
    ready_unpaid: bool = False,
) -> tuple[str, dict[str, Any]]:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    title = f"{prefix} {spec.operation} a{attempt}"[:160]
    create_arguments: dict[str, Any] = {
        "title": title,
        "vehicle": "AutoStop Synthetic",
        "description": f"{prefix} isolated Gateway attestation fixture",
        "deadline": {"total_seconds": 60},
        "tags": [{"label": "AST-GWAT", "color": "green"}],
    }
    if ready_unpaid:
        create_arguments["tags"].append({"label": "Готов", "color": "green"})
    created = await _raw_invoke(
        session,
        name="create_card",
        arguments=create_arguments,
        idempotency_key=(f"{prefix}-{spec.operation}-fixture-a{attempt}")[:160],
        evidence=evidence,
    )
    card_id = _first_entity_id(created, ("card_id", "id"))
    if not card_id:
        raise AttestationError(
            "synthetic_card_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    registry = _synthetic_entities(state)
    registry["cards"].append(
        {
            "id": card_id,
            "case_id": spec.case_id,
            "status": "active",
        }
    )
    card = await _card_context(session, card_id=card_id, evidence=evidence)

    if ready_unpaid:
        updated_at = str(card.get("updated_at") or "")
        if not updated_at:
            raise AttestationError(
                "synthetic_card_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        try:
            await _raw_invoke(
                session,
                name="update_repair_order",
                arguments={
                    "card_id": card_id,
                    "repair_order": {
                        "works": [
                            {
                                "name": f"{prefix} synthetic work",
                                "quantity": "1",
                                "price": "1",
                            }
                        ]
                    },
                    "expected_updated_at": updated_at,
                },
                idempotency_key=(f"{prefix}-{spec.operation}-repair-order-a{attempt}")[:160],
                evidence=evidence,
            )
        except AttestationError as setup_error:
            try:
                await _cleanup_synthetic_board_card(
                    session,
                    spec=spec,
                    state=state,
                    card_id=card_id,
                    evidence=evidence,
                )
            except AttestationError as cleanup_error:
                raise AttestationError(
                    "synthetic_card_cleanup_failed_after_fixture_setup",
                    classification="backend_effect",
                    evidence=[*evidence, *cleanup_error.evidence],
                ) from cleanup_error
            raise AttestationError(
                setup_error.code,
                classification=setup_error.classification,
                evidence=evidence,
            ) from setup_error
        card = await _card_context(session, card_id=card_id, evidence=evidence)
    return card_id, card


async def _archive_synthetic_card(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    card_id: str,
    evidence: list[dict[str, Any]],
) -> None:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    await _raw_invoke(
        session,
        name="archive_card",
        arguments={"card_id": card_id},
        idempotency_key=(f"{prefix}-{spec.operation}-archive-a{attempt}")[:160],
        evidence=evidence,
    )
    await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        include_archived=True,
    )
    for item in _synthetic_entities(state)["cards"]:
        if item.get("id") == card_id:
            item["status"] = "archived"


def _synthetic_repair_order_needs_cleanup(card: dict[str, Any]) -> bool:
    repair_order = card.get("repair_order")
    if not isinstance(repair_order, dict):
        return False
    if repair_order.get("is_empty_for_archive") is True:
        return False
    return bool(
        repair_order.get("works")
        or repair_order.get("materials")
        or repair_order.get("payments")
        or str(repair_order.get("prepayment") or "").strip() not in {"", "0", "0.0", "0.00"}
    )


async def _cleanup_synthetic_board_card(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    card_id: str,
    evidence: list[dict[str, Any]],
) -> None:
    card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        include_archived=None,
        detail="full",
    )
    if bool(card.get("archived")):
        for item in _synthetic_entities(state)["cards"]:
            if item.get("id") == card_id:
                item["status"] = "archived"
        return
    if _synthetic_repair_order_needs_cleanup(card):
        updated_at = str(card.get("updated_at") or "")
        if not updated_at:
            raise AttestationError(
                "synthetic_cleanup_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        attempt = _attempt_number(state, spec.case_id)
        prefix = str(state["refs"]["synthetic_prefix"])
        await _raw_invoke(
            session,
            name="update_repair_order",
            arguments={
                "card_id": card_id,
                "repair_order": {
                    "works": [],
                    "materials": [],
                    "payments": [],
                    "prepayment": "0",
                },
                "expected_updated_at": updated_at,
            },
            idempotency_key=(f"{prefix}-{spec.operation}-cleanup-repair-order-a{attempt}")[:160],
            evidence=evidence,
        )
        card = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            detail="full",
        )
        if _synthetic_repair_order_needs_cleanup(card):
            raise AttestationError(
                "synthetic_repair_order_cleanup_readback_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    await _archive_synthetic_card(
        session,
        spec=spec,
        state=state,
        card_id=card_id,
        evidence=evidence,
    )


async def _reconcile_synthetic_board_registry(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    active_ids = [
        str(item.get("id") or "")
        for item in _synthetic_entities(state)["cards"]
        if item.get("case_id") == spec.case_id
        and item.get("status") == "active"
        and str(item.get("id") or "")
    ]
    for card_id in active_ids:
        await _cleanup_synthetic_board_card(
            session,
            spec=spec,
            state=state,
            card_id=card_id,
            evidence=evidence,
        )


def _board_write_payload(
    operation: str,
    *,
    card_id: str,
    updated_at: str,
    prefix: str,
) -> dict[str, Any]:
    if operation == "bulk_set_deadline_if_below":
        return {
            "card_ids": [card_id],
            "min_total_seconds": 300,
            "target_total_seconds": 600,
            "limit": 1,
            "expected_updated_at_by_card_id": {card_id: updated_at},
        }
    if operation == "bulk_refresh_board_summaries":
        return {
            "card_ids": [card_id],
            "limit": 1,
            "only_missing": True,
            "expected_updated_at_by_card_id": {card_id: updated_at},
        }
    if operation == "cleanup_card":
        return {
            "card_id": card_id,
            "title": f"{prefix} cleanup verified"[:160],
            "description": f"{prefix} exact cleanup-card apply",
            "expected_updated_at": updated_at,
            "response_mode": "compact",
            "refresh_summary": False,
        }
    if operation == "apply_ready_unpaid_followups":
        return {
            "card_ids": [card_id],
            "target_total_seconds": 600,
            "limit": 1,
            "refresh_summary": True,
            "expected_updated_at_by_card_id": {card_id: updated_at},
        }
    raise AttestationError(f"board_write_payload_missing_{operation}")


def _stale_board_write_payload(
    operation: str,
    payload: dict[str, Any],
    *,
    card_id: str,
) -> dict[str, Any]:
    stale = dict(payload)
    if operation == "cleanup_card":
        stale["expected_updated_at"] = "2000-01-01T00:00:00+00:00"
    else:
        stale["expected_updated_at_by_card_id"] = {card_id: "2000-01-01T00:00:00+00:00"}
    return stale


def _assert_board_write_effect(
    operation: str,
    *,
    card: dict[str, Any],
    prefix: str,
) -> None:
    if operation == "bulk_set_deadline_if_below":
        remaining = card.get("remaining_seconds")
        if not isinstance(remaining, int) or remaining < 590:
            raise AttestationError("board_deadline_exact_reread_failed")
        return
    if operation == "bulk_refresh_board_summaries":
        if not str(card.get("board_summary") or "").strip():
            raise AttestationError("board_summary_exact_reread_failed")
        return
    if operation == "cleanup_card":
        if str(card.get("title") or "") != f"{prefix} cleanup verified"[:160]:
            raise AttestationError("cleanup_card_exact_reread_failed")
        return
    if operation == "apply_ready_unpaid_followups":
        tag_text = json.dumps(card.get("tags") or [], ensure_ascii=False).casefold()
        if "ждет оплаты" not in tag_text:
            raise AttestationError("ready_unpaid_followup_exact_reread_failed")
        return
    raise AttestationError(f"board_write_verification_missing_{operation}")


async def _board_write_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    card_id = ""
    failure: AttestationError | None = None
    try:
        await _reconcile_synthetic_board_registry(
            session,
            spec=spec,
            state=state,
            evidence=evidence,
        )
        card_id, card = await _create_synthetic_board_card(
            session,
            spec=spec,
            state=state,
            evidence=evidence,
            ready_unpaid=spec.operation == "apply_ready_unpaid_followups",
        )
        updated_at = str(card.get("updated_at") or "")
        if not updated_at:
            raise AttestationError(
                "synthetic_card_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        payload = _board_write_payload(
            spec.operation,
            card_id=card_id,
            updated_at=updated_at,
            prefix=prefix,
        )

        missing_key_arguments = {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": "",
            "mode": "dry_run",
        }
        _, missing_key_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            missing_key_arguments,
            expect_ok=False,
        )
        evidence.append(missing_key_evidence)
        if missing_key_evidence.get("error_code") != "idempotency_key_required":
            raise AttestationError(
                "board_write_idempotency_gate_invalid",
                classification="policy",
                evidence=evidence,
            )

        stale_arguments = {
            "operation": spec.operation,
            "payload": _stale_board_write_payload(
                spec.operation,
                payload,
                card_id=card_id,
            ),
            "idempotency_key": (f"{prefix}-{spec.operation}-stale-a{attempt}")[:160],
            "mode": "apply",
        }
        _, stale_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            stale_arguments,
            expect_ok=False,
        )
        evidence.append(stale_evidence)

        dry_arguments = {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": (f"{prefix}-{spec.operation}-dry-a{attempt}")[:160],
            "mode": "dry_run",
        }
        _, dry_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            dry_arguments,
        )
        evidence.append(dry_evidence)

        apply_arguments = {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": (f"{prefix}-{spec.operation}-apply-a{attempt}")[:160],
            "mode": "apply",
        }
        _, apply_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            apply_arguments,
        )
        evidence.append(apply_evidence)
        replay, replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            apply_arguments,
        )
        evidence.append(replay_evidence)
        replay_structured = _structured(replay)
        replay_summary = (
            replay_structured.get("summary")
            if isinstance(replay_structured.get("summary"), dict)
            else {}
        )
        replay_verification = (
            replay_structured.get("verification")
            if isinstance(replay_structured.get("verification"), dict)
            else {}
        )
        if (
            replay_summary.get("deduplicated") is not True
            or replay_verification.get("idempotency_reused") is not True
            or replay_verification.get("prior_terminal_state") is not True
        ):
            raise AttestationError(
                "board_write_idempotency_replay_invalid",
                classification="verification",
                evidence=evidence,
            )

        reread = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
        )
        try:
            _assert_board_write_effect(
                spec.operation,
                card=reread,
                prefix=prefix,
            )
        except AttestationError as exc:
            raise AttestationError(
                exc.code,
                classification=exc.classification,
                evidence=evidence,
            ) from exc
    except AttestationError as exc:
        failure = exc

    if card_id:
        try:
            await _cleanup_synthetic_board_card(
                session,
                spec=spec,
                state=state,
                card_id=card_id,
                evidence=evidence,
            )
        except AttestationError as cleanup_error:
            raise AttestationError(
                "synthetic_card_cleanup_failed",
                classification="backend_effect",
                evidence=[*evidence, *cleanup_error.evidence],
            ) from cleanup_error
    if failure is not None:
        raise AttestationError(
            failure.code,
            classification=failure.classification,
            evidence=evidence or failure.evidence,
        ) from failure
    if any(int(item.get("response_bytes") or 0) > 262_144 for item in evidence):
        raise AttestationError(
            "board_write_response_payload_limit_exceeded",
            classification="privacy_payload",
            evidence=evidence,
        )
    return evidence


def _inventory_decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AttestationError(
            "inventory_quantity_not_decimal",
            classification="verification",
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise AttestationError(
            "inventory_quantity_invalid",
            classification="verification",
        )
    return parsed


def _inventory_item_mapping(value: Any, item_id: str) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "") == item_id
            and "updated_at" in mapping
            and "quantity" in mapping
            and "name" in mapping
        ):
            return mapping
    return None


def _inventory_movement_mapping(value: Any, movement_id: str) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "") == movement_id
            and "item_id" in mapping
            and "kind" in mapping
            and "quantity" in mapping
        ):
            return mapping
    return None


def _latest_inventory_movement(
    value: Any,
    *,
    kind: str,
    item_id: str,
    card_id: str = "",
    related_movement_id: str = "",
) -> dict[str, Any] | None:
    candidates = [
        mapping
        for mapping in _walk_mappings(value)
        if str(mapping.get("id") or "")
        and str(mapping.get("kind") or "") == kind
        and str(mapping.get("item_id") or "") == item_id
        and (not card_id or str(mapping.get("card_id") or "") == card_id)
        and (
            not related_movement_id
            or str(mapping.get("related_movement_id") or "") == related_movement_id
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda mapping: (
            str(mapping.get("created_at") or ""),
            str(mapping.get("id") or ""),
        ),
    )


def _contains_scalar(value: Any, expected: str) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current[:100])
        elif str(current) == expected:
            return True
    return False


def _assert_workflow_replay(
    result: Any,
    *,
    code_prefix: str,
    evidence: list[dict[str, Any]],
) -> None:
    structured = _structured(result)
    summary = structured.get("summary") if isinstance(structured.get("summary"), dict) else {}
    verification = (
        structured.get("verification") if isinstance(structured.get("verification"), dict) else {}
    )
    if (
        summary.get("deduplicated") is not True
        or verification.get("idempotency_reused") is not True
        or verification.get("prior_terminal_state") is not True
    ):
        raise AttestationError(
            f"{code_prefix}_idempotency_replay_invalid",
            classification="verification",
            evidence=evidence,
        )


def _assert_response_budget(
    evidence: list[dict[str, Any]],
    *,
    code: str,
) -> None:
    if any(int(item.get("response_bytes") or 0) > 262_144 for item in evidence):
        raise AttestationError(
            code,
            classification="privacy_payload",
            evidence=evidence,
        )


async def _inventory_item_context(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    item_id: str,
    purpose: str,
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    result, read_evidence = await _attested_call(
        session,
        "agent_inventory_workflow",
        {
            "operation": "get_inventory_item",
            "payload": {"item_id": item_id},
            "idempotency_key": (
                f"{prefix}-{spec.operation}-reread-{purpose}-a{attempt}-{len(evidence)}"
            )[:160],
        },
    )
    evidence.append(read_evidence)
    structured = _structured(result)
    item = _inventory_item_mapping(structured, item_id)
    if not isinstance(item, dict):
        raise AttestationError(
            "synthetic_inventory_item_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    if not str(item.get("updated_at") or "").strip():
        raise AttestationError(
            "synthetic_inventory_item_revision_missing",
            classification="verification",
            evidence=evidence,
        )
    _inventory_decimal(item.get("quantity"))
    return item, structured


def _active_inventory_registry_item(state: dict[str, Any]) -> dict[str, Any] | None:
    for item in reversed(_synthetic_entities(state)["inventory_items"]):
        if item.get("status") == "active" and str(item.get("id") or ""):
            return item
    return None


def _register_inventory_item(
    state: dict[str, Any],
    *,
    item_id: str,
    case_id: str,
) -> None:
    registry = _synthetic_entities(state)["inventory_items"]
    if any(str(item.get("id") or "") == item_id for item in registry):
        return
    registry.append({"id": item_id, "case_id": case_id, "status": "active"})


async def _inventory_expected_failure(
    session: Any,
    *,
    spec: CaseSpec,
    arguments: dict[str, Any],
    expected_code: str,
    evidence: list[dict[str, Any]],
) -> None:
    result, call_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
        expect_ok=False,
    )
    evidence.append(call_evidence)
    if not _contains_scalar(_structured(result), expected_code):
        raise AttestationError(
            f"{spec.operation}_{expected_code}_not_proven",
            classification="verification",
            evidence=evidence,
        )
    call_evidence["expected_error_code"] = expected_code


async def _inventory_missing_key_gate(
    session: Any,
    *,
    spec: CaseSpec,
    payload: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    result, call_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": "",
        },
        expect_ok=False,
    )
    evidence.append(call_evidence)
    if call_evidence.get("error_code") != "idempotency_key_required" or not _contains_scalar(
        _structured(result), "idempotency_key_required"
    ):
        raise AttestationError(
            "inventory_write_idempotency_gate_invalid",
            classification="policy",
            evidence=evidence,
        )


async def _inventory_apply_and_replay(
    session: Any,
    *,
    spec: CaseSpec,
    payload: dict[str, Any],
    idempotency_key: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    arguments = {
        "operation": spec.operation,
        "payload": payload,
        "idempotency_key": idempotency_key,
    }
    result, apply_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(apply_evidence)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix=spec.operation,
        evidence=evidence,
    )
    return _structured(result)


async def _inventory_save_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_inventory_registry_item(state)
    item_id = str((registered or {}).get("id") or "")
    current: dict[str, Any] | None = None
    if item_id:
        current, _ = await _inventory_item_context(
            session,
            spec=spec,
            state=state,
            item_id=item_id,
            purpose="resume",
            evidence=evidence,
        )

    payload: dict[str, Any] = {
        "name": f"{prefix} synthetic inventory"[:180],
        "catalog_number": f"{prefix}-ITEM"[:120],
        "unit": "шт",
        "quantity": "0",
        "cost_price": "0",
        "sale_price": "0",
    }
    if current is not None:
        payload.update(
            {
                "item_id": item_id,
                "expected_updated_at": str(current["updated_at"]),
            }
        )
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    invalid_payload = dict(payload)
    invalid_payload["name"] = ""
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": invalid_payload,
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    if current is not None:
        stale_payload = dict(payload)
        stale_payload["expected_updated_at"] = "2000-01-01T00:00:00+00:00"
        await _inventory_expected_failure(
            session,
            spec=spec,
            arguments={
                "operation": spec.operation,
                "payload": stale_payload,
                "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
            },
            expected_code="inventory_item_update_conflict",
            evidence=evidence,
        )

    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    if not item_id:
        item_id = _first_entity_id(applied, ("item_id", "id"))
        if not item_id:
            raise AttestationError(
                "synthetic_inventory_item_id_missing",
                classification="backend_effect",
                evidence=evidence,
            )
        _register_inventory_item(state, item_id=item_id, case_id=spec.case_id)
        state["refs"]["synthetic_inventory_item_id"] = item_id

    reread, _ = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="apply",
        evidence=evidence,
    )
    if (
        str(reread.get("name") or "") != payload["name"]
        or str(reread.get("catalog_number") or "") != payload["catalog_number"]
        or _inventory_decimal(reread.get("quantity")) != Decimal("0")
    ):
        raise AttestationError(
            "save_inventory_item_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    if current is None:
        stale_payload = {
            **payload,
            "item_id": item_id,
            "expected_updated_at": "2000-01-01T00:00:00+00:00",
        }
        await _inventory_expected_failure(
            session,
            spec=spec,
            arguments={
                "operation": spec.operation,
                "payload": stale_payload,
                "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
            },
            expected_code="inventory_item_update_conflict",
            evidence=evidence,
        )
        after_stale, _ = await _inventory_item_context(
            session,
            spec=spec,
            state=state,
            item_id=item_id,
            purpose="stale",
            evidence=evidence,
        )
        if str(after_stale.get("updated_at") or "") != str(reread.get("updated_at") or ""):
            raise AttestationError(
                "save_inventory_item_stale_write_changed_revision",
                classification="backend_effect",
                evidence=evidence,
            )
    _assert_response_budget(
        evidence,
        code="inventory_write_response_payload_limit_exceeded",
    )
    return evidence


async def _inventory_replenish_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_inventory_registry_item(state)
    item_id = str((registered or {}).get("id") or "")
    if not item_id:
        raise AttestationError(
            "synthetic_inventory_item_ref_missing",
            classification="routing",
        )
    before, _ = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="before",
        evidence=evidence,
    )
    before_quantity = _inventory_decimal(before.get("quantity"))
    payload = {
        "item_id": item_id,
        "quantity": "1",
        "expected_updated_at": str(before["updated_at"]),
        "cost_price": "0",
        "sale_price": "0",
        "note": f"{prefix} synthetic replenish"[:240],
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "quantity": "0"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="inventory_item_update_conflict",
        evidence=evidence,
    )
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    after, after_payload = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="apply",
        evidence=evidence,
    )
    if _inventory_decimal(after.get("quantity")) != before_quantity + Decimal("1"):
        raise AttestationError(
            "replenish_inventory_item_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    movement = _latest_inventory_movement(
        after_payload,
        kind="incoming",
        item_id=item_id,
    )
    movement_id = str((movement or {}).get("id") or "")
    if (
        not movement_id
        or not isinstance(movement, dict)
        or str(movement.get("kind") or "") != "incoming"
        or str(movement.get("item_id") or "") != item_id
    ):
        raise AttestationError(
            "replenish_inventory_movement_exact_reread_failed",
            classification="verification",
            evidence=evidence,
        )
    state["refs"]["synthetic_inventory_replenish_movement_id"] = movement_id
    _assert_response_budget(
        evidence,
        code="inventory_write_response_payload_limit_exceeded",
    )
    return evidence


def _latest_unreturned_inventory_write_off(
    value: Any,
    *,
    item_id: str,
    card_id: str,
) -> dict[str, Any] | None:
    movements = [
        mapping
        for mapping in _walk_mappings(value)
        if str(mapping.get("id") or "")
        and str(mapping.get("item_id") or "") == item_id
        and str(mapping.get("kind") or "") in {"write_off", "return"}
    ]
    returned_ids = {
        str(mapping.get("related_movement_id") or "")
        for mapping in movements
        if str(mapping.get("kind") or "") == "return"
    }
    candidates = [
        mapping
        for mapping in movements
        if str(mapping.get("kind") or "") == "write_off"
        and str(mapping.get("card_id") or "") == card_id
        and str(mapping.get("id") or "") not in returned_ids
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda mapping: (
            str(mapping.get("created_at") or ""),
            str(mapping.get("id") or ""),
        ),
    )


async def _reconcile_inventory_write_off_registry(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    item_id: str,
    evidence: list[dict[str, Any]],
) -> None:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cards = [
        str(item.get("id") or "")
        for item in _synthetic_entities(state)["cards"]
        if item.get("case_id") == spec.case_id
        and item.get("status") == "active"
        and str(item.get("id") or "")
    ]
    for index, card_id in enumerate(active_cards, start=1):
        item_before, item_payload = await _inventory_item_context(
            session,
            spec=spec,
            state=state,
            item_id=item_id,
            purpose=f"reconcile-{index}",
            evidence=evidence,
        )
        card = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            include_archived=None,
            detail="full",
        )
        movement = _latest_unreturned_inventory_write_off(
            item_payload,
            item_id=item_id,
            card_id=card_id,
        )
        if movement is not None and not bool(card.get("archived")):
            movement_id = str(movement["id"])
            quantity = _inventory_decimal(movement.get("quantity"))
            result, return_evidence = await _attested_call(
                session,
                "agent_inventory_workflow",
                {
                    "operation": "return_inventory_movement",
                    "payload": {
                        "movement_id": movement_id,
                        "card_id": card_id,
                        "expected_updated_at": str(item_before["updated_at"]),
                        "expected_card_updated_at": str(card.get("updated_at") or ""),
                    },
                    "idempotency_key": (
                        f"{prefix}-{spec.operation}-reconcile-return-a{attempt}-{index}"
                    )[:160],
                },
            )
            evidence.append(return_evidence)
            if not _tool_ok(result):
                raise AttestationError(
                    "synthetic_inventory_write_off_reconcile_return_failed",
                    classification="backend_effect",
                    evidence=evidence,
                )
            item_after, _ = await _inventory_item_context(
                session,
                spec=spec,
                state=state,
                item_id=item_id,
                purpose=f"reconcile-return-{index}",
                evidence=evidence,
            )
            if _inventory_decimal(item_after.get("quantity")) != (
                _inventory_decimal(item_before.get("quantity")) + quantity
            ):
                raise AttestationError(
                    "synthetic_inventory_write_off_reconcile_quantity_failed",
                    classification="backend_effect",
                    evidence=evidence,
                )
        await _cleanup_synthetic_board_card(
            session,
            spec=spec,
            state=state,
            card_id=card_id,
            evidence=evidence,
        )


async def _inventory_write_off_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_inventory_registry_item(state)
    item_id = str((registered or {}).get("id") or "")
    if not item_id:
        raise AttestationError(
            "synthetic_inventory_item_ref_missing",
            classification="routing",
        )
    await _reconcile_inventory_write_off_registry(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        evidence=evidence,
    )
    before, _ = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="before",
        evidence=evidence,
    )
    before_quantity = _inventory_decimal(before.get("quantity"))
    if before_quantity < Decimal("1"):
        raise AttestationError(
            "synthetic_inventory_quantity_not_replenished",
            classification="backend_effect",
            evidence=evidence,
        )
    card_id, card = await _create_synthetic_board_card(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
    )
    card_revision = str(card.get("updated_at") or "")
    if not card_revision:
        raise AttestationError(
            "synthetic_inventory_card_revision_missing",
            classification="verification",
            evidence=evidence,
        )
    payload = {
        "item_id": item_id,
        "card_id": card_id,
        "quantity": "1",
        "expected_updated_at": str(before["updated_at"]),
        "expected_card_updated_at": card_revision,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "quantity": "0"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-item-a{attempt}"[:160],
        },
        expected_code="inventory_item_update_conflict",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_card_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-card-a{attempt}"[:160],
        },
        expected_code="card_update_conflict",
        evidence=evidence,
    )
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    after, after_payload = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="apply",
        evidence=evidence,
    )
    if _inventory_decimal(after.get("quantity")) != before_quantity - Decimal("1"):
        raise AttestationError(
            "write_off_inventory_item_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    movement = _latest_unreturned_inventory_write_off(
        after_payload,
        item_id=item_id,
        card_id=card_id,
    )
    movement_id = str((movement or {}).get("id") or "")
    if (
        not isinstance(movement, dict)
        or str(movement.get("kind") or "") != "write_off"
        or str(movement.get("card_id") or "") != card_id
    ):
        raise AttestationError(
            "write_off_inventory_movement_exact_reread_failed",
            classification="verification",
            evidence=evidence,
        )
    card_after = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    linked_row = next(
        (
            mapping
            for mapping in _walk_mappings(card_after)
            if str(mapping.get("inventory_movement_id") or "") == movement_id
        ),
        None,
    )
    if (
        not isinstance(linked_row, dict)
        or str(linked_row.get("inventory_item_id") or "") != item_id
    ):
        raise AttestationError(
            "write_off_inventory_material_link_exact_reread_failed",
            classification="verification",
            evidence=evidence,
        )
    state["refs"]["synthetic_inventory_card_id"] = card_id
    state["refs"]["synthetic_inventory_write_off_movement_id"] = movement_id
    _assert_response_budget(
        evidence,
        code="inventory_write_response_payload_limit_exceeded",
    )
    return evidence


async def _inventory_return_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_inventory_registry_item(state)
    item_id = str((registered or {}).get("id") or "")
    card_id = str(state["refs"].get("synthetic_inventory_card_id") or "")
    movement_id = str(state["refs"].get("synthetic_inventory_write_off_movement_id") or "")
    if not item_id or not card_id or not movement_id:
        raise AttestationError(
            "synthetic_inventory_return_refs_missing",
            classification="routing",
        )
    before, before_payload = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="before",
        evidence=evidence,
    )
    source_movement = _inventory_movement_mapping(before_payload, movement_id)
    if not isinstance(source_movement, dict):
        raise AttestationError(
            "synthetic_inventory_write_off_movement_missing",
            classification="verification",
            evidence=evidence,
        )
    returned_quantity = _inventory_decimal(source_movement.get("quantity"))
    before_quantity = _inventory_decimal(before.get("quantity"))
    card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    card_revision = str(card.get("updated_at") or "")
    payload = {
        "movement_id": movement_id,
        "card_id": card_id,
        "expected_updated_at": str(before["updated_at"]),
        "expected_card_updated_at": card_revision,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "movement_id": f"{prefix}-missing-movement"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="not_found",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-item-a{attempt}"[:160],
        },
        expected_code="inventory_item_update_conflict",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_card_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-card-a{attempt}"[:160],
        },
        expected_code="card_update_conflict",
        evidence=evidence,
    )
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    after, after_payload = await _inventory_item_context(
        session,
        spec=spec,
        state=state,
        item_id=item_id,
        purpose="apply",
        evidence=evidence,
    )
    if _inventory_decimal(after.get("quantity")) != before_quantity + returned_quantity:
        raise AttestationError(
            "return_inventory_movement_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    return_movement = _latest_inventory_movement(
        after_payload,
        kind="return",
        item_id=item_id,
        card_id=card_id,
        related_movement_id=movement_id,
    )
    return_movement_id = str((return_movement or {}).get("id") or "")
    if (
        not isinstance(return_movement, dict)
        or str(return_movement.get("kind") or "") != "return"
        or str(return_movement.get("related_movement_id") or "") != movement_id
    ):
        raise AttestationError(
            "return_inventory_movement_audit_exact_reread_failed",
            classification="verification",
            evidence=evidence,
        )
    card_after = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    if any(
        str(mapping.get("inventory_movement_id") or "") == movement_id
        for mapping in _walk_mappings(card_after)
    ):
        raise AttestationError(
            "return_inventory_material_link_not_cleared",
            classification="backend_effect",
            evidence=evidence,
        )

    cleanup_item = after
    cleanup_quantity = _inventory_decimal(cleanup_item.get("quantity"))
    if cleanup_quantity > 0:
        cleanup_card_revision = str(card_after.get("updated_at") or "")
        cleanup_payload = {
            "item_id": item_id,
            "card_id": card_id,
            "quantity": str(cleanup_quantity),
            "expected_updated_at": str(cleanup_item["updated_at"]),
            "expected_card_updated_at": cleanup_card_revision,
        }
        cleanup_result, cleanup_evidence = await _attested_call(
            session,
            "agent_inventory_workflow",
            {
                "operation": "write_off_inventory_item",
                "payload": cleanup_payload,
                "idempotency_key": f"{prefix}-{spec.operation}-cleanup-drain-a{attempt}"[:160],
            },
        )
        evidence.append(cleanup_evidence)
        if not _tool_ok(cleanup_result):
            raise AttestationError(
                "synthetic_inventory_quantity_cleanup_failed",
                classification="backend_effect",
                evidence=evidence,
            )
        cleanup_item, _ = await _inventory_item_context(
            session,
            spec=spec,
            state=state,
            item_id=item_id,
            purpose="cleanup",
            evidence=evidence,
        )
    if _inventory_decimal(cleanup_item.get("quantity")) != Decimal("0"):
        raise AttestationError(
            "synthetic_inventory_quantity_not_restored",
            classification="backend_effect",
            evidence=evidence,
        )
    await _cleanup_synthetic_board_card(
        session,
        spec=spec,
        state=state,
        card_id=card_id,
        evidence=evidence,
    )
    for item in _synthetic_entities(state)["inventory_items"]:
        if str(item.get("id") or "") == item_id:
            item["status"] = "compensated"
    state["refs"]["read_inventory_item_id"] = item_id
    state["refs"]["synthetic_inventory_return_movement_id"] = return_movement_id
    _assert_response_budget(
        evidence,
        code="inventory_write_response_payload_limit_exceeded",
    )
    return evidence


async def _inventory_write_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    if spec.operation == "save_inventory_item":
        return await _inventory_save_case(session, spec=spec, state=state)
    if spec.operation == "replenish_inventory_item":
        return await _inventory_replenish_case(session, spec=spec, state=state)
    if spec.operation == "write_off_inventory_item":
        return await _inventory_write_off_case(session, spec=spec, state=state)
    if spec.operation == "return_inventory_movement":
        return await _inventory_return_case(session, spec=spec, state=state)
    raise AttestationError(
        f"inventory_write_executor_missing_{spec.operation}",
        classification="routing",
    )


def _synthetic_pdf_bytes(prefix: str) -> bytes:
    marker = prefix.encode("ascii", errors="ignore")[:96]
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"% AutoStop Gateway attestation " + marker + b"\n%%EOF\n"
    )


def _shared_file_mapping(value: Any, file_id: str) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "") == file_id
            and "original_name" in mapping
            and "updated_at" in mapping
            and "size_bytes" in mapping
        ):
            return mapping
    return None


def _shared_file_for_name(value: Any, file_name: str) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("original_name") or "") == file_name
            and str(mapping.get("id") or "")
            and "updated_at" in mapping
        ):
            return mapping
    return None


def _contains_binary_field(value: Any) -> bool:
    binary_keys = {"base64", "content_base64", "content_bytes", "pdf_base64"}
    return any(binary_keys.intersection(mapping) for mapping in _walk_mappings(value))


async def _shared_file_context(
    session: Any,
    *,
    file_id: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    result, read_evidence = await _attested_call(
        session,
        "agent_entity_context",
        {"entity": "file", "entity_id": file_id, "detail": "full"},
    )
    evidence.append(read_evidence)
    file_item = _shared_file_mapping(_structured(result), file_id)
    if not isinstance(file_item, dict):
        raise AttestationError(
            "synthetic_shared_file_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    if not str(file_item.get("updated_at") or "") or file_item.get("exists_on_disk") is not True:
        raise AttestationError(
            "synthetic_shared_file_metadata_invalid",
            classification="verification",
            evidence=evidence,
        )
    return file_item


def _active_file_registry_item(state: dict[str, Any]) -> dict[str, Any] | None:
    for item in reversed(_synthetic_entities(state)["files"]):
        if item.get("status") == "active" and str(item.get("id") or ""):
            return item
    return None


def _register_shared_file(
    state: dict[str, Any],
    *,
    file_id: str,
    case_id: str,
) -> None:
    registry = _synthetic_entities(state)["files"]
    if any(str(item.get("id") or "") == file_id for item in registry):
        return
    registry.append({"id": file_id, "case_id": case_id, "status": "active"})


async def _create_shared_file_fixture(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
    purpose: str,
) -> tuple[str, dict[str, Any]]:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    content = _synthetic_pdf_bytes(prefix)
    file_name = f"{prefix}-{purpose}-a{attempt}.pdf"
    arguments = {
        "operation": "upload_shared_file",
        "payload": {
            "file_name": file_name,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "mime_type": "application/pdf",
            "x": 0,
            "y": 0,
        },
        "idempotency_key": f"{prefix}-fixture-{purpose}-a{attempt}"[:160],
    }
    result, apply_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(apply_evidence)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix=f"{purpose}_fixture_upload",
        evidence=evidence,
    )
    file_item = _shared_file_for_name(_structured(result), file_name)
    file_id = str((file_item or {}).get("id") or "")
    if not file_id:
        raise AttestationError(
            "synthetic_shared_file_fixture_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_shared_file(state, file_id=file_id, case_id=spec.case_id)
    state["refs"]["synthetic_file_id"] = file_id
    reread = await _shared_file_context(
        session,
        file_id=file_id,
        evidence=evidence,
    )
    if str(reread.get("original_name") or "") != file_name or int(
        reread.get("size_bytes") or 0
    ) != len(content):
        raise AttestationError(
            "synthetic_shared_file_fixture_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    return file_id, reread


async def _document_upload_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    if _active_file_registry_item(state) is not None:
        raise AttestationError(
            "synthetic_shared_file_already_active",
            classification="routing",
        )
    content = _synthetic_pdf_bytes(prefix)
    file_name = f"{prefix}-synthetic.pdf"
    payload = {
        "file_name": file_name,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "mime_type": "application/pdf",
        "x": 0,
        "y": 0,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "content_base64": "%%%invalid%%%"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-base64-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "file_name": f"{prefix}.exe"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-extension-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    file_item = _shared_file_for_name(applied, file_name)
    file_id = str((file_item or {}).get("id") or "")
    if not file_id:
        raise AttestationError(
            "synthetic_shared_file_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_shared_file(state, file_id=file_id, case_id=spec.case_id)
    state["refs"]["synthetic_file_id"] = file_id
    reread = await _shared_file_context(
        session,
        file_id=file_id,
        evidence=evidence,
    )
    if (
        str(reread.get("original_name") or "") != file_name
        or str(reread.get("mime_type") or "") != "application/pdf"
        or int(reread.get("size_bytes") or 0) != len(content)
    ):
        raise AttestationError(
            "upload_shared_file_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="document_write_response_payload_limit_exceeded",
    )
    return evidence


async def _document_download_file_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_file_registry_item(state)
    file_id = str((registered or {}).get("id") or "")
    if not file_id:
        raise AttestationError(
            "synthetic_shared_file_ref_missing",
            classification="routing",
        )
    base_payload = {
        "file_id": file_id,
        "include_base64": True,
        "max_base64_bytes": 4096,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=base_payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**base_payload, "file_id": f"{prefix}-missing-file"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
            "allow_large_output": False,
        },
        expected_code="not_found",
        evidence=evidence,
    )
    bounded_result, bounded_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        {
            "operation": spec.operation,
            "payload": {**base_payload, "max_base64_bytes": 1},
            "idempotency_key": f"{prefix}-{spec.operation}-bounded-a{attempt}"[:160],
            "allow_large_output": False,
        },
    )
    evidence.append(bounded_evidence)
    bounded = _structured(bounded_result)
    if _contains_binary_field(bounded) or not _contains_scalar(bounded, "shared_file_download"):
        raise AttestationError(
            "download_shared_file_compact_payload_invalid",
            classification="privacy_payload",
            evidence=evidence,
        )
    arguments = {
        "operation": spec.operation,
        "payload": base_payload,
        "idempotency_key": f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        "allow_large_output": True,
    }
    result, apply_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(apply_evidence)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix=spec.operation,
        evidence=evidence,
    )
    encoded = _first_value(_structured(result), ("base64",))
    try:
        decoded = base64.b64decode(str(encoded or ""), validate=True)
    except (ValueError, TypeError):
        decoded = b""
    if decoded != _synthetic_pdf_bytes(prefix):
        raise AttestationError(
            "download_shared_file_content_hash_mismatch",
            classification="verification",
            evidence=evidence,
        )
    await _shared_file_context(
        session,
        file_id=file_id,
        evidence=evidence,
    )
    _assert_response_budget(
        evidence,
        code="document_download_response_payload_limit_exceeded",
    )
    return evidence


async def _document_delete_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    registered = _active_file_registry_item(state)
    file_id = str((registered or {}).get("id") or "")
    before: dict[str, Any] | None = None
    if file_id:
        try:
            before = await _shared_file_context(
                session,
                file_id=file_id,
                evidence=evidence,
            )
        except AttestationError as exc:
            missing_proven = any(item.get("error_code") == "not_found" for item in exc.evidence)
            if not missing_proven:
                raise
            evidence.extend(exc.evidence)
            registered["status"] = "deleted"
            state["refs"]["synthetic_file_id"] = ""
            file_id = ""
    if not file_id:
        file_id, before = await _create_shared_file_fixture(
            session,
            spec=spec,
            state=state,
            evidence=evidence,
            purpose="delete-shared-file",
        )
        registered = _active_file_registry_item(state)
    if before is None or registered is None:
        raise AttestationError(
            "synthetic_shared_file_ref_missing",
            classification="routing",
            evidence=evidence,
        )
    expected_updated_at = str(before["updated_at"])
    payload = {
        "file_id": file_id,
        "expected_updated_at": expected_updated_at,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "file_id": f"{prefix}-missing-file",
                "expected_updated_at": expected_updated_at,
            },
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="not_found",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="shared_file_update_conflict",
        evidence=evidence,
    )
    after_stale = await _shared_file_context(
        session,
        file_id=file_id,
        evidence=evidence,
    )
    if str(after_stale.get("updated_at") or "") != expected_updated_at:
        raise AttestationError(
            "delete_shared_file_stale_conflict_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    deleted_mapping = next(
        (
            mapping
            for mapping in _walk_mappings(applied)
            if mapping.get("deleted") is True and str(mapping.get("file_id") or "") == file_id
        ),
        None,
    )
    if deleted_mapping is None:
        raise AttestationError(
            "delete_shared_file_apply_result_invalid",
            classification="backend_effect",
            evidence=evidence,
        )
    missing_result, missing_evidence = await _attested_call(
        session,
        "agent_entity_context",
        {"entity": "file", "entity_id": file_id, "detail": "full"},
        expect_ok=False,
    )
    evidence.append(missing_evidence)
    if missing_evidence.get("error_code") != "not_found" or not _contains_scalar(
        _structured(missing_result), "not_found"
    ):
        raise AttestationError(
            "delete_shared_file_absence_not_proven",
            classification="verification",
            evidence=evidence,
        )
    registered["status"] = "deleted"
    state["refs"]["synthetic_file_id"] = ""
    _assert_response_budget(
        evidence,
        code="document_delete_response_payload_limit_exceeded",
    )
    return evidence


async def _document_repair_order_pdf_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    card_id = ""
    failure: AttestationError | None = None
    try:
        card_id, _card = await _create_synthetic_board_card(
            session,
            spec=spec,
            state=state,
            evidence=evidence,
            ready_unpaid=True,
        )
        payload = {"card_id": card_id, "selected_document_ids": ["repair_order"]}
        await _inventory_missing_key_gate(
            session,
            spec=spec,
            payload=payload,
            evidence=evidence,
        )
        await _inventory_expected_failure(
            session,
            spec=spec,
            arguments={
                "operation": spec.operation,
                "payload": {**payload, "card_id": f"{prefix}-missing-card"},
                "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
                "allow_large_output": False,
            },
            expected_code="not_found",
            evidence=evidence,
        )
        compact, compact_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            {
                "operation": spec.operation,
                "payload": payload,
                "idempotency_key": f"{prefix}-{spec.operation}-compact-a{attempt}"[:160],
                "allow_large_output": False,
            },
        )
        evidence.append(compact_evidence)
        if _contains_binary_field(_structured(compact)):
            raise AttestationError(
                "repair_order_pdf_compact_binary_leak",
                classification="privacy_payload",
                evidence=evidence,
            )
        arguments = {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
            "allow_large_output": True,
        }
        result, result_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            arguments,
        )
        evidence.append(result_evidence)
        replay, replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            arguments,
        )
        evidence.append(replay_evidence)
        _assert_workflow_replay(
            replay,
            code_prefix=spec.operation,
            evidence=evidence,
        )
        encoded = _first_value(_structured(result), ("pdf_base64", "content_base64"))
        try:
            decoded = base64.b64decode(str(encoded or ""), validate=True)
        except (ValueError, TypeError):
            decoded = b""
        if not decoded.startswith(b"%PDF"):
            raise AttestationError(
                "repair_order_pdf_artifact_invalid",
                classification="verification",
                evidence=evidence,
            )
    except AttestationError as exc:
        failure = exc
    if card_id:
        try:
            await _cleanup_synthetic_board_card(
                session,
                spec=spec,
                state=state,
                card_id=card_id,
                evidence=evidence,
            )
        except AttestationError as cleanup_error:
            raise AttestationError(
                "repair_order_pdf_fixture_cleanup_failed",
                classification="backend_effect",
                evidence=[*evidence, *cleanup_error.evidence],
            ) from cleanup_error
    if failure is not None:
        raise AttestationError(
            failure.code,
            classification=failure.classification,
            evidence=evidence or failure.evidence,
        ) from failure
    compact_sizes = [
        int(item.get("response_bytes") or 0)
        for item in evidence
        if item.get("tool") == spec.workflow_tool
    ]
    if compact_sizes and min(compact_sizes) > 262_144:
        raise AttestationError(
            "repair_order_pdf_compact_payload_limit_exceeded",
            classification="privacy_payload",
            evidence=evidence,
        )
    if any(int(item.get("response_bytes") or 0) > 4_194_304 for item in evidence):
        raise AttestationError(
            "repair_order_pdf_explicit_payload_limit_exceeded",
            classification="privacy_payload",
            evidence=evidence,
        )
    return evidence


def _dashboard_message_mapping(value: Any) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("schema_version") or "") == "display_dashboard_message.v1"
            and str(mapping.get("revision") or "")
            and isinstance(mapping.get("image_file_ids"), list)
            and "body_html" in mapping
        ):
            return mapping
    return None


async def _read_display_dashboard_message(
    session: Any,
    *,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    structured = await _raw_invoke(
        session,
        name="api:/api/get_display_dashboard",
        arguments={},
        idempotency_key="",
        evidence=evidence,
        allow_large_output=True,
    )
    message = _dashboard_message_mapping(structured)
    if not isinstance(message, dict):
        raise AttestationError(
            "display_dashboard_message_exact_read_missing",
            classification="verification",
            evidence=evidence,
        )
    return {
        "body_html": str(message.get("body_html") or ""),
        "image_file_ids": [str(item) for item in message.get("image_file_ids") or []],
        "revision": str(message["revision"]),
    }


async def _document_dashboard_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    original = await _read_display_dashboard_message(
        session,
        evidence=evidence,
    )
    synthetic_message = {
        "body_html": f"<p>{prefix} synthetic dashboard verification</p>",
        "image_file_ids": [],
    }
    payload = {
        "display_dashboard_message": synthetic_message,
        "expected_revision": original["revision"],
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_revision": "0" * 64,
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
            "mode": "apply",
        },
        expected_code="revision_conflict",
        evidence=evidence,
    )
    dry_arguments = {
        "operation": spec.operation,
        "payload": payload,
        "idempotency_key": f"{prefix}-{spec.operation}-dry-a{attempt}"[:160],
        "mode": "dry_run",
    }
    _dry_run, dry_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        dry_arguments,
    )
    evidence.append(dry_evidence)
    after_dry = await _read_display_dashboard_message(
        session,
        evidence=evidence,
    )
    if after_dry["revision"] != original["revision"]:
        raise AttestationError(
            "display_dashboard_dry_run_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    applied_revision = ""
    failure: AttestationError | None = None
    try:
        apply_arguments = {
            "operation": spec.operation,
            "payload": payload,
            "idempotency_key": f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
            "mode": "apply",
        }
        _applied, apply_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            apply_arguments,
        )
        evidence.append(apply_evidence)
        replay, replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            apply_arguments,
        )
        evidence.append(replay_evidence)
        _assert_workflow_replay(
            replay,
            code_prefix=spec.operation,
            evidence=evidence,
        )
        applied = await _read_display_dashboard_message(
            session,
            evidence=evidence,
        )
        applied_revision = applied["revision"]
        if (
            applied["body_html"] != synthetic_message["body_html"]
            or applied["image_file_ids"] != []
            or applied_revision == original["revision"]
        ):
            raise AttestationError(
                "display_dashboard_apply_exact_reread_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    except AttestationError as exc:
        failure = exc

    if failure is not None and not applied_revision:
        probe = await _read_display_dashboard_message(
            session,
            evidence=evidence,
        )
        if probe["body_html"] == synthetic_message["body_html"] and probe["image_file_ids"] == []:
            applied_revision = probe["revision"]
        elif probe["revision"] != original["revision"]:
            raise AttestationError(
                "display_dashboard_state_changed_during_failed_apply",
                classification="backend_effect",
                evidence=evidence,
            ) from failure

    if applied_revision:
        restore_payload = {
            "display_dashboard_message": {
                "body_html": original["body_html"],
                "image_file_ids": original["image_file_ids"],
            },
            "expected_revision": applied_revision,
        }
        try:
            _restored, restore_evidence = await _attested_call(
                session,
                spec.workflow_tool,
                {
                    "operation": spec.operation,
                    "payload": restore_payload,
                    "idempotency_key": (f"{prefix}-{spec.operation}-restore-a{attempt}")[:160],
                    "mode": "apply",
                },
            )
            evidence.append(restore_evidence)
            restored = await _read_display_dashboard_message(
                session,
                evidence=evidence,
            )
            if (
                restored["revision"] != original["revision"]
                or restored["body_html"] != original["body_html"]
                or restored["image_file_ids"] != original["image_file_ids"]
            ):
                raise AttestationError(
                    "display_dashboard_restore_exact_reread_failed",
                    classification="backend_effect",
                    evidence=evidence,
                )
        except AttestationError as restore_error:
            raise AttestationError(
                "display_dashboard_restore_failed",
                classification="backend_effect",
                evidence=[*evidence, *restore_error.evidence],
            ) from restore_error
    if failure is not None:
        raise AttestationError(
            failure.code,
            classification=failure.classification,
            evidence=evidence or failure.evidence,
        ) from failure
    _assert_response_budget(
        evidence,
        code="document_dashboard_response_payload_limit_exceeded",
    )
    return evidence


def _cashbox_mappings(value: Any) -> list[dict[str, Any]]:
    return [
        mapping
        for mapping in _walk_mappings(value)
        if str(mapping.get("id") or "")
        and str(mapping.get("name") or "")
        and str(mapping.get("updated_at") or "")
        and "statistics" in mapping
    ]


async def _finance_cashbox_snapshot(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    purpose: str,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    result, read_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        {
            "operation": "list_cashboxes",
            "payload": {"limit": 20},
            "idempotency_key": (f"{prefix}-{spec.operation}-{purpose}-cashboxes-a{attempt}")[:160],
        },
    )
    evidence.append(read_evidence)
    cashboxes = _cashbox_mappings(_structured(result))
    cashboxes.sort(key=lambda item: (int(item.get("order") or 0), str(item["id"])))
    if not cashboxes:
        raise AttestationError(
            "finance_cashbox_snapshot_missing",
            classification="verification",
            evidence=evidence,
        )
    if len({str(item["id"]) for item in cashboxes}) != len(cashboxes):
        raise AttestationError(
            "finance_cashbox_snapshot_duplicate_ids",
            classification="verification",
            evidence=evidence,
        )
    return cashboxes


def _active_cashbox_registry_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in _synthetic_entities(state)["cashboxes"]
        if item.get("status") == "active" and str(item.get("id") or "")
    ]


def _register_cashbox(
    state: dict[str, Any],
    *,
    cashbox_id: str,
    case_id: str,
) -> None:
    registry = _synthetic_entities(state)["cashboxes"]
    if any(str(item.get("id") or "") == cashbox_id for item in registry):
        return
    registry.append({"id": cashbox_id, "case_id": case_id, "status": "active"})


async def _finance_create_cashbox_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    if _active_cashbox_registry_items(state):
        raise AttestationError(
            "synthetic_cashbox_already_active",
            classification="routing",
        )
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload={"name": "", "attestation_run_id": prefix},
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {"name": "", "attestation_run_id": prefix},
            "idempotency_key": f"{prefix}-{spec.operation}-missing-snapshot-a{attempt}"[:160],
        },
        expected_code="cashbox_snapshot_required_reread_exact_list_first",
        evidence=evidence,
    )
    before = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="before-create",
        evidence=evidence,
    )
    expected_ids = [str(item["id"]) for item in before]
    name = f"{prefix}-cashbox-1"[:80]
    payload = {
        "name": name,
        "expected_cashbox_ids": expected_ids,
        "attestation_run_id": prefix,
    }
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "expected_cashbox_ids": [*expected_ids, "stale-id"]},
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_snapshot_conflict",
        evidence=evidence,
    )
    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    created = next(
        (
            mapping
            for mapping in _cashbox_mappings(applied)
            if str(mapping.get("name") or "") == name
        ),
        None,
    )
    cashbox_id = str((created or {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_cashbox_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cashbox(state, cashbox_id=cashbox_id, case_id=spec.case_id)
    state["refs"]["synthetic_cashbox_id"] = cashbox_id
    context, context_evidence = await _attested_call(
        session,
        "agent_entity_context",
        {"entity": "cashbox", "entity_id": cashbox_id, "detail": "full"},
    )
    evidence.append(context_evidence)
    reread = _mapping_for_entity(_structured(context), cashbox_id)
    if (
        not isinstance(reread, dict)
        or str(reread.get("name") or "") != name
        or str(reread.get("updated_at") or "") != str(created.get("updated_at") or "")
    ):
        raise AttestationError(
            "create_cashbox_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    after = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-create",
        evidence=evidence,
    )
    after_ids = [str(item["id"]) for item in after]
    if after_ids != [*expected_ids, cashbox_id]:
        raise AttestationError(
            "create_cashbox_snapshot_effect_invalid",
            classification="backend_effect",
            evidence=evidence,
        )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "name": name,
                "expected_cashbox_ids": after_ids,
                "attestation_run_id": prefix,
            },
            "idempotency_key": f"{prefix}-{spec.operation}-duplicate-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    _assert_response_budget(
        evidence,
        code="finance_create_cashbox_response_payload_limit_exceeded",
    )
    return evidence


def _cash_transaction_mapping(
    value: Any,
    transaction_id: str,
) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "") == transaction_id
            and str(mapping.get("cashbox_id") or "")
            and str(mapping.get("direction") or "")
            and "amount_minor" in mapping
        ):
            return mapping
    return None


def _register_cash_transaction(
    state: dict[str, Any],
    *,
    transaction_id: str,
    cashbox_id: str,
    case_id: str,
    status: str = "active",
) -> None:
    registry = _synthetic_entities(state)["cash_transactions"]
    if any(str(item.get("id") or "") == transaction_id for item in registry):
        return
    registry.append(
        {
            "id": transaction_id,
            "cashbox_id": cashbox_id,
            "case_id": case_id,
            "status": status,
        }
    )


async def _finance_cashbox_context(
    session: Any,
    *,
    cashbox_id: str,
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result, read_evidence = await _attested_call(
        session,
        "agent_entity_context",
        {"entity": "cashbox", "entity_id": cashbox_id, "detail": "full"},
    )
    evidence.append(read_evidence)
    structured = _structured(result)
    cashbox = _mapping_for_entity(structured, cashbox_id)
    if (
        not isinstance(cashbox, dict)
        or not str(cashbox.get("updated_at") or "")
        or not isinstance(cashbox.get("statistics"), dict)
    ):
        raise AttestationError(
            "synthetic_cashbox_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    return cashbox, structured


async def _finance_create_cash_transaction_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str((active_cashboxes[0] if active_cashboxes else {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_cashbox_ref_missing",
            classification="routing",
        )
    before, before_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    accidental = [
        mapping
        for mapping in _walk_mappings(before_structured)
        if str(mapping.get("cashbox_id") or "") == cashbox_id
        and str(mapping.get("note") or "") == f"{prefix} revision gate"
        and str(mapping.get("id") or "")
    ]
    if len(accidental) > 1:
        raise AttestationError(
            "multiple_accidental_revision_gate_transactions",
            classification="backend_effect",
            evidence=evidence,
        )
    if accidental:
        accidental_id = str(accidental[0]["id"])
        cleanup_arguments = {
            "operation": "cancel_last_cash_transaction",
            "payload": {
                "cashbox_id": cashbox_id,
                "transaction_id": accidental_id,
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-cleanup-accidental-a{attempt}")[:160],
        }
        _cleaned, cleanup_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            cleanup_arguments,
        )
        evidence.append(cleanup_evidence)
        cleanup_replay, cleanup_replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            cleanup_arguments,
        )
        evidence.append(cleanup_replay_evidence)
        _assert_workflow_replay(
            cleanup_replay,
            code_prefix="accidental_cash_transaction_cleanup",
            evidence=evidence,
        )
        _register_cash_transaction(
            state,
            transaction_id=accidental_id,
            cashbox_id=cashbox_id,
            case_id=spec.case_id,
            status="deleted_cleanup",
        )
        before, before_structured = await _finance_cashbox_context(
            session,
            cashbox_id=cashbox_id,
            evidence=evidence,
        )
        if _cash_transaction_mapping(before_structured, accidental_id) is not None:
            raise AttestationError(
                "accidental_cash_transaction_cleanup_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    before_revision = str(before["updated_at"])
    before_balance = int((before["statistics"] or {}).get("balance_minor") or 0)
    missing_revision_payload = {
        "cashbox_id": cashbox_id,
        "direction": "income",
        "amount_minor": 0,
        "note": f"{prefix} revision gate",
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=missing_revision_payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": missing_revision_payload,
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revision-a{attempt}"[:160],
        },
        expected_code="cashbox_expected_revision_required_reread_exact_cashbox_first",
        evidence=evidence,
    )
    payload = {
        "cashbox_id": cashbox_id,
        "direction": "income",
        "amount_minor": 100,
        "note": f"{prefix} synthetic cash transaction",
        "expected_updated_at": before_revision,
    }
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    after_stale, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(after_stale.get("updated_at") or "") != before_revision
        or int((after_stale.get("statistics") or {}).get("balance_minor") or 0) != before_balance
    ):
        raise AttestationError(
            "create_cash_transaction_stale_conflict_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )
    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    transaction_id = str(
        _first_value(
            applied,
            ("transaction_id",),
        )
        or ""
    )
    if not transaction_id:
        transaction_id = str(
            next(
                (
                    mapping.get("id")
                    for mapping in _walk_mappings(applied)
                    if mapping.get("cashbox_id") == cashbox_id
                    and mapping.get("amount_minor") == 100
                    and mapping.get("direction") == "income"
                ),
                "",
            )
        )
    if not transaction_id:
        raise AttestationError(
            "synthetic_cash_transaction_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cash_transaction(
        state,
        transaction_id=transaction_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
    )
    state["refs"]["synthetic_cash_transaction_id"] = transaction_id
    after, after_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    transaction = _cash_transaction_mapping(after_structured, transaction_id)
    if (
        not isinstance(transaction, dict)
        or int(transaction.get("amount_minor") or 0) != 100
        or str(transaction.get("direction") or "") != "income"
        or int((after.get("statistics") or {}).get("balance_minor") or 0) != before_balance + 100
        or str(after.get("updated_at") or "") == before_revision
    ):
        raise AttestationError(
            "create_cash_transaction_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_create_cash_transaction_response_payload_limit_exceeded",
    )
    return evidence


async def _ensure_second_synthetic_cashbox(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active = _active_cashbox_registry_items(state)
    if len(active) >= 2:
        return active[:2]
    if len(active) != 1:
        raise AttestationError(
            "synthetic_primary_cashbox_ref_missing",
            classification="routing",
            evidence=evidence,
        )
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    before = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="before-second-fixture",
        evidence=evidence,
    )
    expected_ids = [str(item["id"]) for item in before]
    name = f"{prefix}-cashbox-2"[:80]
    arguments = {
        "operation": "create_cashbox",
        "payload": {
            "name": name,
            "expected_cashbox_ids": expected_ids,
            "attestation_run_id": prefix,
        },
        "idempotency_key": f"{prefix}-fixture-create-cashbox-2-a{attempt}"[:160],
    }
    result, apply_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(apply_evidence)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix="second_cashbox_fixture",
        evidence=evidence,
    )
    created = next(
        (
            mapping
            for mapping in _cashbox_mappings(_structured(result))
            if str(mapping.get("name") or "") == name
        ),
        None,
    )
    cashbox_id = str((created or {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_second_cashbox_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cashbox(state, cashbox_id=cashbox_id, case_id=spec.case_id)
    state["refs"]["synthetic_second_cashbox_id"] = cashbox_id
    reread, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if str(reread.get("name") or "") != name:
        raise AttestationError(
            "synthetic_second_cashbox_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    return _active_cashbox_registry_items(state)[:2]


async def _finance_create_cashbox_transfer_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    cashboxes = await _ensure_second_synthetic_cashbox(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
    )
    source_id = str(cashboxes[0]["id"])
    target_id = str(cashboxes[1]["id"])
    source_before, _ = await _finance_cashbox_context(
        session,
        cashbox_id=source_id,
        evidence=evidence,
    )
    target_before, _ = await _finance_cashbox_context(
        session,
        cashbox_id=target_id,
        evidence=evidence,
    )
    source_revision = str(source_before["updated_at"])
    target_revision = str(target_before["updated_at"])
    source_balance = int((source_before["statistics"] or {}).get("balance_minor") or 0)
    target_balance = int((target_before["statistics"] or {}).get("balance_minor") or 0)
    missing_revision_payload = {
        "from_cashbox_id": source_id,
        "to_cashbox_id": target_id,
        "amount_minor": 0,
        "note": f"{prefix} transfer revision gate",
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=missing_revision_payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": missing_revision_payload,
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revisions-a{attempt}"[:160],
        },
        expected_code="cashbox_transfer_expected_revisions_required_reread_exact_cashboxes_first",
        evidence=evidence,
    )
    payload = {
        "from_cashbox_id": source_id,
        "to_cashbox_id": target_id,
        "amount_minor": 100,
        "note": f"{prefix} synthetic transfer",
        "expected_from_updated_at": source_revision,
        "expected_to_updated_at": target_revision,
    }
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_from_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    transactions = [
        mapping
        for mapping in _walk_mappings(applied)
        if str(mapping.get("id") or "")
        and str(mapping.get("cashbox_id") or "") in {source_id, target_id}
        and int(mapping.get("amount_minor") or 0) == 100
        and str(mapping.get("transfer_group_id") or "")
    ]
    by_cashbox = {str(item["cashbox_id"]): item for item in transactions}
    if set(by_cashbox) != {source_id, target_id}:
        raise AttestationError(
            "synthetic_cashbox_transfer_transaction_ids_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    source_transaction_id = str(by_cashbox[source_id]["id"])
    target_transaction_id = str(by_cashbox[target_id]["id"])
    transfer_group_id = str(by_cashbox[source_id]["transfer_group_id"])
    if (
        transfer_group_id != str(by_cashbox[target_id].get("transfer_group_id") or "")
        or str(by_cashbox[source_id].get("direction") or "") != "expense"
        or str(by_cashbox[target_id].get("direction") or "") != "income"
    ):
        raise AttestationError(
            "synthetic_cashbox_transfer_pair_invalid",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cash_transaction(
        state,
        transaction_id=source_transaction_id,
        cashbox_id=source_id,
        case_id=spec.case_id,
    )
    _register_cash_transaction(
        state,
        transaction_id=target_transaction_id,
        cashbox_id=target_id,
        case_id=spec.case_id,
    )
    state["refs"].update(
        {
            "synthetic_transfer_source_transaction_id": source_transaction_id,
            "synthetic_transfer_target_transaction_id": target_transaction_id,
            "synthetic_transfer_group_id": transfer_group_id,
        }
    )
    source_after, source_structured = await _finance_cashbox_context(
        session,
        cashbox_id=source_id,
        evidence=evidence,
    )
    target_after, target_structured = await _finance_cashbox_context(
        session,
        cashbox_id=target_id,
        evidence=evidence,
    )
    if (
        _cash_transaction_mapping(source_structured, source_transaction_id) is None
        or _cash_transaction_mapping(target_structured, target_transaction_id) is None
        or int((source_after["statistics"] or {}).get("balance_minor") or 0) != source_balance - 100
        or int((target_after["statistics"] or {}).get("balance_minor") or 0) != target_balance + 100
        or str(source_after.get("updated_at") or "") == source_revision
        or str(target_after.get("updated_at") or "") == target_revision
    ):
        raise AttestationError(
            "create_cashbox_transfer_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_cashbox_transfer_response_payload_limit_exceeded",
    )
    return evidence


def _repair_order_payment_mapping(
    value: Any,
    payment_id: str,
) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "") == payment_id
            and str(mapping.get("cashbox_id") or "")
            and "amount" in mapping
        ):
            return mapping
    return None


async def _ensure_synthetic_payment_card(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    card_id = str(state["refs"].get("synthetic_payment_card_id") or "")
    if card_id:
        card = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            detail="full",
        )
        return card_id, card
    card_id, card = await _create_synthetic_board_card(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
        ready_unpaid=True,
    )
    state["refs"]["synthetic_payment_card_id"] = card_id
    return card_id, card


async def _finance_record_repair_order_payment_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str((active_cashboxes[0] if active_cashboxes else {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_cashbox_ref_missing",
            classification="routing",
        )
    card_id, card_before = await _ensure_synthetic_payment_card(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
    )
    cashbox_before, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    card_revision = str(card_before.get("updated_at") or "")
    cashbox_revision = str(cashbox_before.get("updated_at") or "")
    cashbox_balance = int((cashbox_before.get("statistics") or {}).get("balance_minor") or 0)
    if not card_revision or not cashbox_revision:
        raise AttestationError(
            "repair_order_payment_fixture_revision_missing",
            classification="verification",
            evidence=evidence,
        )
    base_payload = {
        "card_id": card_id,
        "cashbox_id": cashbox_id,
        "amount_minor": 100,
        "payment_method": "cash",
        "expected_updated_at": card_revision,
        "expected_cashbox_updated_at": cashbox_revision,
        "attestation_run_id": prefix,
        "note": f"{prefix} synthetic repair order payment",
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload={**base_payload, "amount_minor": 0},
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                key: value
                for key, value in {**base_payload, "amount_minor": 0}.items()
                if key != "expected_cashbox_updated_at"
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-missing-cashbox-revision-a{attempt}")[
                :160
            ],
        },
        expected_code=("payment_expected_revisions_required_reread_exact_targets_first"),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **base_payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-stale-card-a{attempt}")[:160],
        },
        expected_code="payment_revision_conflict",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **base_payload,
                "expected_cashbox_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-stale-cashbox-a{attempt}")[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=base_payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    payment_id = str(_first_value(applied, ("payment_id",)) or "")
    transaction_id = str(_first_value(applied, ("cash_transaction_id",)) or "")
    if not payment_id or not transaction_id:
        raise AttestationError(
            "repair_order_payment_refs_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cash_transaction(
        state,
        transaction_id=transaction_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
    )
    state["refs"].update(
        {
            "synthetic_payment_id": payment_id,
            "synthetic_payment_transaction_id": transaction_id,
        }
    )
    card_after = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    cashbox_after, cashbox_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    payment = _repair_order_payment_mapping(card_after, payment_id)
    transaction = _cash_transaction_mapping(cashbox_structured, transaction_id)
    if (
        not isinstance(payment, dict)
        or str(payment.get("cash_transaction_id") or "") != transaction_id
        or str(payment.get("cashbox_id") or "") != cashbox_id
        or Decimal(str(payment.get("amount") or "0")) != Decimal("1")
        or not isinstance(transaction, dict)
        or int(transaction.get("amount_minor") or 0) != 100
        or str(transaction.get("direction") or "") != "income"
        or int((cashbox_after.get("statistics") or {}).get("balance_minor") or 0)
        != cashbox_balance + 100
        or str(card_after.get("updated_at") or "") == card_revision
        or str(cashbox_after.get("updated_at") or "") == cashbox_revision
    ):
        raise AttestationError(
            "record_repair_order_payment_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_repair_order_payment_response_payload_limit_exceeded",
    )
    return evidence


async def _finance_update_repair_order_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    card_id = str(state["refs"].get("synthetic_payment_card_id") or "")
    payment_id = str(state["refs"].get("synthetic_payment_id") or "")
    if not card_id or not payment_id:
        raise AttestationError(
            "synthetic_payment_card_or_payment_ref_missing",
            classification="routing",
        )
    before = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    order_before = (
        before.get("repair_order") if isinstance(before.get("repair_order"), dict) else {}
    )
    original_comment = str(order_before.get("comment") or "")
    before_revision = str(before.get("updated_at") or "")
    if not before_revision or _repair_order_payment_mapping(before, payment_id) is None:
        raise AttestationError(
            "synthetic_repair_order_fixture_invalid",
            classification="verification",
            evidence=evidence,
        )
    synthetic_comment = f"{prefix} temporary repair order comment"
    payload = {
        "card_id": card_id,
        "repair_order": {"comment": synthetic_comment},
        "expected_updated_at": before_revision,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "card_id": card_id,
                "repair_order": {"comment": synthetic_comment},
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-missing-revision-a{attempt}")[:160],
        },
        expected_code="expected_updated_at_required_reread_exact_card_first",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="card_update_conflict",
        evidence=evidence,
    )

    failure: AttestationError | None = None
    changed = False
    try:
        await _inventory_apply_and_replay(
            session,
            spec=spec,
            payload=payload,
            idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
            evidence=evidence,
        )
        applied = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            detail="full",
        )
        applied_order = (
            applied.get("repair_order") if isinstance(applied.get("repair_order"), dict) else {}
        )
        changed = str(applied_order.get("comment") or "") == synthetic_comment
        if (
            not changed
            or str(applied.get("updated_at") or "") == before_revision
            or _repair_order_payment_mapping(applied, payment_id) is None
        ):
            raise AttestationError(
                "update_repair_order_exact_reread_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    except AttestationError as exc:
        failure = exc

    probe = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    probe_order = probe.get("repair_order") if isinstance(probe.get("repair_order"), dict) else {}
    current_comment = str(probe_order.get("comment") or "")
    changed = changed or current_comment == synthetic_comment
    if not changed and current_comment != original_comment:
        raise AttestationError(
            "update_repair_order_unexpected_state_after_apply",
            classification="backend_effect",
            evidence=evidence,
        ) from failure
    if changed:
        restore_revision = str(probe.get("updated_at") or "")
        if not restore_revision:
            raise AttestationError(
                "update_repair_order_restore_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        try:
            await _inventory_apply_and_replay(
                session,
                spec=spec,
                payload={
                    "card_id": card_id,
                    "repair_order": {"comment": original_comment},
                    "expected_updated_at": restore_revision,
                },
                idempotency_key=f"{prefix}-{spec.operation}-restore-a{attempt}"[:160],
                evidence=evidence,
            )
            restored = await _card_context(
                session,
                card_id=card_id,
                evidence=evidence,
                detail="full",
            )
            restored_order = (
                restored.get("repair_order")
                if isinstance(restored.get("repair_order"), dict)
                else {}
            )
            if (
                str(restored_order.get("comment") or "") != original_comment
                or _repair_order_payment_mapping(restored, payment_id) is None
            ):
                raise AttestationError(
                    "update_repair_order_restore_exact_reread_failed",
                    classification="backend_effect",
                    evidence=evidence,
                )
        except AttestationError as restore_error:
            raise AttestationError(
                "update_repair_order_restore_failed",
                classification="backend_effect",
                evidence=[*evidence, *restore_error.evidence],
            ) from restore_error
    if failure is not None:
        raise AttestationError(
            failure.code,
            classification=failure.classification,
            evidence=evidence,
        ) from failure
    _assert_response_budget(
        evidence,
        code="finance_update_repair_order_response_payload_limit_exceeded",
    )
    return evidence


async def _finance_set_repair_order_status_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    card_id = str(state["refs"].get("synthetic_payment_card_id") or "")
    payment_id = str(state["refs"].get("synthetic_payment_id") or "")
    if not card_id or not payment_id:
        raise AttestationError(
            "synthetic_payment_card_or_payment_ref_missing",
            classification="routing",
        )
    before = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    order_before = (
        before.get("repair_order") if isinstance(before.get("repair_order"), dict) else {}
    )
    original_status = str(order_before.get("status") or "open")
    target_status = "ready" if original_status != "ready" else "open"
    before_revision = str(before.get("updated_at") or "")
    if not before_revision or _repair_order_payment_mapping(before, payment_id) is None:
        raise AttestationError(
            "synthetic_repair_order_fixture_invalid",
            classification="verification",
            evidence=evidence,
        )
    payload = {
        "card_id": card_id,
        "status": target_status,
        "expected_updated_at": before_revision,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {"card_id": card_id, "status": target_status},
            "idempotency_key": (f"{prefix}-{spec.operation}-missing-revision-a{attempt}")[:160],
        },
        expected_code="expected_updated_at_required_reread_exact_card_first",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="card_update_conflict",
        evidence=evidence,
    )

    failure: AttestationError | None = None
    changed = False
    try:
        await _inventory_apply_and_replay(
            session,
            spec=spec,
            payload=payload,
            idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
            evidence=evidence,
        )
        applied = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            detail="full",
        )
        applied_order = (
            applied.get("repair_order") if isinstance(applied.get("repair_order"), dict) else {}
        )
        changed = str(applied_order.get("status") or "") == target_status
        if (
            not changed
            or str(applied.get("updated_at") or "") == before_revision
            or _repair_order_payment_mapping(applied, payment_id) is None
        ):
            raise AttestationError(
                "set_repair_order_status_exact_reread_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    except AttestationError as exc:
        failure = exc

    probe = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        detail="full",
    )
    probe_order = probe.get("repair_order") if isinstance(probe.get("repair_order"), dict) else {}
    current_status = str(probe_order.get("status") or "open")
    changed = changed or current_status == target_status
    if not changed and current_status != original_status:
        raise AttestationError(
            "set_repair_order_status_unexpected_state_after_apply",
            classification="backend_effect",
            evidence=evidence,
        ) from failure
    if changed:
        restore_revision = str(probe.get("updated_at") or "")
        if not restore_revision:
            raise AttestationError(
                "set_repair_order_status_restore_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        try:
            await _inventory_apply_and_replay(
                session,
                spec=spec,
                payload={
                    "card_id": card_id,
                    "status": original_status,
                    "expected_updated_at": restore_revision,
                },
                idempotency_key=f"{prefix}-{spec.operation}-restore-a{attempt}"[:160],
                evidence=evidence,
            )
            restored = await _card_context(
                session,
                card_id=card_id,
                evidence=evidence,
                detail="full",
            )
            restored_order = (
                restored.get("repair_order")
                if isinstance(restored.get("repair_order"), dict)
                else {}
            )
            if (
                str(restored_order.get("status") or "open") != original_status
                or _repair_order_payment_mapping(restored, payment_id) is None
            ):
                raise AttestationError(
                    "set_repair_order_status_restore_exact_reread_failed",
                    classification="backend_effect",
                    evidence=evidence,
                )
        except AttestationError as restore_error:
            raise AttestationError(
                "set_repair_order_status_restore_failed",
                classification="backend_effect",
                evidence=[*evidence, *restore_error.evidence],
            ) from restore_error
    if failure is not None:
        raise AttestationError(
            failure.code,
            classification=failure.classification,
            evidence=evidence,
        ) from failure
    _assert_response_budget(
        evidence,
        code="finance_set_repair_order_status_response_payload_limit_exceeded",
    )
    return evidence


async def _finance_reorder_cashboxes_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active = _active_cashbox_registry_items(state)
    if len(active) != 2:
        raise AttestationError(
            "two_synthetic_cashboxes_required",
            classification="routing",
        )
    synthetic_ids = {str(item["id"]) for item in active}
    before = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="before-reorder",
        evidence=evidence,
    )
    before_ids = [str(item["id"]) for item in before]
    if set(before_ids[-2:]) != synthetic_ids:
        raise AttestationError(
            "synthetic_cashboxes_not_isolated_at_order_tail",
            classification="policy",
            evidence=evidence,
        )
    real_ids = [cashbox_id for cashbox_id in before_ids if cashbox_id not in synthetic_ids]
    first_synthetic_id, second_synthetic_id = before_ids[-2:]
    payload = {
        "cashbox_id": second_synthetic_id,
        "before_cashbox_id": first_synthetic_id,
        "expected_cashbox_ids": before_ids,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "cashbox_id": f"{prefix}-missing-cashbox",
                "before_cashbox_id": first_synthetic_id,
            },
            "idempotency_key": (f"{prefix}-{spec.operation}-missing-snapshot-a{attempt}")[:160],
        },
        expected_code="cashbox_order_snapshot_required_reread_exact_list_first",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_ids": [*before_ids, "stale-cashbox-id"],
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_order_conflict",
        evidence=evidence,
    )
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    after = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-reorder",
        evidence=evidence,
    )
    after_ids = [str(item["id"]) for item in after]
    expected_after_ids = [*real_ids, second_synthetic_id, first_synthetic_id]
    if (
        after_ids != expected_after_ids
        or [item for item in after_ids if item not in synthetic_ids] != real_ids
    ):
        raise AttestationError(
            "reorder_cashboxes_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    try:
        await _inventory_apply_and_replay(
            session,
            spec=spec,
            payload={
                "cashbox_id": first_synthetic_id,
                "before_cashbox_id": second_synthetic_id,
                "expected_cashbox_ids": after_ids,
            },
            idempotency_key=f"{prefix}-{spec.operation}-restore-a{attempt}"[:160],
            evidence=evidence,
        )
        restored = await _finance_cashbox_snapshot(
            session,
            spec=spec,
            state=state,
            purpose="restored-reorder",
            evidence=evidence,
        )
        if [str(item["id"]) for item in restored] != before_ids:
            raise AttestationError(
                "reorder_cashboxes_restore_exact_reread_failed",
                classification="backend_effect",
                evidence=evidence,
            )
    except AttestationError as restore_error:
        raise AttestationError(
            "reorder_cashboxes_restore_failed",
            classification="backend_effect",
            evidence=[*evidence, *restore_error.evidence],
        ) from restore_error
    _assert_response_budget(
        evidence,
        code="finance_reorder_cashboxes_response_payload_limit_exceeded",
    )
    return evidence


def _employee_mappings(value: Any) -> list[dict[str, Any]]:
    return [
        mapping
        for mapping in _walk_mappings(value)
        if str(mapping.get("id") or "")
        and str(mapping.get("name") or "")
        and str(mapping.get("updated_at") or "")
        and "is_active" in mapping
    ]


def _active_employee_registry_item(state: dict[str, Any]) -> dict[str, Any] | None:
    for item in reversed(_synthetic_entities(state)["employees"]):
        if item.get("status") == "active" and str(item.get("id") or ""):
            return item
    return None


def _register_employee(
    state: dict[str, Any],
    *,
    employee_id: str,
    case_id: str,
) -> None:
    registry = _synthetic_entities(state)["employees"]
    if any(str(item.get("id") or "") == employee_id for item in registry):
        return
    registry.append({"id": employee_id, "case_id": case_id, "status": "active"})


async def _finance_employee_snapshot(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    purpose: str,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    structured = await _raw_invoke(
        session,
        name="api:/api/list_employees",
        arguments={},
        idempotency_key=(f"{prefix}-{spec.operation}-{purpose}-employees-a{attempt}")[:160],
        evidence=evidence,
    )
    employees = _employee_mappings(structured)
    employees.sort(
        key=lambda item: (
            not bool(item.get("is_active")),
            str(item.get("name") or "").casefold(),
            str(item["id"]),
        )
    )
    if len({str(item["id"]) for item in employees}) != len(employees):
        raise AttestationError(
            "finance_employee_snapshot_duplicate_ids",
            classification="verification",
            evidence=evidence,
        )
    return employees


async def _ensure_synthetic_employee(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    prefix = str(state["refs"]["synthetic_prefix"])
    expected_name = f"{prefix}-employee"[:80]
    employees = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="before-employee-fixture",
        evidence=evidence,
    )
    registered = _active_employee_registry_item(state)
    registered_id = str((registered or {}).get("id") or "")
    if registered_id:
        employee = next(
            (item for item in employees if str(item.get("id") or "") == registered_id),
            None,
        )
        if not isinstance(employee, dict) or str(employee.get("name") or "") != expected_name:
            raise AttestationError(
                "synthetic_employee_registry_mismatch",
                classification="verification",
                evidence=evidence,
            )
        return employee
    existing = [item for item in employees if str(item.get("name") or "") == expected_name]
    if len(existing) > 1:
        raise AttestationError(
            "multiple_synthetic_employee_fixtures",
            classification="backend_effect",
            evidence=evidence,
        )
    if existing:
        employee = existing[0]
        employee_id = str(employee["id"])
        _register_employee(state, employee_id=employee_id, case_id=spec.case_id)
        state["refs"]["synthetic_employee_id"] = employee_id
        return employee

    attempt = _attempt_number(state, spec.case_id)
    expected_employee_ids = [str(item["id"]) for item in employees]
    created = await _raw_invoke(
        session,
        name="api:/api/save_employee",
        arguments={
            "create_mode": True,
            "name": expected_name,
            "position": "Synthetic Gateway Attestation",
            "is_active": True,
            "salary_mode": "none",
            "base_salary": "0",
            "work_percent": "0",
            "material_percent": "0",
            "repair_order_percent": "0",
            "expected_employee_ids": expected_employee_ids,
            "attestation_run_id": prefix,
        },
        idempotency_key=f"{prefix}-fixture-save-employee-a{attempt}"[:160],
        evidence=evidence,
    )
    employee = next(
        (
            item
            for item in _employee_mappings(created)
            if str(item.get("name") or "") == expected_name
        ),
        None,
    )
    employee_id = str((employee or {}).get("id") or "")
    if not employee_id:
        raise AttestationError(
            "synthetic_employee_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_employee(state, employee_id=employee_id, case_id=spec.case_id)
    state["refs"]["synthetic_employee_id"] = employee_id
    reread = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-employee-fixture",
        evidence=evidence,
    )
    exact = next(
        (item for item in reread if str(item.get("id") or "") == employee_id),
        None,
    )
    if (
        not isinstance(exact, dict)
        or str(exact.get("name") or "") != expected_name
        or not bool(exact.get("is_active"))
    ):
        raise AttestationError(
            "synthetic_employee_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    return exact


def _salary_transaction_mapping(
    value: Any,
    *,
    employee_id: str,
    cashbox_id: str,
    transaction_id: str = "",
) -> dict[str, Any] | None:
    for mapping in _walk_mappings(value):
        if (
            str(mapping.get("id") or "")
            and str(mapping.get("employee_id") or "") == employee_id
            and str(mapping.get("cashbox_id") or "") == cashbox_id
            and str(mapping.get("direction") or "") == "expense"
            and str(mapping.get("transaction_kind") or "") == "salary_payout"
            and (not transaction_id or str(mapping.get("id") or "") == transaction_id)
        ):
            return mapping
    return None


async def _finance_create_employee_salary_transaction_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str((active_cashboxes[0] if active_cashboxes else {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_cashbox_ref_missing",
            classification="routing",
        )
    employee = await _ensure_synthetic_employee(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
    )
    employee_id = str(employee["id"])
    before_cashbox, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    before_revision = str(before_cashbox["updated_at"])
    before_balance = int((before_cashbox["statistics"] or {}).get("balance_minor") or 0)
    employee_revision = str(employee.get("updated_at") or "")
    payload = {
        "employee_id": employee_id,
        "transaction_kind": "salary_payout",
        "amount_minor": 100,
        "cashbox_id": cashbox_id,
        "note": f"{prefix} synthetic salary payout",
        "expected_cashbox_updated_at": before_revision,
        "expected_employee_updated_at": employee_revision,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "expected_cashbox_updated_at",
                    "expected_employee_updated_at",
                }
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revisions-a{attempt}"[:160],
        },
        expected_code=("salary_transaction_expected_revisions_required_reread_exact_targets_first"),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "amount_minor": 0},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_employee_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-employee-a{attempt}"[:160],
        },
        expected_code="employee_update_conflict",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-cashbox-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    after_negative, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(after_negative.get("updated_at") or "") != before_revision
        or int((after_negative.get("statistics") or {}).get("balance_minor") or 0) != before_balance
    ):
        raise AttestationError(
            "salary_transaction_negative_case_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    transaction = _salary_transaction_mapping(
        applied,
        employee_id=employee_id,
        cashbox_id=cashbox_id,
    )
    transaction_id = str((transaction or {}).get("id") or "")
    if not transaction_id:
        raise AttestationError(
            "synthetic_salary_transaction_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cash_transaction(
        state,
        transaction_id=transaction_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
    )
    state["refs"]["synthetic_salary_transaction_id"] = transaction_id
    after_cashbox, after_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    exact_transaction = _salary_transaction_mapping(
        after_structured,
        employee_id=employee_id,
        cashbox_id=cashbox_id,
        transaction_id=transaction_id,
    )
    employees_after = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-salary",
        evidence=evidence,
    )
    employee_after = next(
        (item for item in employees_after if str(item.get("id") or "") == employee_id),
        None,
    )
    ledger = await _raw_invoke(
        session,
        name="api:/api/get_employee_salary_ledger",
        arguments={"employee_id": employee_id, "months": 1},
        idempotency_key=f"{prefix}-{spec.operation}-ledger-a{attempt}"[:160],
        evidence=evidence,
    )
    ledger_row = next(
        (
            mapping
            for mapping in _walk_mappings(ledger)
            if str(mapping.get("transaction_id") or "") == transaction_id
        ),
        None,
    )
    if (
        not isinstance(exact_transaction, dict)
        or int(exact_transaction.get("amount_minor") or 0) != 100
        or not isinstance(employee_after, dict)
        or str(employee_after.get("updated_at") or "") != employee_revision
        or not isinstance(ledger_row, dict)
        or int(ledger_row.get("amount_minor") or 0) != 100
        or int((after_cashbox.get("statistics") or {}).get("balance_minor") or 0)
        != before_balance - 100
        or str(after_cashbox.get("updated_at") or "") == before_revision
    ):
        raise AttestationError(
            "create_employee_salary_transaction_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_salary_transaction_response_payload_limit_exceeded",
    )
    return evidence


def _register_shift_accrual(
    state: dict[str, Any],
    *,
    accrual_id: str,
    employee_id: str,
    case_id: str,
) -> None:
    registry = _synthetic_entities(state)["shift_accruals"]
    if any(str(item.get("id") or "") == accrual_id for item in registry):
        return
    registry.append(
        {
            "id": accrual_id,
            "employee_id": employee_id,
            "case_id": case_id,
            "status": "active",
        }
    )


async def _finance_create_employee_shift_accrual_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    employee = await _ensure_synthetic_employee(
        session,
        spec=spec,
        state=state,
        evidence=evidence,
    )
    employee_id = str(employee["id"])
    employee_revision = str(employee.get("updated_at") or "")
    payload = {
        "employee_id": employee_id,
        "amount_minor": 100,
        "note": f"{prefix} synthetic shift accrual",
        "expected_employee_updated_at": employee_revision,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                key: value
                for key, value in payload.items()
                if key != "expected_employee_updated_at"
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revision-a{attempt}"[:160],
        },
        expected_code=(
            "shift_accrual_expected_employee_revision_required_reread_exact_employee_first"
        ),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "amount_minor": 0},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_employee_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="employee_update_conflict",
        evidence=evidence,
    )
    employees_after_negative = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-shift-negative",
        evidence=evidence,
    )
    employee_after_negative = next(
        (item for item in employees_after_negative if str(item.get("id") or "") == employee_id),
        None,
    )
    if (
        not isinstance(employee_after_negative, dict)
        or str(employee_after_negative.get("updated_at") or "") != employee_revision
    ):
        raise AttestationError(
            "shift_accrual_negative_case_changed_employee",
            classification="backend_effect",
            evidence=evidence,
        )

    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    accrual = next(
        (
            mapping
            for mapping in _walk_mappings(applied)
            if str(mapping.get("id") or "")
            and str(mapping.get("employee_id") or "") == employee_id
            and int(mapping.get("amount_minor") or 0) == 100
            and str(mapping.get("note") or "") == payload["note"]
        ),
        None,
    )
    accrual_id = str((accrual or {}).get("id") or "")
    if not accrual_id:
        raise AttestationError(
            "synthetic_shift_accrual_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_shift_accrual(
        state,
        accrual_id=accrual_id,
        employee_id=employee_id,
        case_id=spec.case_id,
    )
    state["refs"]["synthetic_shift_accrual_id"] = accrual_id
    ledger = await _raw_invoke(
        session,
        name="api:/api/get_employee_salary_ledger",
        arguments={"employee_id": employee_id, "months": 1},
        idempotency_key=f"{prefix}-{spec.operation}-ledger-a{attempt}"[:160],
        evidence=evidence,
    )
    ledger_row = next(
        (
            mapping
            for mapping in _walk_mappings(ledger)
            if str(mapping.get("accrual_id") or "") == accrual_id
        ),
        None,
    )
    employees_after = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-shift",
        evidence=evidence,
    )
    employee_after = next(
        (item for item in employees_after if str(item.get("id") or "") == employee_id),
        None,
    )
    if (
        not isinstance(ledger_row, dict)
        or str(ledger_row.get("kind") or "") != "shift_accrual"
        or int(ledger_row.get("amount_minor") or 0) != 100
        or not isinstance(employee_after, dict)
        or str(employee_after.get("updated_at") or "") != employee_revision
    ):
        raise AttestationError(
            "create_employee_shift_accrual_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_shift_accrual_response_payload_limit_exceeded",
    )
    return evidence


def _set_cash_transaction_registry_status(
    state: dict[str, Any],
    *,
    transaction_id: str,
    status: str,
) -> None:
    for item in _synthetic_entities(state)["cash_transactions"]:
        if str(item.get("id") or "") == transaction_id:
            item["status"] = status
            return


def _set_employee_registry_status(
    state: dict[str, Any],
    *,
    employee_id: str,
    status: str,
) -> None:
    for item in _synthetic_entities(state)["employees"]:
        if str(item.get("id") or "") == employee_id:
            item["status"] = status
            return


def _set_cashbox_registry_status(
    state: dict[str, Any],
    *,
    cashbox_id: str,
    status: str,
) -> None:
    for item in _synthetic_entities(state)["cashboxes"]:
        if str(item.get("id") or "") == cashbox_id:
            item["status"] = status
            return


async def _finance_cancel_cash_transaction_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    transaction_id = str(state["refs"].get("synthetic_salary_transaction_id") or "")
    employee_id = str(state["refs"].get("synthetic_employee_id") or "")
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str((active_cashboxes[0] if active_cashboxes else {}).get("id") or "")
    if not transaction_id or not employee_id or not cashbox_id:
        raise AttestationError(
            "synthetic_salary_cancellation_refs_missing",
            classification="routing",
        )
    before, before_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    original = _salary_transaction_mapping(
        before_structured,
        employee_id=employee_id,
        cashbox_id=cashbox_id,
        transaction_id=transaction_id,
    )
    if not isinstance(original, dict):
        raise AttestationError(
            "synthetic_salary_transaction_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    before_revision = str(before["updated_at"])
    before_balance = int((before["statistics"] or {}).get("balance_minor") or 0)
    payload = {
        "cashbox_id": cashbox_id,
        "transaction_id": transaction_id,
        "reason": f"{prefix} synthetic salary compensation",
        "expected_cashbox_updated_at": before_revision,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                key: value for key, value in payload.items() if key != "expected_cashbox_updated_at"
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revision-a{attempt}"[:160],
        },
        expected_code=("cash_cancellation_expected_revision_required_reread_exact_cashbox_first"),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "reason": "short"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    after_negative, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(after_negative.get("updated_at") or "") != before_revision
        or int((after_negative.get("statistics") or {}).get("balance_minor") or 0) != before_balance
    ):
        raise AttestationError(
            "cash_cancellation_negative_case_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    applied = await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    cancellation = next(
        (
            mapping
            for mapping in _walk_mappings(applied)
            if str(mapping.get("id") or "")
            and str(mapping.get("transaction_kind") or "") == "cashbox_cancellation"
            and str(mapping.get("related_transaction_id") or "") == transaction_id
        ),
        None,
    )
    cancellation_id = str((cancellation or {}).get("id") or "")
    if not cancellation_id:
        raise AttestationError(
            "synthetic_cash_cancellation_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=transaction_id,
        status="compensated",
    )
    _register_cash_transaction(
        state,
        transaction_id=cancellation_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
        status="audit_compensation",
    )
    state["refs"]["synthetic_salary_cancellation_transaction_id"] = cancellation_id
    after, after_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    cancelled = _cash_transaction_mapping(after_structured, transaction_id)
    cancellation_reread = _cash_transaction_mapping(after_structured, cancellation_id)
    ledger = await _raw_invoke(
        session,
        name="api:/api/get_employee_salary_ledger",
        arguments={"employee_id": employee_id, "months": 1},
        idempotency_key=f"{prefix}-{spec.operation}-ledger-a{attempt}"[:160],
        evidence=evidence,
    )
    ledger_row = next(
        (
            mapping
            for mapping in _walk_mappings(ledger)
            if str(mapping.get("transaction_id") or "") == transaction_id
        ),
        None,
    )
    if (
        not isinstance(cancelled, dict)
        or str(cancelled.get("transaction_kind") or "") != "cashbox_cancelled"
        or not isinstance(cancellation_reread, dict)
        or str(cancellation_reread.get("transaction_kind") or "") != "cashbox_cancellation"
        or str(cancellation_reread.get("related_transaction_id") or "") != transaction_id
        or ledger_row is not None
        or int((after.get("statistics") or {}).get("balance_minor") or 0) != before_balance + 100
        or str(after.get("updated_at") or "") == before_revision
    ):
        raise AttestationError(
            "cancel_cash_transaction_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_updated_at": str(after["updated_at"]),
            },
            "idempotency_key": f"{prefix}-{spec.operation}-already-cancelled-a{attempt}"[:160],
        },
        expected_code="validation_error",
        evidence=evidence,
    )
    final, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(final.get("updated_at") or "") != str(after.get("updated_at") or "")
        or int((final.get("statistics") or {}).get("balance_minor") or 0) != before_balance + 100
    ):
        raise AttestationError(
            "cash_cancellation_repeat_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_cash_cancellation_response_payload_limit_exceeded",
    )
    return evidence


async def _finance_cancel_last_cash_transaction_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str(
        state["refs"].get("synthetic_second_cashbox_id")
        or ((active_cashboxes[1] if len(active_cashboxes) > 1 else {}).get("id") or "")
    )
    if not cashbox_id:
        raise AttestationError(
            "synthetic_second_cashbox_ref_missing",
            classification="routing",
        )
    fixture_note = f"{prefix} cancel-last fixture"
    before, before_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    existing = [
        mapping
        for mapping in _walk_mappings(before_structured)
        if str(mapping.get("cashbox_id") or "") == cashbox_id
        and str(mapping.get("note") or "") == fixture_note
        and str(mapping.get("id") or "")
    ]
    if len(existing) > 1:
        raise AttestationError(
            "multiple_cancel_last_fixture_transactions",
            classification="backend_effect",
            evidence=evidence,
        )
    transaction_id = str((existing[0] if existing else {}).get("id") or "")
    if not transaction_id:
        create_arguments = {
            "operation": "create_cash_transaction",
            "payload": {
                "cashbox_id": cashbox_id,
                "direction": "income",
                "amount_minor": 100,
                "note": fixture_note,
                "expected_updated_at": str(before["updated_at"]),
            },
            "idempotency_key": f"{prefix}-cancel-last-fixture-create-a{attempt}"[:160],
        }
        created, create_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            create_arguments,
        )
        evidence.append(create_evidence)
        replay, replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            create_arguments,
        )
        evidence.append(replay_evidence)
        _assert_workflow_replay(
            replay,
            code_prefix="cancel_last_fixture_create",
            evidence=evidence,
        )
        created_structured = _structured(created)
        transaction_id = str(
            next(
                (
                    mapping.get("id")
                    for mapping in _walk_mappings(created_structured)
                    if str(mapping.get("cashbox_id") or "") == cashbox_id
                    and str(mapping.get("note") or "") == fixture_note
                    and int(mapping.get("amount_minor") or 0) == 100
                ),
                "",
            )
        )
        if not transaction_id:
            raise AttestationError(
                "cancel_last_fixture_transaction_id_missing",
                classification="backend_effect",
                evidence=evidence,
            )
        _register_cash_transaction(
            state,
            transaction_id=transaction_id,
            cashbox_id=cashbox_id,
            case_id=spec.case_id,
        )
        state["refs"]["synthetic_cancel_last_transaction_id"] = transaction_id
    ready, ready_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    fixture = _cash_transaction_mapping(ready_structured, transaction_id)
    ready_revision = str(ready.get("updated_at") or "")
    ready_balance = int((ready.get("statistics") or {}).get("balance_minor") or 0)
    if (
        not isinstance(fixture, dict)
        or str(fixture.get("note") or "") != fixture_note
        or int(fixture.get("amount_minor") or 0) != 100
        or not ready_revision
    ):
        raise AttestationError(
            "cancel_last_fixture_exact_reread_failed",
            classification="verification",
            evidence=evidence,
        )
    payload = {
        "cashbox_id": cashbox_id,
        "transaction_id": transaction_id,
        "expected_cashbox_updated_at": ready_revision,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                key: value for key, value in payload.items() if key != "expected_cashbox_updated_at"
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-revision-a{attempt}"[:160],
        },
        expected_code=(
            "cancel_last_cash_transaction_expected_revision_required_reread_exact_cashbox_first"
        ),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {**payload, "transaction_id": "missing-synthetic-transaction"},
            "idempotency_key": f"{prefix}-{spec.operation}-invalid-id-a{attempt}"[:160],
        },
        expected_code="not_found",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    after_negative, after_negative_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(after_negative.get("updated_at") or "") != ready_revision
        or int((after_negative.get("statistics") or {}).get("balance_minor") or 0) != ready_balance
        or _cash_transaction_mapping(after_negative_structured, transaction_id) is None
    ):
        raise AttestationError(
            "cancel_last_negative_case_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=payload,
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=transaction_id,
        status="deleted_cleanup",
    )
    after, after_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        _cash_transaction_mapping(after_structured, transaction_id) is not None
        or int((after.get("statistics") or {}).get("balance_minor") or 0) != ready_balance - 100
        or str(after.get("updated_at") or "") == ready_revision
    ):
        raise AttestationError(
            "cancel_last_cash_transaction_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **payload,
                "expected_cashbox_updated_at": str(after["updated_at"]),
            },
            "idempotency_key": f"{prefix}-{spec.operation}-already-removed-a{attempt}"[:160],
        },
        expected_code="not_found",
        evidence=evidence,
    )
    final, final_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        str(final.get("updated_at") or "") != str(after.get("updated_at") or "")
        or int((final.get("statistics") or {}).get("balance_minor") or 0) != ready_balance - 100
        or _cash_transaction_mapping(final_structured, transaction_id) is not None
    ):
        raise AttestationError(
            "cancel_last_repeat_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_cancel_last_response_payload_limit_exceeded",
    )
    return evidence


def _finance_audit_issue_mappings(value: Any) -> list[dict[str, Any]]:
    container = next(
        (mapping for mapping in _walk_mappings(value) if isinstance(mapping.get("issues"), list)),
        None,
    )
    return [
        item
        for item in (container or {}).get("issues", [])
        if isinstance(item, dict) and str(item.get("id") or "") and str(item.get("code") or "")
    ]


async def _finance_apply_audit_safe_fixes_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    active_cashboxes = _active_cashbox_registry_items(state)
    cashbox_id = str((active_cashboxes[0] if active_cashboxes else {}).get("id") or "")
    if not cashbox_id:
        raise AttestationError(
            "synthetic_cashbox_ref_missing",
            classification="routing",
        )
    employee_name = f"{prefix}-audit-employee"[:80]
    salary_note = f"{prefix} finance audit salary fixture"
    initial_cashbox, initial_cashbox_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    preexisting_salary = next(
        (
            mapping
            for mapping in _walk_mappings(initial_cashbox_structured)
            if str(mapping.get("cashbox_id") or "") == cashbox_id
            and str(mapping.get("note") or "") == salary_note
            and str(mapping.get("transaction_kind") or "") == "salary_payout"
            and str(mapping.get("employee_id") or "")
        ),
        None,
    )
    employees_before = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="before-audit-employee-fixture",
        evidence=evidence,
    )
    matching_employees = [
        item for item in employees_before if str(item.get("name") or "") == employee_name
    ]
    if len(matching_employees) > 1:
        raise AttestationError(
            "multiple_audit_employee_fixtures",
            classification="backend_effect",
            evidence=evidence,
        )
    employee = matching_employees[0] if matching_employees else None
    employee_already_detached = bool(
        preexisting_salary
        and not any(
            str(item.get("id") or "") == str(preexisting_salary.get("employee_id") or "")
            for item in employees_before
        )
    )
    if preexisting_salary:
        if str(preexisting_salary.get("employee_name") or "") != employee_name or (
            employee is not None
            and str(employee.get("id") or "") != str(preexisting_salary.get("employee_id") or "")
        ):
            raise AttestationError(
                "audit_salary_fixture_identity_mismatch",
                classification="verification",
                evidence=evidence,
            )
        if employee_already_detached:
            employee = {
                "id": str(preexisting_salary["employee_id"]),
                "name": employee_name,
                "updated_at": "",
                "is_active": False,
            }
    elif employee is None:
        created = await _raw_invoke(
            session,
            name="api:/api/save_employee",
            arguments={
                "create_mode": True,
                "name": employee_name,
                "position": "Synthetic Finance Audit",
                "is_active": True,
                "salary_mode": "none",
                "base_salary": "0",
                "work_percent": "0",
                "material_percent": "0",
                "repair_order_percent": "0",
                "expected_employee_ids": [str(item["id"]) for item in employees_before],
                "attestation_run_id": prefix,
            },
            idempotency_key=f"{prefix}-audit-fixture-save-employee-a{attempt}"[:160],
            evidence=evidence,
        )
        employee = next(
            (
                item
                for item in _employee_mappings(created)
                if str(item.get("name") or "") == employee_name
            ),
            None,
        )
    employee_id = str((employee or {}).get("id") or "")
    employee_revision = str((employee or {}).get("updated_at") or "")
    if not employee_id or (not employee_revision and not employee_already_detached):
        raise AttestationError(
            "audit_employee_fixture_exact_ref_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_employee(state, employee_id=employee_id, case_id=spec.case_id)
    state["refs"]["synthetic_audit_employee_id"] = employee_id

    before_cashbox = initial_cashbox
    before_balance = int((initial_cashbox.get("statistics") or {}).get("balance_minor") or 0) + (
        100 if preexisting_salary else 0
    )
    existing_salary = preexisting_salary
    salary_transaction_id = str((existing_salary or {}).get("id") or "")
    if not salary_transaction_id:
        salary_arguments = {
            "operation": "create_employee_salary_transaction",
            "payload": {
                "employee_id": employee_id,
                "transaction_kind": "salary_payout",
                "amount_minor": 100,
                "cashbox_id": cashbox_id,
                "note": salary_note,
                "expected_cashbox_updated_at": str(before_cashbox["updated_at"]),
                "expected_employee_updated_at": employee_revision,
                "attestation_run_id": prefix,
            },
            "idempotency_key": f"{prefix}-audit-fixture-salary-a{attempt}"[:160],
        }
        salary_result, salary_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            salary_arguments,
        )
        evidence.append(salary_evidence)
        salary_replay, salary_replay_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            salary_arguments,
        )
        evidence.append(salary_replay_evidence)
        _assert_workflow_replay(
            salary_replay,
            code_prefix="finance_audit_salary_fixture",
            evidence=evidence,
        )
        salary_transaction = _salary_transaction_mapping(
            _structured(salary_result),
            employee_id=employee_id,
            cashbox_id=cashbox_id,
        )
        salary_transaction_id = str((salary_transaction or {}).get("id") or "")
    if not salary_transaction_id:
        raise AttestationError(
            "audit_salary_fixture_transaction_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _register_cash_transaction(
        state,
        transaction_id=salary_transaction_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
    )
    state["refs"]["synthetic_audit_salary_transaction_id"] = salary_transaction_id
    after_salary, after_salary_structured = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    salary_transaction = _salary_transaction_mapping(
        after_salary_structured,
        employee_id=employee_id,
        cashbox_id=cashbox_id,
        transaction_id=salary_transaction_id,
    )
    if (
        not isinstance(salary_transaction, dict)
        or int((after_salary.get("statistics") or {}).get("balance_minor") or 0)
        != before_balance - 100
    ):
        raise AttestationError(
            "audit_salary_fixture_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )

    if not employee_already_detached:
        await _raw_invoke(
            session,
            name="api:/api/delete_employee",
            arguments={
                "employee_id": employee_id,
                "expected_updated_at": employee_revision,
                "attestation_run_id": prefix,
                "attestation_detach_salary_transaction_id": salary_transaction_id,
            },
            idempotency_key=f"{prefix}-audit-fixture-detach-employee-a{attempt}"[:160],
            evidence=evidence,
        )
    employees_detached = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-audit-employee-detach",
        evidence=evidence,
    )
    if any(str(item.get("id") or "") == employee_id for item in employees_detached):
        raise AttestationError(
            "audit_employee_detach_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )

    audit_before = await _raw_invoke(
        session,
        name="api:/api/finance_audit",
        arguments={},
        idempotency_key="",
        evidence=evidence,
        allow_large_output=True,
    )
    issues_before = _finance_audit_issue_mappings(audit_before)
    issue = next(
        (
            item
            for item in issues_before
            if str(item.get("code") or "") == "salary_transaction_missing_employee"
            and str(item.get("cash_transaction_id") or "") == salary_transaction_id
        ),
        None,
    )
    issue_id = str((issue or {}).get("id") or "")
    expected_issue_ids = [str(item["id"]) for item in issues_before]
    if not issue_id or not expected_issue_ids:
        raise AttestationError(
            "synthetic_finance_audit_issue_missing",
            classification="verification",
            evidence=evidence,
        )
    base_payload = {
        "issue_ids": [issue_id],
        "expected_issue_ids": expected_issue_ids,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload={**base_payload, "dry_run": False},
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "dry_run": False,
                "issue_ids": [issue_id],
                "attestation_run_id": prefix,
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-snapshot-a{attempt}"[:160],
        },
        expected_code=("finance_audit_issue_snapshot_required_reread_exact_audit_first"),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **base_payload,
                "dry_run": False,
                "expected_issue_ids": [*expected_issue_ids, "stale-synthetic-issue"],
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="finance_audit_snapshot_conflict",
        evidence=evidence,
    )
    dry_run_arguments = {
        "operation": spec.operation,
        "payload": {**base_payload, "dry_run": True},
        "idempotency_key": f"{prefix}-{spec.operation}-dry-run-a{attempt}"[:160],
    }
    _dry_run, dry_run_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        dry_run_arguments,
    )
    evidence.append(dry_run_evidence)
    employees_after_dry_run = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-audit-dry-run",
        evidence=evidence,
    )
    if any(str(item.get("id") or "") == employee_id for item in employees_after_dry_run):
        raise AttestationError(
            "finance_audit_dry_run_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload={**base_payload, "dry_run": False},
        idempotency_key=f"{prefix}-{spec.operation}-apply-a{attempt}"[:160],
        evidence=evidence,
    )
    employees_restored = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-audit-apply",
        evidence=evidence,
    )
    restored = next(
        (item for item in employees_restored if str(item.get("id") or "") == employee_id),
        None,
    )
    if (
        not isinstance(restored, dict)
        or str(restored.get("name") or "") != employee_name
        or bool(restored.get("is_active"))
    ):
        raise AttestationError(
            "finance_audit_employee_restore_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    audit_after = await _raw_invoke(
        session,
        name="api:/api/finance_audit",
        arguments={},
        idempotency_key="",
        evidence=evidence,
        allow_large_output=True,
    )
    if issue_id in {str(item["id"]) for item in _finance_audit_issue_mappings(audit_after)}:
        raise AttestationError(
            "finance_audit_issue_still_present_after_apply",
            classification="backend_effect",
            evidence=evidence,
        )

    cashbox_before_compensation, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    cancel_arguments = {
        "operation": "cancel_cash_transaction",
        "payload": {
            "cashbox_id": cashbox_id,
            "transaction_id": salary_transaction_id,
            "reason": f"{prefix} audit salary compensation",
            "expected_cashbox_updated_at": str(cashbox_before_compensation["updated_at"]),
            "attestation_run_id": prefix,
        },
        "idempotency_key": f"{prefix}-audit-salary-compensation-a{attempt}"[:160],
    }
    cancelled, cancel_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        cancel_arguments,
    )
    evidence.append(cancel_evidence)
    cancel_replay, cancel_replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        cancel_arguments,
    )
    evidence.append(cancel_replay_evidence)
    _assert_workflow_replay(
        cancel_replay,
        code_prefix="finance_audit_salary_compensation",
        evidence=evidence,
    )
    cancellation = next(
        (
            mapping
            for mapping in _walk_mappings(_structured(cancelled))
            if str(mapping.get("transaction_kind") or "") == "cashbox_cancellation"
            and str(mapping.get("related_transaction_id") or "") == salary_transaction_id
        ),
        None,
    )
    cancellation_id = str((cancellation or {}).get("id") or "")
    if not cancellation_id:
        raise AttestationError(
            "finance_audit_salary_compensation_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=salary_transaction_id,
        status="compensated",
    )
    _register_cash_transaction(
        state,
        transaction_id=cancellation_id,
        cashbox_id=cashbox_id,
        case_id=spec.case_id,
        status="audit_compensation",
    )
    await _raw_invoke(
        session,
        name="api:/api/delete_employee",
        arguments={
            "employee_id": employee_id,
            "expected_updated_at": str(restored["updated_at"]),
        },
        idempotency_key=f"{prefix}-audit-fixture-delete-employee-a{attempt}"[:160],
        evidence=evidence,
    )
    _set_employee_registry_status(
        state,
        employee_id=employee_id,
        status="deleted_cleanup",
    )
    final_employees = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-audit-employee-cleanup",
        evidence=evidence,
    )
    final_cashbox, _ = await _finance_cashbox_context(
        session,
        cashbox_id=cashbox_id,
        evidence=evidence,
    )
    if (
        any(str(item.get("id") or "") == employee_id for item in final_employees)
        or int((final_cashbox.get("statistics") or {}).get("balance_minor") or 0) != before_balance
    ):
        raise AttestationError(
            "finance_audit_fixture_cleanup_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_audit_safe_fix_response_payload_limit_exceeded",
    )
    return evidence


def _cashbox_transaction_rows(value: Any, cashbox_id: str) -> list[dict[str, Any]]:
    for mapping in _walk_mappings(value):
        rows = mapping.get("transactions")
        if not isinstance(rows, list):
            continue
        filtered = [
            item
            for item in rows
            if isinstance(item, dict)
            and str(item.get("cashbox_id") or "") == cashbox_id
            and str(item.get("id") or "")
            and "amount_minor" in item
        ]
        if filtered or not rows:
            return filtered
    return []


async def _finance_delete_cashbox_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    primary_id = str(state["refs"].get("synthetic_cashbox_id") or "")
    second_id = str(state["refs"].get("synthetic_second_cashbox_id") or "")
    transfer_source_id = str(state["refs"].get("synthetic_transfer_source_transaction_id") or "")
    transfer_target_id = str(state["refs"].get("synthetic_transfer_target_transaction_id") or "")
    manual_transaction_id = str(state["refs"].get("synthetic_cash_transaction_id") or "")
    if not all(
        (
            primary_id,
            second_id,
            transfer_source_id,
            transfer_target_id,
            manual_transaction_id,
        )
    ):
        raise AttestationError(
            "synthetic_cashbox_cleanup_refs_missing",
            classification="routing",
        )
    primary, primary_structured = await _finance_cashbox_context(
        session,
        cashbox_id=primary_id,
        evidence=evidence,
    )
    second, second_structured = await _finance_cashbox_context(
        session,
        cashbox_id=second_id,
        evidence=evidence,
    )
    second_rows = _cashbox_transaction_rows(second_structured, second_id)
    second_transaction_ids = [str(item["id"]) for item in second_rows]
    nonzero_payload = {
        "cashbox_id": second_id,
        "expected_cashbox_updated_at": str(second["updated_at"]),
        "expected_transaction_ids": second_transaction_ids,
        "attestation_run_id": prefix,
    }
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=nonzero_payload,
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                "cashbox_id": second_id,
                "attestation_run_id": prefix,
            },
            "idempotency_key": f"{prefix}-{spec.operation}-missing-snapshot-a{attempt}"[:160],
        },
        expected_code=("cashbox_delete_snapshot_required_reread_exact_cashbox_first"),
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": {
                **nonzero_payload,
                "expected_cashbox_updated_at": "2000-01-01T00:00:00+00:00",
            },
            "idempotency_key": f"{prefix}-{spec.operation}-stale-a{attempt}"[:160],
        },
        expected_code="cashbox_update_conflict",
        evidence=evidence,
    )
    await _inventory_expected_failure(
        session,
        spec=spec,
        arguments={
            "operation": spec.operation,
            "payload": nonzero_payload,
            "idempotency_key": f"{prefix}-{spec.operation}-nonzero-a{attempt}"[:160],
        },
        expected_code="cashbox_attestation_balance_not_zero",
        evidence=evidence,
    )
    second_after_negative, second_after_negative_structured = await _finance_cashbox_context(
        session,
        cashbox_id=second_id,
        evidence=evidence,
    )
    if (
        str(second_after_negative.get("updated_at") or "") != str(second.get("updated_at") or "")
        or int((second_after_negative.get("statistics") or {}).get("balance_minor") or 0) != 100
        or _cash_transaction_mapping(
            second_after_negative_structured,
            transfer_target_id,
        )
        is None
    ):
        raise AttestationError(
            "delete_cashbox_negative_case_changed_backend",
            classification="backend_effect",
            evidence=evidence,
        )

    transfer_cancel_arguments = {
        "operation": "cancel_cash_transaction",
        "payload": {
            "cashbox_id": second_id,
            "transaction_id": transfer_target_id,
            "reason": f"{prefix} transfer compensation",
            "expected_cashbox_updated_at": str(second["updated_at"]),
            "expected_related_cashbox_updated_at": str(primary["updated_at"]),
            "attestation_run_id": prefix,
        },
        "idempotency_key": f"{prefix}-delete-cashbox-transfer-compensation-a{attempt}"[:160],
    }
    transfer_cancelled, transfer_cancel_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        transfer_cancel_arguments,
    )
    evidence.append(transfer_cancel_evidence)
    transfer_replay, transfer_replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        transfer_cancel_arguments,
    )
    evidence.append(transfer_replay_evidence)
    _assert_workflow_replay(
        transfer_replay,
        code_prefix="delete_cashbox_transfer_compensation",
        evidence=evidence,
    )
    transfer_cancellations = [
        mapping
        for mapping in _walk_mappings(_structured(transfer_cancelled))
        if str(mapping.get("transaction_kind") or "") == "cashbox_cancellation"
        and str(mapping.get("related_transaction_id") or "")
        in {transfer_source_id, transfer_target_id}
        and str(mapping.get("id") or "")
    ]
    if {str(item.get("related_transaction_id") or "") for item in transfer_cancellations} != {
        transfer_source_id,
        transfer_target_id,
    }:
        raise AttestationError(
            "transfer_compensation_pair_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=transfer_source_id,
        status="compensated",
    )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=transfer_target_id,
        status="compensated",
    )
    for cancellation in transfer_cancellations:
        _register_cash_transaction(
            state,
            transaction_id=str(cancellation["id"]),
            cashbox_id=str(cancellation["cashbox_id"]),
            case_id=spec.case_id,
            status="audit_compensation",
        )
    primary_after_transfer, _ = await _finance_cashbox_context(
        session,
        cashbox_id=primary_id,
        evidence=evidence,
    )
    second_after_transfer, _ = await _finance_cashbox_context(
        session,
        cashbox_id=second_id,
        evidence=evidence,
    )
    if (
        int((primary_after_transfer.get("statistics") or {}).get("balance_minor") or 0) != 100
        or int((second_after_transfer.get("statistics") or {}).get("balance_minor") or 0) != 0
    ):
        raise AttestationError(
            "transfer_compensation_balance_invalid",
            classification="backend_effect",
            evidence=evidence,
        )

    manual_cancel_arguments = {
        "operation": "cancel_cash_transaction",
        "payload": {
            "cashbox_id": primary_id,
            "transaction_id": manual_transaction_id,
            "reason": f"{prefix} manual cash compensation",
            "expected_cashbox_updated_at": str(primary_after_transfer["updated_at"]),
            "attestation_run_id": prefix,
        },
        "idempotency_key": f"{prefix}-delete-cashbox-manual-compensation-a{attempt}"[:160],
    }
    manual_cancelled, manual_cancel_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        manual_cancel_arguments,
    )
    evidence.append(manual_cancel_evidence)
    manual_replay, manual_replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        manual_cancel_arguments,
    )
    evidence.append(manual_replay_evidence)
    _assert_workflow_replay(
        manual_replay,
        code_prefix="delete_cashbox_manual_compensation",
        evidence=evidence,
    )
    manual_cancellation = next(
        (
            mapping
            for mapping in _walk_mappings(_structured(manual_cancelled))
            if str(mapping.get("transaction_kind") or "") == "cashbox_cancellation"
            and str(mapping.get("related_transaction_id") or "") == manual_transaction_id
        ),
        None,
    )
    manual_cancellation_id = str((manual_cancellation or {}).get("id") or "")
    if not manual_cancellation_id:
        raise AttestationError(
            "manual_cash_compensation_id_missing",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_cash_transaction_registry_status(
        state,
        transaction_id=manual_transaction_id,
        status="compensated",
    )
    _register_cash_transaction(
        state,
        transaction_id=manual_cancellation_id,
        cashbox_id=primary_id,
        case_id=spec.case_id,
        status="audit_compensation",
    )
    primary_zero, primary_zero_structured = await _finance_cashbox_context(
        session,
        cashbox_id=primary_id,
        evidence=evidence,
    )
    second_zero, second_zero_structured = await _finance_cashbox_context(
        session,
        cashbox_id=second_id,
        evidence=evidence,
    )
    if any(
        int((cashbox.get("statistics") or {}).get("balance_minor") or 0) != 0
        for cashbox in (primary_zero, second_zero)
    ):
        raise AttestationError(
            "synthetic_cashbox_balance_not_zero_before_delete",
            classification="backend_effect",
            evidence=evidence,
        )

    second_delete_payload = {
        "cashbox_id": second_id,
        "expected_cashbox_updated_at": str(second_zero["updated_at"]),
        "expected_transaction_ids": [
            str(item["id"]) for item in _cashbox_transaction_rows(second_zero_structured, second_id)
        ],
        "attestation_run_id": prefix,
    }
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=second_delete_payload,
        idempotency_key=f"{prefix}-{spec.operation}-second-a{attempt}"[:160],
        evidence=evidence,
    )
    _set_cashbox_registry_status(
        state,
        cashbox_id=second_id,
        status="deleted_cleanup",
    )
    primary_after_second, primary_after_second_structured = await _finance_cashbox_context(
        session,
        cashbox_id=primary_id,
        evidence=evidence,
    )
    primary_delete_payload = {
        "cashbox_id": primary_id,
        "expected_cashbox_updated_at": str(primary_after_second["updated_at"]),
        "expected_transaction_ids": [
            str(item["id"])
            for item in _cashbox_transaction_rows(
                primary_after_second_structured,
                primary_id,
            )
        ],
        "attestation_run_id": prefix,
    }
    await _inventory_apply_and_replay(
        session,
        spec=spec,
        payload=primary_delete_payload,
        idempotency_key=f"{prefix}-{spec.operation}-primary-a{attempt}"[:160],
        evidence=evidence,
    )
    _set_cashbox_registry_status(
        state,
        cashbox_id=primary_id,
        status="deleted_cleanup",
    )
    for transaction in _synthetic_entities(state)["cash_transactions"]:
        if str(transaction.get("cashbox_id") or "") in {primary_id, second_id}:
            transaction["status"] = "deleted_cleanup"
    remaining_cashboxes = await _finance_cashbox_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="after-synthetic-delete",
        evidence=evidence,
    )
    remaining_ids = {str(item["id"]) for item in remaining_cashboxes}
    if primary_id in remaining_ids or second_id in remaining_ids:
        raise AttestationError(
            "synthetic_cashbox_delete_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_delete_cashbox_response_payload_limit_exceeded",
    )
    return evidence


async def _finance_read_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    attempt = _attempt_number(state, spec.case_id)
    prefix = str(state["refs"]["synthetic_prefix"])
    payload = _read_operation_payload(spec.operation, state["refs"])
    await _inventory_missing_key_gate(
        session,
        spec=spec,
        payload=payload,
        evidence=evidence,
    )
    if spec.operation == "get_cashbox":
        await _inventory_expected_failure(
            session,
            spec=spec,
            arguments={
                "operation": spec.operation,
                "payload": {
                    "cashbox_id": f"{prefix}-missing-cashbox",
                    "transaction_limit": 1,
                },
                "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
            },
            expected_code="not_found",
            evidence=evidence,
        )
    elif spec.operation == "get_repair_order":
        await _inventory_expected_failure(
            session,
            spec=spec,
            arguments={
                "operation": spec.operation,
                "payload": {"card_id": f"{prefix}-missing-card"},
                "idempotency_key": f"{prefix}-{spec.operation}-invalid-a{attempt}"[:160],
            },
            expected_code="not_found",
            evidence=evidence,
        )

    arguments = {
        "operation": spec.operation,
        "payload": payload,
        "idempotency_key": f"{prefix}-{spec.operation}-read-a{attempt}"[:160],
    }
    result, result_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(result_evidence)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix=spec.operation,
        evidence=evidence,
    )
    structured = _structured(result)

    if spec.operation == "list_cashboxes":
        cashboxes = _cashbox_mappings(structured)
        if not cashboxes:
            raise AttestationError(
                "list_cashboxes_structure_invalid",
                classification="verification",
                evidence=evidence,
            )
        cashbox_ids = {str(item["id"]) for item in cashboxes}
        verify, verify_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            {
                **arguments,
                "idempotency_key": (f"{prefix}-{spec.operation}-verify-a{attempt}")[:160],
            },
        )
        evidence.append(verify_evidence)
        verify_ids = {str(item["id"]) for item in _cashbox_mappings(_structured(verify))}
        if not verify_ids or verify_ids != cashbox_ids:
            raise AttestationError(
                "list_cashboxes_independent_reread_mismatch",
                classification="verification",
                evidence=evidence,
            )
        state["refs"]["read_cashbox_id"] = sorted(cashbox_ids)[0]
    elif spec.operation == "get_cashbox":
        cashbox_id = str(payload["cashbox_id"])
        cashbox = _mapping_for_entity(structured, cashbox_id)
        if not isinstance(cashbox, dict) or not str(cashbox.get("updated_at") or ""):
            raise AttestationError(
                "get_cashbox_structure_invalid",
                classification="verification",
                evidence=evidence,
            )
        context, context_evidence = await _attested_call(
            session,
            "agent_entity_context",
            {"entity": "cashbox", "entity_id": cashbox_id, "detail": "full"},
        )
        evidence.append(context_evidence)
        reread = _mapping_for_entity(_structured(context), cashbox_id)
        if not isinstance(reread, dict) or str(reread.get("updated_at") or "") != str(
            cashbox.get("updated_at") or ""
        ):
            raise AttestationError(
                "get_cashbox_independent_reread_mismatch",
                classification="verification",
                evidence=evidence,
            )
    elif spec.operation == "get_cash_journal":
        journal = next(
            (
                mapping
                for mapping in _walk_mappings(structured)
                if "entries" in mapping and "totals" in mapping and "meta" in mapping
            ),
            None,
        )
        if not isinstance(journal, dict) or any(key in journal for key in ("markdown", "text")):
            raise AttestationError(
                "get_cash_journal_compact_structure_invalid",
                classification="privacy_payload",
                evidence=evidence,
            )
        verify, verify_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            {
                **arguments,
                "idempotency_key": (f"{prefix}-{spec.operation}-verify-a{attempt}")[:160],
            },
        )
        evidence.append(verify_evidence)
        verify_journal = next(
            (
                mapping
                for mapping in _walk_mappings(_structured(verify))
                if "entries" in mapping and "totals" in mapping and "meta" in mapping
            ),
            None,
        )
        if not isinstance(verify_journal, dict):
            raise AttestationError(
                "get_cash_journal_independent_reread_missing",
                classification="verification",
                evidence=evidence,
            )
    elif spec.operation == "get_repair_order":
        card_id = str(payload["card_id"])
        card = _mapping_for_entity(structured, card_id)
        if not isinstance(card, dict) or not str(card.get("updated_at") or ""):
            raise AttestationError(
                "get_repair_order_structure_invalid",
                classification="verification",
                evidence=evidence,
            )
        context, context_evidence = await _attested_call(
            session,
            "agent_entity_context",
            {"entity": "repair_order", "entity_id": card_id, "detail": "full"},
        )
        evidence.append(context_evidence)
        reread = _mapping_for_entity(_structured(context), card_id)
        if not isinstance(reread, dict) or str(reread.get("updated_at") or "") != str(
            card.get("updated_at") or ""
        ):
            raise AttestationError(
                "get_repair_order_independent_reread_mismatch",
                classification="verification",
                evidence=evidence,
            )
    else:
        raise AttestationError(
            f"finance_read_executor_not_implemented_{spec.operation}",
            classification="routing",
        )
    if _contains_binary_field(structured):
        raise AttestationError(
            "finance_read_binary_payload_leak",
            classification="privacy_payload",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="finance_read_response_payload_limit_exceeded",
    )
    return evidence


async def _operation_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    apply_synthetic: bool,
) -> list[dict[str, Any]]:
    if spec.operation == "download_shared_file":
        return await _document_download_file_case(
            session,
            spec=spec,
            state=state,
        )
    if spec.operation == "download_repair_order_print_pdf":
        return await _document_repair_order_pdf_case(
            session,
            spec=spec,
            state=state,
        )
    if spec.family == "finance" and not spec.requires_apply:
        return await _finance_read_case(
            session,
            spec=spec,
            state=state,
        )
    if spec.requires_apply:
        if not apply_synthetic:
            raise AttestationError(
                "synthetic_apply_flag_required",
                classification="policy",
            )
        if spec.family == "board":
            return await _board_write_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "inventory":
            return await _inventory_write_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "create_cashbox":
            return await _finance_create_cashbox_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "create_cash_transaction":
            return await _finance_create_cash_transaction_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "create_cashbox_transfer":
            return await _finance_create_cashbox_transfer_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "record_repair_order_payment":
            return await _finance_record_repair_order_payment_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "update_repair_order":
            return await _finance_update_repair_order_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "set_repair_order_status":
            return await _finance_set_repair_order_status_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "reorder_cashboxes":
            return await _finance_reorder_cashboxes_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "create_employee_salary_transaction":
            return await _finance_create_employee_salary_transaction_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "create_employee_shift_accrual":
            return await _finance_create_employee_shift_accrual_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "cancel_cash_transaction":
            return await _finance_cancel_cash_transaction_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "cancel_last_cash_transaction":
            return await _finance_cancel_last_cash_transaction_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "apply_finance_audit_safe_fixes":
            return await _finance_apply_audit_safe_fixes_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "finance" and spec.operation == "delete_cashbox":
            return await _finance_delete_cashbox_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "document" and spec.operation == "upload_shared_file":
            return await _document_upload_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "document" and spec.operation == "delete_shared_file":
            return await _document_delete_case(
                session,
                spec=spec,
                state=state,
            )
        if spec.family == "document" and spec.operation == "update_display_dashboard_message":
            return await _document_dashboard_case(
                session,
                spec=spec,
                state=state,
            )
        raise AttestationError(
            f"synthetic_executor_not_implemented_{spec.operation}",
            classification="routing",
        )

    payload = _read_operation_payload(spec.operation, state["refs"])
    item = _find_case(state, spec.case_id)
    attempt = max(1, int(item.get("attempts") or 1))
    idempotency_key = (f"{state['run_id']}-{spec.family}-{spec.operation}-read-a{attempt}")[:160]
    arguments: dict[str, Any] = {
        "operation": spec.operation,
        "payload": payload,
        "idempotency_key": idempotency_key,
    }
    if spec.workflow_tool == "agent_board_workflow":
        arguments["mode"] = "dry_run"
    if spec.workflow_tool == "agent_document_workflow":
        arguments["allow_large_output"] = spec.operation in {
            "create_document_without_card_pdf",
            "download_repair_order_print_pdf",
        }

    invalid_arguments = dict(arguments)
    invalid_arguments["idempotency_key"] = ""
    _, invalid_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        invalid_arguments,
        expect_ok=False,
    )
    if invalid_evidence.get("error_code") != "idempotency_key_required":
        raise AttestationError(
            f"{spec.workflow_tool}_idempotency_gate_invalid",
            classification="policy",
            evidence=[invalid_evidence],
        )

    preliminary_evidence: list[dict[str, Any]] = []
    if spec.operation == "create_document_without_card_pdf":
        compact_arguments = {
            **arguments,
            "idempotency_key": (
                f"{state['run_id']}-{spec.family}-{spec.operation}-compact-a{attempt}"
            )[:160],
            "allow_large_output": False,
        }
        compact_result, compact_evidence = await _attested_call(
            session,
            spec.workflow_tool,
            compact_arguments,
        )
        preliminary_evidence.append(compact_evidence)
        if _contains_binary_field(_structured(compact_result)):
            raise AttestationError(
                "manual_document_pdf_compact_binary_leak",
                classification="privacy_payload",
                evidence=[invalid_evidence, *preliminary_evidence],
            )

    result, evidence = await _attested_call(session, spec.workflow_tool, arguments)
    replay, replay_evidence = await _attested_call(
        session,
        spec.workflow_tool,
        arguments,
    )
    replay_structured = _structured(replay)
    replay_summary = (
        replay_structured.get("summary")
        if isinstance(replay_structured.get("summary"), dict)
        else {}
    )
    replay_verification = (
        replay_structured.get("verification")
        if isinstance(replay_structured.get("verification"), dict)
        else {}
    )
    if (
        replay_summary.get("deduplicated") is not True
        or replay_verification.get("idempotency_reused") is not True
        or replay_verification.get("prior_terminal_state") is not True
    ):
        raise AttestationError(
            f"{spec.workflow_tool}_idempotency_replay_invalid",
            classification="verification",
            evidence=[invalid_evidence, evidence, replay_evidence],
        )
    structured = _structured(result)
    if spec.operation == "list_cashboxes":
        cashbox_id = _first_entity_id(structured, ("cashbox_id", "id"))
        if cashbox_id:
            state["refs"]["read_cashbox_id"] = cashbox_id
    elif spec.operation == "list_inventory_items":
        item_id = _first_entity_id(structured, ("item_id", "id"))
        if item_id:
            state["refs"]["read_inventory_item_id"] = item_id
    return [invalid_evidence, *preliminary_evidence, evidence, replay_evidence]


def _raw_call_payload(
    *,
    name: str,
    arguments: dict[str, Any],
    schema_hash: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "schema_hash": schema_hash,
        "idempotency_key": idempotency_key,
        "allow_large_output": False,
    }


async def _raw_expected_failure(
    session: Any,
    *,
    name: str,
    arguments: dict[str, Any],
    schema_hash: str,
    idempotency_key: str,
    expected_code: str,
    evidence: list[dict[str, Any]],
) -> None:
    _result, call_evidence = await _attested_call(
        session,
        "call_raw_capability",
        _raw_call_payload(
            name=name,
            arguments=arguments,
            schema_hash=schema_hash,
            idempotency_key=idempotency_key,
        ),
        expect_ok=False,
    )
    evidence.append(call_evidence)
    if call_evidence.get("error_code") != expected_code:
        raise AttestationError(
            f"raw_{name}_expected_{expected_code}_missing",
            classification="policy" if "idempotency" in expected_code else "verification",
            evidence=evidence,
        )


async def _raw_common_write_gates(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    schema_hash: str,
    valid_arguments: dict[str, Any],
    invalid_arguments: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    prefix = str(state["refs"]["synthetic_prefix"])
    attempt = _attempt_number(state, spec.case_id)
    await _raw_expected_failure(
        session,
        name=spec.target,
        arguments=valid_arguments,
        schema_hash="0" * 16,
        idempotency_key=f"{prefix}-raw-{spec.target}-stale-schema-a{attempt}"[:160],
        expected_code="schema_hash_mismatch_rediscover_capability",
        evidence=evidence,
    )
    await _raw_expected_failure(
        session,
        name=spec.target,
        arguments=valid_arguments,
        schema_hash=schema_hash,
        idempotency_key="",
        expected_code="idempotency_key_required_for_raw_write",
        evidence=evidence,
    )
    await _raw_expected_failure(
        session,
        name=spec.target,
        arguments=invalid_arguments,
        schema_hash=schema_hash,
        idempotency_key=f"{prefix}-raw-{spec.target}-invalid-a{attempt}"[:160],
        expected_code="raw_capability_failed",
        evidence=evidence,
    )


async def _raw_apply_and_replay(
    session: Any,
    *,
    name: str,
    arguments: dict[str, Any],
    schema_hash: str,
    idempotency_key: str,
    expected_check: str,
    evidence: list[dict[str, Any]],
    already_applied: bool = False,
) -> dict[str, Any]:
    payload = _raw_call_payload(
        name=name,
        arguments=arguments,
        schema_hash=schema_hash,
        idempotency_key=idempotency_key,
    )
    first, first_evidence = await _attested_call(
        session,
        "call_raw_capability",
        payload,
    )
    evidence.append(first_evidence)
    first_structured = _structured(first)
    if already_applied:
        _assert_workflow_replay(
            first,
            code_prefix=f"raw_{name}",
            evidence=evidence,
        )
        return first_structured
    verification = (
        first_structured.get("verification")
        if isinstance(first_structured.get("verification"), dict)
        else {}
    )
    if (
        verification.get("schema_hash_verified") is not True
        or verification.get("executor_ok") is not True
        or verification.get("ledger_closed") is not True
        or verification.get("passed") is not True
        or str(verification.get("check") or "") != expected_check
    ):
        raise AttestationError(
            f"raw_{name}_exact_gateway_verification_invalid",
            classification="verification",
            evidence=evidence,
        )
    replay, replay_evidence = await _attested_call(
        session,
        "call_raw_capability",
        payload,
    )
    evidence.append(replay_evidence)
    _assert_workflow_replay(
        replay,
        code_prefix=f"raw_{name}",
        evidence=evidence,
    )
    return first_structured


async def _raw_client_context(
    session: Any,
    *,
    client_id: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    structured = await _raw_invoke(
        session,
        name="get_client",
        arguments={"client_id": client_id},
        idempotency_key="",
        evidence=evidence,
    )
    client = _mapping_for_entity(structured, client_id)
    if not isinstance(client, dict) or not str(client.get("updated_at") or ""):
        raise AttestationError(
            "raw_client_exact_reread_missing",
            classification="verification",
            evidence=evidence,
        )
    return client


async def _raw_search_client(
    session: Any,
    *,
    query: str,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    structured = await _raw_invoke(
        session,
        name="search_clients",
        arguments={"query": query, "limit": 10},
        idempotency_key="",
        evidence=evidence,
    )
    return [
        mapping
        for mapping in _walk_mappings(structured)
        if str(mapping.get("id") or "")
        and ("display_name" in mapping or "last_name" in mapping or "legal_name" in mapping)
    ]


def _register_raw_entity(
    state: dict[str, Any],
    *,
    registry_name: str,
    entity_id: str,
    case_id: str,
) -> None:
    entries = _synthetic_entities(state)[registry_name]
    if any(str(item.get("id") or "") == entity_id for item in entries):
        return
    entries.append({"id": entity_id, "case_id": case_id, "status": "active"})


def _set_raw_entity_status(
    state: dict[str, Any],
    *,
    registry_name: str,
    entity_id: str,
    status: str,
) -> None:
    for item in _synthetic_entities(state)[registry_name]:
        if str(item.get("id") or "") == entity_id:
            item["status"] = status


async def _raw_create_client_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    prefix = str(state["refs"]["synthetic_prefix"])
    display_name = f"{prefix}-client"
    arguments = {
        "client": {
            "client_type": "person",
            "display_name": display_name,
            "comment": f"{prefix} isolated Gateway attestation fixture",
        }
    }
    schema_hash, risk = await _raw_contract(
        session,
        name=spec.target,
        evidence=evidence,
    )
    if risk != "write":
        raise AttestationError(
            "raw_create_client_risk_invalid",
            classification="policy",
            evidence=evidence,
        )
    await _raw_common_write_gates(
        session,
        spec=spec,
        state=state,
        schema_hash=schema_hash,
        valid_arguments=arguments,
        invalid_arguments={"client": {}},
        evidence=evidence,
    )

    client_id = str(state["refs"].get("raw_client_id") or "")
    if not client_id:
        existing = [
            item
            for item in await _raw_search_client(
                session,
                query=display_name,
                evidence=evidence,
            )
            if str(item.get("display_name") or "") == display_name
        ]
        if len(existing) > 1:
            raise AttestationError(
                "raw_create_client_duplicate_fixture_detected",
                classification="backend_effect",
                evidence=evidence,
            )
        if existing:
            client_id = str(existing[0]["id"])
            state["refs"]["raw_client_id"] = client_id
            _register_raw_entity(
                state,
                registry_name="clients",
                entity_id=client_id,
                case_id=spec.case_id,
            )
    applied = await _raw_apply_and_replay(
        session,
        name=spec.target,
        arguments=arguments,
        schema_hash=schema_hash,
        idempotency_key=f"{prefix}-raw-create-client"[:160],
        expected_check="exact_created_client_readback",
        evidence=evidence,
        already_applied=bool(client_id),
    )
    if not client_id:
        client_id = _first_entity_id(applied, ("client_id", "id"))
        if not client_id:
            raise AttestationError(
                "raw_create_client_id_missing",
                classification="backend_effect",
                evidence=evidence,
            )
        state["refs"]["raw_client_id"] = client_id
        _register_raw_entity(
            state,
            registry_name="clients",
            entity_id=client_id,
            case_id=spec.case_id,
        )
    client = await _raw_client_context(
        session,
        client_id=client_id,
        evidence=evidence,
    )
    if (
        str(client.get("display_name") or "") != display_name
        or str(client.get("comment") or "") != f"{prefix} isolated Gateway attestation fixture"
    ):
        raise AttestationError(
            "raw_create_client_exact_readback_mismatch",
            classification="backend_effect",
            evidence=evidence,
        )
    matches = await _raw_search_client(
        session,
        query=display_name,
        evidence=evidence,
    )
    if {
        str(item.get("id") or "")
        for item in matches
        if str(item.get("display_name") or "") == display_name
    } != {client_id}:
        raise AttestationError(
            "raw_create_client_search_readback_mismatch",
            classification="verification",
            evidence=evidence,
        )
    if _contains_binary_field(applied):
        raise AttestationError(
            "raw_create_client_binary_payload_leak",
            classification="privacy_payload",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="raw_create_client_response_payload_limit_exceeded",
    )
    return evidence


async def _raw_create_card_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    prefix = str(state["refs"]["synthetic_prefix"])
    title = f"{prefix}-raw-card"
    arguments = {
        "title": title,
        "vehicle": "AutoStop Synthetic",
        "description": f"{prefix} isolated raw create_card fixture",
        "deadline": {"total_seconds": 60},
        "tags": [{"label": "AST-GWAT", "color": "green"}],
    }
    schema_hash, risk = await _raw_contract(
        session,
        name=spec.target,
        evidence=evidence,
    )
    if risk != "write":
        raise AttestationError(
            "raw_create_card_risk_invalid",
            classification="policy",
            evidence=evidence,
        )
    await _raw_common_write_gates(
        session,
        spec=spec,
        state=state,
        schema_hash=schema_hash,
        valid_arguments=arguments,
        invalid_arguments={},
        evidence=evidence,
    )

    card_id = str(state["refs"].get("raw_card_id") or "")
    if not card_id:
        searched, search_evidence = await _attested_call(
            session,
            "agent_search",
            {
                "entity": "card",
                "query": title,
                "limit": 10,
                "include_archived": True,
            },
        )
        evidence.append(search_evidence)
        existing = [
            mapping
            for mapping in _walk_mappings(_structured(searched))
            if str(mapping.get("id") or "") and str(mapping.get("title") or "") == title
        ]
        if len(existing) > 1:
            raise AttestationError(
                "raw_create_card_duplicate_fixture_detected",
                classification="backend_effect",
                evidence=evidence,
            )
        if existing:
            card_id = str(existing[0]["id"])
            state["refs"]["raw_card_id"] = card_id
            _register_raw_entity(
                state,
                registry_name="cards",
                entity_id=card_id,
                case_id=spec.case_id,
            )
    applied = await _raw_apply_and_replay(
        session,
        name=spec.target,
        arguments=arguments,
        schema_hash=schema_hash,
        idempotency_key=f"{prefix}-raw-create-card"[:160],
        expected_check="exact_created_card_readback",
        evidence=evidence,
        already_applied=bool(card_id),
    )
    if not card_id:
        card_id = _first_entity_id(applied, ("card_id", "id"))
        if not card_id:
            raise AttestationError(
                "raw_create_card_id_missing",
                classification="backend_effect",
                evidence=evidence,
            )
        state["refs"]["raw_card_id"] = card_id
        _register_raw_entity(
            state,
            registry_name="cards",
            entity_id=card_id,
            case_id=spec.case_id,
        )
    card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
    )
    if (
        str(card.get("title") or "") != title
        or str(card.get("vehicle") or "") != arguments["vehicle"]
        or str(card.get("description") or "") != arguments["description"]
    ):
        raise AttestationError(
            "raw_create_card_exact_readback_mismatch",
            classification="backend_effect",
            evidence=evidence,
        )
    if _contains_binary_field(applied):
        raise AttestationError(
            "raw_create_card_binary_payload_leak",
            classification="privacy_payload",
            evidence=evidence,
        )
    _assert_response_budget(
        evidence,
        code="raw_create_card_response_payload_limit_exceeded",
    )
    return evidence


async def _raw_link_card_to_client_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    prefix = str(state["refs"]["synthetic_prefix"])
    attempt = _attempt_number(state, spec.case_id)
    client_id = str(state["refs"].get("raw_client_id") or "")
    card_id = str(state["refs"].get("raw_card_id") or "")
    if not client_id or not card_id:
        raise AttestationError(
            "raw_link_fixture_refs_missing",
            classification="routing",
        )
    client = await _raw_client_context(
        session,
        client_id=client_id,
        evidence=evidence,
    )
    card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
    )
    if str(card.get("client_id") or "") not in {"", client_id}:
        raise AttestationError(
            "raw_link_card_already_linked_outside_fixture",
            classification="backend_effect",
            evidence=evidence,
        )
    arguments = {
        "card_id": card_id,
        "client_id": client_id,
        "expected_card_updated_at": str(card["updated_at"]),
        "expected_client_updated_at": str(client["updated_at"]),
        "sync_fields": False,
        "sync_vehicle_fields": False,
        "overwrite_card_fields": False,
    }
    schema_hash, risk = await _raw_contract(
        session,
        name=spec.target,
        evidence=evidence,
    )
    if risk != "write":
        raise AttestationError(
            "raw_link_card_to_client_risk_invalid",
            classification="policy",
            evidence=evidence,
        )
    await _raw_common_write_gates(
        session,
        spec=spec,
        state=state,
        schema_hash=schema_hash,
        valid_arguments=arguments,
        invalid_arguments={
            **arguments,
            "card_id": f"{prefix}-missing-card",
            "client_id": f"{prefix}-missing-client",
        },
        evidence=evidence,
    )
    await _raw_expected_failure(
        session,
        name=spec.target,
        arguments={"card_id": card_id, "client_id": client_id},
        schema_hash=schema_hash,
        idempotency_key=f"{prefix}-raw-link-missing-revisions-a{attempt}"[:160],
        expected_code=("card_client_link_expected_revisions_required_reread_exact_targets_first"),
        evidence=evidence,
    )
    if str(card.get("client_id") or "") != client_id:
        await _raw_expected_failure(
            session,
            name=spec.target,
            arguments={
                **arguments,
                "expected_card_updated_at": "2000-01-01T00:00:00+00:00",
            },
            schema_hash=schema_hash,
            idempotency_key=f"{prefix}-raw-link-stale-card-a{attempt}"[:160],
            expected_code="raw_capability_failed",
            evidence=evidence,
        )
        await _raw_expected_failure(
            session,
            name=spec.target,
            arguments={
                **arguments,
                "expected_client_updated_at": "2000-01-01T00:00:00+00:00",
            },
            schema_hash=schema_hash,
            idempotency_key=f"{prefix}-raw-link-stale-client-a{attempt}"[:160],
            expected_code="raw_capability_failed",
            evidence=evidence,
        )
        card_after_conflicts = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
        )
        client_after_conflicts = await _raw_client_context(
            session,
            client_id=client_id,
            evidence=evidence,
        )
        if (
            str(card_after_conflicts.get("updated_at") or "") != str(card.get("updated_at") or "")
            or str(card_after_conflicts.get("client_id") or "")
            or str(client_after_conflicts.get("updated_at") or "")
            != str(client.get("updated_at") or "")
        ):
            raise AttestationError(
                "raw_link_conflict_changed_backend",
                classification="backend_effect",
                evidence=evidence,
            )
    already_applied = str(card.get("client_id") or "") == client_id
    await _raw_apply_and_replay(
        session,
        name=spec.target,
        arguments=arguments,
        schema_hash=schema_hash,
        idempotency_key=f"{prefix}-raw-link-card-client"[:160],
        expected_check="exact_card_client_link_readback",
        evidence=evidence,
        already_applied=already_applied,
    )
    linked_card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
    )
    linked_client = await _raw_client_context(
        session,
        client_id=client_id,
        evidence=evidence,
    )
    if (
        str(linked_card.get("client_id") or "") != client_id
        or str(linked_client.get("id") or "") != client_id
    ):
        raise AttestationError(
            "raw_link_card_client_exact_readback_mismatch",
            classification="backend_effect",
            evidence=evidence,
        )

    delete_schema_hash, delete_risk = await _raw_contract(
        session,
        name="delete_client",
        evidence=evidence,
    )
    if delete_risk != "destructive":
        raise AttestationError(
            "raw_delete_client_cleanup_risk_invalid",
            classification="policy",
            evidence=evidence,
        )
    delete_payload = _raw_call_payload(
        name="delete_client",
        arguments={
            "client_id": client_id,
            "allow_linked": True,
        },
        schema_hash=delete_schema_hash,
        idempotency_key=f"{prefix}-raw-delete-client-cleanup"[:160],
    )
    deleted, deleted_evidence = await _attested_call(
        session,
        "call_raw_capability",
        delete_payload,
    )
    evidence.append(deleted_evidence)
    if not _contains_scalar(_structured(deleted), "executor_contract_only"):
        raise AttestationError(
            "raw_delete_client_cleanup_gateway_result_invalid",
            classification="verification",
            evidence=evidence,
        )
    delete_replay, delete_replay_evidence = await _attested_call(
        session,
        "call_raw_capability",
        delete_payload,
    )
    evidence.append(delete_replay_evidence)
    _assert_workflow_replay(
        delete_replay,
        code_prefix="raw_delete_client_cleanup",
        evidence=evidence,
    )
    remaining_clients = await _raw_search_client(
        session,
        query=f"{prefix}-client",
        evidence=evidence,
    )
    if any(str(item.get("id") or "") == client_id for item in remaining_clients):
        raise AttestationError(
            "raw_client_cleanup_absence_not_proven",
            classification="backend_effect",
            evidence=evidence,
        )
    unlinked_card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
    )
    if str(unlinked_card.get("client_id") or ""):
        raise AttestationError(
            "raw_card_cleanup_unlink_not_proven",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_raw_entity_status(
        state,
        registry_name="clients",
        entity_id=client_id,
        status="deleted",
    )
    state["refs"]["raw_client_id"] = ""
    await _cleanup_synthetic_board_card(
        session,
        spec=spec,
        state=state,
        card_id=card_id,
        evidence=evidence,
    )
    state["refs"]["raw_card_id"] = ""
    _assert_response_budget(
        evidence,
        code="raw_link_card_client_response_payload_limit_exceeded",
    )
    return evidence


async def _raw_case(
    session: Any,
    *,
    spec: CaseSpec,
    _state: dict[str, Any],
    apply_synthetic: bool,
) -> list[dict[str, Any]]:
    if not apply_synthetic:
        raise AttestationError("synthetic_apply_flag_required", classification="policy")
    if spec.target == "create_client":
        return await _raw_create_client_case(
            session,
            spec=spec,
            state=_state,
        )
    if spec.target == "create_card":
        return await _raw_create_card_case(
            session,
            spec=spec,
            state=_state,
        )
    if spec.target == "link_card_to_client":
        return await _raw_link_card_to_client_case(
            session,
            spec=spec,
            state=_state,
        )
    raise AttestationError(
        f"synthetic_executor_not_implemented_{spec.target}",
        classification="routing",
    )


async def _execute_case(
    session: Any,
    *,
    spec: CaseSpec,
    state: dict[str, Any],
    apply_synthetic: bool,
) -> list[dict[str, Any]]:
    if spec.kind == "public_tool":
        return await _public_case(session, target=spec.target, state=state)
    if spec.kind == "operation":
        return await _operation_case(
            session,
            spec=spec,
            state=state,
            apply_synthetic=apply_synthetic,
        )
    if spec.kind == "raw_capability":
        return await _raw_case(
            session,
            spec=spec,
            _state=state,
            apply_synthetic=apply_synthetic,
        )
    raise AttestationError("unknown_attestation_case_kind")


def _find_case(state: dict[str, Any], case_id: str) -> dict[str, Any]:
    for item in state.get("cases", []):
        if item.get("case_id") == case_id:
            return item
    raise AttestationError("attestation_case_not_found")


def _next_case(state: dict[str, Any]) -> dict[str, Any]:
    for item in state.get("cases", []):
        if item.get("status") == "pending":
            return item
    raise AttestationError("no_pending_attestation_cases")


def _case_spec(item: dict[str, Any]) -> CaseSpec:
    fields = {
        key: item[key]
        for key in (
            "case_id",
            "family",
            "target",
            "kind",
            "requires_apply",
            "operation",
            "workflow_tool",
        )
    }
    return CaseSpec(**fields)


async def _run_one_case(
    session: Any,
    *,
    state: dict[str, Any],
    item: dict[str, Any],
    state_path: Path,
    apply_synthetic: bool,
) -> dict[str, Any]:
    if state.get("status") == "blocked_on_command" and item.get("status") != "blocked":
        raise AttestationError("attestation_blocked_on_other_case", classification="policy")
    spec = _case_spec(item)
    item["attempts"] = int(item.get("attempts") or 0) + 1
    item["started_at"] = _utc_now()
    item["status"] = "running"
    state["status"] = "running"
    state["current_case_id"] = spec.case_id
    state["updated_at"] = _utc_now()
    _atomic_json_write(state_path, state)
    try:
        evidence = await _execute_case(
            session,
            spec=spec,
            state=state,
            apply_synthetic=apply_synthetic,
        )
    except AttestationError as exc:
        item["status"] = "blocked"
        item["finished_at"] = _utc_now()
        item["error_code"] = exc.code
        item["classification"] = exc.classification
        item["last_evidence"] = exc.evidence
        state["ok"] = False
        state["status"] = "blocked_on_command"
        state["blocked"] = {
            "case_id": spec.case_id,
            "error_code": exc.code,
            "classification": exc.classification,
        }
    else:
        item["status"] = "passed"
        item["finished_at"] = _utc_now()
        item["error_code"] = ""
        item["classification"] = ""
        item["last_evidence"] = evidence
        state["ok"] = True
        state["status"] = "ready"
        state["blocked"] = None
    state["current_case_id"] = None
    state["summary"] = _state_summary(state)
    if state["summary"]["pending"] == 0 and state["summary"]["blocked"] == 0:
        state["status"] = "completed_pending_cleanup"
    state["updated_at"] = _utc_now()
    _atomic_json_write(state_path, state)
    return state


def _payment_amount_minor(payment: dict[str, Any]) -> int:
    direct = payment.get("amount_minor")
    if isinstance(direct, int) and not isinstance(direct, bool):
        return direct
    try:
        return int(
            (
                Decimal(str(payment.get("amount") or "0").replace(",", ".")) * Decimal("100")
            ).to_integral_value()
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _cash_transaction_effect_minor(rows: list[dict[str, Any]]) -> int:
    effect = 0
    for row in rows:
        amount_minor = int(row.get("amount_minor") or 0)
        direction = str(row.get("direction") or "")
        if direction == "income":
            effect += amount_minor
        elif direction == "expense":
            effect -= amount_minor
        else:
            raise AttestationError(
                "cleanup_cash_transaction_direction_invalid",
                classification="verification",
            )
    return effect


async def _cleanup_payment_fixture(
    session: Any,
    *,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, int]:
    prefix = str(state["refs"]["synthetic_prefix"])
    case = _find_case(state, "operation:finance:record_repair_order_payment")
    spec = _case_spec(case)
    attempt = max(1, int(case.get("attempts") or 1))
    card_id = str(state["refs"].get("synthetic_payment_card_id") or "")
    payment_id = str(state["refs"].get("synthetic_payment_id") or "")
    if not card_id:
        raise AttestationError(
            "cleanup_payment_card_ref_missing",
            classification="routing",
            evidence=evidence,
        )
    card = await _card_context(
        session,
        card_id=card_id,
        evidence=evidence,
        include_archived=None,
        detail="full",
    )
    removed_effect_minor = 0
    removed_transaction_count = 0
    if not bool(card.get("archived")) and _synthetic_repair_order_needs_cleanup(card):
        repair_order = (
            card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
        )
        matching_payments = [
            item
            for item in repair_order.get("payments") or []
            if isinstance(item, dict) and str(item.get("id") or "") == payment_id
        ]
        if (
            len(matching_payments) != 1
            or _payment_amount_minor(matching_payments[0]) != 100
            or not str(matching_payments[0].get("note") or "").startswith(prefix)
        ):
            raise AttestationError(
                "cleanup_payment_exact_fixture_invalid",
                classification="verification",
                evidence=evidence,
            )
        payment = matching_payments[0]
        cashbox_id = str(payment.get("cashbox_id") or "")
        payment_transaction_id = str(payment.get("cash_transaction_id") or "")
        card_revision = str(card.get("updated_at") or "")
        if not cashbox_id or not payment_transaction_id or not card_revision:
            raise AttestationError(
                "cleanup_payment_target_refs_missing",
                classification="verification",
                evidence=evidence,
            )
        cashbox, cashbox_structured = await _finance_cashbox_context(
            session,
            cashbox_id=cashbox_id,
            evidence=evidence,
        )
        scoped_rows = [
            item
            for item in _cashbox_transaction_rows(
                cashbox_structured,
                cashbox_id,
            )
            if prefix in str(item.get("note") or "")
        ]
        scoped_ids = sorted(str(item.get("id") or "") for item in scoped_rows)
        removed_effect_minor = _cash_transaction_effect_minor(scoped_rows)
        if (
            len(scoped_rows) != 3
            or len(set(scoped_ids)) != 3
            or payment_transaction_id not in scoped_ids
            or any(int(item.get("amount_minor") or 0) != 100 for item in scoped_rows)
            or removed_effect_minor != 100
        ):
            raise AttestationError(
                "cleanup_payment_cashbox_snapshot_invalid",
                classification="verification",
                evidence=evidence,
            )
        cashbox_revision = str(cashbox.get("updated_at") or "")
        balance_before = int((cashbox.get("statistics") or {}).get("balance_minor") or 0)
        if not cashbox_revision:
            raise AttestationError(
                "cleanup_payment_cashbox_revision_missing",
                classification="verification",
                evidence=evidence,
            )
        schema_hash, risk = await _raw_contract(
            session,
            name="api:/api/delete_gateway_attestation_payment_fixture",
            evidence=evidence,
        )
        if risk != "destructive":
            raise AttestationError(
                "cleanup_payment_capability_risk_invalid",
                classification="policy",
                evidence=evidence,
            )
        await _raw_apply_and_replay(
            session,
            name="api:/api/delete_gateway_attestation_payment_fixture",
            arguments={
                "card_id": card_id,
                "payment_id": payment_id,
                "expected_updated_at": card_revision,
                "expected_cashbox_updated_at": cashbox_revision,
                "expected_transaction_ids": scoped_ids,
                "attestation_run_id": prefix,
            },
            schema_hash=schema_hash,
            idempotency_key=(f"{prefix}-global-cleanup-payment-a{attempt}")[:160],
            expected_check=("exact_attestation_payment_fixture_absence_readback"),
            evidence=evidence,
        )
        card = await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            detail="full",
        )
        cashbox_after, cashbox_after_structured = await _finance_cashbox_context(
            session,
            cashbox_id=cashbox_id,
            evidence=evidence,
        )
        remaining_ids = {
            str(item.get("id") or "")
            for item in _cashbox_transaction_rows(
                cashbox_after_structured,
                cashbox_id,
            )
        }
        balance_after = int((cashbox_after.get("statistics") or {}).get("balance_minor") or 0)
        if (
            _synthetic_repair_order_needs_cleanup(card)
            or not set(scoped_ids).isdisjoint(remaining_ids)
            or balance_before - balance_after != 100
        ):
            raise AttestationError(
                "cleanup_payment_exact_reread_failed",
                classification="backend_effect",
                evidence=evidence,
            )
        for transaction_id in scoped_ids:
            _register_cash_transaction(
                state,
                transaction_id=transaction_id,
                cashbox_id=cashbox_id,
                case_id=spec.case_id,
                status="deleted_cleanup",
            )
            _set_cash_transaction_registry_status(
                state,
                transaction_id=transaction_id,
                status="deleted_cleanup",
            )
        removed_transaction_count = len(scoped_ids)
        state["refs"]["synthetic_payment_transaction_id"] = ""
        state["refs"]["synthetic_payment_id"] = ""
    if not bool(card.get("archived")):
        if _synthetic_repair_order_needs_cleanup(card):
            raise AttestationError(
                "cleanup_payment_order_not_empty_before_archive",
                classification="backend_effect",
                evidence=evidence,
            )
        await _cleanup_synthetic_board_card(
            session,
            spec=spec,
            state=state,
            card_id=card_id,
            evidence=evidence,
        )
    state["refs"]["synthetic_payment_card_id"] = ""
    return {
        "removed_effect_minor": removed_effect_minor,
        "removed_transaction_count": removed_transaction_count,
    }


async def _cleanup_employee_fixture(
    session: Any,
    *,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> int:
    prefix = str(state["refs"]["synthetic_prefix"])
    case = _find_case(state, "operation:finance:create_employee_shift_accrual")
    spec = _case_spec(case)
    attempt = max(1, int(case.get("attempts") or 1))
    employee_id = str(state["refs"].get("synthetic_employee_id") or "")
    accrual_id = str(state["refs"].get("synthetic_shift_accrual_id") or "")
    if not employee_id or not accrual_id:
        raise AttestationError(
            "cleanup_employee_refs_missing",
            classification="routing",
            evidence=evidence,
        )
    employees = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="global-cleanup-before",
        evidence=evidence,
    )
    employee = next(
        (item for item in employees if str(item.get("id") or "") == employee_id),
        None,
    )
    if isinstance(employee, dict):
        if not str(employee.get("name") or "").startswith(f"{prefix}-") or not str(
            employee.get("updated_at") or ""
        ):
            raise AttestationError(
                "cleanup_employee_exact_fixture_invalid",
                classification="verification",
                evidence=evidence,
            )
        schema_hash, risk = await _raw_contract(
            session,
            name="api:/api/delete_employee",
            evidence=evidence,
        )
        if risk != "destructive":
            raise AttestationError(
                "cleanup_employee_capability_risk_invalid",
                classification="policy",
                evidence=evidence,
            )
        await _raw_apply_and_replay(
            session,
            name="api:/api/delete_employee",
            arguments={
                "employee_id": employee_id,
                "expected_updated_at": str(employee["updated_at"]),
                "attestation_run_id": prefix,
                "attestation_cleanup_shift_accrual_ids": [accrual_id],
            },
            schema_hash=schema_hash,
            idempotency_key=(f"{prefix}-global-cleanup-employee-a{attempt}")[:160],
            expected_check="exact_employee_absence_readback",
            evidence=evidence,
        )
    final_employees = await _finance_employee_snapshot(
        session,
        spec=spec,
        state=state,
        purpose="global-cleanup-after",
        evidence=evidence,
    )
    if any(str(item.get("id") or "") == employee_id for item in final_employees):
        raise AttestationError(
            "cleanup_employee_exact_reread_failed",
            classification="backend_effect",
            evidence=evidence,
        )
    _set_employee_registry_status(
        state,
        employee_id=employee_id,
        status="deleted_cleanup",
    )
    for item in _synthetic_entities(state)["shift_accruals"]:
        if str(item.get("id") or "") == accrual_id:
            item["status"] = "deleted_cleanup"
    state["refs"]["synthetic_employee_id"] = ""
    state["refs"]["synthetic_shift_accrual_id"] = ""
    return 1


async def _verify_global_cleanup(
    session: Any,
    *,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, int]:
    prefix = str(state["refs"]["synthetic_prefix"])
    registry = _synthetic_entities(state)
    for item in registry["cards"]:
        card_id = str(item.get("id") or "")
        if not card_id:
            raise AttestationError(
                "cleanup_card_registry_invalid",
                classification="verification",
                evidence=evidence,
            )
        await _card_context(
            session,
            card_id=card_id,
            evidence=evidence,
            include_archived=True,
        )
        item["status"] = "archived"

    employee_spec = _case_spec(_find_case(state, "operation:finance:create_employee_shift_accrual"))
    employees = await _finance_employee_snapshot(
        session,
        spec=employee_spec,
        state=state,
        purpose="global-cleanup-verify",
        evidence=evidence,
    )
    if any(str(item.get("name") or "").startswith(f"{prefix}-") for item in employees):
        raise AttestationError(
            "cleanup_synthetic_employee_residual",
            classification="backend_effect",
            evidence=evidence,
        )

    cashbox_spec = _case_spec(_find_case(state, "operation:finance:delete_cashbox"))
    cashboxes = await _finance_cashbox_snapshot(
        session,
        spec=cashbox_spec,
        state=state,
        purpose="global-cleanup-verify",
        evidence=evidence,
    )
    if any(str(item.get("name") or "").startswith(f"{prefix}-") for item in cashboxes):
        raise AttestationError(
            "cleanup_synthetic_cashbox_residual",
            classification="backend_effect",
            evidence=evidence,
        )

    client_matches = await _raw_search_client(
        session,
        query=prefix,
        evidence=evidence,
    )
    if any(
        str(item.get("display_name") or "").startswith(prefix)
        or str(item.get("last_name") or "").startswith(prefix)
        or str(item.get("legal_name") or "").startswith(prefix)
        for item in client_matches
    ):
        raise AttestationError(
            "cleanup_synthetic_client_residual",
            classification="backend_effect",
            evidence=evidence,
        )

    document_spec = _case_spec(_find_case(state, "operation:document:list_shared_files"))
    files_result, files_evidence = await _attested_call(
        session,
        document_spec.workflow_tool,
        {
            "operation": "list_shared_files",
            "payload": {},
            "idempotency_key": f"{prefix}-global-cleanup-files"[:160],
            "allow_large_output": False,
        },
    )
    evidence.append(files_evidence)
    if any(
        str(mapping.get("original_name") or "").startswith(prefix)
        for mapping in _walk_mappings(_structured(files_result))
    ):
        raise AttestationError(
            "cleanup_synthetic_file_residual",
            classification="backend_effect",
            evidence=evidence,
        )

    inventory_spec = _case_spec(_find_case(state, "operation:inventory:save_inventory_item"))
    inventory_id = str(state["refs"].get("synthetic_inventory_item_id") or "")
    if not inventory_id:
        raise AttestationError(
            "cleanup_inventory_ref_missing",
            classification="routing",
            evidence=evidence,
        )
    inventory_item, _ = await _inventory_item_context(
        session,
        spec=inventory_spec,
        state=state,
        item_id=inventory_id,
        purpose="global-cleanup-verify",
        evidence=evidence,
    )
    if _inventory_decimal(inventory_item.get("quantity")) != Decimal("0") or not str(
        inventory_item.get("name") or ""
    ).startswith(prefix):
        raise AttestationError(
            "cleanup_inventory_compensation_invalid",
            classification="backend_effect",
            evidence=evidence,
        )
    for item in registry["inventory_items"]:
        if str(item.get("id") or "") == inventory_id:
            item["status"] = "compensated"

    workflow_run_id = int(state["refs"].get("workflow_run_id") or 0)
    if workflow_run_id < 1:
        raise AttestationError(
            "cleanup_workflow_ref_missing",
            classification="routing",
            evidence=evidence,
        )
    workflow_result, workflow_evidence = await _attested_call(
        session,
        "workflow_status",
        {"run_id": workflow_run_id},
    )
    evidence.append(workflow_evidence)
    workflow_payload = _structured(workflow_result)
    if str(workflow_payload.get("status") or "") not in {
        "cancelled",
        "completed",
        "failed",
    }:
        raise AttestationError(
            "cleanup_workflow_not_terminal",
            classification="backend_effect",
            evidence=evidence,
        )

    allowed_statuses = {
        "cards": {"archived"},
        "clients": {"deleted", "deleted_cleanup"},
        "inventory_items": {"compensated"},
        "files": {"deleted", "deleted_cleanup"},
        "cashboxes": {"deleted_cleanup"},
        "cash_transactions": {"deleted_cleanup"},
        "employees": {"deleted_cleanup"},
        "shift_accruals": {"deleted_cleanup"},
    }
    counts: dict[str, int] = {}
    for kind, allowed in allowed_statuses.items():
        rows = registry[kind]
        if any(str(item.get("status") or "") not in allowed for item in rows):
            raise AttestationError(
                f"cleanup_registry_nonterminal_{kind}",
                classification="verification",
                evidence=evidence,
            )
        counts[kind] = len(rows)
    return counts


async def _run_cleanup(
    session: Any,
    *,
    state: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    summary = _state_summary(state)
    if (
        summary.get("total") != len(build_case_specs())
        or summary.get("passed") != summary.get("total")
        or summary.get("pending")
        or summary.get("blocked")
    ):
        raise AttestationError(
            "cleanup_requires_all_cases_passed",
            classification="policy",
        )
    cleanup = state.get("cleanup")
    if isinstance(cleanup, dict) and cleanup.get("status") == "completed":
        state["status"] = "completed"
        state["ok"] = True
        return state
    evidence: list[dict[str, Any]] = []
    state["status"] = "cleanup_running"
    state["ok"] = False
    state["blocked"] = None
    state["cleanup"] = {
        "status": "running",
        "verified": False,
        "started_at": _utc_now(),
    }
    state["updated_at"] = _utc_now()
    _atomic_json_write(state_path, state)
    try:
        payment = await _cleanup_payment_fixture(
            session,
            state=state,
            evidence=evidence,
        )
        state["cleanup"]["payment"] = payment
        state["cleanup"]["last_evidence"] = list(evidence)
        state["updated_at"] = _utc_now()
        _atomic_json_write(state_path, state)

        removed_shift_accruals = await _cleanup_employee_fixture(
            session,
            state=state,
            evidence=evidence,
        )
        state["cleanup"]["removed_shift_accruals"] = removed_shift_accruals
        state["cleanup"]["last_evidence"] = list(evidence)
        state["updated_at"] = _utc_now()
        _atomic_json_write(state_path, state)

        counts = await _verify_global_cleanup(
            session,
            state=state,
            evidence=evidence,
        )
    except AttestationError as exc:
        state["ok"] = False
        state["status"] = "blocked_on_command"
        state["blocked"] = {
            "case_id": "cleanup",
            "error_code": exc.code,
            "classification": exc.classification,
        }
        state["cleanup"] = {
            **(state.get("cleanup") if isinstance(state.get("cleanup"), dict) else {}),
            "status": "blocked",
            "verified": False,
            "error_code": exc.code,
            "classification": exc.classification,
            "last_evidence": list(evidence or exc.evidence),
        }
    else:
        state["ok"] = True
        state["status"] = "completed"
        state["blocked"] = None
        state["cleanup"] = {
            **state["cleanup"],
            "status": "completed",
            "verified": True,
            "completed_at": _utc_now(),
            "entity_counts": counts,
            "limitations": [
                "archived_synthetic_cards_retained_by_crm_audit_policy",
                "zero_quantity_synthetic_inventory_item_retained_no_delete_capability",
                "immutable_synthetic_audit_events_retained_with_run_prefix",
            ],
            "last_evidence": list(evidence),
        }
    state["summary"] = summary
    state["updated_at"] = _utc_now()
    _atomic_json_write(state_path, state)
    return state


def _safe_summary(state: dict[str, Any]) -> dict[str, Any]:
    cleanup = state.get("cleanup") if isinstance(state.get("cleanup"), dict) else {}
    compact_cleanup = {
        key: cleanup[key]
        for key in (
            "status",
            "verified",
            "started_at",
            "completed_at",
            "payment",
            "removed_shift_accruals",
            "entity_counts",
            "limitations",
            "error_code",
            "classification",
        )
        if key in cleanup
    }
    return {
        "ok": state.get("status") in {"ready", "completed_pending_cleanup", "completed"},
        "format": ATTESTATION_FORMAT,
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "manifest_sha256": state.get("manifest_sha256"),
        "summary": state.get("summary"),
        "current_case_id": state.get("current_case_id"),
        "blocked": state.get("blocked"),
        "cleanup": compact_cleanup,
        "data_included": False,
    }


def _nested_attestation_error(exc: BaseException) -> AttestationError | None:
    if isinstance(exc, AttestationError):
        return exc
    nested = getattr(exc, "exceptions", None)
    if isinstance(nested, tuple):
        for item in nested:
            if isinstance(item, BaseException):
                found = _nested_attestation_error(item)
                if found is not None:
                    return found
    return None


async def _refresh_manifest_after_release(
    session: Any,
    *,
    args: argparse.Namespace,
    state: dict[str, Any],
    state_path: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    anonymous_blocked: bool,
    anonymous_status: int,
) -> dict[str, Any]:
    allowed = {
        str(item or "").strip()
        for item in (args.allow_raw_schema_change or [])
        if str(item or "").strip()
    }
    if not allowed or not allowed.issubset(set(MANAGER_RAW_CRM_CAPABILITIES)):
        raise AttestationError(
            "manifest_refresh_allowed_raw_schema_invalid",
            classification="schema",
        )
    release_revision = str(args.release_revision or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{7,64}", release_revision):
        raise AttestationError(
            "manifest_refresh_release_revision_invalid",
            classification="schema",
        )
    live = await _build_live_manifest(
        session,
        anonymous_blocked=anonymous_blocked,
        anonymous_status=anonymous_status,
    )
    for key in (
        "format",
        "tool_count",
        "tools",
        "operation_count",
        "operations",
        "case_ids",
    ):
        if live.get(key) != manifest.get(key):
            raise AttestationError(
                f"manifest_refresh_unexpected_{key}_drift",
                classification="schema",
            )
    old_raw = {
        str(item.get("name") or ""): item
        for item in manifest.get("manager_raw_crm_capabilities") or []
        if isinstance(item, dict)
    }
    live_raw = {
        str(item.get("name") or ""): item
        for item in live.get("manager_raw_crm_capabilities") or []
        if isinstance(item, dict)
    }
    if set(old_raw) != set(MANAGER_RAW_CRM_CAPABILITIES) or set(live_raw) != set(old_raw):
        raise AttestationError(
            "manifest_refresh_raw_capability_set_drift",
            classification="schema",
        )
    changed = {
        name
        for name in old_raw
        if old_raw[name].get("schema_hash") != live_raw[name].get("schema_hash")
        or old_raw[name].get("schema_sha256") != live_raw[name].get("schema_sha256")
        or old_raw[name].get("risk") != live_raw[name].get("risk")
    }
    if changed != allowed:
        raise AttestationError(
            "manifest_refresh_raw_schema_change_set_mismatch",
            classification="schema",
        )
    if any(old_raw[name].get("risk") != live_raw[name].get("risk") for name in changed):
        raise AttestationError(
            "manifest_refresh_raw_risk_change_forbidden",
            classification="policy",
        )
    old_manifest_hash = str(state.get("manifest_sha256") or "")
    refreshed = dict(manifest)
    refreshed["manager_raw_crm_capabilities"] = [
        live_raw[str(item["name"])] for item in manifest["manager_raw_crm_capabilities"]
    ]
    new_manifest_hash = _sha256(refreshed)
    state.setdefault("manifest_revisions", []).append(
        {
            "changed_at": _utc_now(),
            "release_revision": release_revision,
            "raw_capabilities": sorted(changed),
            "old_manifest_sha256": old_manifest_hash,
            "new_manifest_sha256": new_manifest_hash,
            "old_schema_hashes": {
                name: str(old_raw[name].get("schema_hash") or "") for name in sorted(changed)
            },
            "new_schema_hashes": {
                name: str(live_raw[name].get("schema_hash") or "") for name in sorted(changed)
            },
        }
    )
    state["manifest_sha256"] = new_manifest_hash
    state["manifest"] = refreshed
    state["updated_at"] = _utc_now()
    _atomic_json_write(manifest_path, refreshed)
    _atomic_json_write(state_path, state)
    return _safe_summary(state)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_static_contract()
    run_id = str(args.run_id or "").strip()
    if not RUN_ID_RE.fullmatch(run_id):
        raise AttestationError("run_id_format_invalid")
    output_root = Path(args.output_root).resolve()
    run_dir = output_root / run_id
    state_path = run_dir / "state.json"
    manifest_path = run_dir / "manifest.json"
    lock_path = run_dir / ".lock"
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.touch(mode=0o600, exist_ok=True)

    token = str(os.environ.get(args.token_env, "") or "").strip()
    action_needs_session = not args.summary
    if action_needs_session and not token:
        raise AttestationError("token_environment_missing", classification="transport_auth")

    with lock_path.open("r+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        if args.summary:
            return _safe_summary(_load_state(state_path))

        anonymous_blocked, anonymous_status = await _anonymous_access_probe(args.mcp_url)
        if not anonymous_blocked:
            raise AttestationError("anonymous_access_not_blocked", classification="transport_auth")
        http_client, transport = await _open_session(args.mcp_url, token)
        try:
            async with http_client:
                async with transport as (read, write, _):
                    from mcp import ClientSession

                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        if args.inventory:
                            if state_path.exists() and not args.force_inventory:
                                raise AttestationError("attestation_state_already_exists")
                            manifest = await _build_live_manifest(
                                session,
                                anonymous_blocked=anonymous_blocked,
                                anonymous_status=anonymous_status,
                            )
                            state = _new_state(
                                run_id=run_id,
                                mcp_url=args.mcp_url,
                                manifest=manifest,
                            )
                            _atomic_json_write(manifest_path, manifest)
                            _atomic_json_write(state_path, state)
                            return _safe_summary(state)

                        state = _load_state(state_path)
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if _sha256(manifest) != state.get("manifest_sha256"):
                            raise AttestationError(
                                "frozen_manifest_hash_mismatch", classification="schema"
                            )
                        if args.refresh_manifest:
                            return await _refresh_manifest_after_release(
                                session,
                                args=args,
                                state=state,
                                state_path=state_path,
                                manifest=manifest,
                                manifest_path=manifest_path,
                                anonymous_blocked=anonymous_blocked,
                                anonymous_status=anonymous_status,
                            )
                        await _assert_frozen_manifest_live(session, manifest)
                        if args.cleanup:
                            return _safe_summary(
                                await _run_cleanup(
                                    session,
                                    state=state,
                                    state_path=state_path,
                                )
                            )
                        if args.retry:
                            blocked = state.get("blocked")
                            if not isinstance(blocked, dict) or not blocked.get("case_id"):
                                raise AttestationError("no_blocked_case_to_retry")
                            item = _find_case(state, str(blocked["case_id"]))
                        elif args.case:
                            item = _find_case(state, str(args.case))
                            if item.get("status") == "passed" and not args.force_case:
                                raise AttestationError("attestation_case_already_passed")
                        else:
                            item = _next_case(state)
                        return _safe_summary(
                            await _run_one_case(
                                session,
                                state=state,
                                item=item,
                                state_path=state_path,
                                apply_synthetic=bool(args.apply_synthetic),
                            )
                        )
        except AttestationError:
            raise
        except Exception as exc:
            nested = _nested_attestation_error(exc)
            if nested is not None:
                raise nested
            raise AttestationError(
                f"authorized_session_{type(exc).__name__.casefold()}",
                classification="transport_auth",
            ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stop-the-line per-command AutoStop CRM Gateway v2 attestation."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--inventory", action="store_true")
    actions.add_argument("--next", action="store_true")
    actions.add_argument("--resume", action="store_true")
    actions.add_argument("--case", default="")
    actions.add_argument("--retry", action="store_true")
    actions.add_argument("--cleanup", action="store_true")
    actions.add_argument("--refresh-manifest", action="store_true")
    actions.add_argument("--summary", action="store_true")
    parser.add_argument("--force-inventory", action="store_true")
    parser.add_argument("--force-case", action="store_true")
    parser.add_argument("--apply-synthetic", action="store_true")
    parser.add_argument("--allow-raw-schema-change", action="append", default=[])
    parser.add_argument("--release-revision", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except AttestationError as exc:
        result = {
            "ok": False,
            "format": ATTESTATION_FORMAT,
            "run_id": args.run_id,
            "status": "blocked_on_command",
            "blocked": {
                "case_id": str(args.case or "inventory"),
                "error_code": exc.code,
                "classification": exc.classification,
            },
            "data_included": False,
        }
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
