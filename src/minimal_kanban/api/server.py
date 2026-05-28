from __future__ import annotations

import gzip
import html
import json
import logging
import re
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
from time import perf_counter
from urllib.parse import parse_qs, quote, urlsplit

from ..config import (
    get_api_bearer_token,
    get_api_host,
    get_api_port,
    get_api_port_fallback_limit,
)
from ..models import business_timezone, parse_datetime
from ..operator_auth import OperatorAuthService
from ..services.card_service import CardService, ServiceError
from ..services.shared_files_service import SharedFilesService
from ..system_clipboard import ClipboardUnavailableError, list_clipboard_file_paths
from ..web_assets import BOARD_WEB_APP_HTML

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


def _shared_file_clipboard_position(value: object, *, default: int = 24) -> int:
    try:
        return max(0, int(float(str(value))))
    except (TypeError, ValueError):
        return default


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
        return '<tr><td colspan="11" class="empty">За последние 30 дней движений нет.</td></tr>'
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
        '<tr><td colspan="11" class="empty">За последние 30 дней движений нет.</td></tr>'
    )


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
        for candidate_port in range(self._start_port, self._start_port + self._fallback_limit):
            try:
                server = ReusableThreadingHTTPServer((self.host, candidate_port), handler)
                server.api_logger = self._logger
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
        base_url = self.base_url

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
            "/api/get_board_revision": service.get_board_revision,
            "/api/get_board_snapshot": service.get_board_snapshot,
            "/api/get_board_context": service.get_board_context,
            "/api/review_board": service.review_board,
            "/api/get_board_content": service.get_board_content,
            "/api/get_board_events": service.get_board_events,
            "/api/list_cashboxes": service.list_cashboxes,
            "/api/get_cash_journal": service.get_cash_journal,
            "/api/finance_audit": service.get_finance_audit,
            "/api/finance_audit/apply_safe_fixes": service.apply_finance_audit_safe_fixes,
            "/api/list_employees": service.list_employees,
            "/api/save_employee": service.save_employee,
            "/api/toggle_employee": service.toggle_employee,
            "/api/delete_employee": service.delete_employee,
            "/api/list_clients": service.list_clients,
            "/api/search_clients": service.search_clients,
            "/api/get_client": service.get_client,
            "/api/get_client_stats": service.get_client_stats,
            "/api/create_client": service.create_client,
            "/api/update_client": service.update_client,
            "/api/delete_client": service.delete_client,
            "/api/link_card_to_client": service.link_card_to_client,
            "/api/unlink_card_from_client": service.unlink_card_from_client,
            "/api/upsert_client_vehicle": service.upsert_client_vehicle,
            "/api/delete_client_vehicle": service.delete_client_vehicle,
            "/api/suggest_clients_for_card": service.suggest_clients_for_card,
            "/api/get_payroll_report": service.get_payroll_report,
            "/api/get_employee_salary_ledger": service.get_employee_salary_ledger,
            "/api/get_employee_salary_report": service.get_employee_salary_report,
            "/api/get_employee_salary_reconciliation": (service.get_employee_salary_reconciliation),
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
            "/api/correct_repair_order_number": service.correct_repair_order_number,
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
            "/api/set_card_board_summary": service.set_card_board_summary,
            "/api/mark_card_seen": service.mark_card_seen,
            "/api/update_sticky": service.update_sticky,
            "/api/set_card_deadline": service.set_card_deadline,
            "/api/set_card_indicator": service.set_card_indicator,
            "/api/move_card": service.move_card,
            "/api/mark_card_ready": service.mark_card_ready,
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
            "/api/list_shared_files": shared_files_service.list_shared_files,
            "/api/get_shared_file_info": shared_files_service.get_shared_file_info,
            "/api/fetch_shared_file": shared_files_service.fetch_shared_file,
            "/api/upload_shared_file": shared_files_service.upload_shared_file,
            "/api/rename_shared_file": shared_files_service.rename_shared_file,
            "/api/delete_shared_file": shared_files_service.delete_shared_file,
            "/api/copy_shared_file": shared_files_service.copy_shared_file,
            "/api/paste_shared_file": shared_files_service.paste_shared_file,
            "/api/paste_shared_files_from_clipboard": paste_shared_files_from_clipboard,
            "/api/update_shared_file_position": shared_files_service.update_shared_file_position,
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
            "/api/create_client",
            "/api/update_client",
            "/api/delete_client",
            "/api/link_card_to_client",
            "/api/unlink_card_from_client",
            "/api/upsert_client_vehicle",
            "/api/delete_client_vehicle",
            "/api/delete_cashbox",
            "/api/create_cash_transaction",
            "/api/create_employee_salary_transaction",
            "/api/cancel_last_cash_transaction",
            "/api/finance_audit/apply_safe_fixes",
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
            "/api/correct_repair_order_number",
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
            "/api/set_card_board_summary",
            "/api/mark_card_seen",
            "/api/update_sticky",
            "/api/set_card_deadline",
            "/api/set_card_indicator",
            "/api/move_card",
            "/api/mark_card_ready",
            "/api/bulk_move_cards",
            "/api/move_sticky",
            "/api/archive_card",
            "/api/restore_card",
            "/api/delete_sticky",
            "/api/add_card_attachment",
            "/api/remove_card_attachment",
            "/api/upload_shared_file",
            "/api/rename_shared_file",
            "/api/delete_shared_file",
            "/api/paste_shared_file",
            "/api/paste_shared_files_from_clipboard",
            "/api/update_shared_file_position",
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
            "/api/correct_repair_order_number",
            "/api/finance_audit/apply_safe_fixes",
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
                    "/api/list_operator_activity": operator_service.list_activity,
                    "/api/get_operator_activity_details": operator_service.get_activity_details,
                    "/api/get_operator_activity_aggregates": operator_service.get_activity_aggregates,
                    "/api/export_operator_activity": operator_service.export_activity,
                    "/api/open_card": operator_service.open_card,
                }
            )
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
                self.send_response(HTTPStatus.NO_CONTENT)
                self._send_headers("application/json", 0)

            def do_HEAD(self) -> None:
                request_id = str(uuid.uuid4())
                parsed = urlsplit(self.path)
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
                    self._serve_board(request_id)
                    return
                if route == "/favicon.ico":
                    body = _static_asset_bytes("favicon.ico")
                    self._send_bytes_response(
                        body,
                        content_type="image/x-icon",
                        request_id=request_id,
                        route=route,
                        cache_control="public, max-age=86400, immutable",
                    )
                    return
                if route == "/favicon.png":
                    body = _static_asset_bytes("favicon.png")
                    self._send_bytes_response(
                        body,
                        content_type="image/png",
                        request_id=request_id,
                        route=route,
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
                    self._send_bytes_response(
                        body,
                        content_type="application/json",
                        request_id=request_id,
                        route=route,
                    )
                    return
                if route == "/api/attachment":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_attachment(request_id, query)
                    return
                if route == "/api/shared_file":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_shared_file(request_id, query)
                    return
                if route == "/api/repair_order_text":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_repair_order_text(request_id, query)
                    return
                if route == "/employee_salary_reconciliation_print":
                    if not self._authenticate(request_id, query):
                        return
                    self._serve_employee_salary_reconciliation_print(request_id, query)
                    return
                readonly_routes = {
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
                    body = path.read_bytes()
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

            def _serve_shared_file(self, request_id: str, payload: dict) -> None:
                try:
                    path, file_meta = shared_files_service.get_shared_file_download(
                        str(payload.get("file_id", ""))
                    )
                    body = path.read_bytes()
                    disposition = (
                        "inline"
                        if str(payload.get("disposition", "")).strip().lower() == "inline"
                        else "attachment"
                    )
                    self._send_bytes_response(
                        body,
                        content_type=str(file_meta.get("mime_type") or "application/octet-stream"),
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

            def _serve_repair_order_text(self, request_id: str, payload: dict) -> None:
                try:
                    path, file_name = service.get_repair_order_text_download(
                        str(payload.get("card_id", ""))
                    )
                    body = path.read_bytes()
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
                started_at = perf_counter()
                try:
                    payload = self._operator_context_payload(route, payload, request_id)
                    if payload is None:
                        return
                    result = self.ROUTES[route](payload)
                    body = _json_response(ok=True, data=result, error=None, request_id=request_id)
                    app_duration_ms = max(perf_counter() - started_at, 0.0) * 1000
                    response_body, extra_headers = self._prepare_response_body(
                        body,
                        content_type="application/json",
                        server_timing=f"app;dur={app_duration_ms:.1f}",
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
                            "api_request route=%s request_id=%s status=ok duration_ms=%.1f body_bytes=%s encoded_bytes=%s gzip=%s",
                            route,
                            request_id,
                            app_duration_ms,
                            len(body),
                            len(response_body),
                            bool(extra_headers.get("Content-Encoding") == "gzip"),
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
                    )
                    self.send_response(status_code)
                    self._send_headers(
                        "application/json",
                        len(response_body),
                        extra_headers=extra_headers,
                    )
                    self._write_body(
                        response_body,
                        route=self.path,
                        request_id=request_id,
                        status_code=status_code,
                    )
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    logger.warning(
                        "api_client_disconnected route=%s request_id=%s status=%s",
                        self.path,
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
                    {"path": self.path},
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
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Operator-Session",
                )
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                for header, value in (extra_headers or {}).items():
                    if value:
                        self.send_header(header, value)
                self.end_headers()

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
