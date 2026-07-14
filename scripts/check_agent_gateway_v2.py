from __future__ import annotations

import argparse
import asyncio
import json
import os
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
                    workflows = None
                    if not missing:
                        bootstrap = await session.call_tool("agent_bootstrap", {})
                        digest = await session.call_tool("agent_board_digest", {"limit": 1})
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
                            search = await session.call_tool(
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
                            entity_context = await session.call_tool(
                                "agent_entity_context",
                                {"entity": "card", "entity_id": card_id, "detail": "summary"},
                            )
                        workflows = await session.call_tool("list_agent_workflows", {"limit": 50})
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
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "metrics": {
            "tool_count": len(tool_names),
            "tools_bytes": tools_bytes,
            "bootstrap_bytes": bootstrap_bytes,
            "board_digest_bytes": digest_bytes,
            "anonymous_status_code": anonymous_status,
        },
        "missing_tools": missing,
        "unexpected_tools": unexpected,
        "legacy_tools_found": sorted(tool_names & FORBIDDEN_LEGACY_TOOL_NAMES),
        "data_included": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Agent Gateway v2 production smoke.")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--max-tools", type=int, default=40)
    parser.add_argument("--max-tools-bytes", type=int, default=40 * 1024)
    parser.add_argument("--max-bootstrap-bytes", type=int, default=20 * 1024)
    parser.add_argument("--max-board-digest-bytes", type=int, default=50 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(check_gateway(args))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
