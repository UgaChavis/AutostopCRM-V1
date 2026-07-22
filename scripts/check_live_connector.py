from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import (
    get_api_bearer_token,
    get_mcp_bearer_token,
    get_settings_file,
)
from minimal_kanban.json_safety import reject_deeply_nested_json
from minimal_kanban.mcp.client import discover_board_api
from minimal_kanban.mcp.oauth_provider import DEFAULT_KANBAN_SCOPES
from minimal_kanban.settings_models import IntegrationSettings
from minimal_kanban.web_assets import BOARD_WEB_APP_JS_PATH

LIVE_CONNECTOR_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
LIVE_CONNECTOR_SETTINGS_MAX_BYTES = 1 * 1024 * 1024
LIVE_CONNECTOR_SITE_SCRIPT_LIMIT = 4


class _ScriptSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or len(self.sources) >= LIVE_CONNECTOR_SITE_SCRIPT_LIMIT:
            return
        for name, value in attrs:
            if name.lower() == "src" and value:
                self.sources.append(value.strip())
                return


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth < 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any, *, indent: int | None = None) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=indent, allow_nan=False)


def _load_json_text(raw: str, *, context: str) -> Any:
    try:
        payload = json.loads(raw, parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError(f"{context} JSON is too deeply nested") from exc
    reject_deeply_nested_json(payload, message=f"{context} JSON is too deeply nested")
    return payload


def _parse_api_response(raw: str) -> dict[str, Any]:
    payload = _load_json_text(raw, context="API response")
    if not isinstance(payload, dict):
        raise ValueError("API response must be a JSON object")
    return payload


def _read_response_body(
    response: Any, *, limit_bytes: int = LIVE_CONNECTOR_RESPONSE_MAX_BYTES
) -> bytes:
    body = response.read(limit_bytes + 1)
    if len(body) > limit_bytes:
        raise ValueError(f"Live connector response is too large ({limit_bytes} byte limit)")
    return body


def _read_settings_text(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(LIVE_CONNECTOR_SETTINGS_MAX_BYTES + 1)
    if len(raw) > LIVE_CONNECTOR_SETTINGS_MAX_BYTES:
        raise ValueError(
            f"Live connector settings file is too large "
            f"({LIVE_CONNECTOR_SETTINGS_MAX_BYTES} byte limit)"
        )
    return raw.decode("utf-8")


def load_settings() -> IntegrationSettings:
    settings_file = get_settings_file()
    if not settings_file.exists():
        return IntegrationSettings.defaults()
    try:
        payload = _load_json_text(
            _read_settings_text(settings_file), context="Live connector settings"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return IntegrationSettings.defaults()
    return IntegrationSettings.from_dict(payload)


def print_section(title: str) -> None:
    print(f"\n[{title}]")


def _clean_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _urlsplit_clean(value: str | None):
    try:
        return urlsplit(_clean_url(value))
    except ValueError:
        return None


def _url_origin(value: str) -> tuple[str, str, int] | None:
    parts = _urlsplit_clean(value)
    if parts is None:
        return None
    scheme = parts.scheme.lower()
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    return scheme, hostname.lower().rstrip("."), port or (443 if scheme == "https" else 80)


def _fingerprinted_board_script_digest(path: str) -> str | None:
    prefix = "/assets/board."
    suffix = ".js"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    digest = path[len(prefix) : -len(suffix)]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        return None
    return digest


def _board_script_urls(document_url: str, body: str) -> list[str]:
    parser = _ScriptSourceParser()
    try:
        parser.feed(body)
    except Exception:
        return []

    document_origin = _url_origin(document_url)
    if document_origin is None:
        return []
    resolved: list[str] = []
    for source in parser.sources:
        if not source or len(source) > 2048:
            continue
        candidate = urljoin(document_url, source)
        parts = _urlsplit_clean(candidate)
        if (
            parts is None
            or parts.query
            or parts.fragment
            or _url_origin(candidate) != document_origin
            or parts.path != BOARD_WEB_APP_JS_PATH
            or _fingerprinted_board_script_digest(parts.path) is None
            or candidate in resolved
        ):
            continue
        resolved.append(candidate)
    return resolved


def _probe_login_route_in_board_scripts(document_url: str, body: str) -> tuple[bool, int, str, str]:
    checked = 0
    last_error = "board_script_not_found"
    last_asset_url = ""
    headers = {
        "Accept": "application/javascript,text/javascript;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "User-Agent": "AutoStopCRM-check/1.0",
    }
    for asset_url in _board_script_urls(document_url, body):
        checked += 1
        last_asset_url = asset_url
        try:
            request = urllib.request.Request(asset_url, method="GET", headers=headers)
            with _urlopen_no_redirect(request, timeout=10.0) as response:
                script_bytes = _read_response_body(response)
                content_type = (
                    str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                )
                if response.status != 200:
                    last_error = f"board_script_http_{response.status}"
                    continue
                if content_type not in {"application/javascript", "text/javascript"}:
                    last_error = "board_script_content_type_invalid"
                    continue
                parts = _urlsplit_clean(asset_url)
                expected_digest = (
                    _fingerprinted_board_script_digest(parts.path) if parts is not None else None
                )
                if (
                    expected_digest is None
                    or hashlib.sha256(script_bytes).hexdigest() != expected_digest
                ):
                    last_error = "board_script_fingerprint_mismatch"
                    continue
                script = script_bytes.decode("utf-8", errors="replace")
                if "/api/login_operator" in script:
                    return True, checked, "", asset_url
                last_error = "board_script_login_route_missing"
        except urllib.error.HTTPError as exc:
            last_error = f"board_script_http_{exc.code}"
        except Exception:
            last_error = "board_script_probe_failed"
    return False, checked, last_error, last_asset_url


def _fallback_http_url(url: str) -> str:
    parts = _urlsplit_clean(url)
    if parts is None or parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    path = parts.path or ""
    if parts.query:
        path = f"{path}?{parts.query}"
    return f"http://{parts.netloc}{path}"


def _resolve_local_api_url(
    settings: IntegrationSettings, override: str | None, token: str | None
) -> str:
    if override:
        return _clean_url(override)
    discovered = discover_board_api(bearer_token=token or None, timeout_seconds=1.5)
    if discovered:
        return _clean_url(discovered)
    return _clean_url(
        settings.local_api.effective_local_api_url or settings.local_api.runtime_local_api_url
    )


def _resolve_local_api_token(settings: IntegrationSettings, override: str | None) -> str:
    if override is not None:
        return str(override).strip()
    return (
        settings.auth.local_api_bearer_token
        or settings.local_api.local_api_bearer_token
        or settings.auth.access_token
        or get_api_bearer_token()
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
        or get_mcp_bearer_token()
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
    parts = _urlsplit_clean(url)
    host = ((parts.hostname if parts is not None else "") or "").strip().lower().rstrip(".")
    if host in {"127.0.0.1", "localhost"}:
        return "local"
    if host:
        return "public"
    return "unknown"


def _emit_output(text: str) -> None:
    data = text.encode("utf-8")
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.write(b"\n")
        return
    sys.stdout.write(data.decode("utf-8") + "\n")


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
    body = (
        None
        if payload is None
        else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    )
    request = urllib.request.Request(
        f"{_clean_url(base_url)}{path}",
        data=body,
        method=method.upper(),
        headers=request_headers,
    )
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            raw = _read_response_body(response).decode("utf-8")
            return response.status, _parse_api_response(raw)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError(f"API request redirected: {path}") from exc
        raw = _read_response_body(exc).decode("utf-8", errors="replace")
        try:
            return exc.code, _parse_api_response(raw)
        except ValueError:
            return exc.code, {"ok": False, "error": {"code": "http_error", "message": raw}}


def _can_reach_api(base_url: str, *, bearer_token: str | None = None, timeout: float = 3.0) -> bool:
    if not _clean_url(base_url):
        return False
    try:
        status, payload = _api_request(
            base_url, "/api/health", bearer_token=bearer_token, timeout=timeout
        )
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
        "login_route_source": "",
        "asset_probe_url": "",
        "script_assets_checked": 0,
        "script_asset_error": None,
        "probe_url": site_url,
        "error": None,
    }
    if not site_url:
        result["error"] = "site_url_not_configured"
        return result

    site_parts = _urlsplit_clean(site_url)
    if site_parts is None:
        result["error"] = "site_url_invalid"
        return result

    if expect_https and site_parts.scheme.lower() != "https":
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
        try:
            request = urllib.request.Request(probe_url, method="GET", headers=headers)
            with _urlopen_no_redirect(request, timeout=10.0) as response:
                body = _read_response_body(response).decode("utf-8", errors="replace")
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
                if result["contains_login_route"]:
                    result["login_route_source"] = "html"
                else:
                    found, checked, asset_error, asset_url = _probe_login_route_in_board_scripts(
                        final_url, body
                    )
                    result["contains_login_route"] = found
                    result["asset_probe_url"] = asset_url
                    result["script_assets_checked"] = checked
                    result["script_asset_error"] = asset_error or None
                    if found:
                        result["login_route_source"] = "asset"
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
            last_error = (
                "site_probe_redirected" if 300 <= exc.code < 400 else f"http_error_{exc.code}"
            )
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
        context_status, board_context = _api_request(
            base_url, "/api/get_board_context", bearer_token=bearer_token
        )
        snapshot_status, board_snapshot = _api_request(
            base_url,
            "/api/get_board_snapshot?compact=1&include_archive=0",
            bearer_token=bearer_token,
        )
        wall_status, wall = _api_request(
            base_url,
            "/api/get_gpt_wall?compact=1&include_archived=0&event_limit=10",
            bearer_token=bearer_token,
        )
        repair_status, repair_orders = _api_request(
            base_url,
            "/api/list_repair_orders?compact=true&redact_private=true",
            bearer_token=bearer_token,
        )
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
        "wall_cards_total": wall_payload.get("meta", {}).get("active_cards", 0)
        if isinstance(wall_payload.get("meta"), dict)
        else 0,
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

        session_token = (((login_payload or {}).get("data") or {}).get("session") or {}).get(
            "token", ""
        )
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
        result["using_default_admin_credentials"] = bool(
            security_payload.get("using_default_admin_credentials")
        )
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


def check_public_read_protection(site_url: str, *, require_https: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": bool(site_url),
        "ok": False,
        "site_url": site_url,
        "status_code": 0,
        "error_code": "",
        "probe_url": site_url,
        "error": None,
    }
    if not site_url:
        result["error"] = "site_url_not_configured"
        return result
    parsed = _urlsplit_clean(site_url)
    if parsed is None or (require_https and parsed.scheme.lower() != "https"):
        result["error"] = "public_read_probe_requires_https"
        return result
    candidate_urls = [_clean_url(site_url)]
    if not require_https:
        fallback_http = _fallback_http_url(site_url)
        if fallback_http:
            candidate_urls.append(_clean_url(fallback_http))
    last_error = ""
    for candidate in candidate_urls:
        try:
            status, payload = _api_request(candidate, "/api/get_cards", method="GET")
        except urllib.error.URLError as exc:
            last_error = f"public_read_probe_unreachable: {exc}"
            continue
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
            continue
        result["probe_url"] = candidate
        result["status_code"] = status
        error_payload = payload.get("error") or {} if isinstance(payload, dict) else {}
        result["error_code"] = str(error_payload.get("code") or "")
        if status in {401, 403} and result["error_code"] in {"unauthorized", "forbidden"}:
            result["ok"] = True
            return result
        result["error"] = "anonymous_public_read_not_blocked"
        return result
    result["error"] = last_error or "public_read_probe_failed"
    return result


def check_public_write_protection(site_url: str, *, require_https: bool = False) -> dict[str, Any]:
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

    marker = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    parsed = _urlsplit_clean(site_url)
    if parsed is None or (require_https and parsed.scheme.lower() != "https"):
        result["error"] = "public_write_probe_requires_https"
        return result
    candidate_urls = [_clean_url(site_url)]
    if not require_https:
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
    error_payload = (
        ((create_payload or {}).get("error") or {}) if isinstance(create_payload, dict) else {}
    )
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
        "has_agent_bootstrap": False,
        "has_agent_board_digest": False,
        "has_get_runtime_status": False,
        "has_get_connector_identity": False,
        "ping_ok": False,
        "bootstrap_ok": False,
        "digest_ok": False,
        "runtime_ok": False,
        "identity_ok": False,
        "ping_data": None,
        "bootstrap_data": None,
        "digest_data": None,
        "runtime_data": None,
        "identity_data": None,
        "oauth_protected_resource_ok": False,
        "oauth_authorization_server_ok": False,
        "oauth_pkce_s256": False,
        "oauth_refresh_supported": False,
        "oauth_security_schemes_ok": False,
        "legacy_tools_absent": False,
        "error": None,
    }
    if not mcp_url:
        result["error"] = "mcp_url_not_configured"
        return result

    metadata = await _check_oauth_metadata(mcp_url)
    result.update(metadata)
    if not all(metadata.values()):
        result["error"] = "mcp_oauth_metadata_incomplete"
        return result

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    last_error = ""
    for attempt in range(1, 3):
        try:
            timeout = httpx.Timeout(45.0, connect=10.0, read=45.0, write=45.0, pool=45.0)
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout, follow_redirects=False
            ) as http_client:
                async with streamable_http_client(mcp_url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await _collect_mcp_probe_result(session, result)
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
            if attempt < 2:
                await asyncio.sleep(1.0)
    result["error"] = last_error or result["error"] or "mcp_surface_incomplete"
    return result


async def _check_oauth_metadata(mcp_url: str) -> dict[str, bool]:
    parsed = urlsplit(mcp_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    protected_path = parsed.path or "/mcp"
    protected_url = f"{origin}/.well-known/oauth-protected-resource{protected_path}"
    authorization_url = f"{origin}/.well-known/oauth-authorization-server"
    result = {
        "oauth_protected_resource_ok": False,
        "oauth_authorization_server_ok": False,
        "oauth_pkce_s256": False,
        "oauth_refresh_supported": False,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            protected_response = await client.get(protected_url)
            authorization_response = await client.get(authorization_url)
        protected = protected_response.json() if protected_response.status_code == 200 else {}
        authorization = (
            authorization_response.json() if authorization_response.status_code == 200 else {}
        )
    except (httpx.HTTPError, ValueError):
        return result
    expected_resource = mcp_url.rstrip("/")
    result["oauth_protected_resource_ok"] = bool(
        str(protected.get("resource") or "").rstrip("/") == expected_resource
        and origin
        in [str(item).rstrip("/") for item in protected.get("authorization_servers") or []]
        and set(DEFAULT_KANBAN_SCOPES).issubset(set(protected.get("scopes_supported") or []))
    )
    result["oauth_authorization_server_ok"] = bool(
        str(authorization.get("issuer") or "").rstrip("/") == origin
        and str(authorization.get("authorization_endpoint") or "").startswith(origin)
        and str(authorization.get("token_endpoint") or "").startswith(origin)
        and str(authorization.get("registration_endpoint") or "").startswith(origin)
    )
    result["oauth_pkce_s256"] = "S256" in (
        authorization.get("code_challenge_methods_supported") or []
    )
    result["oauth_refresh_supported"] = "refresh_token" in (
        authorization.get("grant_types_supported") or []
    )
    return result


async def _collect_mcp_probe_result(
    session: ClientSession, result: dict[str, Any]
) -> dict[str, Any]:
    tools = await session.list_tools()
    tool_names = {tool.name for tool in tools.tools}
    result["tool_count"] = len(tool_names)
    result["has_ping_connector"] = "ping_connector" in tool_names
    result["has_agent_bootstrap"] = "agent_bootstrap" in tool_names
    result["has_agent_board_digest"] = "agent_board_digest" in tool_names
    result["has_get_runtime_status"] = "get_runtime_status" in tool_names
    result["has_get_connector_identity"] = "get_connector_identity" in tool_names
    result["legacy_tools_absent"] = not bool(
        tool_names.intersection({"get_cards", "search_cards", "bootstrap_context", "update_card"})
    )
    result["oauth_security_schemes_ok"] = bool(tools.tools) and all(
        any(
            scheme.get("type") == "oauth2"
            and set(DEFAULT_KANBAN_SCOPES).issubset(set(scheme.get("scopes") or []))
            for scheme in ((tool.meta or {}).get("securitySchemes") or [])
            if isinstance(scheme, dict)
        )
        for tool in tools.tools
    )

    if result["has_ping_connector"]:
        await _capture_mcp_tool_result(session, result, "ping_connector", "ping_ok", "ping_data")

    if result["has_agent_bootstrap"]:
        await _capture_mcp_tool_result(
            session, result, "agent_bootstrap", "bootstrap_ok", "bootstrap_data"
        )

    if result["has_agent_board_digest"]:
        await _capture_mcp_tool_result(
            session, result, "agent_board_digest", "digest_ok", "digest_data"
        )

    if result["has_get_runtime_status"]:
        await _capture_mcp_tool_result(
            session, result, "get_runtime_status", "runtime_ok", "runtime_data"
        )

    if result["has_get_connector_identity"]:
        await _capture_mcp_tool_result(
            session, result, "get_connector_identity", "identity_ok", "identity_data"
        )

    result["ok"] = all(
        [
            result["has_ping_connector"],
            result["has_agent_bootstrap"],
            result["has_agent_board_digest"],
            result["has_get_runtime_status"],
            result["has_get_connector_identity"],
            result["ping_ok"],
            result["bootstrap_ok"],
            result["digest_ok"],
            result["runtime_ok"],
            result["identity_ok"],
            result["tool_count"] == 24,
            result["legacy_tools_absent"],
            result["oauth_security_schemes_ok"],
        ]
    )
    if not result["ok"]:
        result["error"] = "mcp_surface_incomplete"
    return result


async def _capture_mcp_tool_result(
    session: ClientSession,
    result: dict[str, Any],
    tool_name: str,
    ok_key: str,
    data_key: str,
) -> None:
    tool_result = await session.call_tool(tool_name, {})
    result[ok_key] = bool(
        not tool_result.isError
        and isinstance(tool_result.structuredContent, dict)
        and tool_result.structuredContent.get("ok")
    )
    if isinstance(tool_result.structuredContent, dict):
        result[data_key] = dict(tool_result.structuredContent)


def _print_api_surface(report: dict[str, Any]) -> None:
    print_section("API SURFACE")
    print(f"base_url: {report.get('base_url') or '<not found>'}")
    print(f"surface_kind: {report.get('surface_kind') or '<unknown>'}")
    if not report.get("checked"):
        print("status: skipped")
        print(f"reason: {report.get('error') or 'API credentials were not provided'}")
        return
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
        if report.get("script_asset_error"):
            print(f"script_asset_error: {report.get('script_asset_error')}")


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


def _print_public_read_protection(report: dict[str, Any]) -> None:
    print_section("PUBLIC READ PROTECTION")
    print(f"site_url: {report.get('site_url') or '<not configured>'}")
    if not report.get("checked"):
        print("status: skipped")
        print("reason: site url was not provided")
        return
    if report.get("ok"):
        print("status: ok")
        print("anonymous reads: blocked")
        print(f"status_code: {report.get('status_code')}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")
        print(f"status_code: {report.get('status_code')}")


def _print_mcp(report: dict[str, Any]) -> None:
    print_section("MCP")
    print(f"mcp_url: {report.get('mcp_url') or '<not configured>'}")
    if not report.get("checked"):
        print("status: skipped")
        print(f"reason: {report.get('error') or 'MCP check was not requested'}")
        return
    if report.get("ok"):
        ping_payload = report.get("ping_data") or {}
        bootstrap_data = report.get("bootstrap_data") or {}
        runtime_payload = report.get("runtime_data") or {}
        identity_payload_root = report.get("identity_data") or {}
        digest_data = report.get("digest_data") or {}
        ping_data = ping_payload.get("data") or ping_payload
        runtime_data = runtime_payload.get("data") or runtime_payload
        identity_data = identity_payload_root.get("data") or identity_payload_root
        runtime_status = runtime_data.get("runtime_status") or {}
        runtime_api_status = (
            ((runtime_status.get("api_health") or {}).get("status"))
            or runtime_status.get("status")
            or "<unknown>"
        )
        print("status: ok")
        print(f"tool_count: {report.get('tool_count')}")
        print("legacy_tools_absent: True")
        print("oauth_metadata: ok")
        print("oauth_pkce_s256: True")
        print("oauth_refresh_supported: True")
        print("oauth_security_schemes: ok")
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
        print(f"runtime_status: {runtime_api_status}")
        print(f"gateway_format: {bootstrap_data.get('format', '<unknown>')}")
        summary = digest_data.get("summary") or {}
        print(f"digest_cards: {summary.get('returned', 0)}")
    else:
        print("status: failed")
        print(f"error: {report.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only live diagnostics for AutoStop CRM site, API and MCP."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON instead of human text."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code when a checked surface fails.",
    )
    parser.add_argument(
        "--site-url",
        default="",
        help="Explicit public CRM URL, for example https://crm.autostopcrm.ru.",
    )
    parser.add_argument(
        "--skip-public-site",
        action="store_true",
        help="Skip probing the public CRM site surface even if a public URL is configured in settings.",
    )
    parser.add_argument(
        "--skip-public-write-protection",
        action="store_true",
        help="Skip the anonymous public read/write protection probes.",
    )
    parser.add_argument(
        "--expect-https",
        action="store_true",
        help="Require the site URL and final public URL to use https.",
    )
    parser.add_argument(
        "--local-api-url",
        default="",
        help="Explicit local API base URL, for example http://127.0.0.1:41731.",
    )
    parser.add_argument("--local-api-token", default=None, help="Optional local API bearer token.")
    parser.add_argument(
        "--mcp-url", default="", help="Explicit MCP URL, for example http://127.0.0.1:41831/mcp."
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Skip legacy MCP tool checks when Agent Gateway v2 is verified separately.",
    )
    parser.add_argument("--mcp-token", default=None, help="Optional MCP bearer token.")
    parser.add_argument(
        "--operator-username", default="", help="Optional operator username for auth verification."
    )
    parser.add_argument(
        "--operator-password", default="", help="Optional operator password for auth verification."
    )
    parser.add_argument(
        "--expect-admin",
        action="store_true",
        help="Require the operator credentials to resolve to an admin session.",
    )
    args = parser.parse_args()

    settings = load_settings()
    site_url = "" if args.skip_public_site else _resolve_site_url(settings, args.site_url)
    local_api_token = _resolve_local_api_token(settings, args.local_api_token)
    local_api_url = _resolve_local_api_url(settings, args.local_api_url, local_api_token)
    mcp_url = "" if args.skip_mcp else _resolve_mcp_url(settings, args.mcp_url)
    mcp_token = _resolve_mcp_token(settings, args.mcp_token)
    operator_username = args.operator_username or os.environ.get(
        "AUTOSTOP_SMOKE_OPERATOR_USERNAME", ""
    )
    operator_password = args.operator_password or os.environ.get(
        "AUTOSTOP_SMOKE_OPERATOR_PASSWORD", ""
    )
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
    if api_probe_kind == "public" and not local_api_token:
        api_surface = check_api_surface("")
        api_surface.update(
            {
                "base_url": api_probe_url,
                "surface_kind": api_probe_kind,
                "error": "public_api_credentials_not_provided",
            }
        )
    else:
        api_surface = check_api_surface(api_probe_url, bearer_token=local_api_token or None)
        api_surface["surface_kind"] = api_probe_kind
    operator_auth = check_operator_auth(
        api_probe_url,
        username=operator_username,
        password=operator_password,
        bearer_token=local_api_token or None,
        expect_admin=args.expect_admin,
    )
    operator_auth["surface_kind"] = api_probe_kind
    public_auth_site_url = "" if args.skip_public_write_protection else site_url
    public_read_protection = check_public_read_protection(
        public_auth_site_url, require_https=args.expect_https
    )
    public_write_protection = check_public_write_protection(
        public_auth_site_url, require_https=args.expect_https
    )
    mcp_surface = asyncio.run(check_mcp(mcp_url, bearer_token=mcp_token or None))
    if args.skip_mcp:
        mcp_surface["error"] = "mcp_check_skipped"

    report = {
        "settings_file": str(get_settings_file()),
        "site_surface": site_surface,
        "api_surface": api_surface,
        "operator_auth": operator_auth,
        "public_read_protection": public_read_protection,
        "public_write_protection": public_write_protection,
        "mcp_surface": mcp_surface,
    }

    if args.json:
        _emit_output(_json_dumps(report, indent=2))
    else:
        print("AutoStop CRM live diagnostics")
        print(f"settings_file: {report['settings_file']}")
        _print_site(site_surface)
        _print_api_surface(api_surface)
        _print_operator_auth(operator_auth)
        _print_public_read_protection(public_read_protection)
        _print_public_write_protection(public_write_protection)
        _print_mcp(mcp_surface)

    if not args.strict:
        return 0

    checked_sections = [
        ("site_surface", site_surface.get("checked"), site_surface.get("ok")),
        ("api_surface", api_surface.get("checked"), api_surface.get("ok")),
        ("operator_auth", operator_auth.get("checked"), operator_auth.get("ok")),
        (
            "public_read_protection",
            public_read_protection.get("checked"),
            public_read_protection.get("ok"),
        ),
        (
            "public_write_protection",
            public_write_protection.get("checked"),
            public_write_protection.get("ok"),
        ),
        ("mcp_surface", mcp_surface.get("checked"), mcp_surface.get("ok")),
    ]
    failed = [name for name, checked, ok in checked_sections if checked and not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
