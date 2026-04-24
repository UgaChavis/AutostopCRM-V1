from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
import uuid
from datetime import UTC, datetime
from functools import cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging import Logger
from pathlib import Path, PurePath
from urllib.parse import parse_qs, quote, urlsplit

from ..config import (
    get_api_bearer_token,
    get_api_host,
    get_api_port,
    get_api_port_fallback_limit,
)
from ..operator_auth import OperatorAuthService
from ..services.card_service import CardService, ServiceError
from ..web_assets import BOARD_WEB_APP_HTML

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


QUIET_SUCCESS_ROUTES = frozenset(
    {
        "/api/health",
        "/api/get_board_snapshot",
        "/api/mark_card_seen",
    }
)


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
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _success_log_level(route: str) -> int:
    return logging.DEBUG if route in QUIET_SUCCESS_ROUTES else logging.INFO


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


@cache
def _static_asset_bytes(file_name: str) -> bytes:
    return (STATIC_DIR / file_name).read_bytes()


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
    ) -> None:
        self._service = service
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
        for candidate_port in range(self._start_port, self._start_port + self._fallback_limit):
            try:
                server = ReusableThreadingHTTPServer((self.host, candidate_port), handler)
                self._server = server
                self.port = candidate_port
                break
            except OSError:
                continue
        if self._server is None:
            raise RuntimeError("Не удалось запустить локальный API.")
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="minimal-kanban-api", daemon=True
        )
        self._thread.start()
        self._logger.info(
            "api_server_started bind_host=%s url=%s auth=%s",
            self.host,
            self.base_url,
            bool(self._bearer_token),
        )

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

    def _make_handler(self):
        service = self._service
        logger = self._logger
        bearer_token = self._bearer_token
        operator_service = self._operator_service
        base_url = self.base_url
        routes = {
            "/api/create_card": service.create_card,
            "/api/create_column": service.create_column,
            "/api/rename_column": service.rename_column,
            "/api/move_column": service.move_column,
            "/api/delete_column": service.delete_column,
            "/api/create_sticky": service.create_sticky,
            "/api/get_cards": service.get_cards,
            "/api/get_card": service.get_card,
            "/api/get_card_context": service.get_card_context,
            "/api/get_board_snapshot": service.get_board_snapshot,
            "/api/get_board_context": service.get_board_context,
            "/api/review_board": service.review_board,
            "/api/list_cashboxes": service.list_cashboxes,
            "/api/get_cash_journal": service.get_cash_journal,
            "/api/list_employees": service.list_employees,
            "/api/save_employee": service.save_employee,
            "/api/toggle_employee": service.toggle_employee,
            "/api/delete_employee": service.delete_employee,
            "/api/get_payroll_report": service.get_payroll_report,
            "/api/get_employee_salary_ledger": service.get_employee_salary_ledger,
            "/api/get_employee_salary_report": service.get_employee_salary_report,
            "/api/get_cashbox": service.get_cashbox,
            "/api/create_cashbox": service.create_cashbox,
            "/api/reorder_cashboxes": service.reorder_cashboxes,
            "/api/create_cashbox_transfer": service.create_cashbox_transfer,
            "/api/delete_cashbox": service.delete_cashbox,
            "/api/create_cash_transaction": service.create_cash_transaction,
            "/api/create_employee_salary_transaction": service.create_employee_salary_transaction,
            "/api/cancel_last_cash_transaction": service.cancel_last_cash_transaction,
            "/api/get_gpt_wall": service.get_gpt_wall,
            "/api/autofill_vehicle_data": service.autofill_vehicle_data,
            "/api/autofill_repair_order": service.autofill_repair_order,
            "/api/agent_status": service.agent_status,
            "/api/agent_tasks": service.agent_tasks,
            "/api/agent_actions": service.agent_actions,
            "/api/agent_scheduled_tasks": service.agent_scheduled_tasks,
            "/api/agent_enqueue_task": service.agent_enqueue_task,
            "/api/save_agent_scheduled_task": service.save_agent_scheduled_task,
            "/api/delete_agent_scheduled_task": service.delete_agent_scheduled_task,
            "/api/pause_agent_scheduled_task": service.pause_agent_scheduled_task,
            "/api/resume_agent_scheduled_task": service.resume_agent_scheduled_task,
            "/api/run_agent_scheduled_task": service.run_agent_scheduled_task,
            "/api/run_full_card_enrichment": service.run_full_card_enrichment,
            "/api/cleanup_card_content": service.cleanup_card_content,
            "/api/update_board_settings": service.update_board_settings,
            "/api/get_card_log": service.get_card_log,
            "/api/search_cards": service.search_cards,
            "/api/list_repair_orders": service.list_repair_orders,
            "/api/get_repair_order": service.get_repair_order,
            "/api/update_repair_order": service.update_repair_order,
            "/api/set_repair_order_status": service.set_repair_order_status,
            "/api/replace_repair_order_works": service.replace_repair_order_works,
            "/api/replace_repair_order_materials": service.replace_repair_order_materials,
            "/api/get_repair_order_text": service.get_repair_order_text,
            "/api/get_repair_order_print_workspace": service.get_repair_order_print_workspace,
            "/api/get_inspection_sheet_form": service.get_inspection_sheet_form,
            "/api/save_inspection_sheet_form": service.save_inspection_sheet_form,
            "/api/autofill_inspection_sheet_form": service.autofill_inspection_sheet_form,
            "/api/preview_repair_order_print_documents": service.preview_repair_order_print_documents,
            "/api/export_repair_order_print_pdf": service.export_repair_order_print_pdf,
            "/api/print_repair_order_documents": service.print_repair_order_documents,
            "/api/save_print_template": service.save_print_template,
            "/api/duplicate_print_template": service.duplicate_print_template,
            "/api/delete_print_template": service.delete_print_template,
            "/api/set_default_print_template": service.set_default_print_template,
            "/api/save_print_module_settings": service.save_print_module_settings,
            "/api/update_card": service.update_card,
            "/api/mark_card_seen": service.mark_card_seen,
            "/api/update_sticky": service.update_sticky,
            "/api/set_card_deadline": service.set_card_deadline,
            "/api/set_card_indicator": service.set_card_indicator,
            "/api/move_card": service.move_card,
            "/api/bulk_move_cards": service.bulk_move_cards,
            "/api/move_sticky": service.move_sticky,
            "/api/archive_card": service.archive_card,
            "/api/restore_card": service.restore_card,
            "/api/delete_sticky": service.delete_sticky,
            "/api/list_columns": service.list_columns,
            "/api/list_archived_cards": service.list_archived_cards,
            "/api/list_overdue_cards": service.list_overdue_cards,
            "/api/add_card_attachment": service.add_card_attachment,
            "/api/remove_card_attachment": service.remove_card_attachment,
            "/api/list_card_attachments": service.list_card_attachments,
            "/api/get_card_attachment": service.get_card_attachment,
            "/api/read_card_attachment": service.read_card_attachment,
        }
        proxied_write_routes = {
            "/api/create_card",
            "/api/create_column",
            "/api/rename_column",
            "/api/move_column",
            "/api/delete_column",
            "/api/create_cashbox",
            "/api/reorder_cashboxes",
            "/api/create_cashbox_transfer",
            "/api/save_employee",
            "/api/toggle_employee",
            "/api/delete_cashbox",
            "/api/create_cash_transaction",
            "/api/create_employee_salary_transaction",
            "/api/cancel_last_cash_transaction",
            "/api/create_sticky",
            "/api/autofill_vehicle_data",
            "/api/autofill_repair_order",
            "/api/agent_enqueue_task",
            "/api/save_agent_scheduled_task",
            "/api/delete_agent_scheduled_task",
            "/api/pause_agent_scheduled_task",
            "/api/resume_agent_scheduled_task",
            "/api/run_agent_scheduled_task",
            "/api/run_full_card_enrichment",
            "/api/cleanup_card_content",
            "/api/update_board_settings",
            "/api/update_repair_order",
            "/api/set_repair_order_status",
            "/api/replace_repair_order_works",
            "/api/replace_repair_order_materials",
            "/api/preview_repair_order_print_documents",
            "/api/export_repair_order_print_pdf",
            "/api/print_repair_order_documents",
            "/api/save_inspection_sheet_form",
            "/api/autofill_inspection_sheet_form",
            "/api/save_print_template",
            "/api/duplicate_print_template",
            "/api/delete_print_template",
            "/api/set_default_print_template",
            "/api/save_print_module_settings",
            "/api/update_card",
            "/api/mark_card_seen",
            "/api/update_sticky",
            "/api/set_card_deadline",
            "/api/set_card_indicator",
            "/api/move_card",
            "/api/bulk_move_cards",
            "/api/move_sticky",
            "/api/archive_card",
            "/api/restore_card",
            "/api/delete_sticky",
            "/api/add_card_attachment",
            "/api/remove_card_attachment",
        }
        operator_session_routes = {
            "/api/logout_operator",
            "/api/get_operator_profile",
            "/api/open_card",
        }
        admin_only_routes = {
            "/api/list_operator_users",
            "/api/save_operator_user",
            "/api/delete_operator_user",
            "/api/get_operator_user_report",
        }
        if operator_service is not None:
            routes.update(
                {
                    "/api/login_operator": operator_service.login,
                    "/api/logout_operator": operator_service.logout,
                    "/api/get_operator_profile": operator_service.get_profile,
                    "/api/list_operator_users": operator_service.list_users,
                    "/api/save_operator_user": operator_service.save_user,
                    "/api/delete_operator_user": operator_service.delete_user,
                    "/api/get_operator_user_report": operator_service.get_user_report,
                    "/api/open_card": operator_service.open_card,
                }
            )
            operator_session_routes.update(admin_only_routes)

        class RequestHandler(BaseHTTPRequestHandler):
            ROUTES = routes

            server_version = "MinimalKanbanAPI/1.0"
            sys_version = ""

            def do_OPTIONS(self) -> None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self._send_headers("application/json", 0)

            def do_HEAD(self) -> None:
                request_id = str(uuid.uuid4())
                parsed = urlsplit(self.path)
                route = parsed.path
                if route in {"/", "/index.html"}:
                    body = BOARD_WEB_APP_HTML.encode("utf-8")
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
                            "base_url": base_url,
                            "bind_host": self.server.server_address[0],
                            "auth_required": bool(bearer_token),
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
                parsed = urlsplit(self.path)
                route = parsed.path
                query = self._query_payload(parsed.query)
                if route in {"/", "/index.html"}:
                    self._serve_board()
                    return
                if route == "/favicon.ico":
                    body = _static_asset_bytes("favicon.ico")
                    self.send_response(HTTPStatus.OK)
                    self._send_headers(
                        "image/x-icon",
                        len(body),
                        cache_control="public, max-age=86400, immutable",
                    )
                    self._write_body(
                        body, route=route, request_id=request_id, status_code=HTTPStatus.OK
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
                    self._write_body(
                        body, route=route, request_id=request_id, status_code=HTTPStatus.OK
                    )
                    return
                if route == "/api/health":
                    body = _json_response(
                        ok=True,
                        data={
                            "status": "ok",
                            "base_url": base_url,
                            "bind_host": self.server.server_address[0],
                            "auth_required": bool(bearer_token),
                        },
                        error=None,
                        request_id=request_id,
                    )
                    self.send_response(HTTPStatus.OK)
                    self._send_headers("application/json", len(body))
                    self.wfile.write(body)
                    return
                if route == "/api/attachment":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_attachment(request_id, query)
                    return
                if route == "/api/repair_order_text":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_repair_order_text(request_id, query)
                    return
                readonly_routes = {
                    "/api/list_columns",
                    "/api/get_cards",
                    "/api/get_card",
                    "/api/get_board_snapshot",
                    "/api/get_board_context",
                    "/api/review_board",
                    "/api/list_cashboxes",
                    "/api/get_cash_journal",
                    "/api/list_employees",
                    "/api/get_payroll_report",
                    "/api/get_employee_salary_ledger",
                    "/api/get_employee_salary_report",
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
                }
                if route in readonly_routes:
                    if not self._authenticate(request_id, query):
                        return
                    self._dispatch(route, request_id, query)
                    return
                self._not_found(request_id)

            def do_POST(self) -> None:
                request_id = str(uuid.uuid4())
                route = urlsplit(self.path).path
                if route not in self.ROUTES:
                    self._not_found(request_id)
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.BAD_REQUEST,
                        "validation_error",
                        "Заголовок Content-Length имеет некорректное значение.",
                    )
                    return
                if not self._authenticate(request_id):
                    self._drain_request_body(content_length)
                    return
                raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                try:
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                except json.JSONDecodeError:
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
                parsed = parse_qs(query_string, keep_blank_values=True)
                payload: dict[str, object] = {}
                for key, values in parsed.items():
                    if not values:
                        continue
                    value = values[-1]
                    lowered = value.lower()
                    if lowered in {"true", "1", "yes", "y"}:
                        payload[key] = True
                    elif lowered in {"false", "0", "no", "n"}:
                        payload[key] = False
                    else:
                        payload[key] = value
                return payload

            def _serve_board(self) -> None:
                body = BOARD_WEB_APP_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._send_headers("text/html; charset=utf-8", len(body))
                self.wfile.write(body)

            def _serve_attachment(self, request_id: str, payload: dict) -> None:
                try:
                    path, attachment = service.get_attachment_download(
                        str(payload.get("card_id", "")),
                        str(payload.get("attachment_id", "")),
                    )
                    body = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header(
                        "Content-Type", attachment.mime_type or "application/octet-stream"
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header(
                        "Content-Disposition",
                        _content_disposition_header(attachment.file_name, disposition="attachment"),
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
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

            def _serve_repair_order_text(self, request_id: str, payload: dict) -> None:
                try:
                    path, file_name = service.get_repair_order_text_download(
                        str(payload.get("card_id", ""))
                    )
                    body = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header(
                        "Content-Disposition",
                        _content_disposition_header(file_name, disposition="inline"),
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
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

            def _authenticate(self, request_id: str, query: dict | None = None) -> bool:
                if not bearer_token:
                    return True
                auth_header = self.headers.get("Authorization", "")
                if auth_header == f"Bearer {bearer_token}":
                    return True
                query_payload = (
                    query if query is not None else self._query_payload(urlsplit(self.path).query)
                )
                access_token = str(query_payload.get("access_token", "") or "").strip()
                if access_token == bearer_token:
                    return True
                self._send_error_response(
                    request_id,
                    HTTPStatus.UNAUTHORIZED,
                    "unauthorized",
                    "Для вызова локального API нужен корректный bearer token.",
                )
                return False

            def _dispatch(self, route: str, request_id: str, payload: dict) -> None:
                try:
                    payload = self._operator_context_payload(route, payload, request_id)
                    if payload is None:
                        return
                    result = self.ROUTES[route](payload)
                    body = _json_response(ok=True, data=result, error=None, request_id=request_id)
                    self.send_response(HTTPStatus.OK)
                    self._send_headers("application/json", len(body))
                    if self._write_body(
                        body, route=route, request_id=request_id, status_code=HTTPStatus.OK
                    ):
                        logger.log(
                            _success_log_level(route),
                            "api_request route=%s request_id=%s status=ok",
                            route,
                            request_id,
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
                except ValueError as exc:
                    logger.warning(
                        "api_request route=%s request_id=%s status=error code=validation_error",
                        route,
                        request_id,
                    )
                    self._send_error_response(
                        request_id,
                        HTTPStatus.BAD_REQUEST,
                        "validation_error",
                        str(exc) or "Request payload is invalid.",
                    )
                except Exception as exc:  # pragma: no cover
                    logger.exception(
                        "api_request_failed route=%s request_id=%s error=%s", route, request_id, exc
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
            ) -> None:
                body = _json_response(
                    ok=False,
                    data=None,
                    error={"code": code, "message": message, "details": details or {}},
                    request_id=request_id,
                )
                try:
                    self.send_response(status_code)
                    self._send_headers("application/json", len(body))
                    self._write_body(
                        body, route=self.path, request_id=request_id, status_code=status_code
                    )
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    logger.warning(
                        "api_client_disconnected route=%s request_id=%s status=%s",
                        self.path,
                        request_id,
                        status_code,
                    )

            def _not_found(self, request_id: str) -> None:
                self._send_error_response(
                    request_id,
                    HTTPStatus.NOT_FOUND,
                    "not_found",
                    "Указанный маршрут API не найден.",
                    {"path": self.path},
                )

            def _send_headers(
                self,
                content_type: str,
                content_length: int,
                *,
                cache_control: str = "no-store",
            ) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(content_length))
                self.send_header("Cache-Control", cache_control)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Operator-Session",
                )
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.end_headers()

            def _operator_context_payload(
                self, route: str, payload: dict, request_id: str
            ) -> dict | None:
                if operator_service is None:
                    return payload
                session = operator_service.resolve_session(
                    self.headers.get("X-Operator-Session", "")
                )
                next_payload = dict(payload)
                if session is not None:
                    next_payload["_operator_session"] = session
                    if route not in operator_session_routes and route not in admin_only_routes:
                        next_payload["actor_name"] = session["username"]
                if route in admin_only_routes:
                    if session is None:
                        self._send_error_response(
                            request_id,
                            HTTPStatus.UNAUTHORIZED,
                            "unauthorized",
                            "Нужен вход администратора.",
                            {"auth_type": "operator_session"},
                        )
                        return None
                    if not session.get("is_admin"):
                        self._send_error_response(
                            request_id,
                            HTTPStatus.FORBIDDEN,
                            "forbidden",
                            "Нужны права администратора.",
                            {"auth_type": "operator_session"},
                        )
                        return None
                    return next_payload
                if route in operator_session_routes:
                    if session is None:
                        self._send_error_response(
                            request_id,
                            HTTPStatus.UNAUTHORIZED,
                            "unauthorized",
                            "Нужен вход оператора.",
                            {"auth_type": "operator_session"},
                        )
                        return None
                    return next_payload
                # Reverse-proxy deployments forward X-Forwarded-For/X-Real-IP
                # to this local API. In that proxied scenario, block anonymous
                # mutating or expensive operator-only routes while leaving
                # direct localhost/MCP calls intact.
                if route in proxied_write_routes and session is None and self._is_proxied_request():
                    self._send_error_response(
                        request_id,
                        HTTPStatus.UNAUTHORIZED,
                        "unauthorized",
                        "Нужен вход оператора.",
                        {"auth_type": "operator_session"},
                    )
                    return None
                if str(next_payload.get("source", "")).strip().lower() == "ui" and session is None:
                    self._send_error_response(
                        request_id,
                        HTTPStatus.UNAUTHORIZED,
                        "unauthorized",
                        "Нужен вход оператора.",
                        {"auth_type": "operator_session"},
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
    daemon_threads = False
    block_on_close = True
