from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_settings_file
from minimal_kanban.mcp.client import discover_board_api
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


def _clean_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _resolve_local_api_url(settings: IntegrationSettings, override: str | None, token: str | None) -> str:
    if override:
        return _clean_url(override)
    discovered = discover_board_api(bearer_token=token or None, timeout_seconds=1.5)
    if discovered:
        return _clean_url(discovered)
    return _clean_url(settings.local_api.effective_local_api_url or settings.local_api.runtime_local_api_url)


def _resolve_local_api_token(settings: IntegrationSettings, override: str | None) -> str:
    if override is not None:
        return str(override).strip()
    return (
        settings.auth.local_api_bearer_token
        or settings.local_api.local_api_bearer_token
        or settings.auth.access_token
        or ""
    ).strip()


def _resolve_mcp_url(settings: IntegrationSettings, override: str | None) -> str:
    if override:
        return _clean_url(override)
    return _clean_url(settings.mcp.effective_mcp_url or settings.mcp.local_mcp_url)


def _resolve_mcp_token(settings: IntegrationSettings, override: str | None) -> str:
    if override is not None:
        return str(override).strip()
    return (
        settings.auth.mcp_bearer_token
        or settings.mcp.mcp_bearer_token
        or settings.auth.access_token
        or ""
    ).strip()


def _api_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    bearer_token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, Any]]:
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if bearer_token:
        request_headers["Authorization"] = f"Bearer {bearer_token}"
    if headers:
        request_headers.update(headers)
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{_clean_url(base_url)}{path}",
        data=body,
        method=method.upper(),
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"ok": False, "error": {"code": "http_error", "message": raw}}


def _envelope_ok(payload: dict[str, Any] | None) -> bool:
    return bool(isinstance(payload, dict) and payload.get("ok"))


def check_api_surface(base_url: str, *, bearer_token: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(base_url),
        "ok": False,
        "base_url": base_url,
        "health": None,
        "board_context": None,
        "board_snapshot": None,
        "wall": None,
        "repair_orders": None,
        "summary": {},
        "error": None,
    }
    if not base_url:
        result["error"] = "local_api_not_found"
        return result

    try:
        health_status, health = _api_request(base_url, "/api/health", bearer_token=bearer_token)
        context_status, board_context = _api_request(base_url, "/api/get_board_context", bearer_token=bearer_token)
        snapshot_status, board_snapshot = _api_request(base_url, "/api/get_board_snapshot", bearer_token=bearer_token)
        wall_status, wall = _api_request(base_url, "/api/get_gpt_wall", bearer_token=bearer_token)
        repair_status, repair_orders = _api_request(base_url, "/api/list_repair_orders", bearer_token=bearer_token)
    except Exception as exc:  # pragma: no cover
        result["error"] = str(exc)
        return result

    result["health"] = health
    result["board_context"] = board_context
    result["board_snapshot"] = board_snapshot
    result["wall"] = wall
    result["repair_orders"] = repair_orders

    context_payload = ((board_context or {}).get("data") or {}).get("context") or {}
    snapshot_payload = (board_snapshot or {}).get("data") or {}
    repair_payload = (repair_orders or {}).get("data") or {}
    wall_payload = (wall or {}).get("data") or {}

    result["summary"] = {
        "board_name": context_payload.get("board_name", ""),
        "columns_total": context_payload.get("columns_total", 0),
        "active_cards_total": context_payload.get("active_cards_total", 0),
        "archived_cards_total": context_payload.get("archived_cards_total", 0),
        "stickies_total": context_payload.get("stickies_total", 0),
        "snapshot_cards": len(snapshot_payload.get("cards") or []),
        "snapshot_columns": len(snapshot_payload.get("columns") or []),
        "repair_orders_total": len(repair_payload.get("repair_orders") or []),
        "wall_cards_total": wall_payload.get("meta", {}).get("active_cards", 0) if isinstance(wall_payload.get("meta"), dict) else 0,
    }
    result["ok"] = all(
        [
            health_status == 200 and _envelope_ok(health),
            context_status == 200 and _envelope_ok(board_context),
            snapshot_status == 200 and _envelope_ok(board_snapshot),
            wall_status == 200 and _envelope_ok(wall),
            repair_status == 200 and _envelope_ok(repair_orders),
        ]
    )
    if not result["ok"]:
        result["error"] = "api_surface_incomplete"
    return result


def check_operator_auth(
    base_url: str,
    *,
    username: str | None,
    password: str | None,
    bearer_token: str | None = None,
    expect_admin: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(base_url and username and password),
        "ok": False,
        "username": str(username or "").strip(),
        "expect_admin": bool(expect_admin),
        "login": None,
        "profile": None,
        "users": None,
        "is_admin": False,
        "using_default_admin_credentials": False,
        "warning": "",
        "error": None,
    }
    if not result["checked"]:
        result["error"] = "operator_credentials_not_provided"
        return result

    try:
        login_status, login_payload = _api_request(
            base_url,
            "/api/login_operator",
            method="POST",
            payload={"username": username, "password": password},
            bearer_token=bearer_token,
        )
        result["login"] = login_payload
        if login_status != 200 or not _envelope_ok(login_payload):
            result["error"] = "operator_login_failed"
            return result

        session_token = (((login_payload or {}).get("data") or {}).get("session") or {}).get("token", "")
        if not session_token:
            result["error"] = "operator_session_missing"
            return result

        operator_headers = {"X-Operator-Session": session_token}
        profile_status, profile_payload = _api_request(
            base_url,
            "/api/get_operator_profile",
            headers=operator_headers,
            bearer_token=bearer_token,
        )
        result["profile"] = profile_payload
        if profile_status != 200 or not _envelope_ok(profile_payload):
            result["error"] = "operator_profile_failed"
            return result

        user_payload = ((profile_payload or {}).get("data") or {}).get("user") or {}
        security_payload = ((profile_payload or {}).get("data") or {}).get("security") or {}
        result["is_admin"] = bool(user_payload.get("is_admin"))
        result["using_default_admin_credentials"] = bool(security_payload.get("using_default_admin_credentials"))
        result["warning"] = str(security_payload.get("warning") or "")

        if expect_admin and not result["is_admin"]:
            result["error"] = "operator_is_not_admin"
            return result

        if result["is_admin"]:
            users_status, users_payload = _api_request(
                base_url,
                "/api/list_operator_users",
                headers=operator_headers,
                bearer_token=bearer_token,
            )
            result["users"] = users_payload
            if users_status != 200 or not _envelope_ok(users_payload):
                result["error"] = "operator_user_listing_failed"
                return result

        result["ok"] = True
        return result
    except Exception as exc:  # pragma: no cover
        result["error"] = str(exc)
        return result


async def check_mcp(mcp_url: str, *, bearer_token: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(mcp_url),
        "ok": False,
        "mcp_url": mcp_url,
        "tool_count": 0,
        "has_ping_connector": False,
        "has_bootstrap_context": False,
        "has_get_runtime_status": False,
        "ping_ok": False,
        "bootstrap_ok": False,
        "runtime_ok": False,
        "ping_data": None,
        "bootstrap_data": None,
        "runtime_data": None,
        "error": None,
    }
    if not mcp_url:
        result["error"] = "mcp_url_not_configured"
        return result

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

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
                    result["has_get_runtime_status"] = "get_runtime_status" in tool_names

                    if result["has_ping_connector"]:
                        ping = await session.call_tool("ping_connector", {})
                        result["ping_ok"] = bool(
                            not ping.isError
                            and isinstance(ping.structuredContent, dict)
                            and ping.structuredContent.get("ok")
                        )
                        if isinstance(ping.structuredContent, dict):
                            result["ping_data"] = ping.structuredContent.get("data")

                    if result["has_bootstrap_context"]:
                        bootstrap = await session.call_tool("bootstrap_context", {})
                        result["bootstrap_ok"] = bool(
                            not bootstrap.isError
                            and isinstance(bootstrap.structuredContent, dict)
                            and bootstrap.structuredContent.get("ok")
                        )
                        if isinstance(bootstrap.structuredContent, dict):
                            result["bootstrap_data"] = bootstrap.structuredContent.get("data")

                    if result["has_get_runtime_status"]:
                        runtime = await session.call_tool("get_runtime_status", {})
                        result["runtime_ok"] = bool(
                            not runtime.isError
                            and isinstance(runtime.structuredContent, dict)
                            and runtime.structuredContent.get("ok")
                        )
                        if isinstance(runtime.structuredContent, dict):
                            result["runtime_data"] = runtime.structuredContent.get("data")

                    result["ok"] = all(
                        [
                            result["has_ping_connector"],
                            result["has_bootstrap_context"],
                            result["has_get_runtime_status"],
                            result["ping_ok"],
                            result["bootstrap_ok"],
                            result["runtime_ok"],
                        ]
                    )
                    if not result["ok"]:
                        result["error"] = "mcp_surface_incomplete"
    except Exception as exc:  # pragma: no cover
        result["error"] = str(exc)
    return result


def _print_api_surface(report: dict[str, Any]) -> None:
    print_section("LOCAL API")
    print(f"base_url: {report.get('base_url') or '<not found>'}")
    if report.get("ok"):
        summary = report.get("summary") or {}
        print("status: ok")
        print(f"board_name: {summary.get('board_name') or '<unknown>'}")
        print(f"columns_total: {summary.get('columns_total', 0)}")
        print(f"active_cards_total: {summary.get('active_cards_total', 0)}")
        print(f"archived_cards_total: {summary.get('archived_cards_total', 0)}")
        print(f"stickies_total: {summary.get('stickies_total', 0)}")
        print(f"snapshot_cards: {summary.get('snapshot_cards', 0)}")
        print(f"repair_orders_total: {summary.get('repair_orders_total', 0)}")
        print(f"wall_cards_total: {summary.get('wall_cards_total', 0)}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def _print_operator_auth(report: dict[str, Any]) -> None:
    print_section("OPERATOR AUTH")
    if not report.get("checked"):
        print("status: skipped")
        print("reason: operator credentials were not provided")
        return
    print(f"username: {report.get('username') or '<empty>'}")
    if report.get("ok"):
        print("status: ok")
        print(f"is_admin: {report.get('is_admin')}")
        print(f"using_default_admin_credentials: {report.get('using_default_admin_credentials')}")
        if report.get("warning"):
            print(f"warning: {report.get('warning')}")
        users_payload = ((report.get("users") or {}).get("data") or {}).get("users") or []
        print(f"users_visible: {len(users_payload)}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def _print_mcp(report: dict[str, Any]) -> None:
    print_section("MCP")
    print(f"mcp_url: {report.get('mcp_url') or '<not configured>'}")
    if report.get("ok"):
        ping_data = report.get("ping_data") or {}
        bootstrap_data = report.get("bootstrap_data") or {}
        runtime_data = report.get("runtime_data") or {}
        board_context = bootstrap_data.get("board_context") or {}
        board_name = (
            ((board_context.get("context") or {}).get("board_name"))
            or board_context.get("board_name")
            or "<unknown>"
        )
        runtime_status = runtime_data.get("runtime_status") or {}
        runtime_api_status = (
            ((runtime_status.get("api_health") or {}).get("status"))
            or runtime_status.get("status")
            or "<unknown>"
        )
        print("status: ok")
        print(f"tool_count: {report.get('tool_count')}")
        print(f"connector_name: {ping_data.get('connector_name', '<unknown>')}")
        print(f"resource_url: {ping_data.get('resource_url', '<unknown>')}")
        print(f"board_name: {board_name}")
        print(f"runtime_status: {runtime_api_status}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live diagnostics for AutoStop CRM API/MCP.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")
    parser.add_argument("--strict", action="store_true", help="Return a non-zero exit code when a checked surface fails.")
    parser.add_argument("--local-api-url", default="", help="Explicit local API base URL, for example http://127.0.0.1:41731.")
    parser.add_argument("--local-api-token", default=None, help="Optional local API bearer token.")
    parser.add_argument("--mcp-url", default="", help="Explicit MCP URL, for example http://127.0.0.1:41831/mcp.")
    parser.add_argument("--mcp-token", default=None, help="Optional MCP bearer token.")
    parser.add_argument("--operator-username", default="", help="Optional operator username for auth verification.")
    parser.add_argument("--operator-password", default="", help="Optional operator password for auth verification.")
    parser.add_argument("--expect-admin", action="store_true", help="Require the operator credentials to resolve to an admin session.")
    args = parser.parse_args()

    settings = load_settings()
    local_api_token = _resolve_local_api_token(settings, args.local_api_token)
    local_api_url = _resolve_local_api_url(settings, args.local_api_url, local_api_token)
    mcp_url = _resolve_mcp_url(settings, args.mcp_url)
    mcp_token = _resolve_mcp_token(settings, args.mcp_token)

    api_surface = check_api_surface(local_api_url, bearer_token=local_api_token or None)
    operator_auth = check_operator_auth(
        local_api_url,
        username=args.operator_username,
        password=args.operator_password,
        bearer_token=local_api_token or None,
        expect_admin=args.expect_admin,
    )
    mcp_surface = asyncio.run(check_mcp(mcp_url, bearer_token=mcp_token or None))

    report = {
        "settings_file": str(get_settings_file()),
        "api_surface": api_surface,
        "operator_auth": operator_auth,
        "mcp_surface": mcp_surface,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("AutoStop CRM live diagnostics")
        print(f"settings_file: {report['settings_file']}")
        _print_api_surface(api_surface)
        _print_operator_auth(operator_auth)
        _print_mcp(mcp_surface)

    if not args.strict:
        return 0

    checked_sections = [
        ("api_surface", api_surface.get("checked"), api_surface.get("ok")),
        ("operator_auth", operator_auth.get("checked"), operator_auth.get("ok")),
        ("mcp_surface", mcp_surface.get("checked"), mcp_surface.get("ok")),
    ]
    failed = [name for name, checked, ok in checked_sections if checked and not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
