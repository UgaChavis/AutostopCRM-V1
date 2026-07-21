from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_MCP_URL = "http://127.0.0.1:41831/mcp"
DEFAULT_TOKEN_ENV = "MINIMAL_KANBAN_MCP_BEARER_TOKEN"
EXPECTED_TOOL_NAMES = frozenset(
    {
        "agent_board_digest",
        "agent_board_workflow",
        "agent_bootstrap",
        "agent_document_workflow",
        "agent_entity_context",
        "agent_finance_workflow",
        "agent_inventory_workflow",
        "agent_search",
        "call_raw_capability",
        "complete_external_step",
        "discover_raw_capabilities",
        "get_connector_identity",
        "get_raw_capability_schema",
        "get_runtime_status",
        "list_agent_workflows",
        "ping_connector",
        "prepare_action_contract",
        "start_workflow",
        "workflow_cancel",
        "workflow_checkpoint",
        "workflow_resume",
        "workflow_status",
        "workflow_transition",
        "workflow_wait_for_external",
    }
)
FORBIDDEN_LEGACY_TOOL_NAMES = frozenset(
    {
        "bootstrap_context",
        "get_cards",
        "get_card_context",
        "manager_board_scan",
        "prepare_crm_card_action",
        "review_board",
        "search_cards",
        "update_card",
    }
)
MAINTENANCE_SKIPPED_TOOL_NAMES = frozenset(
    {
        "agent_board_workflow",
        "agent_finance_workflow",
        "agent_inventory_workflow",
        "agent_document_workflow",
        "start_workflow",
        "workflow_checkpoint",
        "workflow_transition",
        "workflow_wait_for_external",
        "complete_external_step",
        "workflow_resume",
        "workflow_cancel",
        "workflow_status",
    }
)
DEDUPLICATED_LIFECYCLE_SKIPPED_TOOL_NAMES = frozenset(
    {
        "workflow_checkpoint",
        "workflow_transition",
        "workflow_wait_for_external",
        "complete_external_step",
        "workflow_resume",
        "workflow_cancel",
    }
)
STORE_OWNER_CAPABILITIES_NAME = "store_owner_capabilities"
STORE_OWNER_API_NAME = "store_owner_api"
STORE_OWNER_SAFE_DRY_RUN_METHOD = "POST"
STORE_OWNER_SAFE_DRY_RUN_PATH = "/api/v1/categories"
STORE_OWNER_PREFLIGHT_CONTRACT_VERSION = "store-owner-preflight-v1"
CHANGE_FEED_BOOTSTRAP_NAME = "api:/api/change_feed/bootstrap"
CHANGE_FEED_READ_NAME = "api:/api/change_feed/read"
CHANGE_FEED_ACK_NAME = "api:/api/change_feed/ack"
CHANGE_FEED_SMOKE_CONSUMER_ID = "gateway-release-smoke"
CHANGE_FEED_SMOKE_PAGE_LIMIT = 25
CHANGE_FEED_EVENT_KEYS = frozenset(
    {
        "sequence",
        "event_id",
        "occurred_at",
        "action",
        "entity_type",
        "entity_id",
        "change_type",
        "tombstone",
        "correlation_ref",
        "idempotency_ref",
        "producer",
    }
)
CHANGE_FEED_REQUIRED_EVENT_KEYS = frozenset(
    {
        "sequence",
        "event_id",
        "occurred_at",
        "action",
        "entity_type",
        "entity_id",
        "change_type",
        "tombstone",
    }
)
RELEASE_REVISION_PATTERN = re.compile(r"[0-9a-f]{40,64}")


def _release_smoke_id(release_revision: str) -> str:
    normalized = str(release_revision or "").strip().casefold()
    if RELEASE_REVISION_PATTERN.fullmatch(normalized) is None:
        raise ValueError("release revision must be a 40-64 character lowercase hex digest")
    return hashlib.sha256(
        f"autostop-gateway-v2-release-smoke:v1:{normalized}".encode("ascii")
    ).hexdigest()[:32]


def _release_smoke_proof(token: str, release_revision: str) -> str:
    return hmac.new(
        str(token or "").encode("utf-8"),
        f"autostop-gateway-v2-release-smoke:v1:{release_revision}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _maintenance_raw_fields(release_revision: str, release_smoke_proof: str) -> dict[str, str]:
    if not release_revision or not release_smoke_proof:
        return {}
    return {
        "release_smoke_revision": release_revision,
        "release_smoke_proof": release_smoke_proof,
    }


def _serialized_size(value: Any) -> int:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


async def _open_session(mcp_url: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = httpx.Timeout(30.0, connect=5.0, read=30.0, write=30.0, pool=30.0)
    http_client = httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False)
    transport = streamable_http_client(mcp_url, http_client=http_client)
    return http_client, transport


async def _anonymous_access_probe(mcp_url: str) -> tuple[bool, int]:
    timeout = httpx.Timeout(15.0, connect=5.0, read=15.0, write=15.0, pool=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                mcp_url,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": "anonymous-auth-probe",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "autostop-auth-probe", "version": "2"},
                    },
                },
            )
        return response.status_code in {401, 403}, response.status_code
    except Exception:
        return False, 0


def _tool_ok(result: Any) -> bool:
    if getattr(result, "isError", False):
        return False
    structured = getattr(result, "structuredContent", None)
    if not isinstance(structured, dict):
        return False
    if "ok" in structured:
        return bool(structured.get("ok"))
    return bool(structured.get("data") or structured.get("status"))


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structuredContent", None)
    return payload if isinstance(payload, dict) else {}


def _state_version(payload: dict[str, Any]) -> int:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    value = summary.get("state_version")
    if not isinstance(value, int):
        raise RuntimeError("workflow response is missing state_version")
    return value


async def _call(
    session: ClientSession,
    calls: dict[str, bool],
    name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    result = await session.call_tool(name, arguments or {})
    # One public tool may be used for several raw capabilities. Preserve the
    # first failure instead of letting a later successful invocation mask it.
    calls[name] = calls.get(name, True) and _tool_ok(result)
    return result


def _required_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is missing")
    return value


def _required_raw_executor(result: Any, *, name: str) -> dict[str, Any]:
    outer = _structured(result)
    if not _tool_ok(result):
        raise RuntimeError(f"{name} raw call failed")
    executor = _required_mapping(outer.get("data"), label=f"{name} executor result")
    if executor.get("ok") is not True:
        raise RuntimeError(f"{name} executor rejected the probe")
    return executor


def _required_raw_data(result: Any, *, name: str) -> dict[str, Any]:
    executor = _required_raw_executor(result, name=name)
    return _required_mapping(executor.get("data"), label=f"{name} response data")


def _require_raw_write_ledger(result: Any, *, name: str, check: str | None = None) -> None:
    outer = _structured(result)
    verification = _required_mapping(outer.get("verification"), label=f"{name} verification")
    if (
        outer.get("status") != "completed"
        or verification.get("schema_hash_verified") is not True
        or verification.get("executor_ok") is not True
        or verification.get("passed") is not True
        or verification.get("ledger_closed") is not True
    ):
        raise RuntimeError(f"{name} raw write ledger did not close cleanly")
    if check is not None and verification.get("check") != check:
        raise RuntimeError(f"{name} exact verification check is missing")


def _raw_write_reused_completed(result: Any) -> bool:
    outer = _structured(result)
    verification = outer.get("verification")
    return bool(
        _tool_ok(result)
        and outer.get("status") == "completed"
        and isinstance(verification, dict)
        and verification.get("idempotency_reused") is True
        and verification.get("prior_terminal_state") is True
    )


async def _discover_raw_schema(
    session: ClientSession,
    calls: dict[str, bool],
    *,
    name: str,
    allowed_risks: frozenset[str],
) -> str:
    discovered = await _call(
        session,
        calls,
        "discover_raw_capabilities",
        {"query": name, "limit": 10},
    )
    discovered_data = _required_mapping(
        _structured(discovered).get("data"), label=f"{name} discovery data"
    )
    capabilities = discovered_data.get("capabilities")
    if not isinstance(capabilities, list):
        raise RuntimeError(f"{name} discovery inventory is missing")
    matches = [
        item
        for item in capabilities
        if isinstance(item, dict) and str(item.get("name") or "") == name
    ]
    if len(matches) != 1 or str(matches[0].get("risk") or "") not in allowed_risks:
        raise RuntimeError(f"{name} discovery contract is invalid")

    schema = await _call(
        session,
        calls,
        "get_raw_capability_schema",
        {"name": name},
    )
    schema_payload = _structured(schema)
    schema_summary = _required_mapping(
        schema_payload.get("summary"), label=f"{name} schema summary"
    )
    schema_data = _required_mapping(schema_payload.get("data"), label=f"{name} schema data")
    input_schema = schema_data.get("input_schema")
    schema_hash = str(schema_summary.get("schema_hash") or "")
    if (
        not _tool_ok(schema)
        or not schema_hash
        or schema_summary.get("risk") not in allowed_risks
        or not isinstance(input_schema, dict)
        or input_schema.get("type") != "object"
    ):
        raise RuntimeError(f"{name} schema contract is invalid")
    return schema_hash


async def _run_store_owner_probes(
    session: ClientSession,
    calls: dict[str, bool],
    *,
    smoke_id: str,
    release_revision: str = "",
    release_smoke_proof: str = "",
) -> dict[str, Any]:
    capabilities_hash = await _discover_raw_schema(
        session,
        calls,
        name=STORE_OWNER_CAPABILITIES_NAME,
        allowed_risks=frozenset({"read"}),
    )
    owner_api_hash = await _discover_raw_schema(
        session,
        calls,
        name=STORE_OWNER_API_NAME,
        allowed_risks=frozenset({"write", "destructive"}),
    )

    inventory_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": STORE_OWNER_CAPABILITIES_NAME,
            "arguments": {"query": STORE_OWNER_SAFE_DRY_RUN_PATH, "limit": 25},
            "schema_hash": capabilities_hash,
            # This inventory contains contracts only, never business data.
            "allow_large_output": True,
        },
    )
    inventory = _required_raw_executor(inventory_result, name=STORE_OWNER_CAPABILITIES_NAME)
    items = inventory.get("items")
    if not isinstance(items, list):
        raise RuntimeError("store owner capability inventory is missing")
    candidates = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("method") or "").upper() == STORE_OWNER_SAFE_DRY_RUN_METHOD
        and str(item.get("path") or "") == STORE_OWNER_SAFE_DRY_RUN_PATH
        and str(item.get("risk") or "") == "write"
        and item.get("request_required") is True
        and item.get("path_parameters") == []
        and "application/json" in (item.get("request_content_types") or [])
    ]
    if len(candidates) != 1:
        raise RuntimeError("safe reversible store owner dry-run operation is unavailable")
    capability = candidates[0]
    operation_id = str(capability.get("operation_id") or "")
    if not operation_id or not str(capability.get("schema_hash") or ""):
        raise RuntimeError("safe store owner capability identity is incomplete")

    target_id = f"collection:{STORE_OWNER_SAFE_DRY_RUN_PATH}"
    synthetic_name = f"Gateway release smoke {smoke_id[:12]}"
    revision_arguments = {
        "operation_id": operation_id,
        "mode": "revision",
        "target_id": target_id,
        "body": {"name": synthetic_name},
        "correlation_id": f"gateway-v2-owner-revision-{smoke_id}",
    }
    revision_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": STORE_OWNER_API_NAME,
            "arguments": revision_arguments,
            "schema_hash": owner_api_hash,
            "idempotency_key": f"gateway-v2-owner-revision-{smoke_id}",
            "allow_large_output": True,
            **_maintenance_raw_fields(release_revision, release_smoke_proof),
        },
    )
    revision_executor = _required_raw_executor(revision_result, name="store owner revision")
    revision_summary = _required_mapping(
        revision_executor.get("summary"), label="store owner revision summary"
    )
    current_revision = revision_summary.get("current_revision")
    revision_required = revision_summary.get("expected_revision_required")
    if (
        revision_executor.get("status") != "completed"
        or revision_summary.get("operation_id") != operation_id
        or revision_summary.get("method") != STORE_OWNER_SAFE_DRY_RUN_METHOD
        or revision_summary.get("path") != STORE_OWNER_SAFE_DRY_RUN_PATH
        or revision_summary.get("route_key")
        != f"{STORE_OWNER_SAFE_DRY_RUN_METHOD} {STORE_OWNER_SAFE_DRY_RUN_PATH}"
        or revision_summary.get("contract_version") != STORE_OWNER_PREFLIGHT_CONTRACT_VERSION
        or revision_summary.get("revision_kind") != "revision_exempt"
        or revision_required is not False
        or current_revision is not None
    ):
        raise RuntimeError("store owner current route revision contract is invalid")

    request_key = f"gateway-v2-owner-dry-run-{smoke_id}"
    dry_run_arguments = {
        "operation_id": operation_id,
        "mode": "dry_run",
        "target_id": target_id,
        "body": {"name": synthetic_name},
        "owner_intent": "production release smoke server-side dry run only",
        "idempotency_key": request_key,
        "correlation_id": request_key,
        # Pass through the exact route revision result, including the explicit
        # revision-exempt None value for this reviewed reversible collection create.
        "expected_revision": current_revision,
    }
    dry_run_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": STORE_OWNER_API_NAME,
            "arguments": dry_run_arguments,
            "schema_hash": owner_api_hash,
            "idempotency_key": f"gateway-v2-owner-dry-run-ledger-{smoke_id}",
            "allow_large_output": True,
            **_maintenance_raw_fields(release_revision, release_smoke_proof),
        },
    )
    if _raw_write_reused_completed(dry_run_result):
        return {
            "ok": True,
            "operation_id": operation_id,
            "route_key": revision_summary.get("route_key"),
            "revision_kind": revision_summary.get("revision_kind"),
            "route_revision_verified": True,
            "dry_run_only": True,
            "domain_handler_executed": False,
            "deduplicated": True,
        }
    _require_raw_write_ledger(
        dry_run_result,
        name="store owner dry run",
        check="store_owner_server_dry_run_receipt",
    )
    dry_run_executor = _required_raw_executor(dry_run_result, name="store owner dry run")
    dry_run_summary = _required_mapping(
        dry_run_executor.get("summary"), label="store owner dry-run summary"
    )
    dry_run_meta = _required_mapping(
        dry_run_executor.get("meta"), label="store owner dry-run metadata"
    )
    proof = str(dry_run_summary.get("dry_run_proof") or "")
    if (
        dry_run_executor.get("status") != "planned"
        or dry_run_summary.get("operation_id") != operation_id
        or dry_run_summary.get("method") != STORE_OWNER_SAFE_DRY_RUN_METHOD
        or dry_run_summary.get("path") != STORE_OWNER_SAFE_DRY_RUN_PATH
        or dry_run_summary.get("current_revision") != current_revision
        or dry_run_summary.get("revision_kind") != "revision_exempt"
        or dry_run_summary.get("contract_version") != STORE_OWNER_PREFLIGHT_CONTRACT_VERSION
        or len(proof) != 64
        or any(character not in "0123456789abcdef" for character in proof)
        or dry_run_meta.get("request_dispatched") is not True
        or dry_run_meta.get("outcome_uncertain") is not False
        or dry_run_meta.get("domain_handler_executed") is not False
    ):
        raise RuntimeError("store owner server-side dry-run proof is invalid")

    return {
        "ok": True,
        "operation_id": operation_id,
        "route_key": revision_summary.get("route_key"),
        "revision_kind": revision_summary.get("revision_kind"),
        "route_revision_verified": True,
        "dry_run_only": True,
        "domain_handler_executed": False,
    }


def _validated_change_feed_page(data: dict[str, Any], *, consumer_id: str) -> list[dict[str, Any]]:
    events = data.get("events")
    if (
        data.get("format") != "crm_change_feed_page_v1"
        or data.get("consumer_id") != consumer_id
        or not isinstance(data.get("generation"), str)
        or not data.get("generation")
        or not isinstance(events, list)
        or len(events) > CHANGE_FEED_SMOKE_PAGE_LIMIT
        or isinstance(data.get("high_water"), bool)
        or not isinstance(data.get("high_water"), int)
        or data["high_water"] < 0
        or data.get("delivery_high_water") != data.get("high_water")
        or isinstance(data.get("acked_sequence"), bool)
        or not isinstance(data.get("acked_sequence"), int)
        or data["acked_sequence"] < 0
        or data["acked_sequence"] > data["high_water"]
    ):
        raise RuntimeError("change-feed page contract is invalid")
    if not events:
        if (
            data.get("caught_up") is not True
            or data.get("from_sequence") is not None
            or data.get("through_sequence") is not None
            or data.get("replay_cursor") is not None
            or data.get("next_cursor") is not None
            or data.get("ack") is not None
        ):
            raise RuntimeError("empty change-feed page is not a clean caught-up checkpoint")
        return []
    if (
        not isinstance(data.get("replay_cursor"), str)
        or not data.get("replay_cursor")
        or not isinstance(data.get("ack"), str)
        or not data.get("ack")
    ):
        raise RuntimeError("non-empty change-feed page is missing replay or ACK tokens")
    previous_sequence = 0
    for event in events:
        if (
            not isinstance(event, dict)
            or not CHANGE_FEED_REQUIRED_EVENT_KEYS <= set(event)
            or not set(event) <= CHANGE_FEED_EVENT_KEYS
            or isinstance(event.get("sequence"), bool)
            or not isinstance(event.get("sequence"), int)
            or event["sequence"] < 1
            or (previous_sequence and event["sequence"] != previous_sequence + 1)
        ):
            raise RuntimeError("change-feed event is not the bounded PII-free projection")
        previous_sequence = event["sequence"]
    if (
        data.get("from_sequence") != events[0]["sequence"]
        or data.get("through_sequence") != events[-1]["sequence"]
        or events[0]["sequence"] != data["acked_sequence"] + 1
        or events[-1]["sequence"] > data["high_water"]
        or data.get("caught_up") != (events[-1]["sequence"] >= data["high_water"])
        or (data.get("caught_up") is True and data.get("next_cursor") is not None)
        or (
            data.get("caught_up") is False
            and (not isinstance(data.get("next_cursor"), str) or not data.get("next_cursor"))
        )
    ):
        raise RuntimeError("change-feed page sequence window is invalid")
    return events


async def _ack_change_feed_page(
    session: ClientSession,
    calls: dict[str, bool],
    *,
    schema_hash: str,
    consumer_id: str,
    page: dict[str, Any],
    release_revision: str = "",
    release_smoke_proof: str = "",
) -> dict[str, Any]:
    through_sequence = page.get("through_sequence")
    ack_token = str(page.get("ack") or "")
    if (
        isinstance(through_sequence, bool)
        or not isinstance(through_sequence, int)
        or through_sequence < 1
        or not ack_token
    ):
        raise RuntimeError("change-feed page cannot be acknowledged safely")
    ack_identity = hashlib.sha256(
        (f"{consumer_id}\0{page.get('generation')}\0{through_sequence}\0{ack_token}").encode()
    ).hexdigest()[:32]
    ack_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": CHANGE_FEED_ACK_NAME,
            "arguments": {"consumer_id": consumer_id, "ack": ack_token},
            "schema_hash": schema_hash,
            # Bound to the exact ACK request, so a retry reuses the completed
            # ledger entry without creating random durable smoke rows.
            "idempotency_key": f"gateway-v2-feed-ack-{ack_identity}",
            **_maintenance_raw_fields(release_revision, release_smoke_proof),
        },
    )
    if _raw_write_reused_completed(ack_result):
        return {
            "format": "crm_change_feed_ack_v1",
            "consumer_id": consumer_id,
            "generation": page.get("generation"),
            "acked_sequence": through_sequence,
            "changed": False,
            "delivery_complete": None,
            "deduplicated": True,
        }
    _require_raw_write_ledger(
        ack_result,
        name="change feed ACK",
        check="exact_change_feed_ack_checkpoint",
    )
    ack = _required_raw_data(ack_result, name="change feed ACK")
    if (
        ack.get("format") != "crm_change_feed_ack_v1"
        or ack.get("consumer_id") != consumer_id
        or ack.get("generation") != page.get("generation")
        or ack.get("acked_sequence") != through_sequence
        or ack.get("changed") not in {True, False}
        or not isinstance(ack.get("delivery_complete"), bool)
    ):
        raise RuntimeError("change-feed ACK did not bind the exact synthetic consumer page")
    return ack


async def _run_change_feed_probes(
    session: ClientSession,
    calls: dict[str, bool],
    *,
    smoke_id: str,
    release_revision: str = "",
    release_smoke_proof: str = "",
) -> dict[str, Any]:
    schema_hashes = {
        name: await _discover_raw_schema(
            session,
            calls,
            name=name,
            allowed_risks=frozenset({risk}),
        )
        for name, risk in (
            (CHANGE_FEED_BOOTSTRAP_NAME, "write"),
            (CHANGE_FEED_READ_NAME, "read"),
            (CHANGE_FEED_ACK_NAME, "write"),
        )
    }
    # A stable technical consumer bounds persistent state to one consumer and
    # at most one resumable delivery across all releases. Per-call workflow
    # idempotency remains unique through smoke_id.
    consumer_id = CHANGE_FEED_SMOKE_CONSUMER_ID

    bootstrap_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": CHANGE_FEED_BOOTSTRAP_NAME,
            "arguments": {"consumer_id": consumer_id},
            "schema_hash": schema_hashes[CHANGE_FEED_BOOTSTRAP_NAME],
            "idempotency_key": f"gateway-v2-feed-bootstrap-{smoke_id}",
            **_maintenance_raw_fields(release_revision, release_smoke_proof),
        },
    )
    bootstrap: dict[str, Any] | None = None
    if not _raw_write_reused_completed(bootstrap_result):
        _require_raw_write_ledger(
            bootstrap_result,
            name="change feed bootstrap",
            check="exact_change_feed_bootstrap_checkpoint",
        )
        bootstrap = _required_raw_data(bootstrap_result, name="change feed bootstrap")
        if (
            bootstrap.get("format") != "crm_change_feed_bootstrap_v1"
            or bootstrap.get("consumer_id") != consumer_id
            or not isinstance(bootstrap.get("generation"), str)
            or not bootstrap.get("generation")
            or isinstance(bootstrap.get("high_water"), bool)
            or not isinstance(bootstrap.get("high_water"), int)
            or bootstrap["high_water"] < 0
            or isinstance(bootstrap.get("acked_sequence"), bool)
            or not isinstance(bootstrap.get("acked_sequence"), int)
            or bootstrap["acked_sequence"] < 0
            or bootstrap["acked_sequence"] > bootstrap["high_water"]
        ):
            raise RuntimeError("change-feed bootstrap contract is invalid")

    first_result = await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": CHANGE_FEED_READ_NAME,
            "arguments": {
                "consumer_id": consumer_id,
                "limit": CHANGE_FEED_SMOKE_PAGE_LIMIT,
            },
            "schema_hash": schema_hashes[CHANGE_FEED_READ_NAME],
            # Feed events are an explicitly bounded PII-free projection.
            "allow_large_output": True,
        },
    )
    first_page = _required_raw_data(first_result, name="change feed read")
    first_events = _validated_change_feed_page(first_page, consumer_id=consumer_id)
    if not first_events:
        if bootstrap is not None:
            if (
                first_page.get("generation") != bootstrap.get("generation")
                or bootstrap.get("has_unacked") is not False
                or bootstrap.get("pending_high_water") is not None
                or bootstrap.get("acked_sequence") != bootstrap.get("high_water")
                or first_page.get("acked_sequence") != bootstrap.get("acked_sequence")
                or first_page.get("high_water") != bootstrap.get("high_water")
            ):
                raise RuntimeError(
                    "empty change-feed read left an inconsistent delivery checkpoint"
                )
        return {
            "ok": True,
            "consumer_id": consumer_id,
            "generation": first_page.get("generation"),
            "status": "caught_up_empty",
            "event_count": 0,
            "through_sequence": None,
            "acked_sequence": first_page.get("acked_sequence"),
            "bootstrap_ledger_closed": True,
            "replay_required": False,
            "replay_exact": None,
            "ack_required": False,
            "ack_ledger_closed": None,
            "pii_free_projection": True,
        }

    try:
        if bootstrap is not None and first_page.get("generation") != bootstrap.get("generation"):
            raise RuntimeError("change-feed generation changed during smoke")
        replay_result = await _call(
            session,
            calls,
            "call_raw_capability",
            {
                "name": CHANGE_FEED_READ_NAME,
                "arguments": {
                    "consumer_id": consumer_id,
                    "cursor": first_page["replay_cursor"],
                    "limit": CHANGE_FEED_SMOKE_PAGE_LIMIT,
                },
                "schema_hash": schema_hashes[CHANGE_FEED_READ_NAME],
                "allow_large_output": True,
            },
        )
        replay_page = _required_raw_data(replay_result, name="change feed replay")
        replay_events = _validated_change_feed_page(replay_page, consumer_id=consumer_id)
        if replay_page != first_page or replay_events != first_events:
            raise RuntimeError("change-feed replay is not byte-equivalent at the data contract")

        through_sequence = first_page.get("through_sequence")
        if (
            isinstance(through_sequence, bool)
            or not isinstance(through_sequence, int)
            or through_sequence != first_events[-1]["sequence"]
        ):
            raise RuntimeError("change-feed contiguous sequence proof is invalid")
        ack = await _ack_change_feed_page(
            session,
            calls,
            schema_hash=schema_hashes[CHANGE_FEED_ACK_NAME],
            consumer_id=consumer_id,
            page=first_page,
            release_revision=release_revision,
            release_smoke_proof=release_smoke_proof,
        )
    except Exception as probe_error:
        # A valid first page owns a resumable delivery. Best-effort ACK that
        # exact page before surfacing the original smoke failure so a stable
        # technical consumer never accumulates abandoned deliveries.
        try:
            await _ack_change_feed_page(
                session,
                calls,
                schema_hash=schema_hashes[CHANGE_FEED_ACK_NAME],
                consumer_id=consumer_id,
                page=first_page,
                release_revision=release_revision,
                release_smoke_proof=release_smoke_proof,
            )
        except Exception as cleanup_error:
            probe_error.add_note(f"change-feed cleanup ACK failed: {cleanup_error}")
        raise

    delivery_complete = ack.get("delivery_complete")
    deduplicated_ack = ack.get("deduplicated") is True
    status = (
        "replayed_and_acked_deduplicated"
        if deduplicated_ack
        else "replayed_and_acked"
        if delivery_complete is True
        else "replayed_and_acked_partial"
    )

    return {
        "ok": True,
        "consumer_id": consumer_id,
        "generation": first_page.get("generation"),
        "status": status,
        "event_count": len(first_events),
        "through_sequence": through_sequence,
        "acked_sequence": ack.get("acked_sequence"),
        "bootstrap_ledger_closed": True,
        "replay_required": True,
        "replay_exact": True,
        "ack_required": True,
        "ack_ledger_closed": True,
        "delivery_complete": delivery_complete,
        "pii_free_projection": True,
    }


def _change_feed_probe_checks(probe: dict[str, Any]) -> dict[str, bool]:
    status = str(probe.get("status") or "")
    has_event = status in {
        "replayed_and_acked",
        "replayed_and_acked_partial",
        "replayed_and_acked_deduplicated",
    }
    is_empty = status == "caught_up_empty"
    return {
        "change_feed_bootstrap_and_ack_ledgers_ok": bool(
            probe.get("ok")
            and probe.get("bootstrap_ledger_closed") is True
            and (
                (
                    has_event
                    and probe.get("ack_required") is True
                    and probe.get("ack_ledger_closed") is True
                )
                or (
                    is_empty
                    and probe.get("ack_required") is False
                    and probe.get("ack_ledger_closed") is None
                )
            )
        ),
        "change_feed_replay_exact": bool(
            (
                has_event
                and probe.get("replay_required") is True
                and probe.get("replay_exact") is True
            )
            or (
                is_empty
                and probe.get("replay_required") is False
                and probe.get("replay_exact") is None
            )
        ),
        "change_feed_projection_pii_free": bool(probe.get("pii_free_projection") is True),
    }


def _safe_inventory_contract_arguments(smoke_id: str) -> dict[str, Any]:
    return {
        "domain": "inventory",
        "action": "adjust",
        "target_id": "synthetic-inventory-target",
        "planned_changes": {
            "movement_type": "write_off",
            "quantity": 1,
            "card_id": "synthetic-card-target",
        },
        "owner_intent": "safe exhaustive Gateway v2 release check",
        "expected_revision": "synthetic-inventory-target@release-smoke",
        "idempotency_key": f"gateway-v2-contract-{smoke_id}",
        "dry_run": True,
    }


async def _run_exhaustive_checks(
    session: ClientSession,
    calls: dict[str, bool],
    *,
    smoke_id: str,
    require_store: bool = False,
    maintenance_safe: bool = False,
    release_revision: str = "",
    release_smoke_proof: str = "",
) -> dict[str, Any]:
    for name in ("ping_connector", "get_connector_identity", "get_runtime_status"):
        await _call(session, calls, name)

    await _call(
        session,
        calls,
        "prepare_action_contract",
        _safe_inventory_contract_arguments(smoke_id),
    )

    domain_calls = (
        (
            "agent_board_workflow",
            {
                "operation": "manager_board_scan",
                "payload": {},
                "idempotency_key": f"gateway-v2-board-{smoke_id}",
                "mode": "dry_run",
            },
        ),
        (
            "agent_finance_workflow",
            {
                "operation": "list_cashboxes",
                "payload": {},
                "idempotency_key": f"gateway-v2-finance-{smoke_id}",
            },
        ),
        (
            "agent_inventory_workflow",
            {
                "operation": "list_inventory_items",
                "payload": {},
                "idempotency_key": f"gateway-v2-inventory-{smoke_id}",
            },
        ),
        (
            "agent_document_workflow",
            {
                "operation": "list_shared_files",
                "payload": {},
                "idempotency_key": f"gateway-v2-document-{smoke_id}",
                "allow_large_output": False,
            },
        ),
    )
    if not maintenance_safe:
        for name, arguments in domain_calls:
            await _call(session, calls, name, arguments)

    await _call(
        session,
        calls,
        "discover_raw_capabilities",
        {"query": "get_cards", "limit": 5},
    )
    schema_result = await _call(
        session,
        calls,
        "get_raw_capability_schema",
        {"name": "get_cards"},
    )
    schema_payload = _structured(schema_result)
    schema_summary = (
        schema_payload.get("summary") if isinstance(schema_payload.get("summary"), dict) else {}
    )
    schema_hash = str(schema_summary.get("schema_hash") or "")
    if not schema_hash:
        raise RuntimeError("get_cards schema hash is missing")
    await _call(
        session,
        calls,
        "call_raw_capability",
        {
            "name": "get_cards",
            "arguments": {"include_archived": False, "compact": True},
            "schema_hash": schema_hash,
            "allow_large_output": False,
        },
    )

    if maintenance_safe:
        store_owner_probe: dict[str, Any] = {}
        change_feed_probe: dict[str, Any] = {}
        if require_store:
            store_owner_probe = await _run_store_owner_probes(
                session,
                calls,
                smoke_id=smoke_id,
                release_revision=release_revision,
                release_smoke_proof=release_smoke_proof,
            )
            change_feed_probe = await _run_change_feed_probes(
                session,
                calls,
                smoke_id=smoke_id,
                release_revision=release_revision,
                release_smoke_proof=release_smoke_proof,
            )
        return {
            "synthetic_run_id": None,
            "synthetic_terminal_status": "maintenance_skipped",
            "synthetic_deduplicated": False,
            "maintenance_safe": True,
            "store_owner_probe": store_owner_probe,
            "change_feed_probe": change_feed_probe,
        }

    started = await _call(
        session,
        calls,
        "start_workflow",
        {
            "workflow_id": "gateway_v2_release_smoke",
            "intent": "gateway_v2_release_smoke",
            "idempotency_key": f"gateway-v2-lifecycle-{smoke_id}",
            "query": "safe synthetic lifecycle smoke",
            "dry_run": True,
            "source": "release-smoke",
            "metadata": {"synthetic": True},
        },
    )
    started_payload = _structured(started)
    run_id = started_payload.get("run_id")
    if not isinstance(run_id, int):
        raise RuntimeError("start_workflow response is missing run_id")
    started_summary = (
        started_payload.get("summary") if isinstance(started_payload.get("summary"), dict) else {}
    )
    synthetic_deduplicated = started_summary.get("deduplicated") is True
    status = await _call(
        session,
        calls,
        "workflow_status",
        {"run_id": run_id, "include_events": False, "include_external_steps": True},
    )
    status_payload = _structured(status)
    if synthetic_deduplicated:
        if status_payload.get("status") != "cancelled":
            raise RuntimeError("deduplicated release workflow is not terminal")
        cancelled_payload = status_payload
    else:
        version = _state_version(status_payload)

        checkpoint = await _call(
            session,
            calls,
            "workflow_checkpoint",
            {
                "run_id": run_id,
                "checkpoint": {"phase": "release_smoke", "next_action": "execute"},
                "message": "synthetic release checkpoint",
                "expected_state_version": version,
            },
        )
        version = _state_version(_structured(checkpoint))

        executing = await _call(
            session,
            calls,
            "workflow_transition",
            {
                "run_id": run_id,
                "status": "executing",
                "message": "synthetic release execution",
                "expected_state_version": version,
            },
        )
        version = _state_version(_structured(executing))

        step_id = f"release-smoke-{smoke_id}"
        waiting = await _call(
            session,
            calls,
            "workflow_wait_for_external",
            {
                "run_id": run_id,
                "step_id": step_id,
                "connector": "release-smoke",
                "action": "refs-only-probe",
                "request_refs": {"thread_id": step_id},
                "expected_state_version": version,
            },
        )
        version = _state_version(_structured(waiting))

        completed_step = await _call(
            session,
            calls,
            "complete_external_step",
            {
                "run_id": run_id,
                "step_id": step_id,
                "result_refs": {"message_id": step_id},
                "expected_state_version": version,
            },
        )
        version = _state_version(_structured(completed_step))

        resumed = await _call(
            session,
            calls,
            "workflow_resume",
            {"run_id": run_id, "expected_state_version": version},
        )
        version = _state_version(_structured(resumed))

        cancelled = await _call(
            session,
            calls,
            "workflow_cancel",
            {
                "run_id": run_id,
                "reason": "synthetic release smoke complete",
                "expected_state_version": version,
            },
        )
        cancelled_payload = _structured(cancelled)
    store_owner_probe: dict[str, Any] = {}
    change_feed_probe: dict[str, Any] = {}
    if require_store:
        store_owner_probe = await _run_store_owner_probes(
            session,
            calls,
            smoke_id=smoke_id,
            release_revision=release_revision,
            release_smoke_proof=release_smoke_proof,
        )
        change_feed_probe = await _run_change_feed_probes(
            session,
            calls,
            smoke_id=smoke_id,
            release_revision=release_revision,
            release_smoke_proof=release_smoke_proof,
        )
    return {
        "synthetic_run_id": run_id,
        "synthetic_terminal_status": cancelled_payload.get("status"),
        "synthetic_deduplicated": synthetic_deduplicated,
        "store_owner_probe": store_owner_probe,
        "change_feed_probe": change_feed_probe,
    }


async def _run_web_checks(
    session: ClientSession,
    calls: dict[str, bool],
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    probes = (
        (
            "search_web_multi",
            {"query": "AutoStop автосервис Красноярск", "limit": 1, "providers": ["searxng"]},
        ),
        ("fetch_page_excerpt", {"url": "https://example.com", "max_chars": 300}),
        (
            "fetch_page_browser",
            {"url": "https://example.com", "max_chars": 300, "wait_ms": 0},
        ),
    )
    for capability_name, arguments in probes:
        discovered = await _call(
            session,
            calls,
            "discover_raw_capabilities",
            {"query": capability_name, "limit": 5},
        )
        discovered_payload = _structured(discovered)
        capabilities = (
            (discovered_payload.get("data") or {}).get("capabilities") or []
            if isinstance(discovered_payload.get("data"), dict)
            else []
        )
        checks[f"{capability_name}_discoverable"] = any(
            isinstance(item, dict) and item.get("name") == capability_name for item in capabilities
        )
        schema = await _call(
            session,
            calls,
            "get_raw_capability_schema",
            {"name": capability_name},
        )
        schema_payload = _structured(schema)
        schema_summary = (
            schema_payload.get("summary") if isinstance(schema_payload.get("summary"), dict) else {}
        )
        schema_hash = str(schema_summary.get("schema_hash") or "")
        checks[f"{capability_name}_schema_ok"] = bool(schema_hash) and _tool_ok(schema)
        if not schema_hash:
            checks[f"{capability_name}_call_ok"] = False
            continue
        result = await _call(
            session,
            calls,
            "call_raw_capability",
            {
                "name": capability_name,
                "arguments": arguments,
                "schema_hash": schema_hash,
                "allow_large_output": False,
            },
        )
        checks[f"{capability_name}_call_ok"] = _tool_ok(result)
    return checks


async def check_gateway(args: argparse.Namespace) -> dict[str, Any]:
    release_revision = str(getattr(args, "release_revision", "") or "").strip().casefold()
    maintenance_safe = bool(getattr(args, "maintenance_safe", False))
    if maintenance_safe and not args.exhaustive:
        return {"ok": False, "error": "--maintenance-safe requires --exhaustive"}
    if args.exhaustive:
        if release_revision:
            try:
                smoke_id = _release_smoke_id(release_revision)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        elif maintenance_safe:
            return {
                "ok": False,
                "error": "--maintenance-safe requires --release-revision",
            }
        else:
            smoke_id = os.urandom(16).hex()
    else:
        smoke_id = ""
    token = str(os.environ.get(args.token_env, "") or "").strip()
    if not token:
        return {"ok": False, "error": f"token environment variable is missing: {args.token_env}"}
    release_smoke_proof = _release_smoke_proof(token, release_revision) if maintenance_safe else ""

    anonymous_blocked, anonymous_status = await _anonymous_access_probe(args.mcp_url)

    http_client, transport = await _open_session(args.mcp_url, token)
    try:
        async with http_client:
            async with transport as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_names = {tool.name for tool in tools.tools}
                    tools_bytes = _serialized_size(tools)
                    missing = sorted(EXPECTED_TOOL_NAMES - tool_names)
                    unexpected = sorted(tool_names - EXPECTED_TOOL_NAMES)
                    bootstrap = None
                    digest = None
                    search = None
                    entity_context = None
                    store_runtime = None
                    store_probe = None
                    store_sourcing_probe = None
                    web_checks: dict[str, bool] = {}
                    workflows = None
                    calls: dict[str, bool] = {}
                    exhaustive: dict[str, Any] = {}
                    if not missing:
                        bootstrap = await _call(session, calls, "agent_bootstrap")
                        digest = await _call(session, calls, "agent_board_digest", {"limit": 1})
                        digest_payload = getattr(digest, "structuredContent", None)
                        digest_cards = (
                            ((digest_payload or {}).get("data") or {}).get("cards") or []
                            if isinstance(digest_payload, dict)
                            else []
                        )
                        digest_card_id = (
                            str(digest_cards[0].get("id") or "")
                            if digest_cards and isinstance(digest_cards[0], dict)
                            else ""
                        )
                        digest_search_query = (
                            str(
                                digest_cards[0].get("short_id")
                                or digest_cards[0].get("vehicle")
                                or digest_card_id
                            )
                            if digest_cards and isinstance(digest_cards[0], dict)
                            else ""
                        )
                        if digest_card_id:
                            search = await _call(
                                session,
                                calls,
                                "agent_search",
                                {"entity": "card", "query": digest_search_query, "limit": 1},
                            )
                        search_payload = getattr(search, "structuredContent", None)
                        search_items = (
                            ((search_payload or {}).get("data") or {}).get("items") or []
                            if isinstance(search_payload, dict)
                            else []
                        )
                        card_id = (
                            str(search_items[0].get("id") or "")
                            if search_items and isinstance(search_items[0], dict)
                            else ""
                        )
                        if card_id:
                            entity_context = await _call(
                                session,
                                calls,
                                "agent_entity_context",
                                {"entity": "card", "entity_id": card_id, "detail": "summary"},
                            )
                        workflows = await _call(
                            session, calls, "list_agent_workflows", {"limit": 50}
                        )
                        if args.require_store:
                            store_runtime = await _call(session, calls, "get_runtime_status")
                            store_probe = await _call(
                                session,
                                calls,
                                "agent_search",
                                {
                                    "entity": "store_state",
                                    "query": "state",
                                    "limit": 1,
                                },
                            )
                            store_sourcing_probe = await _call(
                                session,
                                calls,
                                "agent_search",
                                {
                                    "entity": "store_sourcing_offer",
                                    "query": "масляный фильтр",
                                    "limit": 1,
                                },
                            )
                        if args.require_web:
                            web_checks = await _run_web_checks(session, calls)
                        if args.exhaustive:
                            exhaustive = await _run_exhaustive_checks(
                                session,
                                calls,
                                smoke_id=smoke_id,
                                require_store=args.require_store,
                                maintenance_safe=maintenance_safe,
                                release_revision=release_revision,
                                release_smoke_proof=release_smoke_proof,
                            )
                    bootstrap_bytes = _serialized_size(bootstrap) if bootstrap is not None else 0
                    digest_bytes = _serialized_size(digest) if digest is not None else 0
    except Exception:
        return {
            "ok": False,
            "anonymous_access_blocked": anonymous_blocked,
            "anonymous_status_code": anonymous_status,
            "error": "authorized MCP session failed",
        }

    checks = {
        "anonymous_access_blocked": anonymous_blocked,
        "required_tools_present": not missing,
        "unexpected_tools_absent": not unexpected,
        "legacy_tools_absent": not (tool_names & FORBIDDEN_LEGACY_TOOL_NAMES),
        "tool_count_exactly_24": len(tool_names) == 24,
        "tool_count_within_budget": len(tool_names) <= args.max_tools,
        "tools_payload_within_budget": tools_bytes <= args.max_tools_bytes,
        "bootstrap_ok": bootstrap is not None and _tool_ok(bootstrap),
        "bootstrap_payload_within_budget": bootstrap_bytes <= args.max_bootstrap_bytes,
        "board_digest_ok": digest is not None and _tool_ok(digest),
        "board_digest_payload_within_budget": digest_bytes <= args.max_board_digest_bytes,
        "search_ok": search is not None and _tool_ok(search),
        "entity_context_ok": entity_context is not None and _tool_ok(entity_context),
        "workflow_registry_ok": workflows is not None and _tool_ok(workflows),
    }
    if args.require_store:
        runtime_payload = _structured(store_runtime)
        runtime_data = (
            runtime_payload.get("data") if isinstance(runtime_payload.get("data"), dict) else {}
        )
        store_status = (
            runtime_data.get("store") if isinstance(runtime_data.get("store"), dict) else {}
        )
        store_summary = (
            store_status.get("summary") if isinstance(store_status.get("summary"), dict) else {}
        )
        store_adapter = (
            store_summary.get("adapter") if isinstance(store_summary.get("adapter"), dict) else {}
        )
        store_features = (
            store_summary.get("features") if isinstance(store_summary.get("features"), dict) else {}
        )
        checks.update(
            {
                "store_runtime_ready": bool(store_status.get("ok")),
                "store_state_read_ok": store_probe is not None and _tool_ok(store_probe),
                "store_quote_adapter_configured": bool(store_adapter.get("quote_configured")),
                "store_quote_full_read_enabled": bool(
                    store_features.get("quote_full_read_enabled")
                ),
                "store_quote_draft_write_enabled": bool(
                    store_features.get("quote_draft_write_enabled")
                ),
                "store_supplier_lookup_enabled": bool(
                    store_features.get("supplier_lookup_enabled")
                ),
                "store_sourcing_read_ok": store_sourcing_probe is not None
                and _tool_ok(store_sourcing_probe),
            }
        )
    if args.require_web:
        checks.update(web_checks)
    if args.exhaustive:
        expected_invocations = (
            EXPECTED_TOOL_NAMES - MAINTENANCE_SKIPPED_TOOL_NAMES
            if maintenance_safe
            else EXPECTED_TOOL_NAMES - DEDUPLICATED_LIFECYCLE_SKIPPED_TOOL_NAMES
            if exhaustive.get("synthetic_deduplicated") is True
            else EXPECTED_TOOL_NAMES
        )
        checks.update(
            {
                "all_tools_invoked": set(calls) == set(expected_invocations),
                "all_tool_invocations_ok": all(calls.values()),
                "synthetic_workflow_terminal": (
                    exhaustive.get("synthetic_terminal_status") == "maintenance_skipped"
                    if maintenance_safe
                    else exhaustive.get("synthetic_terminal_status") == "cancelled"
                ),
            }
        )
        if args.require_store:
            store_owner_exhaustive = (
                exhaustive.get("store_owner_probe")
                if isinstance(exhaustive.get("store_owner_probe"), dict)
                else {}
            )
            change_feed_exhaustive = (
                exhaustive.get("change_feed_probe")
                if isinstance(exhaustive.get("change_feed_probe"), dict)
                else {}
            )
            checks.update(
                {
                    "store_owner_capabilities_and_revision_ok": bool(
                        store_owner_exhaustive.get("ok")
                        and store_owner_exhaustive.get("route_revision_verified") is True
                    ),
                    "store_owner_server_dry_run_only": bool(
                        store_owner_exhaustive.get("dry_run_only") is True
                        and store_owner_exhaustive.get("domain_handler_executed") is False
                    ),
                    **_change_feed_probe_checks(change_feed_exhaustive),
                }
            )
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "metrics": {
            "tool_count": len(tool_names),
            "tools_bytes": tools_bytes,
            "bootstrap_bytes": bootstrap_bytes,
            "board_digest_bytes": digest_bytes,
            "anonymous_status_code": anonymous_status,
            "invoked_tool_count": len(calls),
        },
        "missing_tools": missing,
        "unexpected_tools": unexpected,
        "legacy_tools_found": sorted(tool_names & FORBIDDEN_LEGACY_TOOL_NAMES),
        "invoked_tools": sorted(calls),
        "failed_invocations": sorted(name for name, ok in calls.items() if not ok),
        "exhaustive": exhaustive,
        "data_included": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe Agent Gateway v2 production smoke.")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--max-tools", type=int, default=40)
    parser.add_argument("--max-tools-bytes", type=int, default=40 * 1024)
    parser.add_argument("--max-bootstrap-bytes", type=int, default=20 * 1024)
    parser.add_argument("--max-board-digest-bytes", type=int, default=50 * 1024)
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Invoke all 24 tools using read-only, dry-run, or synthetic terminal inputs.",
    )
    parser.add_argument(
        "--release-revision",
        default="",
        help="Immutable 40-64 character lowercase hex revision required by --maintenance-safe.",
    )
    parser.add_argument(
        "--maintenance-safe",
        action="store_true",
        help=(
            "Skip public domain/lifecycle writes and sign only allowlisted technical raw "
            "release probes while the production maintenance marker is active."
        ),
    )
    parser.add_argument(
        "--require-store",
        action="store_true",
        help=(
            "Require live AutoStop App health and a non-cursor-consuming store state read. "
            "With --exhaustive, also require owner capability/revision/server-dry-run and "
            "PII-free CRM change-feed bootstrap/replay/ACK probes."
        ),
    )
    parser.add_argument(
        "--require-web",
        action="store_true",
        help="Require discovery, schema, and live calls for all guarded web research capabilities.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(check_gateway(args))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
