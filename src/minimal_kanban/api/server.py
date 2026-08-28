from __future__ import annotations

import gzip
import hmac
import ipaddress
import json
import logging
import math
import re
import socket
import sys
import threading
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from pathlib import Path, PurePath
from time import perf_counter, sleep
from urllib.parse import parse_qs, quote, urlsplit

from ..config import (
    get_api_bearer_token,
    get_api_host,
    get_api_port,
    get_api_port_fallback_limit,
    get_mcp_bearer_token,
)
from ..deployment_security import (
    is_maintenance_mode,
    load_agent_gateway_security_policy,
    release_smoke_proof_matches,
)
from ..json_safety import reject_deeply_nested_json
from ..mcp.oauth_provider import (
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    verify_oauth_audit_assertion,
)
from ..models import utc_now_iso
from ..operator_auth import OperatorAuthService
from ..operator_permissions import operator_has_permission
from ..performance import request_performance_trace
from ..services.card_service import CardService
from ..services.change_feed_service import ChangeFeedService
from ..services.errors import ServiceError
from ..services.shared_files_service import SharedFilesService
from ..services.snapshot_cache import PreparedSnapshotData
from ..storage.json_store import StateFileCorruptedError
from ..storage.limited_io import read_bytes_limited
from ..system_clipboard import ClipboardUnavailableError, list_clipboard_file_paths
from ..web_assets import (
    BOARD_WEB_APP_CSS,
    BOARD_WEB_APP_CSS_PATH,
    BOARD_WEB_APP_HTML,
    BOARD_WEB_APP_JS,
    BOARD_WEB_APP_JS_PATH,
    DISPLAY_DASHBOARD_HTML,
    MODULE_MAP_HTML,
    MODULE_MAP_INFRASTRUCTURE,
)
from .change_feed import (
    build_change_feed_routes,
)
from .route_registry import (
    build_operator_routes,
    build_route_specs,
    build_service_routes,
    merge_route_specs,
)
from .salary_reconciliation_html import _employee_salary_reconciliation_print_html

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
MODULE_MAP_INFRASTRUCTURE_ROUTE = "/api/get_module_map_infrastructure"


QUIET_SUCCESS_ROUTES = frozenset(
    {
        "/api/health",
        "/api/get_board_revision",
        "/api/get_board_snapshot",
        "/api/get_display_dashboard",
        "/api/mark_card_seen",
        "/api/mark_cashbox_notifications_seen",
    }
)

JSON_GZIP_MIN_BYTES = 1024
MAX_JSON_BODY_BYTES = 25 * 1024 * 1024
OVERSIZED_JSON_DRAIN_BYTES = 1024 * 1024
API_FILE_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
STATIC_ASSET_MAX_BYTES = 1 * 1024 * 1024
IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
MAX_QUERY_STRING_BYTES = 16 * 1024
MAX_QUERY_FIELDS = 128
OPERATOR_LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
OPERATOR_LOGIN_FAILURE_LIMIT_PER_CLIENT = 8
OPERATOR_LOGIN_RATE_LIMIT_MAX_CLIENTS = 2048
HTTP_QVALUE_RE = re.compile(r"^(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)$")
BOOLEAN_QUERY_KEYS = frozenset(
    {
        "allow_linked",
        "compact",
        "compact_groups",
        "create_vehicle_from_card",
        "dry_run",
        "include_archive",
        "include_archived",
        "include_base64",
        "include_full_details",
        "include_markdown",
        "include_removed",
        "include_repair_order_text",
        "include_stats",
        "only_missing",
        "only_stale",
        "overwrite",
        "overwrite_card_fields",
        "redact_private",
        "refresh_summary",
        "sync_fields",
        "sync_linked_cards",
        "sync_vehicle_fields",
    }
)
ACTIVE_INLINE_MIME_TYPES = frozenset(
    {
        "application/xhtml+xml",
        "application/xml",
        "image/svg+xml",
        "text/html",
        "text/xml",
    }
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant: {value}")


def _json_safe_value(value: object, *, depth: int = 8) -> object:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item, depth=depth - 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_response(
    *,
    ok: bool,
    data: dict | None = None,
    error: dict | None = None,
    request_id: str,
) -> bytes:
    payload = {
        "ok": ok,
        "data": data,
        "error": error,
        "meta": {
            "request_id": request_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _json_response_from_preencoded_data(*, data: bytes, request_id: str) -> bytes:
    response_meta = json.dumps(
        {
            "request_id": request_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return b'{"ok": true, "data": ' + data + b', "error": null, "meta": ' + response_meta + b"}"


def _success_log_level(route: str) -> int:
    return logging.DEBUG if route in QUIET_SUCCESS_ROUTES else logging.INFO


def _safe_request_target(value: object) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return "<invalid-request-target>"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?<redacted>"
    if parsed.fragment:
        path = f"{path}#<redacted>"
    return path


def _request_target_parts(value: object):
    try:
        return urlsplit(str(value or ""))
    except ValueError:
        return None


def _origin_default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _same_host_cors_origin(
    origin: object,
    host: object,
    *,
    allow_named_host: bool = False,
) -> str:
    raw_origin = str(origin or "").strip()
    raw_host = str(host or "").strip()
    if not raw_origin or not raw_host:
        return ""
    try:
        origin_parts = urlsplit(raw_origin)
        host_parts = urlsplit(f"//{raw_host}")
    except ValueError:
        return ""
    scheme = origin_parts.scheme.lower()
    if scheme not in {"http", "https"} or not origin_parts.netloc:
        return ""
    origin_host = (origin_parts.hostname or "").lower().rstrip(".")
    request_host = (host_parts.hostname or "").lower().rstrip(".")
    if not origin_host or origin_host != request_host:
        return ""
    if not allow_named_host and request_host != "localhost":
        try:
            ipaddress.ip_address(request_host)
        except ValueError:
            return ""
    default_port = _origin_default_port(scheme)
    try:
        origin_port = origin_parts.port or default_port
        request_port = host_parts.port or default_port
    except ValueError:
        return ""
    if origin_port != request_port:
        return ""
    return f"{scheme}://{origin_parts.netloc.lower()}"


def _accepts_gzip(value: object) -> bool:
    gzip_qualities: list[float] = []
    wildcard_qualities: list[float] = []
    for member in str(value or "").split(","):
        parts = [part.strip() for part in member.split(";")]
        coding = parts[0].casefold()
        if coding not in {"gzip", "*"}:
            continue
        quality = 1.0
        quality_seen = False
        for parameter in parts[1:]:
            name, separator, raw_quality = parameter.partition("=")
            if name.strip().casefold() != "q":
                continue
            normalized_quality = raw_quality.strip()
            if (
                quality_seen
                or not separator
                or HTTP_QVALUE_RE.fullmatch(normalized_quality) is None
            ):
                quality = 0.0
                break
            quality_seen = True
            quality = float(normalized_quality)
        target = gzip_qualities if coding == "gzip" else wildcard_qualities
        target.append(quality)
    if gzip_qualities:
        return max(gzip_qualities) > 0
    return bool(wildcard_qualities and max(wildcard_qualities) > 0)


def _shared_file_clipboard_position(value: object, *, default: int = 24) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(str(value))
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed) or not parsed.is_integer():
        return default
    if parsed > 100_000:
        return 100_000
    try:
        return max(0, int(parsed))
    except (TypeError, ValueError, OverflowError):
        return default


def _content_length_header(value: object) -> int | None:
    text = "0" if value is None or value == "" else str(value).strip()
    if not text:
        return 0
    sign = -1 if text.startswith("-") else 1
    digits = text[1:] if text[:1] in {"+", "-"} else text
    if not digits.isdecimal():
        return None
    max_digits = len(str(MAX_JSON_BODY_BYTES))
    if len(digits) > max_digits:
        return sign * (MAX_JSON_BODY_BYTES + 1)
    return sign * int(digits)


def _ascii_download_name(file_name: str, *, fallback: str = "attachment") -> str:
    suffix = PurePath(str(file_name or "")).suffix
    stem = str(file_name or "")
    if suffix:
        stem = stem[: -len(suffix)]
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    ascii_stem = re.sub(r"[^A-Za-z0-9!#$&+.^_`|~-]+", "_", ascii_stem).strip("._") or fallback
    ascii_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix) or ""
    return f"{ascii_stem}{ascii_suffix}"


def _content_disposition_header(file_name: str, *, disposition: str) -> str:
    fallback_name = _ascii_download_name(file_name)
    return (
        f"{disposition}; filename=\"{fallback_name}\"; filename*=UTF-8''{quote(file_name, safe='')}"
    )


def _is_active_inline_mime_type(value: object) -> bool:
    mime_type = str(value or "").split(";", 1)[0].strip().lower()
    return mime_type in ACTIVE_INLINE_MIME_TYPES or mime_type.endswith("+xml")


def _shared_file_response_metadata(
    file_meta: dict[str, object], *, disposition: str
) -> tuple[str, str]:
    content_type = str(file_meta.get("mime_type") or "application/octet-stream")
    if disposition == "inline" and _is_active_inline_mime_type(content_type):
        return "attachment", "application/octet-stream"
    return disposition, content_type


def _read_bounded_file_response(path: Path) -> bytes:
    return read_bytes_limited(
        path,
        max_bytes=API_FILE_RESPONSE_MAX_BYTES,
        label="API file response",
    )


@cache
def _static_asset_bytes(file_name: str) -> bytes:
    return read_bytes_limited(
        STATIC_DIR / file_name,
        max_bytes=STATIC_ASSET_MAX_BYTES,
        label="static asset",
    )


@cache
def _board_html_bytes() -> bytes:
    return BOARD_WEB_APP_HTML.encode("utf-8")


@cache
def _board_html_gzip_bytes() -> bytes:
    return gzip.compress(_board_html_bytes())


_BOARD_ASSETS = {
    BOARD_WEB_APP_CSS_PATH: (
        BOARD_WEB_APP_CSS.encode("utf-8"),
        "text/css; charset=utf-8",
    ),
    BOARD_WEB_APP_JS_PATH: (
        BOARD_WEB_APP_JS.encode("utf-8"),
        "application/javascript; charset=utf-8",
    ),
}
_BOARD_ASSETS_GZIP = {
    route: gzip.compress(asset[0], mtime=0) for route, asset in _BOARD_ASSETS.items()
}


def _board_asset_bytes(route: str) -> tuple[bytes, str] | None:
    return _BOARD_ASSETS.get(route)


def _board_asset_gzip_bytes(route: str) -> bytes | None:
    return _BOARD_ASSETS_GZIP.get(route)


@cache
def _display_dashboard_html_bytes() -> bytes:
    return DISPLAY_DASHBOARD_HTML.encode("utf-8")


@cache
def _display_dashboard_html_gzip_bytes() -> bytes:
    return gzip.compress(_display_dashboard_html_bytes())


@cache
def _module_map_html_bytes() -> bytes:
    return MODULE_MAP_HTML.encode("utf-8")


@cache
def _module_map_html_gzip_bytes() -> bytes:
    return gzip.compress(_module_map_html_bytes())


def _display_dashboard_shared_file_info(
    shared_files_service: SharedFilesService,
    file_id: str,
) -> dict | None:
    try:
        result = shared_files_service.get_shared_file_info({"file_id": file_id})
    except ServiceError:
        return None
    file_info = result.get("file") if isinstance(result, dict) else None
    return file_info if isinstance(file_info, dict) else None


class RequestContextFactory:
    """Parse bounded HTTP input without deciding authentication or routing."""

    @staticmethod
    def drain_request_body(handler: BaseHTTPRequestHandler, content_length: int) -> None:
        remaining = max(0, int(content_length))
        while remaining > 0:
            try:
                chunk = handler.rfile.read(min(65536, remaining))
            except OSError:
                break
            if not chunk:
                break
            remaining -= len(chunk)

    @staticmethod
    def query_payload(query_string: str) -> dict:
        query_size_bytes = len(query_string.encode("utf-8", errors="surrogatepass"))
        if query_size_bytes > MAX_QUERY_STRING_BYTES:
            raise ServiceError(
                "request_too_large",
                "Строка запроса превышает допустимый лимит.",
                status_code=HTTPStatus.REQUEST_URI_TOO_LONG,
                details={
                    "max_size_bytes": MAX_QUERY_STRING_BYTES,
                    "query_size_bytes": query_size_bytes,
                },
            )
        try:
            parsed = parse_qs(
                query_string,
                keep_blank_values=True,
                max_num_fields=MAX_QUERY_FIELDS,
            )
        except ValueError as exc:
            raise ServiceError(
                "request_too_large",
                "Строка запроса содержит слишком много параметров.",
                status_code=HTTPStatus.REQUEST_URI_TOO_LONG,
                details={"max_fields": MAX_QUERY_FIELDS},
            ) from exc
        payload: dict[str, object] = {}
        for key, values in parsed.items():
            if not values:
                continue
            value = values[-1]
            if key == "access_token":
                payload[key] = value
                continue
            lowered = value.lower()
            if key in BOOLEAN_QUERY_KEYS and lowered in {"true", "1", "yes", "y", "on"}:
                payload[key] = True
            elif key in BOOLEAN_QUERY_KEYS and lowered in {
                "false",
                "0",
                "no",
                "n",
                "off",
            }:
                payload[key] = False
            else:
                payload[key] = value
        return payload

    @staticmethod
    def read_json_object(handler: BaseHTTPRequestHandler, content_length: int) -> dict:
        raw_body = handler.rfile.read(content_length) if content_length > 0 else b"{}"
        if content_length and len(raw_body) != content_length:
            handler.close_connection = True
            raise ServiceError(
                "invalid_json",
                "Тело запроса передано не полностью.",
                status_code=HTTPStatus.BAD_REQUEST,
            )
        try:
            payload = json.loads(
                raw_body.decode("utf-8") or "{}",
                parse_constant=_reject_json_constant,
            )
            reject_deeply_nested_json(payload)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ServiceError(
                "invalid_json",
                "Тело запроса должно содержать корректный JSON.",
                status_code=HTTPStatus.BAD_REQUEST,
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceError(
                "validation_error",
                "Тело запроса должно быть JSON-объектом.",
                status_code=HTTPStatus.BAD_REQUEST,
            )
        return payload


class HttpResponseWriter:
    """Write the established API envelopes and transport headers."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    def send_error(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
        *,
        server_timing: str = "",
    ) -> None:
        body = _json_response(
            ok=False,
            data=None,
            error={"code": code, "message": message, "details": details or {}},
            request_id=request_id,
        )
        try:
            response_body, extra_headers = self.prepare_body(
                handler,
                body,
                content_type="application/json",
                server_timing=server_timing,
            )
            handler.send_response(status_code)
            self.send_headers(
                handler,
                "application/json",
                len(response_body),
                extra_headers=extra_headers,
            )
            self.write_body(
                handler,
                response_body,
                route=_safe_request_target(handler.path),
                request_id=request_id,
                status_code=status_code,
            )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self._logger.warning(
                "api_client_disconnected route=%s request_id=%s status=%s",
                _safe_request_target(handler.path),
                request_id,
                status_code,
            )

    def send_bytes(
        self,
        handler: BaseHTTPRequestHandler,
        body: bytes,
        *,
        content_type: str,
        request_id: str,
        route: str,
        status_code: int = HTTPStatus.OK,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        try:
            handler.send_response(status_code)
            self.send_headers(
                handler,
                content_type,
                len(body),
                cache_control=cache_control,
                extra_headers=extra_headers,
            )
            self.write_body(
                handler,
                body,
                route=route,
                request_id=request_id,
                status_code=status_code,
            )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            self._logger.warning(
                "api_client_disconnected route=%s request_id=%s status=%s error_type=%s",
                route,
                request_id,
                status_code,
                type(exc).__name__,
            )

    def send_headers(
        self,
        handler: BaseHTTPRequestHandler,
        content_type: str,
        content_length: int,
        *,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(content_length))
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("Connection", "close")
        handler.close_connection = True
        provided_headers = {key.casefold() for key in (extra_headers or {})}
        if "x-content-type-options" not in provided_headers:
            handler.send_header("X-Content-Type-Options", "nosniff")
        cors_origin = self.cors_allowed_origin(handler)
        if cors_origin and "access-control-allow-origin" not in provided_headers:
            handler.send_header("Access-Control-Allow-Origin", cors_origin)
            handler.send_header("Vary", "Origin")
            handler.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-Operator-Session",
            )
            handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        for header, value in (extra_headers or {}).items():
            if value:
                handler.send_header(header, value)
        handler.end_headers()

    @staticmethod
    def cors_allowed_origin(handler: BaseHTTPRequestHandler) -> str:
        return _same_host_cors_origin(
            handler.headers.get("Origin", ""),
            handler.headers.get("Host", ""),
            allow_named_host=bool(
                str(handler.headers.get("X-Forwarded-For", "") or "").strip()
                or str(handler.headers.get("X-Real-IP", "") or "").strip()
            ),
        )

    @staticmethod
    def prepare_body(
        handler: BaseHTTPRequestHandler,
        body: bytes,
        *,
        content_type: str,
        server_timing: str = "",
    ) -> tuple[bytes, dict[str, str]]:
        headers: dict[str, str] = {}
        if server_timing:
            headers["Server-Timing"] = server_timing
        if (
            content_type.startswith("application/json")
            and len(body) >= JSON_GZIP_MIN_BYTES
            and _accepts_gzip(handler.headers.get("Accept-Encoding", ""))
        ):
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
            return gzip.compress(body), headers
        if content_type.startswith("application/json"):
            headers["Vary"] = "Accept-Encoding"
        return body, headers

    def write_body(
        self,
        handler: BaseHTTPRequestHandler,
        body: bytes,
        *,
        route: str,
        request_id: str,
        status_code: int,
    ) -> bool:
        try:
            handler.wfile.write(body)
            handler.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            self._logger.warning(
                "api_client_disconnected route=%s request_id=%s status=%s error_type=%s",
                route,
                request_id,
                status_code,
                type(exc).__name__,
            )
            return False


class StaticAndDownloadResponder:
    """Serve public assets and already-authorized binary/content routes."""

    PROTECTED_ROUTES = frozenset(
        {
            MODULE_MAP_INFRASTRUCTURE_ROUTE,
            "/api/attachment",
            "/api/shared_file",
            "/api/repair_order_text",
            "/employee_salary_reconciliation_print",
        }
    )

    def __init__(
        self,
        *,
        service: CardService,
        shared_files_service: SharedFilesService,
        logger: Logger,
        api_server: ApiServer,
        bearer_token: str,
    ) -> None:
        self._service = service
        self._shared_files_service = shared_files_service
        self._logger = logger
        self._api_server = api_server
        self._bearer_token = bearer_token

    def serve_head(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        route: str,
        request_id: str,
    ) -> bool:
        if route in {"/", "/index.html"}:
            body = _board_html_bytes()
            handler.send_response(HTTPStatus.OK)
            handler._send_headers("text/html; charset=utf-8", len(body))
            return True
        if route in {"/dashboard", "/dashboard/"}:
            body = _display_dashboard_html_bytes()
            handler.send_response(HTTPStatus.OK)
            handler._send_headers("text/html; charset=utf-8", len(body))
            return True
        if route in {"/module-map", "/module-map/"}:
            gzip_ok = _accepts_gzip(handler.headers.get("Accept-Encoding", ""))
            body = _module_map_html_gzip_bytes() if gzip_ok else _module_map_html_bytes()
            extra_headers = {"Vary": "Accept-Encoding"}
            if gzip_ok:
                extra_headers["Content-Encoding"] = "gzip"
            handler.send_response(HTTPStatus.OK)
            handler._send_headers(
                "text/html; charset=utf-8",
                len(body),
                extra_headers=extra_headers,
            )
            return True
        board_asset = _board_asset_bytes(route)
        if board_asset is not None:
            body, content_type = board_asset
            extra_headers = {"Vary": "Accept-Encoding"}
            if _accepts_gzip(handler.headers.get("Accept-Encoding", "")):
                body = _board_asset_gzip_bytes(route) or body
                extra_headers["Content-Encoding"] = "gzip"
            handler.send_response(HTTPStatus.OK)
            handler._send_headers(
                content_type,
                len(body),
                cache_control=IMMUTABLE_ASSET_CACHE_CONTROL,
                extra_headers=extra_headers,
            )
            return True
        if route == "/favicon.ico":
            body = _static_asset_bytes("favicon.ico")
            handler.send_response(HTTPStatus.OK)
            handler._send_headers(
                "image/x-icon",
                len(body),
                cache_control="public, max-age=86400, immutable",
            )
            return True
        if route == "/favicon.png":
            body = _static_asset_bytes("favicon.png")
            handler.send_response(HTTPStatus.OK)
            handler._send_headers(
                "image/png",
                len(body),
                cache_control="public, max-age=86400, immutable",
            )
            return True
        if route == "/api/health":
            body = self._health_body(handler, request_id)
            handler.send_response(HTTPStatus.OK)
            handler._send_headers("application/json", len(body))
            return True
        return False

    def serve_static_route(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        route: str,
        request_id: str,
    ) -> bool:
        if route in {"/", "/index.html"}:
            self._serve_board(handler, request_id)
            return True
        if route in {"/dashboard", "/dashboard/"}:
            self._serve_display_dashboard(handler, request_id)
            return True
        if route in {"/module-map", "/module-map/"}:
            self._serve_module_map(handler, request_id)
            return True
        board_asset = _board_asset_bytes(route)
        if board_asset is not None:
            body, content_type = board_asset
            extra_headers = {"Vary": "Accept-Encoding"}
            if _accepts_gzip(handler.headers.get("Accept-Encoding", "")):
                body = _board_asset_gzip_bytes(route) or body
                extra_headers["Content-Encoding"] = "gzip"
            handler._send_bytes_response(
                body,
                content_type=content_type,
                request_id=request_id,
                route=route,
                cache_control=IMMUTABLE_ASSET_CACHE_CONTROL,
                extra_headers=extra_headers,
            )
            return True
        if route == "/favicon.ico":
            body = _static_asset_bytes("favicon.ico")
            handler._send_bytes_response(
                body,
                content_type="image/x-icon",
                request_id=request_id,
                route=route,
                cache_control="public, max-age=86400, immutable",
            )
            return True
        if route == "/favicon.png":
            body = _static_asset_bytes("favicon.png")
            handler._send_bytes_response(
                body,
                content_type="image/png",
                request_id=request_id,
                route=route,
                cache_control="public, max-age=86400, immutable",
            )
            return True
        if route == "/api/health":
            handler._send_bytes_response(
                self._health_body(handler, request_id),
                content_type="application/json",
                request_id=request_id,
                route=route,
            )
            return True
        return False

    def serve_protected_route(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        route: str,
        request_id: str,
        payload: dict,
    ) -> None:
        handlers = {
            MODULE_MAP_INFRASTRUCTURE_ROUTE: self._serve_module_map_infrastructure,
            "/api/attachment": self._serve_attachment,
            "/api/shared_file": self._serve_shared_file,
            "/api/repair_order_text": self._serve_repair_order_text,
            "/employee_salary_reconciliation_print": self._serve_employee_salary_reconciliation_print,
        }
        handlers[route](handler, request_id, payload)

    def _health_body(self, handler: BaseHTTPRequestHandler, request_id: str) -> bytes:
        return _json_response(
            ok=True,
            data={
                "status": "ok",
                "base_url": self._api_server.base_url,
                "bind_host": handler.server.server_address[0],
                "auth_required": bool(self._bearer_token),
                "maintenance_mode": is_maintenance_mode(),
            },
            error=None,
            request_id=request_id,
        )

    @staticmethod
    def _serve_board(handler: BaseHTTPRequestHandler, request_id: str) -> None:
        gzip_ok = _accepts_gzip(handler.headers.get("Accept-Encoding", ""))
        body = _board_html_gzip_bytes() if gzip_ok else _board_html_bytes()
        extra_headers = {"Vary": "Accept-Encoding"}
        if gzip_ok:
            extra_headers["Content-Encoding"] = "gzip"
        handler._send_bytes_response(
            body,
            content_type="text/html; charset=utf-8",
            request_id=request_id,
            route=urlsplit(handler.path).path or "/",
            extra_headers=extra_headers,
        )

    @staticmethod
    def _serve_display_dashboard(handler: BaseHTTPRequestHandler, request_id: str) -> None:
        gzip_ok = _accepts_gzip(handler.headers.get("Accept-Encoding", ""))
        body = _display_dashboard_html_gzip_bytes() if gzip_ok else _display_dashboard_html_bytes()
        extra_headers = {"Vary": "Accept-Encoding"}
        if gzip_ok:
            extra_headers["Content-Encoding"] = "gzip"
        handler._send_bytes_response(
            body,
            content_type="text/html; charset=utf-8",
            request_id=request_id,
            route=urlsplit(handler.path).path or "/dashboard",
            extra_headers=extra_headers,
        )

    @staticmethod
    def _serve_module_map(handler: BaseHTTPRequestHandler, request_id: str) -> None:
        gzip_ok = _accepts_gzip(handler.headers.get("Accept-Encoding", ""))
        body = _module_map_html_gzip_bytes() if gzip_ok else _module_map_html_bytes()
        extra_headers = {"Vary": "Accept-Encoding"}
        if gzip_ok:
            extra_headers["Content-Encoding"] = "gzip"
        handler._send_bytes_response(
            body,
            content_type="text/html; charset=utf-8",
            request_id=request_id,
            route=urlsplit(handler.path).path or "/module-map",
            extra_headers=extra_headers,
        )

    @staticmethod
    def _serve_module_map_infrastructure(
        handler: BaseHTTPRequestHandler,
        request_id: str,
        _payload: dict,
    ) -> None:
        body = _json_response(
            ok=True,
            data=MODULE_MAP_INFRASTRUCTURE,
            error=None,
            request_id=request_id,
        )
        response_body, extra_headers = handler._prepare_response_body(
            body,
            content_type="application/json",
        )
        handler._send_bytes_response(
            response_body,
            content_type="application/json",
            request_id=request_id,
            route=MODULE_MAP_INFRASTRUCTURE_ROUTE,
            extra_headers=extra_headers,
        )

    def _serve_attachment(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        payload: dict,
    ) -> None:
        try:
            path, attachment = self._service.get_attachment_download(
                str(payload.get("card_id", "")),
                str(payload.get("attachment_id", "")),
            )
            body = _read_bounded_file_response(path)
            handler._send_bytes_response(
                body,
                content_type=attachment.mime_type or "application/octet-stream",
                request_id=request_id,
                route=urlsplit(handler.path).path,
                extra_headers={
                    "Content-Disposition": _content_disposition_header(
                        attachment.file_name,
                        disposition="attachment",
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except ServiceError as exc:
            handler._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
        except FileNotFoundError:
            handler._send_error_response(
                request_id,
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Файл не найден на диске.",
            )
        except ValueError:
            handler._send_error_response(
                request_id,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "validation_error",
                "Файл слишком большой для скачивания через API.",
            )

    def _serve_shared_file(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        payload: dict,
    ) -> None:
        try:
            path, file_meta = self._shared_files_service.get_shared_file_download(
                str(payload.get("file_id", ""))
            )
            body = _read_bounded_file_response(path)
            disposition = (
                "inline"
                if str(payload.get("disposition", "")).strip().lower() == "inline"
                else "attachment"
            )
            disposition, content_type = _shared_file_response_metadata(
                file_meta,
                disposition=disposition,
            )
            handler._send_bytes_response(
                body,
                content_type=content_type,
                request_id=request_id,
                route=urlsplit(handler.path).path,
                extra_headers={
                    "Content-Disposition": _content_disposition_header(
                        str(file_meta.get("original_name") or "shared-file"),
                        disposition=disposition,
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except ServiceError as exc:
            handler._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
        except FileNotFoundError:
            handler._send_error_response(
                request_id,
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Файл не найден на диске.",
            )
        except ValueError:
            handler._send_error_response(
                request_id,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "validation_error",
                "Файл слишком большой для скачивания через API.",
            )

    def _serve_repair_order_text(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        payload: dict,
    ) -> None:
        try:
            path, file_name = self._service.get_repair_order_text_download(
                str(payload.get("card_id", ""))
            )
            body = _read_bounded_file_response(path)
            handler._send_bytes_response(
                body,
                content_type="text/plain; charset=utf-8",
                request_id=request_id,
                route=urlsplit(handler.path).path,
                extra_headers={
                    "Content-Disposition": _content_disposition_header(
                        file_name,
                        disposition="inline",
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except ServiceError as exc:
            handler._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
        except FileNotFoundError:
            handler._send_error_response(
                request_id,
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Файл заказ-наряда не найден на диске.",
            )
        except ValueError:
            handler._send_error_response(
                request_id,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "validation_error",
                "Файл заказ-наряда слишком большой для скачивания через API.",
            )

    def _serve_employee_salary_reconciliation_print(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        _payload: dict,
    ) -> None:
        route = "/employee_salary_reconciliation_print"
        started_at = perf_counter()
        try:
            report = self._service.get_employee_salary_reconciliation(_payload)
            body = _employee_salary_reconciliation_print_html(report)
            app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
            handler._send_bytes_response(
                body,
                content_type="text/html; charset=utf-8",
                request_id=request_id,
                route=route,
                extra_headers={"Server-Timing": f"app;dur={app_duration_ms:.1f}"},
            )
            self._logger.log(
                _success_log_level(route),
                "api_request route=%s request_id=%s status=ok duration_ms=%.1f body_bytes=%s",
                route,
                request_id,
                app_duration_ms,
                len(body),
            )
        except ServiceError as exc:
            self._logger.warning(
                "api_request route=%s request_id=%s status=error code=%s",
                route,
                request_id,
                exc.code,
            )
            handler._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
        except Exception as exc:  # pragma: no cover
            self._logger.error(
                "api_request_failed route=%s request_id=%s error_type=%s",
                route,
                request_id,
                type(exc).__name__,
            )
            handler._send_error_response(
                request_id,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "На сервере произошла непредвиденная ошибка.",
            )


class OperatorLoginLimiter:
    """Keep failed-login reservations isolated to one ApiServer handler factory."""

    def __init__(self) -> None:
        self._attempts: dict[str, list[tuple[float, str]]] = {}
        self._lock = threading.Lock()

    def reserve(self, client_key: str, request_id: str) -> bool:
        now = perf_counter()
        with self._lock:
            recent = [
                attempt
                for attempt in self._attempts.pop(client_key, [])
                if now - attempt[0] < OPERATOR_LOGIN_FAILURE_WINDOW_SECONDS
            ]
            if len(recent) >= OPERATOR_LOGIN_FAILURE_LIMIT_PER_CLIENT:
                self._attempts[client_key] = recent
                return False
            recent.append((now, request_id))
            self._attempts[client_key] = recent
            while len(self._attempts) > OPERATOR_LOGIN_RATE_LIMIT_MAX_CLIENTS:
                self._attempts.pop(next(iter(self._attempts)), None)
            return True

    def release(
        self,
        client_key: str,
        request_id: str,
        *,
        clear_client: bool = False,
    ) -> None:
        with self._lock:
            if clear_client:
                self._attempts.pop(client_key, None)
                return
            retained = [
                attempt
                for attempt in self._attempts.get(client_key, [])
                if attempt[1] != request_id
            ]
            if retained:
                self._attempts[client_key] = retained
            else:
                self._attempts.pop(client_key, None)

    @staticmethod
    def client_key(handler: BaseHTTPRequestHandler) -> str:
        peer_host = str(handler.client_address[0] if handler.client_address else "unknown")
        try:
            peer_ip = ipaddress.ip_address(peer_host)
        except ValueError:
            return peer_host
        if peer_ip.is_loopback or peer_ip.is_private:
            real_ip_header = str(handler.headers.get("X-Real-IP", "") or "").strip()
            try:
                real_ip = ipaddress.ip_address(real_ip_header)
            except ValueError:
                pass
            else:
                if not real_ip.is_unspecified:
                    return real_ip.compressed
        return peer_ip.compressed


class AuthenticationPolicy:
    """Apply bearer, operator, admin and trusted-service authentication rules."""

    def __init__(
        self,
        *,
        bearer_token: str,
        operator_service: OperatorAuthService | None,
        readonly_routes: set[str],
        operator_session_routes: set[str],
        admin_only_routes: set[str],
        maintenance_technical_write_routes: set[str],
        operator_permission_routes: dict[str, str] | None = None,
    ) -> None:
        self._bearer_token = bearer_token
        self._operator_service = operator_service
        self._readonly_routes = frozenset(readonly_routes)
        self._operator_session_routes = frozenset(operator_session_routes)
        self._admin_only_routes = frozenset(admin_only_routes)
        self._maintenance_technical_write_routes = frozenset(maintenance_technical_write_routes)
        self._operator_permission_routes = dict(operator_permission_routes or {})
        self._login_limiter = OperatorLoginLimiter()

    def authenticate(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        query: dict | None = None,
    ) -> bool:
        if not self._bearer_token:
            return True
        auth_header = handler.headers.get("Authorization", "")
        if hmac.compare_digest(auth_header, f"Bearer {self._bearer_token}"):
            return True
        try:
            query_payload = (
                query if query is not None else handler._query_payload(urlsplit(handler.path).query)
            )
            access_token = str(query_payload.get("access_token", "") or "").strip()
        except ServiceError as exc:
            handler._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
            return False
        if hmac.compare_digest(access_token, self._bearer_token):
            return True
        handler._send_error_response(
            request_id,
            HTTPStatus.UNAUTHORIZED,
            "unauthorized",
            "Для вызова локального API нужен корректный bearer token.",
        )
        return False

    def login_operator(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict,
        request_id: str,
    ) -> dict:
        client_key = self._login_limiter.client_key(handler)
        if not self._login_limiter.reserve(client_key, request_id):
            raise ServiceError(
                "rate_limited",
                "Слишком много неуспешных попыток входа. Повторите позже.",
                status_code=HTTPStatus.TOO_MANY_REQUESTS,
                details={
                    "retry_after_seconds": OPERATOR_LOGIN_FAILURE_WINDOW_SECONDS,
                },
            )
        try:
            result = handler.ROUTES["/api/login_operator"](payload)
        except ServiceError as exc:
            if exc.code != "unauthorized":
                self._login_limiter.release(client_key, request_id)
            raise
        except Exception:
            self._login_limiter.release(client_key, request_id)
            raise
        self._login_limiter.release(client_key, request_id, clear_client=True)
        return result

    @staticmethod
    def is_proxied_request(handler: BaseHTTPRequestHandler) -> bool:
        return bool(
            str(handler.headers.get("X-Forwarded-For", "") or "").strip()
            or str(handler.headers.get("X-Real-IP", "") or "").strip()
        )

    def maintenance_technical_change_feed_write_allowed(
        self,
        handler: BaseHTTPRequestHandler,
        route: str,
        payload: dict,
    ) -> bool:
        if (
            route not in self._maintenance_technical_write_routes
            or str(payload.get("consumer_id") or "").strip() != "gateway-release-smoke"
            or self._trusted_agent_session(handler, route, payload) is None
        ):
            return False
        return release_smoke_proof_matches(
            str(get_mcp_bearer_token() or ""),
            handler.headers.get("X-Autostop-Release-Smoke-Revision", ""),
            handler.headers.get("X-Autostop-Release-Smoke-Proof", ""),
        )

    def operator_context_payload(
        self,
        handler: BaseHTTPRequestHandler,
        route: str,
        payload: dict,
        request_id: str,
        *,
        resolved_operator_session: dict | None = None,
        operator_session_resolved: bool = False,
    ) -> dict | None:
        if self._operator_service is None:
            if route != "/api/login_operator" and self.is_proxied_request(handler):
                self._reject(
                    handler,
                    request_id,
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    code="operator_auth_unavailable",
                    message="Сервис входа операторов временно недоступен.",
                )
                return None
            return payload
        if operator_session_resolved:
            session = resolved_operator_session
        else:
            session = self._operator_service.resolve_session(
                handler.headers.get("X-Operator-Session", "")
            )
            if session is None:
                session = self._trusted_agent_session(handler, route, payload)
        next_payload = self._payload_with_session(route, payload, session)
        if route != "/api/login_operator" and session is None and self.is_proxied_request(handler):
            self._reject(
                handler,
                request_id,
                status=HTTPStatus.UNAUTHORIZED,
                code="unauthorized",
                message="Для доступа к рабочей CRM нужен вход оператора.",
            )
            return None
        if route in self._admin_only_routes:
            if session is None:
                self._reject(
                    handler,
                    request_id,
                    status=HTTPStatus.UNAUTHORIZED,
                    code="unauthorized",
                    message="Нужен вход администратора.",
                )
                return None
            if not session.get("is_admin"):
                self._reject(
                    handler,
                    request_id,
                    status=HTTPStatus.FORBIDDEN,
                    code="forbidden",
                    message="Нужны права администратора.",
                )
                return None
            return next_payload
        if route in self._operator_session_routes:
            if session is None:
                self._reject(
                    handler,
                    request_id,
                    status=HTTPStatus.UNAUTHORIZED,
                    code="unauthorized",
                    message="Нужен вход оператора.",
                )
                return None
            required_permission = self._operator_permission_routes.get(route, "")
            if required_permission and not operator_has_permission(session, required_permission):
                self._reject(
                    handler,
                    request_id,
                    status=HTTPStatus.FORBIDDEN,
                    code="forbidden",
                    message="Нет права на это действие.",
                )
                return None
            return next_payload
        if str(next_payload.get("source", "")).strip().lower() == "ui" and session is None:
            self._reject(
                handler,
                request_id,
                status=HTTPStatus.UNAUTHORIZED,
                code="unauthorized",
                message="Нужен вход оператора.",
            )
            return None
        return next_payload

    def _payload_with_session(
        self,
        route: str,
        payload: dict,
        session: dict | None,
    ) -> dict:
        next_payload = dict(payload)
        next_payload.pop("_operator_session", None)
        if session is not None:
            next_payload["_operator_session"] = session
            if route not in self._operator_session_routes and route not in self._admin_only_routes:
                next_payload["actor_name"] = str(
                    session.get("audit_actor_name") or session["username"]
                )
        return next_payload

    def _trusted_agent_session(
        self,
        handler: BaseHTTPRequestHandler,
        route: str,
        payload: dict,
    ) -> dict | None:
        """Authorize the local MCP service identity without impersonating a human."""

        if self.is_proxied_request(handler):
            return None
        request_source = payload.get("source")
        if not isinstance(request_source, str) or request_source.strip() != "mcp_agent_gateway_v2":
            return None
        try:
            policy = load_agent_gateway_security_policy()
        except Exception:
            return None
        if not (policy.gateway_enabled and policy.raw_enabled):
            return None
        if route not in self._readonly_routes and not policy.writes_enabled:
            return None
        identity = str(handler.headers.get("X-Autostop-Agent-Identity", "") or "").strip()
        supplied_token = str(handler.headers.get("X-Autostop-Agent-Token", "") or "").strip()
        expected_token = str(get_mcp_bearer_token() or "").strip()
        if not identity or not hmac.compare_digest(identity, policy.service_identity):
            return None
        if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
            return None
        session = {
            "token": "service-identity",
            "username": policy.service_identity,
            "role": "admin",
            "is_admin": True,
            "employee_id": "",
            "service_identity": True,
        }
        audit_actor = str(handler.headers.get(OAUTH_AUDIT_ACTOR_HEADER, "") or "").strip()
        audit_assertion = str(handler.headers.get(OAUTH_AUDIT_ASSERTION_HEADER, "") or "").strip()
        if bool(audit_actor) != bool(audit_assertion):
            raise ServiceError(
                "unauthorized",
                "OAuth-аудит Gateway не прошёл проверку.",
                status_code=HTTPStatus.UNAUTHORIZED,
            )
        if audit_actor:
            if not verify_oauth_audit_assertion(
                subject=audit_actor,
                method=handler.command,
                route=route,
                payload=payload,
                assertion=audit_assertion,
            ):
                raise ServiceError(
                    "unauthorized",
                    "OAuth-аудит Gateway не прошёл проверку.",
                    status_code=HTTPStatus.UNAUTHORIZED,
                )
            verified_actor = (
                self._operator_service.resolve_oauth_audit_admin(audit_actor)
                if self._operator_service is not None
                else None
            )
            if not verified_actor:
                raise ServiceError(
                    "unauthorized",
                    "OAuth-пользователь больше не является активным администратором CRM.",
                    status_code=HTTPStatus.UNAUTHORIZED,
                )
            session["audit_actor_name"] = verified_actor
        return session

    @staticmethod
    def _reject(
        handler: BaseHTTPRequestHandler,
        request_id: str,
        *,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        handler._send_error_response(
            request_id,
            status,
            code,
            message,
            {"auth_type": "operator_session"},
        )


class JsonRouteDispatcher:
    """Dispatch validated JSON requests through the RouteSpec-backed registry."""

    def __init__(
        self,
        *,
        service: CardService,
        logger: Logger,
        authentication_policy: AuthenticationPolicy,
    ) -> None:
        self._service = service
        self._logger = logger
        self._authentication_policy = authentication_policy

    def dispatch(
        self,
        handler: BaseHTTPRequestHandler,
        route: str,
        request_id: str,
        payload: dict,
        *,
        resolved_operator_session: dict | None = None,
        operator_session_resolved: bool = False,
    ) -> None:
        started_at = perf_counter()
        with request_performance_trace() as performance_trace:
            try:
                payload = self._authentication_policy.operator_context_payload(
                    handler,
                    route,
                    payload,
                    request_id,
                    resolved_operator_session=resolved_operator_session,
                    operator_session_resolved=operator_session_resolved,
                )
                if payload is None:
                    return
                route_spec = handler.ROUTE_SPECS.get(route)
                if route_spec is None or route_spec.path != route:
                    raise RuntimeError("Request route has no matching RouteSpec.")
                if route == "/api/get_board_snapshot":
                    result = self._service.get_board_snapshot_for_http(payload)
                elif route == "/api/login_operator":
                    result = self._authentication_policy.login_operator(
                        handler,
                        payload,
                        request_id,
                    )
                else:
                    result = handler.ROUTES[route](payload)
                if isinstance(result, PreparedSnapshotData):
                    body = _json_response_from_preencoded_data(
                        data=result.render(generated_at=utc_now_iso()),
                        request_id=request_id,
                    )
                else:
                    body = _json_response(
                        ok=True,
                        data=result,
                        error=None,
                        request_id=request_id,
                    )
                app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                server_timing = performance_trace.server_timing(app_duration_ms=app_duration_ms)
                response_body, extra_headers = handler._prepare_response_body(
                    body,
                    content_type="application/json",
                    server_timing=server_timing,
                )
                handler.send_response(HTTPStatus.OK)
                handler._send_headers(
                    "application/json",
                    len(response_body),
                    extra_headers=extra_headers,
                )
                if handler._write_body(
                    response_body,
                    route=route,
                    request_id=request_id,
                    status_code=HTTPStatus.OK,
                ):
                    self._logger.log(
                        _success_log_level(route),
                        "api_request route=%s request_id=%s status=ok duration_ms=%.1f "
                        "body_bytes=%s encoded_bytes=%s gzip=%s %s",
                        route,
                        request_id,
                        app_duration_ms,
                        len(body),
                        len(response_body),
                        bool(extra_headers.get("Content-Encoding") == "gzip"),
                        performance_trace.log_fields(app_duration_ms=app_duration_ms),
                    )
            except ServiceError as exc:
                app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                self._logger.warning(
                    "api_request route=%s request_id=%s status=error code=%s %s",
                    route,
                    request_id,
                    exc.code,
                    performance_trace.log_fields(app_duration_ms=app_duration_ms),
                )
                handler._send_error_response(
                    request_id,
                    exc.status_code,
                    exc.code,
                    exc.message,
                    exc.details,
                    server_timing=performance_trace.server_timing(app_duration_ms=app_duration_ms),
                )
            except StateFileCorruptedError as exc:
                app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                self._logger.error(
                    "api_request route=%s request_id=%s status=error "
                    "code=state_file_corrupted error=%s %s",
                    route,
                    request_id,
                    exc,
                    performance_trace.log_fields(app_duration_ms=app_duration_ms),
                )
                handler._send_error_response(
                    request_id,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "state_file_corrupted",
                    "Файл состояния поврежден. Автоматический сброс отключен; "
                    "восстановите данные из резервной копии.",
                    server_timing=performance_trace.server_timing(app_duration_ms=app_duration_ms),
                )
            except ValueError as exc:
                app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                self._logger.warning(
                    "api_request route=%s request_id=%s status=error code=validation_error %s",
                    route,
                    request_id,
                    performance_trace.log_fields(app_duration_ms=app_duration_ms),
                )
                handler._send_error_response(
                    request_id,
                    HTTPStatus.BAD_REQUEST,
                    "validation_error",
                    str(exc) or "Request payload is invalid.",
                    server_timing=performance_trace.server_timing(app_duration_ms=app_duration_ms),
                )
            except Exception as exc:  # pragma: no cover
                app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                self._logger.error(
                    "api_request_failed route=%s request_id=%s error_type=%s %s",
                    route,
                    request_id,
                    type(exc).__name__,
                    performance_trace.log_fields(app_duration_ms=app_duration_ms),
                )
                handler._send_error_response(
                    request_id,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "На сервере произошла непредвиденная ошибка.",
                    server_timing=performance_trace.server_timing(app_duration_ms=app_duration_ms),
                )


class _ApiRequestHandler(BaseHTTPRequestHandler):
    """Thin stdlib transport adapter; runtime collaborators live on each subclass."""

    server_version = "MinimalKanbanAPI/1.0"
    sys_version = ""

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin") and not self._cors_allowed_origin():
            self.send_response(HTTPStatus.FORBIDDEN)
            self._send_headers("application/json", 0)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_headers("application/json", 0)

    def do_HEAD(self) -> None:
        request_id = str(uuid.uuid4())
        parsed = _request_target_parts(self.path)
        if parsed is None:
            self.send_response(HTTPStatus.BAD_REQUEST)
            self._send_headers("application/json", 0)
            return
        route = parsed.path
        if self.CONTENT_RESPONDER.serve_head(
            self,
            route=route,
            request_id=request_id,
        ):
            return
        self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('HEAD')")

    def do_GET(self) -> None:
        request_id = str(uuid.uuid4())
        parsed = _request_target_parts(self.path)
        if parsed is None:
            self._send_error_response(
                request_id,
                HTTPStatus.BAD_REQUEST,
                "validation_error",
                "Адрес запроса имеет некорректный формат.",
            )
            return
        route = parsed.path
        try:
            query = self._query_payload(parsed.query)
        except ServiceError as exc:
            self._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
            return
        if self._serve_static_route(route, request_id):
            return
        if self._serve_authenticated_get_route(route, request_id, query):
            return
        if self._serve_readonly_get_route(route, request_id, query):
            return
        self._not_found(request_id)

    def do_POST(self) -> None:
        request_id = str(uuid.uuid4())
        if self.headers.get("Origin") and not self._cors_allowed_origin():
            self._send_error_response(
                request_id,
                HTTPStatus.FORBIDDEN,
                "forbidden",
                "Cross-origin API request is not allowed.",
            )
            return
        parsed = _request_target_parts(self.path)
        if parsed is None:
            self._send_error_response(
                request_id,
                HTTPStatus.BAD_REQUEST,
                "validation_error",
                "Адрес запроса имеет некорректный формат.",
            )
            return
        route = parsed.path
        if route not in self.ROUTES:
            self._not_found(request_id)
            return
        content_length = _content_length_header(self.headers.get("Content-Length", "0"))
        if content_length is None:
            self._send_error_response(
                request_id,
                HTTPStatus.BAD_REQUEST,
                "validation_error",
                "Заголовок Content-Length имеет некорректное значение.",
            )
            return
        if content_length < 0:
            self._send_error_response(
                request_id,
                HTTPStatus.BAD_REQUEST,
                "validation_error",
                "Заголовок Content-Length не может быть отрицательным.",
            )
            return
        if not self._authenticate(request_id):
            self._drain_request_body(content_length)
            return
        resolved_operator_session: dict | None = None
        operator_session_resolved = False
        if route != "/api/login_operator" and self._is_proxied_request():
            preflight_payload = self._operator_context_payload(route, {}, request_id)
            if preflight_payload is None:
                self.close_connection = True
                return
            resolved_operator_session = preflight_payload.get("_operator_session")
            operator_session_resolved = True
        if content_length > MAX_JSON_BODY_BYTES:
            self._drain_request_body(min(content_length, OVERSIZED_JSON_DRAIN_BYTES))
            self.close_connection = True
            self._send_error_response(
                request_id,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request_too_large",
                "Размер JSON-запроса превышает допустимый лимит.",
                {
                    "max_size_bytes": MAX_JSON_BODY_BYTES,
                    "content_length": content_length,
                },
            )
            return
        if (
            route in self.PROXIED_WRITE_ROUTES
            and is_maintenance_mode()
            and route not in self.MAINTENANCE_TECHNICAL_WRITE_ROUTES
        ):
            self._drain_request_body(content_length)
            self._send_error_response(
                request_id,
                HTTPStatus.SERVICE_UNAVAILABLE,
                "maintenance_mode",
                "Запись временно остановлена на время безопасного обслуживания CRM.",
            )
            return
        try:
            payload = self.REQUEST_CONTEXT_FACTORY.read_json_object(
                self,
                content_length,
            )
        except ServiceError as exc:
            self._send_error_response(
                request_id, exc.status_code, exc.code, exc.message, exc.details
            )
            return
        if (
            route in self.MAINTENANCE_TECHNICAL_WRITE_ROUTES
            and is_maintenance_mode()
            and not self._maintenance_technical_change_feed_write_allowed(route, payload)
        ):
            self._send_error_response(
                request_id,
                HTTPStatus.SERVICE_UNAVAILABLE,
                "maintenance_mode",
                "Запись временно остановлена на время безопасного обслуживания CRM.",
            )
            return
        self.DISPATCHER.dispatch(
            self,
            route,
            request_id,
            payload,
            resolved_operator_session=resolved_operator_session,
            operator_session_resolved=operator_session_resolved,
        )

    def _drain_request_body(self, content_length: int) -> None:
        self.REQUEST_CONTEXT_FACTORY.drain_request_body(self, content_length)

    def _query_payload(self, query_string: str) -> dict:
        return self.REQUEST_CONTEXT_FACTORY.query_payload(query_string)

    def _authenticate(self, request_id: str, query: dict | None = None) -> bool:
        return self.AUTHENTICATION_POLICY.authenticate(self, request_id, query)

    def _send_error_response(
        self,
        request_id: str,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
        *,
        server_timing: str = "",
    ) -> None:
        self.RESPONSE_WRITER.send_error(
            self,
            request_id,
            status_code,
            code,
            message,
            details,
            server_timing=server_timing,
        )

    def _send_bytes_response(
        self,
        body: bytes,
        *,
        content_type: str,
        request_id: str,
        route: str,
        status_code: int = HTTPStatus.OK,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.RESPONSE_WRITER.send_bytes(
            self,
            body,
            content_type=content_type,
            request_id=request_id,
            route=route,
            status_code=status_code,
            cache_control=cache_control,
            extra_headers=extra_headers,
        )

    def _not_found(self, request_id: str) -> None:
        self._send_error_response(
            request_id,
            HTTPStatus.NOT_FOUND,
            "not_found",
            "Указанный маршрут API не найден.",
            {"path": _safe_request_target(self.path)},
        )

    def _send_headers(
        self,
        content_type: str,
        content_length: int,
        *,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.RESPONSE_WRITER.send_headers(
            self,
            content_type,
            content_length,
            cache_control=cache_control,
            extra_headers=extra_headers,
        )

    def _cors_allowed_origin(self) -> str:
        return self.RESPONSE_WRITER.cors_allowed_origin(self)

    def _prepare_response_body(
        self,
        body: bytes,
        *,
        content_type: str,
        server_timing: str = "",
    ) -> tuple[bytes, dict[str, str]]:
        return self.RESPONSE_WRITER.prepare_body(
            self,
            body,
            content_type=content_type,
            server_timing=server_timing,
        )

    def _serve_static_route(self, route: str, request_id: str) -> bool:
        return self.CONTENT_RESPONDER.serve_static_route(
            self,
            route=route,
            request_id=request_id,
        )

    def _serve_authenticated_get_route(
        self,
        route: str,
        request_id: str,
        query: dict,
    ) -> bool:
        if route not in self.CONTENT_RESPONDER.PROTECTED_ROUTES:
            return False
        protected_query = self._operator_context_payload(route, query, request_id)
        if protected_query is None:
            return True
        if not self._authenticate(request_id, protected_query):
            return True
        self.CONTENT_RESPONDER.serve_protected_route(
            self,
            route=route,
            request_id=request_id,
            payload=protected_query,
        )
        return True

    def _serve_readonly_get_route(
        self,
        route: str,
        request_id: str,
        query: dict,
    ) -> bool:
        if route not in self.READONLY_ROUTES:
            return False
        if not self._authenticate(request_id, query):
            return True
        self.DISPATCHER.dispatch(self, route, request_id, query)
        return True

    def _operator_context_payload(
        self,
        route: str,
        payload: dict,
        request_id: str,
        *,
        resolved_operator_session: dict | None = None,
        operator_session_resolved: bool = False,
    ) -> dict | None:
        return self.AUTHENTICATION_POLICY.operator_context_payload(
            self,
            route,
            payload,
            request_id,
            resolved_operator_session=resolved_operator_session,
            operator_session_resolved=operator_session_resolved,
        )

    def _maintenance_technical_change_feed_write_allowed(
        self,
        route: str,
        payload: dict,
    ) -> bool:
        return self.AUTHENTICATION_POLICY.maintenance_technical_change_feed_write_allowed(
            self,
            route,
            payload,
        )

    def _is_proxied_request(self) -> bool:
        return self.AUTHENTICATION_POLICY.is_proxied_request(self)

    def _write_body(
        self,
        body: bytes,
        *,
        route: str,
        request_id: str,
        status_code: int,
    ) -> bool:
        return self.RESPONSE_WRITER.write_body(
            self,
            body,
            route=route,
            request_id=request_id,
            status_code=status_code,
        )

    def log_message(self, format: str, *args) -> None:
        return


class ApiServer:
    def __init__(
        self,
        service: CardService,
        logger: Logger,
        *,
        operator_service: OperatorAuthService | None = None,
        host: str | None = None,
        start_port: int | None = None,
        fallback_limit: int | None = None,
        bearer_token: str | None = None,
        shared_files_service: SharedFilesService | None = None,
        change_feed_service: ChangeFeedService | None = None,
        clipboard_file_provider: Callable[[], Iterable[Path | str]] | None = None,
    ) -> None:
        self._service = service
        self._shared_files_service = shared_files_service
        self._change_feed_service = change_feed_service
        self._logger = logger
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        resolved_host = host if host is not None else get_api_host()
        resolved_start_port = start_port if start_port is not None else get_api_port()
        resolved_fallback_limit = (
            fallback_limit if fallback_limit is not None else get_api_port_fallback_limit()
        )
        self.host = resolved_host
        self.port = resolved_start_port
        self._start_port = resolved_start_port
        self._fallback_limit = resolved_fallback_limit
        self._bearer_token = bearer_token if bearer_token is not None else get_api_bearer_token()
        self._operator_service = operator_service
        self._clipboard_file_provider = clipboard_file_provider or list_clipboard_file_paths

    @property
    def base_url(self) -> str:
        display_host = self.host
        if display_host in {"0.0.0.0", "::", "[::]"}:
            display_host = "127.0.0.1"
        elif ":" in display_host and not display_host.startswith("["):
            display_host = f"[{display_host}]"
        return f"http://{display_host}:{self.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler = self._make_handler()
        candidate_ports = (
            range(self._start_port, self._start_port + self._fallback_limit)
            if self._start_port > 0
            else [0] * max(1, self._fallback_limit)
        )
        last_error: BaseException | None = None
        for candidate_port in candidate_ports:
            try:
                server = ReusableThreadingHTTPServer((self.host, candidate_port), handler)
            except OSError as exc:
                last_error = exc
                continue
            server.api_logger = self._logger
            thread = threading.Thread(
                target=server.serve_forever, name="minimal-kanban-api", daemon=True
            )
            self._server = server
            self._thread = thread
            self.port = int(server.server_address[1])
            thread.start()
            try:
                self._wait_until_accepting()
            except RuntimeError as exc:
                last_error = exc
                self._server = None
                self._thread = None
                server.shutdown()
                thread.join(timeout=1)
                server.server_close()
                continue
            self._logger.info(
                "api_server_started bind_host=%s url=%s auth=%s",
                self.host,
                self.base_url,
                bool(self._bearer_token),
            )
            return
        raise RuntimeError("Не удалось запустить локальный API.") from last_error

    def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        self._server = None
        server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        server.server_close()
        self._logger.info("api_server_stopped")

    def _wait_until_accepting(self, *, timeout_seconds: float = 5.0) -> None:
        deadline = perf_counter() + timeout_seconds
        last_error: BaseException | None = None
        connect_host = self.host
        if connect_host in {"0.0.0.0", "::", "[::]"}:
            connect_host = "127.0.0.1"
        elif connect_host.startswith("[") and connect_host.endswith("]"):
            connect_host = connect_host[1:-1]
        request = (
            f"GET /api/health HTTP/1.1\r\nHost: {connect_host}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        while perf_counter() < deadline:
            try:
                with socket.create_connection((connect_host, self.port), timeout=0.2) as sock:
                    sock.settimeout(0.5)
                    sock.sendall(request)
                    status_line = sock.recv(64).split(b"\r\n", 1)[0]
                    if status_line.startswith(b"HTTP/") and b" 200 " in status_line:
                        return
                    last_error = RuntimeError(
                        f"Локальный API вернул неожиданный health status: {status_line!r}"
                    )
            except (OSError, TimeoutError) as exc:
                last_error = exc
            sleep(0.02)
        raise RuntimeError("Локальный API запущен, но health endpoint не отвечает.") from last_error

    def _build_shared_files_service(self, service: CardService) -> SharedFilesService:
        store = getattr(service, "_store", None)
        base_dir = getattr(store, "base_dir", None)
        change_feed_store = getattr(store, "change_feed_store", None)
        if isinstance(base_dir, Path):
            return SharedFilesService(
                storage_dir=base_dir / "shared-files",
                index_file=base_dir / "shared_files_index.json",
                logger=self._logger,
                change_feed_store=change_feed_store,
            )
        return SharedFilesService(
            logger=self._logger,
            change_feed_store=change_feed_store,
        )

    @staticmethod
    def _build_change_feed_service(
        service: CardService,
        shared_files_service: SharedFilesService,
        operator_service: OperatorAuthService | None,
    ) -> ChangeFeedService:
        store = getattr(service, "_store", None)
        change_feed_store = getattr(store, "change_feed_store", None)
        reconcile_state = getattr(store, "reconcile_change_feed", None)
        if change_feed_store is None:
            raise RuntimeError("CardService storage does not expose the durable change feed.")

        def reconcile() -> None:
            if callable(reconcile_state):
                reconcile_state()
            print_reconcile = getattr(service, "reconcile_print_change_feed", None)
            if callable(print_reconcile):
                print_reconcile()
            shared_files_service.reconcile_change_feed()
            operator_reconcile = getattr(operator_service, "reconcile_change_feed", None)
            if callable(operator_reconcile):
                operator_reconcile()

        return ChangeFeedService(
            change_feed_store,
            reconcile=reconcile,
        )

    def _make_handler(self):
        service = self._service
        shared_files_service = self._shared_files_service
        if shared_files_service is None:
            shared_files_service = self._build_shared_files_service(service)
            self._shared_files_service = shared_files_service
        service.configure_display_dashboard_shared_file_resolver(
            lambda file_id: _display_dashboard_shared_file_info(
                shared_files_service,
                file_id,
            )
        )
        change_feed_service = self._change_feed_service
        if change_feed_service is None:
            change_feed_service = self._build_change_feed_service(
                service, shared_files_service, self._operator_service
            )
            self._change_feed_service = change_feed_service
        logger = self._logger
        bearer_token = self._bearer_token
        operator_service = self._operator_service
        api_server = self
        request_context_factory = RequestContextFactory()
        response_writer = HttpResponseWriter(logger)
        content_responder = StaticAndDownloadResponder(
            service=service,
            shared_files_service=shared_files_service,
            logger=logger,
            api_server=api_server,
            bearer_token=bearer_token,
        )

        def paste_shared_files_from_clipboard(payload: dict | None = None) -> dict:
            payload = payload or {}
            try:
                clipboard_paths = [Path(item) for item in self._clipboard_file_provider()]
            except ClipboardUnavailableError as exc:
                raise ServiceError(
                    "clipboard_unavailable",
                    str(exc) or "Не удалось прочитать буфер обмена Windows.",
                    status_code=HTTPStatus.CONFLICT,
                ) from exc
            file_paths = [path for path in clipboard_paths if path.exists() and path.is_file()]
            if not file_paths:
                raise ServiceError(
                    "clipboard_empty",
                    "В буфере обмена нет файлов. Скопируйте файл в Проводнике и повторите вставку.",
                    status_code=HTTPStatus.CONFLICT,
                )
            base_x = _shared_file_clipboard_position(payload.get("x"))
            base_y = _shared_file_clipboard_position(payload.get("y"))
            files: list[dict] = []
            storage: dict | None = None
            for index, file_path in enumerate(file_paths):
                uploaded = shared_files_service.upload_shared_file_from_local_path(
                    {
                        "path": str(file_path),
                        "actor_name": payload.get("actor_name"),
                        "source": payload.get("source") or "ui",
                        "x": base_x + (index % 7) * 116,
                        "y": base_y + (index // 7) * 126,
                    }
                )
                files.append(uploaded["file"])
                storage = uploaded.get("storage")
            return {
                "files": files,
                "storage": storage or shared_files_service.list_shared_files({})["storage"],
            }

        service_routes = build_service_routes(
            service,
            shared_files_service,
            paste_shared_files_from_clipboard=paste_shared_files_from_clipboard,
        )
        feed_routes = build_change_feed_routes(change_feed_service)
        operator_routes = build_operator_routes(operator_service) if operator_service else {}
        route_specs = merge_route_specs(
            build_route_specs(service_routes, registry="service"),
            build_route_specs(feed_routes, registry="change_feed"),
            build_route_specs(operator_routes, registry="operator"),
        )
        routes = {**service_routes, **feed_routes, **operator_routes}
        proxied_write_routes = {
            path
            for path, spec in route_specs.items()
            if spec.maintenance_behavior in {"blocked", "technical"}
        }
        maintenance_technical_write_routes = {
            path for path, spec in route_specs.items() if spec.maintenance_behavior == "technical"
        }
        operator_session_routes = {
            path for path, spec in route_specs.items() if spec.auth_kind == "operator"
        }
        operator_session_routes.add(MODULE_MAP_INFRASTRUCTURE_ROUTE)
        admin_only_routes = {
            path for path, spec in route_specs.items() if spec.auth_kind == "admin"
        }
        readonly_routes = {path for path, spec in route_specs.items() if "GET" in spec.methods}
        operator_permission_routes = {
            path: spec.required_permission
            for path, spec in route_specs.items()
            if spec.required_permission
        }

        authentication_policy = AuthenticationPolicy(
            bearer_token=bearer_token,
            operator_service=operator_service,
            readonly_routes=readonly_routes,
            operator_session_routes=operator_session_routes,
            admin_only_routes=admin_only_routes,
            maintenance_technical_write_routes=maintenance_technical_write_routes,
            operator_permission_routes=operator_permission_routes,
        )
        dispatcher = JsonRouteDispatcher(
            service=service,
            logger=logger,
            authentication_policy=authentication_policy,
        )

        class RequestHandler(_ApiRequestHandler):
            ROUTES = routes
            ROUTE_SPECS = route_specs
            REQUEST_CONTEXT_FACTORY = request_context_factory
            RESPONSE_WRITER = response_writer
            CONTENT_RESPONDER = content_responder
            AUTHENTICATION_POLICY = authentication_policy
            DISPATCHER = dispatcher
            PROXIED_WRITE_ROUTES = frozenset(proxied_write_routes)
            MAINTENANCE_TECHNICAL_WRITE_ROUTES = frozenset(maintenance_technical_write_routes)
            READONLY_ROUTES = frozenset(readonly_routes)

        return RequestHandler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 64
    api_logger: Logger | None = None

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            if self.api_logger is not None:
                self.api_logger.debug(
                    "api_client_disconnected_before_response client=%s error=%s",
                    client_address,
                    exc,
                )
            return
        super().handle_error(request, client_address)
