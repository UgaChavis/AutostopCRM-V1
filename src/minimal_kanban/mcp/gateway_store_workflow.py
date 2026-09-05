from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.types import CallToolResult

from .store_gateway import (
    STORE_MANAGEMENT_CAPABILITY_NAME,
    STORE_MANAGEMENT_OPERATIONS,
    store_action_arguments,
    store_gateway_envelope,
    store_implicit_idempotency_key,
    store_management_requires_native_guard,
    validate_store_workflow_request,
)


@dataclass(frozen=True)
class StoreWorkflowHandling:
    is_store_operation: bool
    correlation_id: str = ""
    immediate_result: CallToolResult | None = None


async def handle_store_workflow(
    *,
    workflow_id: str,
    operation: str,
    payload: dict[str, Any],
    idempotency_key: str,
    mode: str | None,
    raw_tools: Mapping[str, Any],
    invoke: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    policy_error: Callable[..., str | None],
    workflow_error: Callable[..., CallToolResult],
    envelope_factory: Callable[..., dict[str, Any]],
    compact: Callable[..., Any],
    tool_result: Callable[..., CallToolResult],
) -> StoreWorkflowHandling:
    """Handle direct Store coordination without weakening guarded actions."""

    is_store_operation = workflow_id == "inventory" and operation in STORE_MANAGEMENT_OPERATIONS
    low_risk = is_store_operation and not store_management_requires_native_guard(operation)
    supplied_key = str(idempotency_key or "").strip()
    if not supplied_key and not low_risk:
        return StoreWorkflowHandling(
            is_store_operation=is_store_operation,
            immediate_result=workflow_error(
                workflow_id, "idempotency_key_required", status="failed"
            ),
        )
    if not is_store_operation:
        return StoreWorkflowHandling(is_store_operation=False)

    request_validation = validate_store_workflow_request(
        operation,
        payload,
        idempotency_key=idempotency_key,
        mode=mode,
    )
    if not request_validation["passed"]:
        return StoreWorkflowHandling(
            is_store_operation=True,
            immediate_result=tool_result(
                envelope_factory(
                    ok=False,
                    status="blocked",
                    summary={
                        "workflow_id": workflow_id,
                        "operation": operation,
                        "missing_fields": request_validation.get("missing_fields", []),
                    },
                    warnings=[str(request_validation["warning"])],
                    next_actions=["agent_entity_context for the exact store target"]
                    if request_validation.get("missing_fields")
                    else [],
                ),
                label=workflow_id,
            ),
        )
    correlation_id = str(request_validation["correlation_id"])
    if not low_risk:
        return StoreWorkflowHandling(
            is_store_operation=True,
            correlation_id=correlation_id,
        )
    if STORE_MANAGEMENT_CAPABILITY_NAME not in raw_tools:
        return StoreWorkflowHandling(
            is_store_operation=True,
            correlation_id=correlation_id,
            immediate_result=workflow_error(
                workflow_id, "executor_capability_missing", status="failed"
            ),
        )
    policy_issue = policy_error(tool_name=operation, risk="write", arguments=payload)
    if policy_issue:
        return StoreWorkflowHandling(
            is_store_operation=True,
            correlation_id=correlation_id,
            immediate_result=workflow_error(workflow_id, policy_issue),
        )
    effective_mode = str(request_validation.get("mode") or mode or "apply")
    effective_key = supplied_key or store_implicit_idempotency_key(operation, payload)
    result = await invoke(
        STORE_MANAGEMENT_CAPABILITY_NAME,
        store_action_arguments(
            raw_tools,
            operation,
            payload,
            idempotency_key=effective_key,
            mode=effective_mode,
            correlation_id=correlation_id,
        ),
    )
    return StoreWorkflowHandling(
        is_store_operation=True,
        correlation_id=correlation_id,
        immediate_result=tool_result(
            store_gateway_envelope(
                result,
                summary={
                    "workflow_id": workflow_id,
                    "operation": operation,
                    "mode": effective_mode,
                    "executor": STORE_MANAGEMENT_CAPABILITY_NAME,
                    "risk": "write",
                    "direct_low_risk_update": True,
                },
                item_limit=10,
                envelope_factory=envelope_factory,
                compact=compact,
            ),
            label=workflow_id,
        ),
    )
