"""Fixed, bounded metadata checks for the separately admitted cold candidate.

The closed phase never invokes a tool, Store, scheduler or business route.
The open phase adds only the existing get_runtime_status read relay. Neither
phase certifies a customer operation or changes a feed/checkpoint/queue.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import j1_forward_io as io
from scripts import j1_forward_runtime as http

SCHEMA = "autostop.j1.cold-readonly-runtime.v1"
PUBLIC = "https://crm.autostopcrm.ru"
API = "http://127.0.0.1:41731"
REVISION_ROUTE = "/api/get_board_revision?compact=1&include_archive=0"
FIELDS = {
    "schema",
    "ok",
    "attempt_id",
    "candidate",
    "phase",
    "public",
    "api",
    "mcp",
    "business_acceptance",
    "store_effects_exercised",
    "elapsed_ms",
}


def validate_report(value, *, candidate, attempt, phase):
    io.require(type(value) is dict and set(value) == FIELDS, "cold_runtime_fields")
    io.require(
        value["schema"] == SCHEMA
        and value["ok"] is True
        and value["attempt_id"] == attempt
        and value["candidate"] == candidate
        and value["phase"] == phase
        and phase in {"closed", "open"}
        and value["business_acceptance"] is False
        and value["store_effects_exercised"] is False
        and type(value["elapsed_ms"]) is int
        and 0 <= value["elapsed_ms"] <= 80000,
        "cold_runtime_context",
    )
    public, api, mcp = (value[name] for name in ("public", "api", "mcp"))
    io.require(
        type(public) is dict
        and set(public) == {"health_http", "ui_http", "board_asset_sha256", "anonymous_guards"}
        and public["health_http"] == public["ui_http"] == 200
        and re.fullmatch(r"[0-9a-f]{64}", public["board_asset_sha256"])
        and public["anonymous_guards"] == {"public_api": 401, "local_api": 401, "public_mcp": 401},
        "cold_runtime_public",
    )
    io.require(
        type(api) is dict
        and set(api) == {"authorized_read_http", "revision_sha256", "metadata_only"}
        and api["authorized_read_http"] == 200
        and api["metadata_only"] is True
        and re.fullmatch(r"[0-9a-f]{64}", api["revision_sha256"]),
        "cold_runtime_api",
    )
    io.require(
        type(mcp) is dict
        and set(mcp)
        == {
            "authenticated",
            "tool_catalog_sha256",
            "tool_count",
            "readonly_runtime_tool",
            "store_live_check",
            "crm_ready",
            "store_ready",
        }
        and mcp["authenticated"] is True
        and mcp["readonly_runtime_tool"] is True
        and re.fullmatch(r"[0-9a-f]{64}", mcp["tool_catalog_sha256"])
        and type(mcp["tool_count"]) is int
        and 0 < mcp["tool_count"] <= 1024
        and mcp["store_live_check"] is (phase == "open")
        and mcp["crm_ready"] is (True if phase == "open" else None)
        and mcp["store_ready"] is (True if phase == "open" else None),
        "cold_runtime_mcp",
    )
    return value


async def inspect_session(session, *, phase):
    await session.initialize()
    tools, cursor = {}, None
    for _ in range(4):
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            io.require(tool.name not in tools and len(tools) < 1024, "cold_runtime_catalog_limit")
            tools[tool.name] = tool.model_dump(mode="json", exclude_none=False)
        cursor = page.nextCursor
        if cursor is None:
            break
    io.require(cursor is None and "get_runtime_status" in tools, "cold_runtime_catalog_incomplete")
    annotation = tools["get_runtime_status"].get("annotations") or {}
    io.require(annotation.get("readOnlyHint") is True, "cold_runtime_tool_not_readonly")
    result = {
        "authenticated": True,
        "tool_catalog_sha256": io.sha(io.canonical([tools[key] for key in sorted(tools)])),
        "tool_count": len(tools),
        "readonly_runtime_tool": True,
        "store_live_check": phase == "open",
        "crm_ready": None,
        "store_ready": None,
    }
    if phase == "open":
        answer = await session.call_tool("get_runtime_status", arguments={})
        io.require(not answer.isError, "cold_runtime_relay_error")
        payload = answer.structuredContent
        if payload is None:
            io.require(
                len(answer.content) == 1 and answer.content[0].type == "text",
                "cold_runtime_relay_shape",
            )
            text = answer.content[0].text
            io.require(len(text.encode("utf-8")) <= http.MAX_BYTES, "cold_runtime_relay_limit")
            payload = io.document(text.encode("utf-8"))
        io.require(
            type(payload) is dict
            and payload.get("ok") is True
            and payload.get("status") == "ready",
            "cold_runtime_crm_degraded",
        )
        store = (payload.get("data") or {}).get("store")
        io.require(
            type(store) is dict and store.get("ok") is True and store.get("status") == "ready",
            "cold_runtime_store_degraded",
        )
        result.update(crm_ready=True, store_ready=True)
    return result


async def inspect_mcp(token, *, phase, deadline):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    remaining = min(45, deadline - time.monotonic())
    io.require(remaining > 0, "cold_runtime_deadline")
    async with asyncio.timeout(remaining):
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + http.header_value(token)},
            timeout=httpx.Timeout(min(30, remaining), connect=min(5, remaining)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with streamable_http_client(PUBLIC + "/mcp", http_client=client) as (
                reader,
                writer,
                _,
            ):
                async with ClientSession(reader, writer) as session:
                    return await inspect_session(session, phase=phase)


def probe(
    candidate,
    attempt,
    phase,
    *,
    environment=None,
    client_factory=http.Client,
    mcp_inspector=inspect_mcp,
):
    import uuid

    io.require(
        str(uuid.UUID(attempt)) == attempt and phase in {"closed", "open"}, "cold_runtime_arguments"
    )
    io.require(
        type(candidate) is dict
        and set(candidate) == {"image", "revision", "tree"}
        and re.fullmatch(r"sha256:[0-9a-f]{64}", candidate["image"])
        and all(re.fullmatch(r"[0-9a-f]{40}", candidate[key]) for key in ("revision", "tree")),
        "cold_runtime_candidate",
    )
    environment = os.environ if environment is None else environment
    api_token = environment.get("MINIMAL_KANBAN_API_BEARER_TOKEN", "")
    mcp_token = environment.get("MINIMAL_KANBAN_MCP_BEARER_TOKEN", "")
    io.require(bool(api_token and mcp_token), "cold_runtime_existing_credentials_missing")
    started = time.monotonic()
    deadline = started + 80
    client = client_factory(deadline)
    public = http.public_checks(client, PUBLIC, API)
    response = client.request(
        API + REVISION_ROUTE, headers={"Authorization": "Bearer " + http.header_value(api_token)}
    )
    data = http.envelope(response, "cold_runtime_authorized_read_failed")
    io.require(
        type(data.get("revision")) is str and 0 < len(data["revision"]) <= 256,
        "cold_runtime_revision_missing",
    )
    mcp = asyncio.run(mcp_inspector(mcp_token, phase=phase, deadline=deadline))
    report = {
        "schema": SCHEMA,
        "ok": True,
        "attempt_id": attempt,
        "candidate": candidate,
        "phase": phase,
        "public": public,
        "api": {
            "authorized_read_http": response.status,
            "revision_sha256": io.sha(data["revision"].encode()),
            "metadata_only": True,
        },
        "mcp": mcp,
        "business_acceptance": False,
        "store_effects_exercised": False,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    return validate_report(report, candidate=candidate, attempt=attempt, phase=phase)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--phase", required=True, choices=("closed", "open"))
    arguments = parser.parse_args(argv)
    try:
        result = probe(
            io.document(arguments.candidate.encode()), arguments.attempt_id, arguments.phase
        )
    except Exception:
        result = {"ok": False, "error": "cold_readonly_runtime_refused"}
    sys.stdout.buffer.write(io.canonical(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
