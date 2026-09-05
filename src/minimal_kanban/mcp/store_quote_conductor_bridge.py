from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.types import CallToolResult

from ..deployment_security import is_maintenance_mode
from .agent_gateway_support import _envelope, _policy_error
from .gateway_media import tool_result
from .store_gateway import (
    STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
    STORE_QUOTE_CONDUCTOR_LEGACY_DIALOGUE_OPERATIONS,
    store_quote_conductor_arguments,
    store_quote_conductor_safe_projection,
    store_quote_conductor_safe_verification,
    store_quote_conductor_safe_warnings,
    validate_store_quote_conductor_request,
)
from .workflow_guards import workflow_error_result

StoreInvoker = Callable[[str, Mapping[str, Any]], Awaitable[dict[str, Any]]]


async def execute_store_quote_conductor(
    raw_tools: Mapping[str, Any],
    invoke_store: StoreInvoker,
    payload: dict[str, Any],
    idempotency_key: str,
    mode: str | None,
) -> CallToolResult:
    """Run the narrow public bridge to Manager's durable quote conductor."""

    if is_maintenance_mode():
        return workflow_error_result("inventory", "maintenance_mode_domain_writes_blocked")
    validation = validate_store_quote_conductor_request(
        payload,
        idempotency_key=idempotency_key,
        mode=mode,
    )
    if not bool(validation.get("passed")):
        return tool_result(
            _envelope(
                ok=False,
                status="blocked",
                summary={
                    "workflow_id": "inventory",
                    "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                    "conductor_operation": str(payload.get("operation") or "").strip(),
                    "missing_fields": list(validation.get("missing_fields") or []),
                },
                warnings=[str(validation.get("warning") or "store_quote_conductor_invalid")],
            ),
            label="inventory",
        )
    conductor_operation = str(validation["operation"])
    risk = "write" if bool(validation["is_store_write"]) else "read"
    policy_error = _policy_error(
        tool_name=STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
        risk=risk,
        arguments={"operation": conductor_operation},
    )
    if policy_error:
        return workflow_error_result("inventory", policy_error)
    effective_mode = str(validation["mode"])
    if conductor_operation in STORE_QUOTE_CONDUCTOR_LEGACY_DIALOGUE_OPERATIONS:
        return tool_result(
            _envelope(
                ok=False,
                status="unavailable",
                summary={
                    "workflow_id": "inventory",
                    "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                    "conductor_operation": conductor_operation,
                    "dialogue_workflow": "separate_telegram_workflow",
                },
                warnings=["store_quote_conductor_dialogue_workflow_required"],
                next_actions=["Use the separate Telegram workflow for customer dialogue."],
                meta={
                    "mode": effective_mode,
                    "dry_run": False,
                    "ledger_owned_by_named_workflow": True,
                    "refs_only": True,
                    "external_store_write": False,
                },
            ),
            label="inventory",
        )
    effective_idempotency_key = str(validation["idempotency_key"])
    arguments = store_quote_conductor_arguments(
        raw_tools,
        payload,
        idempotency_key=effective_idempotency_key,
        mode=effective_mode,
    )
    result = await invoke_store(STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME, arguments)
    source_status = str(result.get("status") or "").strip().casefold()
    status = (
        source_status
        if source_status and re.fullmatch(r"[a-z_]{2,64}", source_status) is not None
        else "completed"
        if bool(result.get("ok"))
        else "failed"
    )
    safe_data = store_quote_conductor_safe_projection(result)
    verification = store_quote_conductor_safe_verification(result)
    source_warnings = store_quote_conductor_safe_warnings(result)
    if not bool(result.get("ok")) and not source_warnings:
        source_warnings = ["store_quote_conductor_failed"]
    raw_run_id = result.get("run_id")
    run_id = raw_run_id if isinstance(raw_run_id, int) and raw_run_id > 0 else None
    summary: dict[str, Any] = {
        "workflow_id": "inventory",
        "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
        "conductor_operation": conductor_operation,
        "mode": effective_mode,
        "executor": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
        "risk": risk,
    }
    for key in ("phase", "state_version", "deduplicated", "idempotency_replay"):
        if key in safe_data:
            summary[key] = safe_data[key]
    return tool_result(
        _envelope(
            ok=bool(result.get("ok")),
            status=status,
            run_id=run_id,
            summary=summary,
            data=safe_data,
            verification=verification,
            warnings=source_warnings,
            next_actions=(
                []
                if bool(result.get("ok"))
                else ["workflow_status and reconcile the exact Store quote workflow"]
            ),
            meta={
                "mode": effective_mode,
                "dry_run": effective_mode == "dry_run",
                "refs_only": True,
                "external_store_write": bool(validation["is_store_write"]),
            },
        ),
        label="inventory",
    )
