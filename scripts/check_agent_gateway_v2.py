from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
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
    calls[name] = _tool_ok(result)
    return result


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
) -> dict[str, Any]:
    smoke_id = uuid.uuid4().hex

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

    status = await _call(
        session,
        calls,
        "workflow_status",
        {"run_id": run_id, "include_events": False, "include_external_steps": True},
    )
    version = _state_version(_structured(status))

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
    return {
        "synthetic_run_id": run_id,
        "synthetic_terminal_status": cancelled_payload.get("status"),
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
    token = str(os.environ.get(args.token_env, "") or "").strip()
    if not token:
        return {"ok": False, "error": f"token environment variable is missing: {args.token_env}"}

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
                            exhaustive = await _run_exhaustive_checks(session, calls)
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
        checks.update(
            {
                "all_tools_invoked": set(calls) == set(EXPECTED_TOOL_NAMES),
                "all_tool_invocations_ok": all(calls.values()),
                "synthetic_workflow_terminal": exhaustive.get("synthetic_terminal_status")
                == "cancelled",
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
        "--require-store",
        action="store_true",
        help="Require live AutoStop App health and a non-cursor-consuming store state read.",
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
