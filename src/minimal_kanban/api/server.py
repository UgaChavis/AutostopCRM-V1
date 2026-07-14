from __future__ import annotations

import gzip
import hmac
import html
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
from ..deployment_security import is_maintenance_mode, load_agent_gateway_security_policy
from ..json_safety import reject_deeply_nested_json
from ..models import business_timezone, parse_datetime
from ..operator_auth import OperatorAuthService
from ..performance import request_performance_trace
from ..services.card_service import CardService
from ..services.errors import ServiceError
from ..services.shared_files_service import SharedFilesService
from ..storage.json_store import StateFileCorruptedError
from ..storage.limited_io import read_bytes_limited
from ..system_clipboard import ClipboardUnavailableError, list_clipboard_file_paths
from ..web_assets import BOARD_WEB_APP_HTML
from .route_registry import (
    ADMIN_ONLY_ROUTES,
    OPERATOR_SESSION_ROUTES,
    PROXIED_WRITE_ROUTES,
    build_operator_routes,
    build_service_routes,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


QUIET_SUCCESS_ROUTES = frozenset(
    {
        "/api/health",
        "/api/get_board_revision",
        "/api/get_board_snapshot",
        "/api/mark_card_seen",
    }
)

JSON_GZIP_MIN_BYTES = 1024
MAX_JSON_BODY_BYTES = 25 * 1024 * 1024
OVERSIZED_JSON_DRAIN_BYTES = 1024 * 1024
API_FILE_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
STATIC_ASSET_MAX_BYTES = 1 * 1024 * 1024
MAX_QUERY_STRING_BYTES = 16 * 1024
MAX_QUERY_FIELDS = 128
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

READONLY_GET_ROUTES = frozenset(
    {
        "/api/list_columns",
        "/api/get_cards",
        "/api/get_card",
        "/api/get_board_revision",
        "/api/get_board_snapshot",
        "/api/get_board_context",
        "/api/review_board",
        "/api/get_board_content",
        "/api/get_board_events",
        "/api/list_cashboxes",
        "/api/get_cash_journal",
        "/api/finance_audit",
        "/api/repair_order_number_audit",
        "/api/list_employees",
        "/api/list_clients",
        "/api/search_clients",
        "/api/get_client",
        "/api/get_client_stats",
        "/api/suggest_clients_for_card",
        "/api/get_payroll_report",
        "/api/get_employee_salary_ledger",
        "/api/get_employee_salary_report",
        "/api/get_employee_salary_reconciliation",
        "/api/get_cashbox",
        "/api/get_gpt_wall",
        "/api/agent_status",
        "/api/agent_tasks",
        "/api/agent_actions",
        "/api/agent_scheduled_tasks",
        "/api/get_card_log",
        "/api/search_cards",
        "/api/list_archived_cards",
        "/api/list_overdue_cards",
        "/api/list_repair_orders",
        "/api/get_operator_profile",
        "/api/list_operator_users",
        "/api/get_operator_user_report",
        "/api/list_operator_activity",
        "/api/get_operator_activity_details",
        "/api/get_operator_activity_aggregates",
        "/api/export_operator_activity",
        "/api/list_shared_files",
        "/api/get_shared_file_info",
        "/api/list_inventory_items",
        "/api/get_inventory_item",
        "/api/list_inventory_movements",
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


def _same_host_cors_origin(origin: object, host: object) -> str:
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
    default_port = _origin_default_port(scheme)
    try:
        origin_port = origin_parts.port or default_port
        request_port = host_parts.port or default_port
    except ValueError:
        return ""
    if origin_port != request_port:
        return ""
    return f"{scheme}://{origin_parts.netloc.lower()}"


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


def _html_text(value: object, *, fallback: str = "-") -> str:
    text = str(value if value is not None else "").strip()
    return html.escape(text or fallback, quote=True)


def _employee_salary_reconciliation_vehicle_html(row: dict) -> str:
    vehicle = str(row.get("vehicle") or "").strip()
    plate = str(row.get("license_plate") or "").strip()
    if vehicle and plate:
        return (
            f"{_html_text(vehicle, fallback='')}"
            f'<br><span class="muted">госномер: {_html_text(plate, fallback="")}</span>'
        )
    return _html_text(vehicle or plate)


def _employee_salary_reconciliation_rows_html(report: dict) -> str:
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        return (
            '<tr><td colspan="11" class="empty">'
            f"{_employee_salary_reconciliation_empty_text(report)}"
            "</td></tr>"
        )
    rendered: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rendered.append(
            "<tr>"
            f'<td class="is-num">{_html_text(row.get("number"), fallback="")}</td>'
            f"<td>{_html_text(row.get('date'))}</td>"
            f"<td>{_html_text(row.get('kind_label'))}</td>"
            f"<td>{_html_text(row.get('repair_order_number'))}</td>"
            f"<td>{_employee_salary_reconciliation_vehicle_html(row)}</td>"
            f"<td>{_html_text(row.get('item'))}</td>"
            f"<td>{_html_text(row.get('calculation_base'))}</td>"
            f"<td>{_html_text(row.get('scheme'))}</td>"
            f'<td class="money">{_html_text(row.get("accrued_display"), fallback="")}</td>'
            f'<td class="money">{_html_text(row.get("payment_display"), fallback="")}</td>'
            f"<td>{_html_text(row.get('note'), fallback='')}</td>"
            "</tr>"
        )
    return "".join(rendered) or (
        '<tr><td colspan="11" class="empty">'
        f"{_employee_salary_reconciliation_empty_text(report)}"
        "</td></tr>"
    )


def _employee_salary_reconciliation_empty_text(report: dict) -> str:
    period = report.get("period")
    if isinstance(period, dict):
        label = str(period.get("label") or "").strip()
        if label:
            return _html_text(f"За период {label} движений нет.", fallback="")
    return "За выбранный период движений нет."


def _employee_salary_reconciliation_totals_html(report: dict) -> str:
    totals = report.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    items = (
        ("Всего начислено", totals.get("accrued_total_display") or totals.get("accrued_total")),
        ("Выплачено", totals.get("payout_total_display") or totals.get("payout_total")),
        ("Авансы", totals.get("advance_total_display") or totals.get("advance_total")),
        (
            "Итог к выплате",
            totals.get("amount_due_total_display") or totals.get("amount_due_total"),
        ),
    )
    return "".join(
        '<div class="summary-item">'
        f"<span>{_html_text(label)}</span>"
        f"<strong>{_html_text(value or '0')}</strong>"
        "</div>"
        for label, value in items
    )


def _employee_salary_reconciliation_print_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = parse_datetime(raw)
    if parsed is None:
        return raw
    return parsed.astimezone(business_timezone()).strftime("%d.%m.%Y")


def _employee_salary_reconciliation_print_html(report: dict) -> bytes:
    employee = report.get("employee")
    if not isinstance(employee, dict):
        employee = {}
    period = report.get("period")
    if not isinstance(period, dict):
        period = {}
    generated_at = _employee_salary_reconciliation_print_date(period.get("generated_at"))
    body = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        "<title>Акт сверки зарплаты</title>"
        "<style>"
        "@page { size: A4 landscape; margin: 12mm; }"
        'body { margin: 0; color: #111; background: #fff; font: 12px/1.35 "Segoe UI", Arial, sans-serif; }'
        ".toolbar { position: sticky; top: 0; display: flex; justify-content: flex-end; gap: 8px; padding: 10px 0; background: #fff; border-bottom: 1px solid #ddd; margin-bottom: 18px; }"
        ".print-button { border: 1px solid #111; background: #111; color: #fff; padding: 8px 14px; cursor: pointer; font-weight: 700; letter-spacing: .04em; }"
        "h1 { margin: 0 0 10px; font-size: 22px; line-height: 1.15; }"
        ".meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 18px; margin-bottom: 14px; }"
        ".meta div, .summary-item { border: 1px solid #d4d4d4; padding: 7px 8px; }"
        ".meta span, .summary-item span { display: block; color: #555; font-size: 10px; text-transform: uppercase; }"
        ".meta strong, .summary-item strong { display: block; margin-top: 2px; font-size: 13px; }"
        ".summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 10px 0 16px; }"
        "table { width: 100%; border-collapse: collapse; table-layout: fixed; }"
        "th, td { border: 1px solid #c9c9c9; padding: 5px 6px; vertical-align: top; word-break: break-word; }"
        "th { background: #efefef; text-align: left; font-size: 10px; text-transform: uppercase; }"
        ".is-num, .money { text-align: right; white-space: nowrap; }"
        ".muted { color: #555; }"
        ".empty { text-align: center; padding: 18px; color: #555; }"
        ".signatures { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 28px; }"
        ".signature { border-top: 1px solid #111; padding-top: 6px; min-height: 34px; }"
        "@media print { .toolbar { display: none; } body { font-size: 11px; } th, td { padding: 4px 5px; } }"
        "</style></head><body>"
        '<div class="toolbar"><button class="print-button" type="button" onclick="window.print()">ПЕЧАТЬ</button></div>'
        "<main>"
        "<h1>Акт сверки зарплаты</h1>"
        '<section class="meta">'
        f"<div><span>Сотрудник</span><strong>{_html_text(employee.get('name'), fallback='Сотрудник')}</strong></div>"
        f"<div><span>Должность</span><strong>{_html_text(employee.get('position'), fallback='Не указана')}</strong></div>"
        f"<div><span>Период</span><strong>{_html_text(period.get('label'), fallback='Последние 30 дней')}</strong></div>"
        "</section>"
        f'<section class="summary">{_employee_salary_reconciliation_totals_html(report)}</section>'
        "<table><thead><tr>"
        '<th style="width:34px;">№</th><th style="width:84px;">Дата</th><th style="width:76px;">Движение</th><th style="width:58px;">ЗН</th>'
        '<th style="width:130px;">Авто / госномер</th><th>Работа / позиция</th><th style="width:120px;">База расчета</th>'
        '<th style="width:105px;">Схема</th><th style="width:92px;">Начислено</th><th style="width:98px;">Выплата / аванс</th><th>Примечание</th>'
        f"</tr></thead><tbody>{_employee_salary_reconciliation_rows_html(report)}</tbody></table>"
        '<section class="signatures">'
        '<div class="signature">Бухгалтер</div>'
        '<div class="signature">Сотрудник</div>'
        f'<div class="signature">Дата{": " + _html_text(generated_at, fallback="") if generated_at else ""}</div>'
        "</section>"
        "</main></body></html>"
    )
    return body.encode("utf-8")


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
        clipboard_file_provider: Callable[[], Iterable[Path | str]] | None = None,
    ) -> None:
        self._service = service
        self._shared_files_service = shared_files_service
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
        if isinstance(base_dir, Path):
            return SharedFilesService(
                storage_dir=base_dir / "shared-files",
                index_file=base_dir / "shared_files_index.json",
                logger=self._logger,
            )
        return SharedFilesService(logger=self._logger)

    def _make_handler(self):
        service = self._service
        shared_files_service = self._shared_files_service
        if shared_files_service is None:
            shared_files_service = self._build_shared_files_service(service)
            self._shared_files_service = shared_files_service
        logger = self._logger
        bearer_token = self._bearer_token
        operator_service = self._operator_service
        api_server = self

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

        routes = build_service_routes(
            service,
            shared_files_service,
            paste_shared_files_from_clipboard=paste_shared_files_from_clipboard,
        )
        proxied_write_routes = set(PROXIED_WRITE_ROUTES)
        operator_session_routes = set(OPERATOR_SESSION_ROUTES)
        admin_only_routes = set(ADMIN_ONLY_ROUTES)
        readonly_routes = set(READONLY_GET_ROUTES)
        if operator_service is not None:
            routes.update(build_operator_routes(operator_service))
            operator_session_routes.update(admin_only_routes)
            operator_session_routes.update(
                {
                    "/api/list_operator_activity",
                    "/api/get_operator_activity_details",
                    "/api/get_operator_activity_aggregates",
                    "/api/export_operator_activity",
                }
            )

        class RequestHandler(BaseHTTPRequestHandler):
            ROUTES = routes

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
                if route in {"/", "/index.html"}:
                    body = _board_html_bytes()
                    self.send_response(HTTPStatus.OK)
                    self._send_headers("text/html; charset=utf-8", len(body))
                    return
                if route == "/favicon.ico":
                    body = _static_asset_bytes("favicon.ico")
                    self.send_response(HTTPStatus.OK)
                    self._send_headers(
                        "image/x-icon",
                        len(body),
                        cache_control="public, max-age=86400, immutable",
                    )
                    return
                if route == "/favicon.png":
                    body = _static_asset_bytes("favicon.png")
                    self.send_response(HTTPStatus.OK)
                    self._send_headers(
                        "image/png",
                        len(body),
                        cache_control="public, max-age=86400, immutable",
                    )
                    return
                if route == "/api/health":
                    body = _json_response(
                        ok=True,
                        data={
                            "status": "ok",
                            "base_url": api_server.base_url,
                            "bind_host": self.server.server_address[0],
                            "auth_required": bool(bearer_token),
                            "maintenance_mode": is_maintenance_mode(),
                        },
                        error=None,
                        request_id=request_id,
                    )
                    self.send_response(HTTPStatus.OK)
                    self._send_headers("application/json", len(body))
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
                if not self._authenticate(request_id):
                    self._drain_request_body(content_length)
                    return
                if route in proxied_write_routes and is_maintenance_mode():
                    self._drain_request_body(content_length)
                    self._send_error_response(
                        request_id,
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "maintenance_mode",
                        "Запись временно остановлена на время безопасного обслуживания CRM.",
                    )
                    return
                raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                try:
                    payload = json.loads(
                        raw_body.decode("utf-8") or "{}",
                        parse_constant=_reject_json_constant,
                    )
                    reject_deeply_nested_json(payload)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    self._send_error_response(
                        request_id,
                        HTTPStatus.BAD_REQUEST,
                        "invalid_json",
                        "Тело запроса должно содержать корректный JSON.",
                    )
                    return
                if not isinstance(payload, dict):
                    self._send_error_response(
                        request_id,
                        HTTPStatus.BAD_REQUEST,
                        "validation_error",
                        "Тело запроса должно быть JSON-объектом.",
                    )
                    return
                self._dispatch(route, request_id, payload)

            def _drain_request_body(self, content_length: int) -> None:
                remaining = max(0, int(content_length))
                while remaining > 0:
                    try:
                        chunk = self.rfile.read(min(65536, remaining))
                    except OSError:
                        break
                    if not chunk:
                        break
                    remaining -= len(chunk)

            def _query_payload(self, query_string: str) -> dict:
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

            def _serve_board(self, request_id: str) -> None:
                gzip_ok = "gzip" in str(self.headers.get("Accept-Encoding", "")).lower()
                body = _board_html_gzip_bytes() if gzip_ok else _board_html_bytes()
                extra_headers = {"Vary": "Accept-Encoding"}
                if gzip_ok:
                    extra_headers["Content-Encoding"] = "gzip"
                self._send_bytes_response(
                    body,
                    content_type="text/html; charset=utf-8",
                    request_id=request_id,
                    route=urlsplit(self.path).path or "/",
                    extra_headers=extra_headers,
                )

            def _serve_attachment(self, request_id: str, payload: dict) -> None:
                try:
                    path, attachment = service.get_attachment_download(
                        str(payload.get("card_id", "")),
                        str(payload.get("attachment_id", "")),
                    )
                    body = _read_bounded_file_response(path)
                    self._send_bytes_response(
                        body,
                        content_type=attachment.mime_type or "application/octet-stream",
                        request_id=request_id,
                        route=urlsplit(self.path).path,
                        extra_headers={
                            "Content-Disposition": _content_disposition_header(
                                attachment.file_name,
                                disposition="attachment",
                            ),
                            "X-Content-Type-Options": "nosniff",
                        },
                    )
                except ServiceError as exc:
                    self._send_error_response(
                        request_id, exc.status_code, exc.code, exc.message, exc.details
                    )
                except FileNotFoundError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.NOT_FOUND,
                        "not_found",
                        "Файл не найден на диске.",
                    )
                except ValueError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "validation_error",
                        "Файл слишком большой для скачивания через API.",
                    )

            def _serve_shared_file(self, request_id: str, payload: dict) -> None:
                try:
                    path, file_meta = shared_files_service.get_shared_file_download(
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
                    self._send_bytes_response(
                        body,
                        content_type=content_type,
                        request_id=request_id,
                        route=urlsplit(self.path).path,
                        extra_headers={
                            "Content-Disposition": _content_disposition_header(
                                str(file_meta.get("original_name") or "shared-file"),
                                disposition=disposition,
                            ),
                            "X-Content-Type-Options": "nosniff",
                        },
                    )
                except ServiceError as exc:
                    self._send_error_response(
                        request_id, exc.status_code, exc.code, exc.message, exc.details
                    )
                except FileNotFoundError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.NOT_FOUND,
                        "not_found",
                        "Файл не найден на диске.",
                    )
                except ValueError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "validation_error",
                        "Файл слишком большой для скачивания через API.",
                    )

            def _serve_repair_order_text(self, request_id: str, payload: dict) -> None:
                try:
                    path, file_name = service.get_repair_order_text_download(
                        str(payload.get("card_id", ""))
                    )
                    body = _read_bounded_file_response(path)
                    self._send_bytes_response(
                        body,
                        content_type="text/plain; charset=utf-8",
                        request_id=request_id,
                        route=urlsplit(self.path).path,
                        extra_headers={
                            "Content-Disposition": _content_disposition_header(
                                file_name,
                                disposition="inline",
                            ),
                            "X-Content-Type-Options": "nosniff",
                        },
                    )
                except ServiceError as exc:
                    self._send_error_response(
                        request_id, exc.status_code, exc.code, exc.message, exc.details
                    )
                except FileNotFoundError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.NOT_FOUND,
                        "not_found",
                        "Файл заказ-наряда не найден на диске.",
                    )
                except ValueError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "validation_error",
                        "Файл заказ-наряда слишком большой для скачивания через API.",
                    )

            def _authenticate(self, request_id: str, query: dict | None = None) -> bool:
                if not bearer_token:
                    return True
                auth_header = self.headers.get("Authorization", "")
                if hmac.compare_digest(auth_header, f"Bearer {bearer_token}"):
                    return True
                try:
                    query_payload = (
                        query
                        if query is not None
                        else self._query_payload(urlsplit(self.path).query)
                    )
                    access_token = str(query_payload.get("access_token", "") or "").strip()
                except ServiceError as exc:
                    self._send_error_response(
                        request_id, exc.status_code, exc.code, exc.message, exc.details
                    )
                    return False
                if hmac.compare_digest(access_token, bearer_token):
                    return True
                self._send_error_response(
                    request_id,
                    HTTPStatus.UNAUTHORIZED,
                    "unauthorized",
                    "Для вызова локального API нужен корректный bearer token.",
                )
                return False

            def _dispatch(self, route: str, request_id: str, payload: dict) -> None:
                started_at = perf_counter()
                with request_performance_trace() as performance_trace:
                    try:
                        payload = self._operator_context_payload(route, payload, request_id)
                        if payload is None:
                            return
                        result = self.ROUTES[route](payload)
                        body = _json_response(
                            ok=True, data=result, error=None, request_id=request_id
                        )
                        app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                        server_timing = performance_trace.server_timing(
                            app_duration_ms=app_duration_ms
                        )
                        response_body, extra_headers = self._prepare_response_body(
                            body,
                            content_type="application/json",
                            server_timing=server_timing,
                        )
                        self.send_response(HTTPStatus.OK)
                        self._send_headers(
                            "application/json",
                            len(response_body),
                            extra_headers=extra_headers,
                        )
                        if self._write_body(
                            response_body,
                            route=route,
                            request_id=request_id,
                            status_code=HTTPStatus.OK,
                        ):
                            logger.log(
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
                        logger.warning(
                            "api_request route=%s request_id=%s status=error code=%s %s",
                            route,
                            request_id,
                            exc.code,
                            performance_trace.log_fields(app_duration_ms=app_duration_ms),
                        )
                        self._send_error_response(
                            request_id,
                            exc.status_code,
                            exc.code,
                            exc.message,
                            exc.details,
                            server_timing=performance_trace.server_timing(
                                app_duration_ms=app_duration_ms
                            ),
                        )
                    except StateFileCorruptedError as exc:
                        app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                        logger.error(
                            "api_request route=%s request_id=%s status=error "
                            "code=state_file_corrupted error=%s %s",
                            route,
                            request_id,
                            exc,
                            performance_trace.log_fields(app_duration_ms=app_duration_ms),
                        )
                        self._send_error_response(
                            request_id,
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            "state_file_corrupted",
                            "Файл состояния поврежден. Автоматический сброс отключен; восстановите данные из резервной копии.",
                            server_timing=performance_trace.server_timing(
                                app_duration_ms=app_duration_ms
                            ),
                        )
                    except ValueError as exc:
                        app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                        logger.warning(
                            "api_request route=%s request_id=%s status=error "
                            "code=validation_error %s",
                            route,
                            request_id,
                            performance_trace.log_fields(app_duration_ms=app_duration_ms),
                        )
                        self._send_error_response(
                            request_id,
                            HTTPStatus.BAD_REQUEST,
                            "validation_error",
                            str(exc) or "Request payload is invalid.",
                            server_timing=performance_trace.server_timing(
                                app_duration_ms=app_duration_ms
                            ),
                        )
                    except Exception as exc:  # pragma: no cover
                        app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                        logger.exception(
                            "api_request_failed route=%s request_id=%s error=%s %s",
                            route,
                            request_id,
                            exc,
                            performance_trace.log_fields(app_duration_ms=app_duration_ms),
                        )
                        self._send_error_response(
                            request_id,
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            "internal_error",
                            "На сервере произошла непредвиденная ошибка.",
                            server_timing=performance_trace.server_timing(
                                app_duration_ms=app_duration_ms
                            ),
                        )

            def _serve_employee_salary_reconciliation_print(
                self, request_id: str, query: dict
            ) -> None:
                route = "/employee_salary_reconciliation_print"
                started_at = perf_counter()
                try:
                    report = service.get_employee_salary_reconciliation(query)
                    body = _employee_salary_reconciliation_print_html(report)
                    app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                    self._send_bytes_response(
                        body,
                        content_type="text/html; charset=utf-8",
                        request_id=request_id,
                        route=route,
                        extra_headers={"Server-Timing": f"app;dur={app_duration_ms:.1f}"},
                    )
                    logger.log(
                        _success_log_level(route),
                        "api_request route=%s request_id=%s status=ok duration_ms=%.1f body_bytes=%s",
                        route,
                        request_id,
                        app_duration_ms,
                        len(body),
                    )
                except ServiceError as exc:
                    logger.warning(
                        "api_request route=%s request_id=%s status=error code=%s",
                        route,
                        request_id,
                        exc.code,
                    )
                    self._send_error_response(
                        request_id, exc.status_code, exc.code, exc.message, exc.details
                    )
                except Exception as exc:  # pragma: no cover
                    logger.exception(
                        "api_request_failed route=%s request_id=%s error=%s",
                        route,
                        request_id,
                        exc,
                    )
                    self._send_error_response(
                        request_id,
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "internal_error",
                        "На сервере произошла непредвиденная ошибка.",
                    )

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
                body = _json_response(
                    ok=False,
                    data=None,
                    error={"code": code, "message": message, "details": details or {}},
                    request_id=request_id,
                )
                try:
                    response_body, extra_headers = self._prepare_response_body(
                        body,
                        content_type="application/json",
                        server_timing=server_timing,
                    )
                    self.send_response(status_code)
                    self._send_headers(
                        "application/json",
                        len(response_body),
                        extra_headers=extra_headers,
                    )
                    self._write_body(
                        response_body,
                        route=_safe_request_target(self.path),
                        request_id=request_id,
                        status_code=status_code,
                    )
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    logger.warning(
                        "api_client_disconnected route=%s request_id=%s status=%s",
                        _safe_request_target(self.path),
                        request_id,
                        status_code,
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
                try:
                    self.send_response(status_code)
                    self._send_headers(
                        content_type,
                        len(body),
                        cache_control=cache_control,
                        extra_headers=extra_headers,
                    )
                    self._write_body(
                        body,
                        route=route,
                        request_id=request_id,
                        status_code=status_code,
                    )
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                    logger.warning(
                        "api_client_disconnected route=%s request_id=%s status=%s error=%s",
                        route,
                        request_id,
                        status_code,
                        exc,
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
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(content_length))
                self.send_header("Cache-Control", cache_control)
                self.send_header("Connection", "close")
                self.close_connection = True
                provided_headers = {key.casefold() for key in (extra_headers or {})}
                if "x-content-type-options" not in provided_headers:
                    self.send_header("X-Content-Type-Options", "nosniff")
                cors_origin = self._cors_allowed_origin()
                if cors_origin and "access-control-allow-origin" not in provided_headers:
                    self.send_header("Access-Control-Allow-Origin", cors_origin)
                    self.send_header("Vary", "Origin")
                    self.send_header(
                        "Access-Control-Allow-Headers",
                        "Content-Type, Authorization, X-Operator-Session",
                    )
                    self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                for header, value in (extra_headers or {}).items():
                    if value:
                        self.send_header(header, value)
                self.end_headers()

            def _cors_allowed_origin(self) -> str:
                return _same_host_cors_origin(
                    self.headers.get("Origin", ""),
                    self.headers.get("Host", ""),
                )

            def _prepare_response_body(
                self,
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
                    and "gzip" in str(self.headers.get("Accept-Encoding", "")).lower()
                ):
                    headers["Content-Encoding"] = "gzip"
                    headers["Vary"] = "Accept-Encoding"
                    return gzip.compress(body), headers
                if content_type.startswith("application/json"):
                    headers["Vary"] = "Accept-Encoding"
                return body, headers

            def _route_is_static(self, route: str) -> bool:
                return route in {"/", "/index.html", "/favicon.ico", "/favicon.png", "/api/health"}

            def _serve_static_route(self, route: str, request_id: str) -> bool:
                if route in {"/", "/index.html"}:
                    self._serve_board(request_id)
                    return True
                if route == "/favicon.ico":
                    body = _static_asset_bytes("favicon.ico")
                    self._send_bytes_response(
                        body,
                        content_type="image/x-icon",
                        request_id=request_id,
                        route=route,
                        cache_control="public, max-age=86400, immutable",
                    )
                    return True
                if route == "/favicon.png":
                    body = _static_asset_bytes("favicon.png")
                    self._send_bytes_response(
                        body,
                        content_type="image/png",
                        request_id=request_id,
                        route=route,
                        cache_control="public, max-age=86400, immutable",
                    )
                    return True
                if route == "/api/health":
                    body = _json_response(
                        ok=True,
                        data={
                            "status": "ok",
                            "base_url": api_server.base_url,
                            "bind_host": self.server.server_address[0],
                            "auth_required": bool(bearer_token),
                            "maintenance_mode": is_maintenance_mode(),
                        },
                        error=None,
                        request_id=request_id,
                    )
                    self._send_bytes_response(
                        body,
                        content_type="application/json",
                        request_id=request_id,
                        route=route,
                    )
                    return True
                return False

            def _serve_authenticated_get_route(
                self, route: str, request_id: str, query: dict
            ) -> bool:
                if route == "/api/attachment":
                    if not self._authenticate(request_id, query):
                        return True
                    self._serve_attachment(request_id, query)
                    return True
                if route == "/api/shared_file":
                    if not self._authenticate(request_id, query):
                        return True
                    self._serve_shared_file(request_id, query)
                    return True
                if route == "/api/repair_order_text":
                    if not self._authenticate(request_id, query):
                        return True
                    self._serve_repair_order_text(request_id, query)
                    return True
                if route == "/employee_salary_reconciliation_print":
                    if not self._authenticate(request_id, query):
                        return True
                    self._serve_employee_salary_reconciliation_print(request_id, query)
                    return True
                return False

            def _serve_readonly_get_route(self, route: str, request_id: str, query: dict) -> bool:
                if route not in readonly_routes:
                    return False
                if self._is_proxied_request() and operator_service is not None:
                    session = operator_service.resolve_session(
                        self.headers.get("X-Operator-Session", "")
                    )
                    if session is None:
                        self._operator_context_payload_reject(
                            request_id,
                            status=HTTPStatus.UNAUTHORIZED,
                            code="unauthorized",
                            message="Для чтения рабочей CRM нужен вход оператора.",
                        )
                        return True
                if not self._authenticate(request_id, query):
                    return True
                self._dispatch(route, request_id, query)
                return True

            def _operator_context_payload_with_session(
                self, route: str, payload: dict, session: dict | None
            ) -> dict:
                next_payload = dict(payload)
                if session is not None:
                    next_payload["_operator_session"] = session
                    if route not in operator_session_routes and route not in admin_only_routes:
                        next_payload["actor_name"] = session["username"]
                return next_payload

            def _trusted_agent_session(self, payload: dict) -> dict | None:
                """Authorize the local MCP service identity without impersonating a human."""

                if self._is_proxied_request():
                    return None
                if str(payload.get("source") or "").strip() != "mcp_agent_gateway_v2":
                    return None
                try:
                    policy = load_agent_gateway_security_policy()
                except Exception:
                    return None
                if not (policy.gateway_enabled and policy.writes_enabled and policy.raw_enabled):
                    return None
                identity = str(self.headers.get("X-Autostop-Agent-Identity", "") or "").strip()
                supplied_token = str(self.headers.get("X-Autostop-Agent-Token", "") or "").strip()
                expected_token = str(get_mcp_bearer_token() or "").strip()
                if not identity or not hmac.compare_digest(identity, policy.service_identity):
                    return None
                if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
                    return None
                return {
                    "token": "service-identity",
                    "username": policy.service_identity,
                    "role": "admin",
                    "is_admin": True,
                    "employee_id": "",
                    "service_identity": True,
                }

            def _operator_context_payload_reject(
                self,
                request_id: str,
                *,
                status: HTTPStatus,
                code: str,
                message: str,
            ) -> None:
                self._send_error_response(
                    request_id,
                    status,
                    code,
                    message,
                    {"auth_type": "operator_session"},
                )

            def _operator_context_payload(
                self, route: str, payload: dict, request_id: str
            ) -> dict | None:
                if operator_service is None:
                    return payload
                session = operator_service.resolve_session(
                    self.headers.get("X-Operator-Session", "")
                )
                if session is None:
                    session = self._trusted_agent_session(payload)
                next_payload = self._operator_context_payload_with_session(route, payload, session)
                if route in admin_only_routes:
                    if session is None:
                        self._operator_context_payload_reject(
                            request_id,
                            status=HTTPStatus.UNAUTHORIZED,
                            code="unauthorized",
                            message="Нужен вход администратора.",
                        )
                        return None
                    if not session.get("is_admin"):
                        self._operator_context_payload_reject(
                            request_id,
                            status=HTTPStatus.FORBIDDEN,
                            code="forbidden",
                            message="Нужны права администратора.",
                        )
                        return None
                    return next_payload
                if route in operator_session_routes:
                    if session is None:
                        self._operator_context_payload_reject(
                            request_id,
                            status=HTTPStatus.UNAUTHORIZED,
                            code="unauthorized",
                            message="Нужен вход оператора.",
                        )
                        return None
                    return next_payload
                if route in proxied_write_routes and session is None and self._is_proxied_request():
                    self._operator_context_payload_reject(
                        request_id,
                        status=HTTPStatus.UNAUTHORIZED,
                        code="unauthorized",
                        message="Нужен вход оператора.",
                    )
                    return None
                if str(next_payload.get("source", "")).strip().lower() == "ui" and session is None:
                    self._operator_context_payload_reject(
                        request_id,
                        status=HTTPStatus.UNAUTHORIZED,
                        code="unauthorized",
                        message="Нужен вход оператора.",
                    )
                    return None
                return next_payload

            def _is_proxied_request(self) -> bool:
                return bool(
                    str(self.headers.get("X-Forwarded-For", "") or "").strip()
                    or str(self.headers.get("X-Real-IP", "") or "").strip()
                )

            def _write_body(
                self, body: bytes, *, route: str, request_id: str, status_code: int
            ) -> bool:
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                    logger.warning(
                        "api_client_disconnected route=%s request_id=%s status=%s error=%s",
                        route,
                        request_id,
                        status_code,
                        exc,
                    )
                    return False

            def log_message(self, format: str, *args) -> None:
                return

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
