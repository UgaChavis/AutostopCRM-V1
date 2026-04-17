from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _fallback_http_url(url: str) -> str:
    parts = urlsplit(_clean_url(url))
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    path = parts.path or ""
    if parts.query:
        path = f"{path}?{parts.query}"
    return f"http://{parts.netloc}{path}"


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


def _resolve_site_url(settings: IntegrationSettings, override: str | None) -> str:
    if override:
        return _clean_url(override)
    public_base = _clean_url(settings.mcp.public_https_base_url)
    if public_base:
        return public_base
    return ""


def _classify_probe_url(url: str) -> str:
    host = (urlsplit(_clean_url(url)).hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost"}:
        return "local"
    if host:
        return "public"
    return "unknown"


def _emit_output(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    data = text.encode(encoding, errors="backslashreplace")
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.write(b"\n")
        return
    sys.stdout.write(data.decode(encoding, errors="replace") + "\n")


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


def _can_reach_api(base_url: str, *, bearer_token: str | None = None, timeout: float = 3.0) -> bool:
    if not _clean_url(base_url):
        return False
    try:
        status, payload = _api_request(base_url, "/api/health", bearer_token=bearer_token, timeout=timeout)
    except Exception:
        return False
    return bool(status == 200 and _envelope_ok(payload))


def _envelope_ok(payload: dict[str, Any] | None) -> bool:
    return bool(isinstance(payload, dict) and payload.get("ok"))


def check_site(site_url: str, *, expect_https: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(site_url),
        "ok": False,
        "site_url": site_url,
        "final_url": "",
        "status_code": 0,
        "content_type": "",
        "title": "",
        "contains_autostop": False,
        "contains_login_route": False,
        "probe_url": site_url,
        "error": None,
    }
    if not site_url:
        result["error"] = "site_url_not_configured"
        return result

    if expect_https and urlsplit(site_url).scheme.lower() != "https":
        result["error"] = "site_url_is_not_https"
        return result

    candidate_urls = [site_url]
    fallback_http = _fallback_http_url(site_url)
    if fallback_http:
        candidate_urls.append(fallback_http)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": "AutoStopCRM-check/1.0",
    }
    last_error = ""
    for probe_url in candidate_urls:
        request = urllib.request.Request(probe_url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10.0) as response:
                body = response.read().decode("utf-8", errors="replace")
                final_url = response.geturl()
                title = ""
                title_start = body.lower().find("<title>")
                title_end = body.lower().find("</title>")
                if title_start != -1 and title_end != -1 and title_end > title_start:
                    title = body[title_start + 7 : title_end].strip()
                result["probe_url"] = probe_url
                result["final_url"] = final_url
                result["status_code"] = int(response.status)
                result["content_type"] = str(response.headers.get("Content-Type") or "")
                result["title"] = title
                result["contains_autostop"] = "AUTOSTOP" in body.upper()
                result["contains_login_route"] = "/api/login_operator" in body
                result["ok"] = bool(
                    response.status == 200
                    and result["contains_autostop"]
                    and result["contains_login_route"]
                    and (not expect_https or final_url.lower().startswith("https://"))
                )
                if not result["ok"]:
                    result["error"] = "site_surface_incomplete"
                return result
        except urllib.error.HTTPError as exc:
            result["probe_url"] = probe_url
            result["status_code"] = exc.code
            last_error = f"http_error_{exc.code}"
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
    result["error"] = last_error or "site_probe_failed"
    return result


def check_api_surface(base_url: str, *, bearer_token: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(base_url),
        "ok": False,
        "base_url": base_url,
        "surface_kind": "unknown",
        "health": None,
        "board_context": None,
        "board_snapshot": None,
        "wall": None,
        "repair_orders": None,
        "summary": {},
        "error": None,
    }
    if not base_url:
        result["error"] = "api_base_url_not_found"
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
        "base_url": base_url,
        "surface_kind": "unknown",
        "username": str(username or "").strip(),
        "expect_admin": bool(expect_admin),
        "login": None,
        "profile": None,
        "users": None,
        "is_admin": False,
        "has_security_payload": False,
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
        security_payload = ((profile_payload or {}).get("data") or {}).get("security")
        result["has_security_payload"] = isinstance(security_payload, dict)
        if not result["has_security_payload"]:
            result["error"] = "operator_security_payload_missing"
            return result
        security_payload = security_payload or {}
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


def check_public_write_protection(site_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(site_url),
        "ok": False,
        "site_url": site_url,
        "status_code": 0,
        "error_code": "",
        "error_message": "",
        "unexpected_write_succeeded": False,
        "cleanup_ok": False,
        "probe_url": site_url,
        "error": None,
    }
    if not site_url:
        result["error"] = "site_url_not_configured"
        return result

    marker = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate_urls = [_clean_url(site_url)]
    fallback_http = _fallback_http_url(site_url)
    if fallback_http:
        candidate_urls.append(_clean_url(fallback_http))
    last_error = ""
    create_status = 0
    create_payload: dict[str, Any] | None = None
    probe_base_url = candidate_urls[0]
    for candidate in candidate_urls:
        try:
            create_status, create_payload = _api_request(
                candidate,
                "/api/create_sticky",
                method="POST",
                payload={
                    "text": f"AUDIT TEMP {marker}",
                    "x": 1,
                    "y": 1,
                    "deadline": {"days": 0, "hours": 1},
                },
            )
            probe_base_url = candidate
            break
        except urllib.error.URLError as exc:
            last_error = f"public_write_probe_unreachable: {exc}"
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
    else:
        result["error"] = last_error or "public_write_probe_failed"
        return result

    result["probe_url"] = probe_base_url
    result["status_code"] = create_status
    error_payload = ((create_payload or {}).get("error") or {}) if isinstance(create_payload, dict) else {}
    result["error_code"] = str(error_payload.get("code") or "")
    result["error_message"] = str(error_payload.get("message") or "")

    if create_status in {401, 403} and result["error_code"] in {"unauthorized", "forbidden"}:
        result["ok"] = True
        return result

    sticky_payload = ((create_payload or {}).get("data") or {}).get("sticky") or {}
    sticky_id = str(sticky_payload.get("id") or "").strip()
    if sticky_id:
        result["unexpected_write_succeeded"] = True
        delete_status, delete_payload = _api_request(
            probe_base_url,
            "/api/delete_sticky",
            method="POST",
            payload={"sticky_id": sticky_id},
        )
        result["cleanup_ok"] = bool(delete_status == 200 and _envelope_ok(delete_payload))
    result["error"] = "anonymous_public_write_not_blocked"
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
        "has_get_connector_identity": False,
        "has_review_board": False,
        "ping_ok": False,
        "bootstrap_ok": False,
        "runtime_ok": False,
        "identity_ok": False,
        "review_ok": False,
        "ping_data": None,
        "bootstrap_data": None,
        "runtime_data": None,
        "identity_data": None,
        "review_data": None,
        "error": None,
    }
    if not mcp_url:
        result["error"] = "mcp_url_not_configured"
        return result

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    last_error = ""
    for attempt in range(1, 3):
        try:
            timeout = httpx.Timeout(45.0, connect=10.0, read=45.0, write=45.0, pool=45.0)
            async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
                async with streamable_http_client(mcp_url, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        tool_names = {tool.name for tool in tools.tools}
                        result["tool_count"] = len(tool_names)
                        result["has_ping_connector"] = "ping_connector" in tool_names
                        result["has_bootstrap_context"] = "bootstrap_context" in tool_names
                        result["has_get_runtime_status"] = "get_runtime_status" in tool_names
                        result["has_get_connector_identity"] = "get_connector_identity" in tool_names
                        result["has_review_board"] = "review_board" in tool_names

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

                        if result["has_get_connector_identity"]:
                            identity = await session.call_tool("get_connector_identity", {})
                            result["identity_ok"] = bool(
                                not identity.isError
                                and isinstance(identity.structuredContent, dict)
                                and identity.structuredContent.get("ok")
                            )
                            if isinstance(identity.structuredContent, dict):
                                result["identity_data"] = identity.structuredContent.get("data")

                        if result["has_review_board"]:
                            review = await session.call_tool("review_board", {})
                            result["review_ok"] = bool(
                                not review.isError
                                and isinstance(review.structuredContent, dict)
                                and review.structuredContent.get("ok")
                            )
                            if isinstance(review.structuredContent, dict):
                                result["review_data"] = review.structuredContent.get("data")

                        result["ok"] = all(
                            [
                                result["has_ping_connector"],
                                result["has_bootstrap_context"],
                                result["has_get_runtime_status"],
                                result["has_get_connector_identity"],
                                result["has_review_board"],
                                result["ping_ok"],
                                result["bootstrap_ok"],
                                result["runtime_ok"],
                                result["identity_ok"],
                                result["review_ok"],
                            ]
                        )
                        if not result["ok"]:
                            result["error"] = "mcp_surface_incomplete"
                        return result
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
            if attempt < 2:
                await asyncio.sleep(1.0)
    result["error"] = last_error or result["error"] or "mcp_surface_incomplete"
    return result


def _print_api_surface(report: dict[str, Any]) -> None:
    print_section("API SURFACE")
    print(f"base_url: {report.get('base_url') or '<not found>'}")
    print(f"surface_kind: {report.get('surface_kind') or '<unknown>'}")
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


def _print_site(report: dict[str, Any]) -> None:
    print_section("PUBLIC SITE")
    print(f"site_url: {report.get('site_url') or '<not configured>'}")
    if not report.get("checked"):
        print("status: skipped")
        print("reason: site url was not provided")
        return
    if report.get("ok"):
        print("status: ok")
        print(f"final_url: {report.get('final_url') or '<unknown>'}")
        print(f"status_code: {report.get('status_code')}")
        print(f"title: {report.get('title') or '<unknown>'}")
        print(f"content_type: {report.get('content_type') or '<unknown>'}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def _print_operator_auth(report: dict[str, Any]) -> None:
    print_section("OPERATOR AUTH")
    if not report.get("checked"):
        print("status: skipped")
        print("reason: operator credentials were not provided")
        return
    print(f"base_url: {report.get('base_url') or '<not found>'}")
    print(f"surface_kind: {report.get('surface_kind') or '<unknown>'}")
    print(f"username: {report.get('username') or '<empty>'}")
    if report.get("ok"):
        print("status: ok")
        print(f"is_admin: {report.get('is_admin')}")
        print(f"has_security_payload: {report.get('has_security_payload')}")
        print(f"using_default_admin_credentials: {report.get('using_default_admin_credentials')}")
        if report.get("warning"):
            print(f"warning: {report.get('warning')}")
        users_payload = ((report.get("users") or {}).get("data") or {}).get("users") or []
        print(f"users_visible: {len(users_payload)}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def _print_public_write_protection(report: dict[str, Any]) -> None:
    print_section("PUBLIC WRITE PROTECTION")
    print(f"site_url: {report.get('site_url') or '<not configured>'}")
    if not report.get("checked"):
        print("status: skipped")
        print("reason: site url was not provided")
        return
    if report.get("ok"):
        print("status: ok")
        print("anonymous writes: blocked")
        print(f"status_code: {report.get('status_code')}")
        if report.get("error_code"):
            print(f"error_code: {report.get('error_code')}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")
        print(f"status_code: {report.get('status_code')}")
        if report.get("error_code"):
            print(f"error_code: {report.get('error_code')}")
        if report.get("unexpected_write_succeeded"):
            print("unexpected_write_succeeded: True")
            print(f"cleanup_ok: {report.get('cleanup_ok')}")


def _print_mcp(report: dict[str, Any]) -> None:
    print_section("MCP")
    print(f"mcp_url: {report.get('mcp_url') or '<not configured>'}")
    if report.get("ok"):
        ping_data = report.get("ping_data") or {}
        bootstrap_data = report.get("bootstrap_data") or {}
        runtime_data = report.get("runtime_data") or {}
        identity_data = report.get("identity_data") or {}
        review_data = report.get("review_data") or {}
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
        identity_payload = identity_data.get("identity") or {}
        connector_scope = (
            identity_payload.get("board_scope")
            or identity_data.get("board_scope")
            or identity_data.get("scope")
            or "<unknown>"
        )
        print(f"connector_scope: {connector_scope}")
        print(f"board_name: {board_name}")
        print(f"runtime_status: {runtime_api_status}")
        summary = review_data.get("summary") or {}
        print(f"review_active_cards: {summary.get('active_cards', 0)}")
        print(f"review_alerts: {len(review_data.get('alerts') or [])}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live diagnostics for AutoStop CRM site, API and MCP.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of human text.")
    parser.add_argument("--strict", action="store_true", help="Return a non-zero exit code when a checked surface fails.")
    parser.add_argument("--site-url", default="", help="Explicit public CRM URL, for example https://crm.autostopcrm.ru.")
    parser.add_argument(
        "--skip-public-site",
        action="store_true",
        help="Skip probing the public CRM site surface even if a public URL is configured in settings.",
    )
    parser.add_argument(
        "--skip-public-write-protection",
        action="store_true",
        help="Skip the anonymous public write-protection probe.",
    )
    parser.add_argument("--expect-https", action="store_true", help="Require the site URL and final public URL to use https.")
    parser.add_argument("--local-api-url", default="", help="Explicit local API base URL, for example http://127.0.0.1:41731.")
    parser.add_argument("--local-api-token", default=None, help="Optional local API bearer token.")
    parser.add_argument("--mcp-url", default="", help="Explicit MCP URL, for example http://127.0.0.1:41831/mcp.")
    parser.add_argument("--mcp-token", default=None, help="Optional MCP bearer token.")
    parser.add_argument("--operator-username", default="", help="Optional operator username for auth verification.")
    parser.add_argument("--operator-password", default="", help="Optional operator password for auth verification.")
    parser.add_argument("--expect-admin", action="store_true", help="Require the operator credentials to resolve to an admin session.")
    args = parser.parse_args()

    settings = load_settings()
    site_url = "" if args.skip_public_site else _resolve_site_url(settings, args.site_url)
    local_api_token = _resolve_local_api_token(settings, args.local_api_token)
    local_api_url = _resolve_local_api_url(settings, args.local_api_url, local_api_token)
    mcp_url = _resolve_mcp_url(settings, args.mcp_url)
    mcp_token = _resolve_mcp_token(settings, args.mcp_token)
    api_probe_url = local_api_url or site_url
    explicit_local_api = bool(_clean_url(args.local_api_url))
    if (
        not explicit_local_api
        and site_url
        and local_api_url
        and _classify_probe_url(local_api_url) == "local"
        and not _can_reach_api(local_api_url, bearer_token=local_api_token or None, timeout=2.5)
    ):
        api_probe_url = site_url
    api_probe_kind = _classify_probe_url(api_probe_url)

    site_surface = check_site(site_url, expect_https=args.expect_https)
    api_surface = check_api_surface(api_probe_url, bearer_token=local_api_token or None)
    api_surface["surface_kind"] = api_probe_kind
    operator_auth = check_operator_auth(
        api_probe_url,
        username=args.operator_username,
        password=args.operator_password,
        bearer_token=local_api_token or None,
        expect_admin=args.expect_admin,
    )
    operator_auth["surface_kind"] = api_probe_kind
    public_write_site_url = "" if args.skip_public_write_protection else site_url
    public_write_protection = check_public_write_protection(public_write_site_url)
    mcp_surface = asyncio.run(check_mcp(mcp_url, bearer_token=mcp_token or None))

    report = {
        "settings_file": str(get_settings_file()),
        "site_surface": site_surface,
        "api_surface": api_surface,
        "operator_auth": operator_auth,
        "public_write_protection": public_write_protection,
        "mcp_surface": mcp_surface,
    }

    if args.json:
        _emit_output(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("AutoStop CRM live diagnostics")
        print(f"settings_file: {report['settings_file']}")
        _print_site(site_surface)
        _print_api_surface(api_surface)
        _print_operator_auth(operator_auth)
        _print_public_write_protection(public_write_protection)
        _print_mcp(mcp_surface)

    if not args.strict:
        return 0

    checked_sections = [
        ("site_surface", site_surface.get("checked"), site_surface.get("ok")),
        ("api_surface", api_surface.get("checked"), api_surface.get("ok")),
        ("operator_auth", operator_auth.get("checked"), operator_auth.get("ok")),
        ("public_write_protection", public_write_protection.get("checked"), public_write_protection.get("ok")),
        ("mcp_surface", mcp_surface.get("checked"), mcp_surface.get("ok")),
    ]
    failed = [name for name, checked, ok in checked_sections if checked and not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
