from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_settings_file
from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError, discover_board_api
from minimal_kanban.settings_models import IntegrationSettings


def load_settings() -> IntegrationSettings:
    settings_file = get_settings_file()
    if not settings_file.exists():
        return IntegrationSettings.defaults()
    try:
        payload = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return IntegrationSettings.defaults()
    return IntegrationSettings.from_dict(payload)


def print_section(title: str) -> None:
    print(f"\n[{title}]")


def check_local_api(settings: IntegrationSettings) -> dict:
    token = (
        settings.auth.local_api_bearer_token
        or settings.local_api.local_api_bearer_token
        or settings.auth.access_token
        or None
    )
    base_url = discover_board_api(bearer_token=token, timeout_seconds=1.5)
    result: dict[str, object] = {
        "ok": False,
        "base_url": base_url,
        "health": None,
        "board_context": None,
        "error": None,
    }
    if not base_url:
        result["error"] = "local_api_not_found"
        return result
    client = BoardApiClient(base_url, bearer_token=token, timeout_seconds=5.0)
    try:
        result["health"] = client.health()
        result["board_context"] = client.get_board_context()
        result["ok"] = bool(
            isinstance(result["health"], dict)
            and result["health"].get("ok")
            and isinstance(result["board_context"], dict)
            and result["board_context"].get("ok")
        )
    except BoardApiTransportError as exc:
        result["error"] = str(exc)
    return result


async def check_public_mcp(settings: IntegrationSettings) -> dict:
    mcp_url = (settings.mcp.effective_mcp_url or "").strip()
    result: dict[str, object] = {
        "ok": False,
        "mcp_url": mcp_url,
        "tool_count": 0,
        "has_ping_connector": False,
        "has_bootstrap_context": False,
        "has_get_gpt_wall": False,
        "ping_ok": False,
        "ping_data": None,
        "error": None,
    }
    if not mcp_url:
        result["error"] = "mcp_url_not_configured"
        return result

    headers: dict[str, str] = {}
    token = (
        settings.auth.mcp_bearer_token
        or settings.mcp.mcp_bearer_token
        or settings.auth.access_token
        or ""
    ).strip()
    if token and settings.mcp.mcp_auth_mode == "bearer":
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0) as http_client:
            async with streamable_http_client(mcp_url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_names = {tool.name for tool in tools.tools}
                    result["tool_count"] = len(tool_names)
                    result["has_ping_connector"] = "ping_connector" in tool_names
                    result["has_bootstrap_context"] = "bootstrap_context" in tool_names
                    result["has_get_gpt_wall"] = "get_gpt_wall" in tool_names

                    if "ping_connector" in tool_names:
                        ping = await session.call_tool("ping_connector", {})
                        result["ping_ok"] = bool(
                            not ping.isError
                            and isinstance(ping.structuredContent, dict)
                            and ping.structuredContent.get("ok")
                        )
                        if isinstance(ping.structuredContent, dict):
                            result["ping_data"] = ping.structuredContent.get("data")

                    result["ok"] = bool(
                        result["has_ping_connector"]
                        and result["has_bootstrap_context"]
                        and result["has_get_gpt_wall"]
                    )
    except Exception as exc:  # pragma: no cover
        result["error"] = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live diagnostics for Minimal Kanban API/MCP.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")
    args = parser.parse_args()

    settings = load_settings()
    local_api = check_local_api(settings)
    public_mcp = asyncio.run(check_public_mcp(settings))

    report = {
        "settings_file": str(get_settings_file()),
        "local_api": local_api,
        "public_mcp": public_mcp,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("Minimal Kanban live diagnostics")
    print(f"settings_file: {report['settings_file']}")

    print_section("LOCAL API")
    print(f"base_url: {local_api.get('base_url') or '<not found>'}")
    if local_api.get("ok"):
        health = (local_api.get("health") or {}).get("data") or {}
        context = (local_api.get("board_context") or {}).get("data") or {}
        board = (context.get("context") or {}) if isinstance(context, dict) else {}
        print("status: ok")
        print(f"api_status: {health.get('status', 'unknown')}")
        print(f"board_name: {board.get('board_name', '<unknown>')}")
        print(f"columns_total: {board.get('columns_total', 0)}")
        print(f"active_cards_total: {board.get('active_cards_total', 0)}")
        print(f"archived_cards_total: {board.get('archived_cards_total', 0)}")
        print(f"stickies_total: {board.get('stickies_total', 0)}")
    else:
        print("status: failed")
        print(f"error: {local_api.get('error')}")

    print_section("PUBLIC MCP")
    print(f"mcp_url: {public_mcp.get('mcp_url') or '<not configured>'}")
    if public_mcp.get("ok"):
        ping_data = public_mcp.get("ping_data") or {}
        print("status: ok")
        print(f"tool_count: {public_mcp.get('tool_count')}")
        print(f"has_ping_connector: {public_mcp.get('has_ping_connector')}")
        print(f"has_bootstrap_context: {public_mcp.get('has_bootstrap_context')}")
        print(f"has_get_gpt_wall: {public_mcp.get('has_get_gpt_wall')}")
        print(f"ping_ok: {public_mcp.get('ping_ok')}")
        print(f"connector_name: {ping_data.get('connector_name', '<unknown>')}")
        print(f"resource_url: {ping_data.get('resource_url', '<unknown>')}")
    else:
        print("status: failed")
        print(f"error: {public_mcp.get('error')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
