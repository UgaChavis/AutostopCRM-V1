from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.json_safety import reject_deeply_nested_json
from minimal_kanban.operator_activity import OperatorActivityService
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.shared_files_service import SharedFilesService
from minimal_kanban.storage.json_store import JsonStore

SMOKE_SCENARIOS = (
    "login_gate_hides_board_until_operator_login",
    "desktop_board_card_roundtrip",
    "move_card_delta_roundtrip",
    "personal_extra_board_column",
    "display_dashboard_popup_1920x1080",
    "card_timer_start_stop",
    "card_long_description_controls_reachable",
    "cashbox_journal_workspace",
    "cashbox_journal_filters_and_no_audit",
    "cashbox_journal_compact_cleanup",
    "cashbox_journal_mode_and_period_navigation",
    "cashbox_journal_first_render_budget",
    "cashbox_transaction_cancellation",
    "repair_order_payments_modal",
    "repair_order_material_executor_defaults_to_operator_employee",
    "completion_act_editor_draft_roundtrip",
    "clients_modal",
    "clients_search_selects_realistic_row",
    "files_modal",
    "shared_files_scanability_markup",
    "employees_repair_order_returns_to_employee",
    "employee_shift_accrual_manual_salary",
    "clients_repair_order_returns_to_client",
    "repair_orders_list_returns_to_list",
    "repair_orders_toolbar_stays_available_while_list_scrolls",
    "repair_order_salary_override_popover",
    "payroll_chain_reaches_reports_and_reconciliation",
    "archive_search_filters_visible_rows",
    "cashboxes_journal_transfer_returns_to_cashbox",
    "escape_closes_top_modal_only",
    "operator_admin_employee_binding_returns_to_users",
)

MOBILE_SMOKE_SCENARIOS = (
    "mobile_board_load",
    "mobile_personal_extra_column",
    "mobile_card_detail",
    "mobile_cashboxes_workspace",
    "mobile_repair_orders_workspace",
    "mobile_clients_panel",
    "mobile_employees_panel",
    "mobile_archive_panel",
    "mobile_files_panel",
)

SMOKE_SCENARIOS = SMOKE_SCENARIOS + MOBILE_SMOKE_SCENARIOS
DESKTOP_SMOKE_SCENARIOS = tuple(
    name
    for name in SMOKE_SCENARIOS
    if name not in set(MOBILE_SMOKE_SCENARIOS) | {"login_gate_hides_board_until_operator_login"}
)

BROWSER_READ_RETRY_LIMIT = 1
BROWSER_READ_RETRY_DELAY_SECONDS = 0.15
BROWSER_SMOKE_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
CASHBOX_JOURNAL_FIRST_RENDER_BUDGET_MS = 2500
DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS = 240.0
PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS = 10.0
SMOKE_ACTION_TIMEOUT_MS = 20000
SMOKE_NAVIGATION_TIMEOUT_MS = 15000
SMOKE_UI_BIND_TIMEOUT_MS = 30000
BENIGN_FAILED_REQUEST_MARKERS = ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "AbortError")


def _pdf_file_is_parseable(path: Path) -> bool:
    try:
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return False
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        return bool(
            error == QPdfDocument.Error.None_
            and document.status() == QPdfDocument.Status.Ready
            and document.pageCount() > 0
        )
    finally:
        document.close()


def _pdfinfo_page_count(path: Path) -> int:
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    output = result.stdout.decode("utf-8", errors="replace")
    match = re.search(r"^Pages:\s*(\d+)\s*$", output, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def _pdf_page_texts(path: Path, page_count: int) -> list[str]:
    pages: list[str] = []
    for page_number in range(1, page_count + 1):
        try:
            result = subprocess.run(
                [
                    "pdftotext",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-layout",
                    str(path),
                    "-",
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        pages.append(result.stdout.decode("utf-8", errors="replace").strip("\f\r\n "))
    return pages


def _completion_act_footer_sequence(
    page_texts: list[str],
    *,
    platform_name: str | None = None,
) -> list[tuple[int, int]]:
    effective_platform = os.name if platform_name is None else platform_name
    footer_sequence: list[tuple[int, int]] = []
    for page_text in page_texts:
        compact = re.sub(r"\s+", " ", page_text)
        matches = re.findall(r"страница\s+(\d+)\s+из\s+(\d+)", compact, flags=re.IGNORECASE)
        if len(matches) == 1:
            footer_sequence.append(tuple(int(value) for value in matches[0]))
            continue
        if matches or effective_platform != "nt":
            return []

        non_empty_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not non_empty_lines:
            return []
        fallback = re.search(r"(\d+)\s+(\d+)\s*$", non_empty_lines[-1])
        if fallback is None:
            return []
        footer_sequence.append(tuple(int(value) for value in fallback.groups()))
    return footer_sequence


def _completion_act_pdf_contract(
    path: Path,
    *,
    expected_page_count: int,
    expected_item_names: list[str],
) -> bool:
    page_count = _pdfinfo_page_count(path)
    if page_count != expected_page_count:
        return False
    page_texts = _pdf_page_texts(path, page_count)
    if len(page_texts) != page_count or any(not text.strip() for text in page_texts):
        return False
    footer_sequence = _completion_act_footer_sequence(page_texts)
    if footer_sequence != [(page_number, page_count) for page_number in range(1, page_count + 1)]:
        return False
    combined_text = "\n".join(page_texts)
    return all(combined_text.count(name) == 1 for name in expected_item_names)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
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


def _json_dumps(payload: Any) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)


def _browser_timeout_seconds(value: Any) -> float:
    if isinstance(value, bool):
        return DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS
    try:
        timeout = float(
            DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS if value is None or value == "" else value
        )
    except (OverflowError, TypeError, ValueError):
        timeout = DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS
    if not math.isfinite(timeout):
        timeout = DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS
    if timeout < 30.0:
        return 30.0
    if timeout > 3600.0:
        return 3600.0
    return timeout


def _browser_attempts(value: Any) -> int:
    if isinstance(value, bool):
        return 4
    try:
        attempts = float(4 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return 4
    if not math.isfinite(attempts) or not attempts.is_integer():
        return 4
    if attempts < 1:
        return 1
    if attempts > 10:
        return 10
    return int(attempts)


def _browser_start_port(value: Any) -> int:
    if isinstance(value, bool):
        return 42731
    try:
        port = float(42731 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return 42731
    if not math.isfinite(port) or not port.is_integer():
        return 42731
    if port < 1:
        return 42731
    if port > 65535:
        return 65535
    return int(port)


@dataclass
class TempRuntime:
    temp_dir: tempfile.TemporaryDirectory[str]
    api: ApiServer
    service: CardService
    cashbox_id: str
    card_id: str
    extra_column_card_id: str
    employee_id: str
    payroll_card_id: str
    payroll_month: str
    salary_override_card_id: str
    client_id: str
    client_card_id: str
    archived_card_id: str

    @property
    def base_url(self) -> str:
        return self.api.base_url

    def close(self) -> None:
        self.api.stop()
        self.temp_dir.cleanup()


def _logger() -> logging.Logger:
    logger = logging.getLogger("autostop.browser_smoke")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def configure_stdout_utf8() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


def _is_transient_read_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    transient_types = (TimeoutError, ConnectionAbortedError, ConnectionResetError)
    return isinstance(exc, transient_types) or isinstance(reason, transient_types)


def _read_response_body(
    response: Any, *, limit_bytes: int = BROWSER_SMOKE_RESPONSE_MAX_BYTES
) -> bytes:
    body = response.read(limit_bytes + 1)
    if len(body) > limit_bytes:
        raise ValueError(f"Browser smoke response is too large ({limit_bytes} byte limit)")
    return body


def _read_bytes(url: str, *, accept: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": accept}, method="GET")
    for attempt in range(BROWSER_READ_RETRY_LIMIT + 1):
        try:
            with _urlopen_no_redirect(request, timeout=timeout) as response:
                return _read_response_body(response)
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionAbortedError,
            ConnectionResetError,
        ) as exc:
            if attempt < BROWSER_READ_RETRY_LIMIT and _is_transient_read_error(exc):
                time.sleep(BROWSER_READ_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise
    raise RuntimeError("Не удалось прочитать локальный smoke URL.")


def _load_json_response(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("API response JSON is too deeply nested") from exc
    reject_deeply_nested_json(
        payload,
        message="API response JSON is too deeply nested",
    )
    if not isinstance(payload, dict):
        raise ValueError("API response must be a JSON object")
    return payload


def _read_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    return _load_json_response(_read_bytes(url, accept="application/json", timeout=timeout))


def _read_text(url: str, *, timeout: float = 8.0) -> str:
    return _read_bytes(url, accept="text/html", timeout=timeout).decode("utf-8")


def _port_has_listener(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _first_free_port(start_port: int, *, host: str = "127.0.0.1", limit: int = 50) -> int:
    for candidate in range(start_port, start_port + limit):
        if not _port_has_listener(host, candidate):
            return candidate
    raise RuntimeError("Не удалось найти свободный локальный порт для browser smoke.")


def start_temp_runtime(*, start_port: int = 42731) -> TempRuntime:
    temp_dir = tempfile.TemporaryDirectory(prefix="autostop-browser-smoke-")
    base_dir = Path(temp_dir.name)
    logger = _logger()
    start_port = _first_free_port(start_port)
    store = JsonStore(state_file=base_dir / "state.json", logger=logger)
    service = CardService(
        store,
        logger,
        attachments_dir=base_dir / "attachments",
        repair_orders_dir=base_dir / "repair-orders",
    )
    service.set_onboarding_seen(True)
    cashbox = service.create_cashbox({"name": "Наличный", "actor_name": "SMOKE"})["cashbox"]
    service.create_cashbox({"name": "Безналичный", "actor_name": "SMOKE"})
    service.create_cash_transaction(
        {
            "cashbox_id": cashbox["id"],
            "direction": "income",
            "amount": "1000",
            "note": "Smoke opening balance",
            "actor_name": "SMOKE",
        }
    )
    for index in range(260):
        service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income" if index % 3 else "expense",
                "amount": str(10 + index),
                "note": f"Smoke journal batch {index:03d}",
                "actor_name": "SMOKE",
            }
        )
    card = service.create_card(
        {
            "vehicle": "Toyota Smoke",
            "title": "Browser smoke initial",
            "description": "Temporary card created by browser smoke.",
            "actor_name": "SMOKE",
        }
    )["card"]
    extra_column_card = service.create_card(
        {
            "vehicle": "Toyota Extra Column Smoke",
            "title": "Browser smoke extra column",
            "tags": [
                {"label": "ГОТОВ", "color": "green"},
                {"label": "SMOKE ПЕРВАЯ", "color": "green"},
                {"label": "SMOKE ВТОРАЯ", "color": "yellow"},
                {"label": "НАДО ЧТО ТО СДЕЛАТЬ", "color": "red"},
            ],
            "actor_name": "SMOKE",
        }
    )["card"]
    employee = service.save_employee(
        {
            "name": "Smoke Мастер",
            "position": "Механик",
            "salary_mode": "salary_plus_percent",
            "base_salary": "40000",
            "work_percent": "25",
            "actor_name": "SMOKE",
        }
    )["employee"]
    for index in range(1, 16):
        ranking_employee = service.save_employee(
            {
                "name": f"Smoke Сотрудник {index:02d}",
                "position": "Механик",
                "salary_mode": "none",
                "actor_name": "SMOKE",
            }
        )["employee"]
        service.create_employee_shift_accrual(
            {
                "employee_id": ranking_employee["id"],
                "amount": (f"{(16 - index) * 1000}.01" if index == 1 else str((16 - index) * 1000)),
                "note": "Smoke недельный рейтинг",
                "actor_name": "SMOKE",
            }
        )
    payroll_card = service.create_card(
        {
            "vehicle": "Lada Payroll Smoke",
            "title": "Browser smoke payroll order",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.update_card(
        {
            "card_id": payroll_card["id"],
            "repair_order": {
                "number": "901",
                "status": "open",
                "vehicle": "Lada Payroll Smoke",
                "payments": [
                    {
                        "amount": "20000",
                        "paid_at": "18.05.2026 10:00",
                        "payment_method": "cash",
                    }
                ],
                "works": [
                    {
                        "name": "Smoke payroll work",
                        "quantity": "1",
                        "price": "20000",
                        "executor_id": employee["id"],
                        "work_salary_override_enabled": "true",
                        "work_salary_guarantee": "5000",
                        "work_salary_percent_override": "45",
                        "work_salary_note": "Smoke salary override",
                    }
                ],
            },
            "actor_name": "SMOKE",
        }
    )
    closed_payroll = service.set_repair_order_status(
        {"card_id": payroll_card["id"], "status": "closed", "actor_name": "SMOKE"}
    )
    payroll_month = datetime.strptime(
        closed_payroll["repair_order"]["closed_at"], "%d.%m.%Y %H:%M"
    ).strftime("%Y-%m")
    salary_override_card = service.create_card(
        {
            "vehicle": "Lada Salary Override",
            "title": "Browser smoke salary override gear",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.update_card(
        {
            "card_id": salary_override_card["id"],
            "repair_order": {
                "number": "903",
                "status": "open",
                "vehicle": "Lada Salary Override",
                "payments": [
                    {
                        "amount": "20000",
                        "paid_at": "18.05.2026 11:00",
                        "payment_method": "cash",
                    }
                ],
                "works": [
                    {
                        "name": "Smoke override gear work",
                        "quantity": "1",
                        "price": "20000",
                        "executor_id": employee["id"],
                    }
                ],
            },
            "actor_name": "SMOKE",
        }
    )
    client = service.create_client(
        {
            "display_name": "Smoke Клиент",
            "phone": "+7 900 000-00-01",
            "actor_name": "SMOKE",
        }
    )["client"]
    client_card = service.create_card(
        {
            "vehicle": "Nissan Client Smoke",
            "title": "Browser smoke client order",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.link_card_to_client(
        {"card_id": client_card["id"], "client_id": client["id"], "actor_name": "SMOKE"}
    )
    service.update_card(
        {
            "card_id": client_card["id"],
            "repair_order": {
                "number": "902",
                "status": "open",
                "client": "Smoke Клиент",
                "vehicle": "Nissan Client Smoke",
                "works": [{"name": "Smoke client work", "quantity": "1", "price": "2500"}],
            },
            "actor_name": "SMOKE",
        }
    )
    for index in range(18):
        list_card = service.create_card(
            {
                "vehicle": f"Smoke Scroll Vehicle {index:02d}",
                "title": f"Browser smoke repair-order scroll row {index:02d}",
                "deadline": {"hours": 2},
                "actor_name": "SMOKE",
            }
        )["card"]
        service.update_card(
            {
                "card_id": list_card["id"],
                "repair_order": {
                    "number": str(920 + index),
                    "status": "open",
                    "vehicle": f"Smoke Scroll Vehicle {index:02d}",
                    "works": [
                        {
                            "name": f"Smoke scrolling work {index:02d}",
                            "quantity": "1",
                            "price": "1000",
                        }
                    ],
                },
                "actor_name": "SMOKE",
            }
        )
    archived_card = service.create_card(
        {
            "vehicle": "Archive Filter Smoke",
            "title": "Browser smoke archived search target",
            "description": "Archive search regression row.",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.archive_card({"card_id": archived_card["id"], "actor_name": "SMOKE"})
    shared_files_service = SharedFilesService(
        storage_dir=base_dir / "shared-files",
        index_file=base_dir / "shared_files_index.json",
        logger=logger,
    )
    shared_files_service.upload_shared_file(
        {
            "file_name": "Очень длинное имя файла для проверки читаемости smoke report.txt",
            "content_base64": base64.b64encode(b"autostop smoke shared file").decode("ascii"),
            "mime_type": "text/plain",
            "x": 24,
            "y": 24,
            "actor_name": "SMOKE",
            "source": "system",
        }
    )
    operator_service = OperatorAuthService(
        store,
        service,
        users_file=base_dir / "users.json",
        activity_service=OperatorActivityService(
            activity_dir=base_dir / "operator-activity",
            logger=logger,
        ),
        logger=logger,
    )
    admin_session = operator_service.login({"username": "admin", "password": "admin"})["session"]
    operator_service.set_user_employee(
        {
            "_operator_session": admin_session,
            "username": "admin",
            "employee_id": employee["id"],
            "source": "smoke",
        }
    )
    api = ApiServer(
        service,
        logger,
        operator_service=operator_service,
        host="127.0.0.1",
        start_port=start_port,
        fallback_limit=50,
        bearer_token="",
        shared_files_service=shared_files_service,
    )
    api.start()
    return TempRuntime(
        temp_dir=temp_dir,
        api=api,
        service=service,
        cashbox_id=cashbox["id"],
        card_id=card["id"],
        extra_column_card_id=extra_column_card["id"],
        employee_id=employee["id"],
        payroll_card_id=payroll_card["id"],
        payroll_month=payroll_month,
        salary_override_card_id=salary_override_card["id"],
        client_id=client["id"],
        client_card_id=client_card["id"],
        archived_card_id=archived_card["id"],
    )


def summarize_browser_events(
    *,
    console_errors: list[str],
    page_errors: list[str],
    failed_requests: list[str],
    first_render_ms: float,
) -> dict[str, Any]:
    actionable_failed_requests = [
        request for request in failed_requests if not is_benign_failed_request(request)
    ]
    ignored_failed_requests = [
        request for request in failed_requests if is_benign_failed_request(request)
    ]
    actionable_console_errors = list(console_errors)
    if not actionable_failed_requests:
        actionable_console_errors = [
            error
            for error in actionable_console_errors
            if "Failed to load resource: net::ERR_CONNECTION_TIMED_OUT" not in error
        ]
    return {
        "ok": not actionable_console_errors and not page_errors and not actionable_failed_requests,
        "first_render_ms": first_render_ms,
        "console_errors": actionable_console_errors,
        "page_errors": page_errors,
        "failed_requests": actionable_failed_requests,
        "ignored_failed_requests": ignored_failed_requests,
    }


def _request_failure_text(request: Any) -> str:
    failure = getattr(request, "failure", None)
    if callable(failure):
        failure = failure()
    if isinstance(failure, dict):
        return str(failure.get("errorText") or "").strip()
    return str(failure or "").strip()


def format_failed_request(request: Any) -> str:
    return f"{request.method} {request.url} {_request_failure_text(request)}".strip()


def is_benign_failed_request(value: str) -> bool:
    text = str(value or "").strip()
    if not text.upper().startswith("GET "):
        return False
    if any(marker in text for marker in BENIGN_FAILED_REQUEST_MARKERS):
        return True
    if "net::ERR_CONNECTION_TIMED_OUT" in text and (
        "/api/get_board_revision" in text or "/api/get_board_snapshot" in text
    ):
        return True
    return False


async def _wait_modal_open(page: Any, selector: str) -> None:
    await page.wait_for_function(
        "(selector) => document.querySelector(selector)?.classList.contains('is-open')",
        arg=selector,
    )


async def _wait_modal_closed(page: Any, selector: str) -> None:
    await page.wait_for_function(
        "(selector) => !document.querySelector(selector)?.classList.contains('is-open')",
        arg=selector,
    )


async def _is_modal_open(page: Any, selector: str) -> bool:
    return bool(
        await page.evaluate(
            "(selector) => document.querySelector(selector)?.classList.contains('is-open')",
            selector,
        )
    )


async def _close_card_modal_if_open(page: Any) -> bool:
    if not await _is_modal_open(page, "#cardModal"):
        return False
    await page.click("#cardModalCloseButtonTop")
    await _wait_modal_closed(page, "#cardModal")
    return True


async def _wait_clients_search_ready(
    page: Any, *, client_id: str, mobile: bool = False, query: str = ""
) -> None:
    if mobile:
        await page.wait_for_timeout(250)
        await page.wait_for_function(
            """(expectedQuery) => {
              const normalizedQuery = String(expectedQuery || '').trim().toLowerCase();
              const inputValue = String(document.querySelector('#mobileClientsSearchInput')?.value || '').trim().toLowerCase();
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              const rows = Array.from(document.querySelectorAll('#mobileClientsList [data-mobile-client-id]'));
              return (
                rows.length > 0 &&
                inputValue === normalizedQuery &&
                !meta.includes('ЗАГРУЗКА') &&
                (!normalizedQuery || rows.some((row) => row.textContent.toLowerCase().includes(normalizedQuery)))
              );
            }""",
            arg=query,
        )
        return
    await page.wait_for_timeout(250)
    await page.wait_for_function(
        """([clientId, expectedQuery]) => {
          const normalizedQuery = String(expectedQuery || '').trim().toLowerCase();
          const inputValue = String(document.querySelector('#clientsSearchInput')?.value || '').trim().toLowerCase();
          const meta = document.querySelector('#clientsMeta')?.textContent || '';
          const row = document.querySelector('[data-client-id="' + clientId + '"]');
          const rowText = String(row?.textContent || '').toLowerCase();
          return Boolean(
            row &&
            inputValue === normalizedQuery &&
            !meta.includes('ПОИСК ПО ВСЕМ КЛИЕНТАМ') &&
            !meta.includes('ЗАГРУЗКА КРАТКОГО СПИСКА') &&
            (!normalizedQuery || (meta.includes('НАЙДЕНО') && rowText.includes(normalizedQuery)))
          );
        }""",
        arg=[client_id, query],
    )


async def _login(page: Any) -> None:
    await page.wait_for_selector("#identityInput", state="visible")
    await page.wait_for_function(
        "() => window.__AUTOSTOP_UI_BOUND__ === true",
        timeout=SMOKE_UI_BIND_TIMEOUT_MS,
    )
    await page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          let mockedLoginFailureUsed = false;
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : String(input?.url || '');
            if (!mockedLoginFailureUsed && url.split('?')[0] === '/api/login_operator') {
              mockedLoginFailureUsed = true;
              return Promise.resolve(new Response(JSON.stringify({
                ok: false,
                data: null,
                error: { code: 'unauthorized', message: 'Неверный логин или пароль.', details: {} },
                meta: { request_id: 'browser-smoke-login-failure' },
              }), {
                status: 401,
                headers: { 'Content-Type': 'application/json' },
              }));
            }
            return originalFetch(input, init);
          };
        }"""
    )
    await page.fill("#identityInput", "admin")
    await page.fill("#identityPassword", "wrong-password")
    await page.click("#identitySave")
    await page.wait_for_function(
        """() => {
          const meta = document.querySelector('#identityMeta');
          return (
            document.querySelector('#identityModal')?.classList.contains('is-open') &&
            meta?.dataset.tone === 'error' &&
            meta?.textContent.includes('Неверный логин или пароль')
          );
        }"""
    )
    await page.fill("#identityInput", "admin")
    await page.fill("#identityPassword", "admin")
    await page.click("#identitySave")
    await _wait_modal_closed(page, "#identityModal")
    await page.wait_for_function(
        """() => {
          const statusText = document.querySelector('#statusLine')?.textContent || '';
          return !statusText.includes('Неверный логин или пароль');
        }"""
    )
    if await _is_modal_open(page, "#operatorProfileModal"):
        await page.click('[data-close="operator-profile"]')
        await _wait_modal_closed(page, "#operatorProfileModal")


async def _login_gate_hides_board(page: Any) -> bool:
    await page.wait_for_selector("#identityModal.is-open")
    await page.fill("#identityInput", "focus-regression-user")
    await page.fill("#identityPassword", "focus-regression-password")
    await page.focus("#identityPassword")
    return bool(
        await page.evaluate(
            """() => {
              openOperatorLoginModal();
              const body = document.body;
              const shell = document.querySelector('.shell');
              const modal = document.querySelector('#identityModal');
              const login = document.querySelector('#identityInput');
              const password = document.querySelector('#identityPassword');
              const shellStyle = shell ? getComputedStyle(shell) : null;
              return Boolean(
                body.classList.contains('operator-login-gate-open') &&
                modal?.classList.contains('operator-login-gate') &&
                shell?.getAttribute('aria-hidden') === 'true' &&
                shell?.hasAttribute('inert') &&
                shellStyle?.visibility === 'hidden' &&
                login?.value === 'focus-regression-user' &&
                password?.value === 'focus-regression-password' &&
                document.activeElement === password
              );
            }"""
        )
    )


def _api_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _payroll_chain_reaches_reports_and_reconciliation(runtime: TempRuntime) -> bool:
    query = urllib.parse.urlencode({"employee_id": runtime.employee_id})
    month_query = urllib.parse.urlencode(
        {"employee_id": runtime.employee_id, "month": runtime.payroll_month}
    )
    payroll = _api_data(_read_json(f"{runtime.base_url}/api/get_payroll_report?{month_query}"))
    ledger = _api_data(
        _read_json(f"{runtime.base_url}/api/get_employee_salary_ledger?{query}&months=6")
    )
    salary_report = _api_data(
        _read_json(f"{runtime.base_url}/api/get_employee_salary_report?{month_query}")
    )
    reconciliation = _api_data(
        _read_json(f"{runtime.base_url}/api/get_employee_salary_reconciliation?{query}")
    )
    print_html = _read_text(f"{runtime.base_url}/employee_salary_reconciliation_print?{query}")

    payroll_rows = payroll.get("detail_rows") or []
    payroll_ok = any(
        row.get("employee_id") == runtime.employee_id
        and row.get("card_id") == runtime.payroll_card_id
        and row.get("salary_amount") == "11750"
        for row in payroll_rows
    )
    ledger_rows = ledger.get("journal_rows") or []
    ledger_ok = any(
        row.get("kind") == "accrual"
        and row.get("card_id") == runtime.payroll_card_id
        and row.get("amount_display") == "11750"
        and "Выплата исполнителю" in str(row.get("scheme") or "")
        for row in ledger_rows
    )
    report_ok = (
        salary_report.get("totals", {}).get("work_accrued_total") == "11750"
        and "Выплата исполнителю" in str(salary_report.get("text") or "")
        and "11 750" in str(salary_report.get("text") or "").replace("\xa0", " ")
    )
    reconciliation_rows = reconciliation.get("rows") or []
    reconciliation_ok = reconciliation.get("meta", {}).get(
        "schema_version"
    ) == "employee_salary_reconciliation.v1" and any(
        row.get("kind") == "work_accrual"
        and row.get("card_id") == runtime.payroll_card_id
        and row.get("accrued") == "11750"
        and "Выплата исполнителю 5 000,00 ₽ + 45%" in str(row.get("scheme") or "")
        for row in reconciliation_rows
    )
    print_ok = (
        "Акт сверки зарплаты" in print_html
        and "Выплата исполнителю" in print_html
        and "11 750" in print_html.replace("\xa0", " ")
        and "@media print" in print_html
    )
    return bool(payroll_ok and ledger_ok and report_ok and reconciliation_ok and print_ok)


async def _exercise_operator_admin_employee_binding(page: Any) -> bool:
    await page.click("#operatorButton")
    await _wait_modal_open(page, "#operatorProfileModal")
    await page.click("#operatorAdminButton")
    await _wait_modal_open(page, "#operatorAdminModal")
    users_tab = page.locator('[data-operator-admin-tab="users"]')
    if await users_tab.count():
        await users_tab.first.click()
    await page.wait_for_selector("[data-bind-operator-employee]")
    await page.click("[data-bind-operator-employee]")
    await page.wait_for_selector("#operatorUserEmployeeBindingPanel:not(.hidden)")
    await page.wait_for_function(
        """() => document.querySelector('#operatorAdminCloseButton')?.textContent.trim() === 'НАЗАД'"""
    )
    await page.keyboard.press("Escape")
    await page.wait_for_function(
        """() => document.querySelector('#operatorUserEmployeeBindingPanel')?.classList.contains('hidden')"""
    )
    escape_ok = bool(
        await page.evaluate(
            """() => {
              return Boolean(
                document.querySelector('#operatorAdminModal')?.classList.contains('is-open') &&
                !document.querySelector('#operatorUserEditorPanel')?.classList.contains('hidden') &&
                !document.querySelector('#operatorUsersListPanel')?.classList.contains('hidden') &&
                document.querySelector('#operatorAdminCloseButton')?.textContent.trim() === 'ЗАКРЫТЬ'
              );
            }"""
        )
    )
    await page.click("[data-bind-operator-employee]")
    await page.wait_for_selector("#operatorUserEmployeeBindingPanel:not(.hidden)")
    await page.click("#operatorAdminCloseButton")
    await page.wait_for_function(
        """() => document.querySelector('#operatorUserEmployeeBindingPanel')?.classList.contains('hidden')"""
    )
    back_ok = await _is_modal_open(page, "#operatorAdminModal")
    await page.click("#operatorAdminCloseButton")
    await _wait_modal_closed(page, "#operatorAdminModal")
    final_close_ok = not await _is_modal_open(page, "#operatorAdminModal")
    await page.click('[data-close="operator-profile"]')
    await _wait_modal_closed(page, "#operatorProfileModal")
    return bool(escape_ok and back_ok and final_close_ok)


async def _exercise_card_modal_roundtrip(
    page: Any, runtime: TempRuntime
) -> tuple[bool, bool, bool]:
    card_selector = f'[data-card-id="{runtime.card_id}"]'
    await page.wait_for_selector(card_selector)
    await page.click(card_selector)
    await _wait_modal_open(page, "#cardModal")
    await page.wait_for_function(
        """() => {
          const editor = document.querySelector('#cardDescriptionEditor');
          const saveButton = document.querySelector('#saveCardButton');
          return !editor?.classList.contains('is-loading') && !saveButton?.disabled;
        }"""
    )
    timer_initial_ok = bool(
        await page.evaluate(
            """() => {
              const state = document.querySelector('#signalState');
              const start = document.querySelector('#signalStartButton');
              const stop = document.querySelector('#signalStopButton');
              const actions = document.querySelector('#signalActions');
              const daysValue = document.querySelector('#signalDaysValue');
              const hoursValue = document.querySelector('#signalHoursValue');
              return (
                state?.dataset.state === 'inactive' &&
                state?.textContent.trim() === 'ВЫКЛ' &&
                !start?.disabled &&
                start?.textContent.trim() === 'ЗАПУСТИТЬ' &&
                stop?.hidden === true &&
                actions?.dataset.layout === 'single' &&
                /^[0-9]+$/.test(daysValue?.textContent.trim() || '') &&
                /^[0-9]+$/.test(hoursValue?.textContent.trim() || '')
              );
            }"""
        )
    )
    await page.fill("#cardTitle", "Timer unsaved draft")
    await page.click("#signalStartButton")
    await page.wait_for_function(
        """() => {
          const state = document.querySelector('#signalState');
          const stop = document.querySelector('#signalStopButton');
          return state?.dataset.state === 'running' && stop?.hidden === false && !stop?.disabled;
        }"""
    )
    timer_running_visual_ok = bool(
        await page.evaluate(
            """() => {
              const panel = document.querySelector('.signal-panel');
              const state = document.querySelector('#signalState');
              const preview = document.querySelector('#signalPreview');
              const actions = document.querySelector('#signalActions');
              const start = document.querySelector('#signalStartButton');
              const stop = document.querySelector('#signalStopButton');
              if (!panel || !state || !preview || !actions || !start || !stop) return false;
              const rows = [
                panel.querySelector('.signal-panel__head'),
                preview,
                panel.querySelector('.signal-grid--timer'),
                actions,
              ];
              if (rows.some((row) => !row)) return false;
              const panelRect = panel.getBoundingClientRect();
              const rects = rows.map((row) => row.getBoundingClientRect());
              const noOverlap = rects.every((rect, index) => (
                rect.top >= panelRect.top - 0.5 &&
                rect.bottom <= panelRect.bottom + 0.5 &&
                (index === 0 || rect.top >= rects[index - 1].bottom - 0.5)
              ));
              const steppersFit = Array.from(panel.querySelectorAll('.signal-stepper')).every((stepper) => {
                const stepperRect = stepper.getBoundingClientRect();
                const controlRects = Array.from(stepper.children).map((control) => control.getBoundingClientRect());
                return controlRects.length === 3 && controlRects.every((rect, index) => (
                  rect.left >= stepperRect.left - 0.5 &&
                  rect.right <= stepperRect.right + 0.5 &&
                  (index === 0 || rect.left >= controlRects[index - 1].right - 0.5)
                ));
              });
              return (
                noOverlap &&
                steppersFit &&
                state.textContent.trim() === 'ИДЁТ' &&
                preview.textContent.includes(':') &&
                !document.querySelector('#signalRemaining') &&
                actions.dataset.layout === 'split' &&
                start.textContent.trim() === 'ЗАПУСТИТЬ' &&
                stop.textContent.trim() === 'СТОП' &&
                !start.hidden &&
                !stop.hidden
              );
            }"""
        )
    )
    screenshot_dir = str(os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR") or "").strip()
    if screenshot_dir:
        artifact_dir = Path(screenshot_dir).expanduser().resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await page.locator(".signal-panel").screenshot(
            path=str(artifact_dir / "card-timer-running.png")
        )
    started_card = runtime.service.get_card({"card_id": runtime.card_id, "actor_name": "SMOKE"})[
        "card"
    ]
    await page.click("#signalStopButton")
    await page.wait_for_function(
        """() => {
          const state = document.querySelector('#signalState');
          const start = document.querySelector('#signalStartButton');
          const stop = document.querySelector('#signalStopButton');
          return state?.dataset.state === 'inactive' && !start?.disabled && stop?.hidden === true;
        }"""
    )
    stopped_card = runtime.service.get_card({"card_id": runtime.card_id, "actor_name": "SMOKE"})[
        "card"
    ]
    timer_ok = bool(
        timer_initial_ok
        and timer_running_visual_ok
        and started_card.get("timer_state") == "running"
        and started_card.get("remaining_seconds", 0) > 0
        and stopped_card.get("timer_state") == "inactive"
        and stopped_card.get("remaining_seconds") == 0
        and await page.input_value("#cardTitle") == "Timer unsaved draft"
    )
    long_description = "\n".join(
        f"Long description regression line {index:03d}" for index in range(1, 101)
    )
    await page.fill("#cardDescriptionEditor", long_description)
    await page.wait_for_function(
        """() => {
          const editor = document.querySelector('#cardDescriptionEditor');
          return editor && editor.scrollHeight > editor.clientHeight;
        }"""
    )
    controls_ok = bool(
        await page.evaluate(
            """() => {
              const overview = document.querySelector('#cardModal [data-panel="overview"]');
              const editor = document.querySelector('#cardDescriptionEditor');
              const tagInput = document.querySelector('#tagInput');
              const tagAddButton = document.querySelector('#tagAddButton');
              const tagsPanel = document.querySelector('.tags-panel');
              const signalPanel = document.querySelector('.signal-panel');
              const signalDaysDecrement = document.querySelector('#signalDaysDecrementButton');
              const signalDaysIncrement = document.querySelector('#signalDaysIncrementButton');
              const signalHoursDecrement = document.querySelector('#signalHoursDecrementButton');
              const signalHoursIncrement = document.querySelector('#signalHoursIncrementButton');
              const signalDaysValue = document.querySelector('#signalDaysValue');
              const signalHoursValue = document.querySelector('#signalHoursValue');
              const signalRows = [
                signalPanel?.querySelector('.signal-panel__head'),
                document.querySelector('#signalPreview'),
                signalPanel?.querySelector('.signal-grid--timer'),
                document.querySelector('#signalActions'),
              ];
              const repairOrderButton = document.querySelector('#repairOrderButton');
              const bottomClose = document.querySelector('#cardModalCloseButtonBottom');
              const saveButton = document.querySelector('#saveCardButton');
              if (!overview || !editor || !tagInput || !tagAddButton || !tagsPanel || !signalPanel || !signalDaysDecrement || !signalDaysIncrement || !signalHoursDecrement || !signalHoursIncrement || !signalDaysValue || !signalHoursValue || signalRows.some((row) => !row) || !repairOrderButton || !bottomClose || !saveButton) return false;
              const visibleInOverview = (node) => {
                const viewport = overview.getBoundingClientRect();
                const rect = node.getBoundingClientRect();
                return rect.top >= viewport.top && rect.bottom <= viewport.bottom && rect.left >= viewport.left && rect.right <= viewport.right;
              };
              const visibleInWindow = (node) => {
                const rect = node.getBoundingClientRect();
                return rect.top >= 0 && rect.bottom <= window.innerHeight && rect.left >= 0 && rect.right <= window.innerWidth;
              };
              tagInput.scrollIntoView({ block: 'center', inline: 'nearest' });
              const signalHeight = signalPanel.getBoundingClientRect().height;
              const tagsHeight = tagsPanel.getBoundingClientRect().height;
              const signalRect = signalPanel.getBoundingClientRect();
              const signalRowRects = signalRows.map((row) => row.getBoundingClientRect());
              const signalRowsDoNotOverlap = signalRowRects.every((rect, index) => (
                rect.top >= signalRect.top - 0.5 &&
                rect.bottom <= signalRect.bottom + 0.5 &&
                (index === 0 || rect.top >= signalRowRects[index - 1].bottom - 0.5)
              ));
              const signalSteppersFit = Array.from(signalPanel.querySelectorAll('.signal-stepper')).every((stepper) => {
                const stepperRect = stepper.getBoundingClientRect();
                const controlRects = Array.from(stepper.children).map((control) => control.getBoundingClientRect());
                return controlRects.length === 3 && controlRects.every((rect, index) => (
                  rect.left >= stepperRect.left - 0.5 &&
                  rect.right <= stepperRect.right + 0.5 &&
                  (index === 0 || rect.left >= controlRects[index - 1].right - 0.5)
                ));
              });
              return (
                editor.scrollHeight > editor.clientHeight &&
                Math.abs(signalHeight - tagsHeight) <= 2 &&
                signalRowsDoNotOverlap &&
                signalSteppersFit &&
                visibleInOverview(tagInput) &&
                visibleInOverview(tagAddButton) &&
                visibleInOverview(tagsPanel) &&
                visibleInOverview(signalPanel) &&
                visibleInOverview(signalDaysDecrement) &&
                visibleInOverview(signalDaysIncrement) &&
                visibleInOverview(signalHoursDecrement) &&
                visibleInOverview(signalHoursIncrement) &&
                visibleInOverview(signalDaysValue) &&
                visibleInOverview(signalHoursValue) &&
                visibleInOverview(repairOrderButton) &&
                visibleInWindow(bottomClose) &&
                visibleInWindow(saveButton)
              );
            }"""
        )
    )
    await page.fill("#cardTitle", "Browser smoke saved")
    await page.click("#saveCardButton")
    await page.wait_for_function(
        """() => {
          const statusText = document.querySelector('#statusLine')?.textContent || '';
          const saveButton = document.querySelector('#saveCardButton');
          return statusText.includes('КАРТОЧКА СОХРАНЕНА') && saveButton && !saveButton.disabled;
        }"""
    )
    snapshot = _read_json(f"{runtime.base_url}/api/get_board_snapshot?compact=1&include_archive=0")
    cards = snapshot.get("data", {}).get("cards", [])
    roundtrip_ok = any(
        card.get("id") == runtime.card_id and card.get("title") == "Browser smoke saved"
        for card in cards
    )
    await _close_card_modal_if_open(page)
    return controls_ok, bool(roundtrip_ok), timer_ok


async def _exercise_display_dashboard(page: Any) -> bool:
    await page.click("#boardSettingsButton")
    await _wait_modal_open(page, "#boardSettingsModal")
    popup_errors: list[str] = []
    popup_failed_requests: list[str] = []
    async with page.expect_popup() as popup_info:
        await page.click("#openDisplayDashboardButton")
    dashboard_page = await popup_info.value
    _set_page_timeouts(dashboard_page)
    dashboard_page.on(
        "console",
        lambda msg: popup_errors.append(msg.text) if msg.type == "error" else None,
    )
    dashboard_page.on("pageerror", lambda exc: popup_errors.append(str(exc)))
    dashboard_page.on(
        "requestfailed",
        lambda request: popup_failed_requests.append(format_failed_request(request)),
    )
    try:
        await dashboard_page.set_viewport_size({"width": 1920, "height": 1080})
        await dashboard_page.wait_for_selector("h1", state="visible")
        await dashboard_page.wait_for_selector(".message-board", state="visible")
        await dashboard_page.wait_for_selector(".week-card", state="visible")
        await dashboard_page.wait_for_function(
            """() => (
              document.querySelectorAll('.week-card').length === 4 &&
              document.querySelectorAll('.week-card[data-current="true"]').length === 1 &&
              !document.querySelector('.salary-row') &&
              document.querySelector('#statusBadge')?.textContent.trim() === 'АКТУАЛЬНО'
            )"""
        )
        initial_dashboard_text = await dashboard_page.locator(
            "#messageBoard, #weeksChart"
        ).all_inner_texts()
        whole_ruble_display_ok = bool(
            await dashboard_page.evaluate(
                r"""() => {
                  const amount = document.querySelector('.week-card .week-amount')?.textContent || '';
                  const normalized = amount.replace(/[\s\u00a0\u202f]/g, '');
                  return /^\d+₽$/.test(normalized);
                }"""
            )
        )
        geometry_ok = bool(
            await dashboard_page.evaluate(
                """() => {
                  const dashboard = document.querySelector('#dashboard');
                  const header = document.querySelector('.dashboard-header');
                  const panels = Array.from(document.querySelectorAll('.panel'));
                  const messageBoard = document.querySelector('.message-board');
                  const weekCards = Array.from(document.querySelectorAll('.week-card'));
                  const majorNodes = [header, ...panels].filter(Boolean);
                  const insideViewport = [messageBoard, ...weekCards].filter(Boolean).every((node) => {
                    const rect = node.getBoundingClientRect();
                    return rect.left >= -0.5 && rect.top >= -0.5 && rect.right <= innerWidth + 0.5 && rect.bottom <= innerHeight + 0.5;
                  });
                  const cardsDoNotOverlap = (cards) => cards.every((card, index) => {
                    const rect = card.getBoundingClientRect();
                    return cards.slice(index + 1).every((other) => {
                      const next = other.getBoundingClientRect();
                      return rect.right <= next.left + 0.5 || next.right <= rect.left + 0.5 || rect.bottom <= next.top + 0.5 || next.bottom <= rect.top + 0.5;
                    });
                  });
                  return Boolean(
                    dashboard &&
                    document.title === 'Результаты автосервиса' &&
                    document.querySelector('h1')?.textContent.trim().toUpperCase() === 'РЕЗУЛЬТАТЫ АВТОСЕРВИСА' &&
                    document.querySelector('#weeksTitle')?.textContent.includes('4 недели') &&
                    !document.querySelector('.salary-row') &&
                    document.documentElement.scrollHeight <= innerHeight + 1 &&
                    document.body.scrollHeight <= innerHeight + 1 &&
                    document.documentElement.scrollWidth <= innerWidth + 1 &&
                    insideViewport &&
                    weekCards.length === 4 &&
                    cardsDoNotOverlap(majorNodes) &&
                    cardsDoNotOverlap(weekCards)
                  );
                }"""
            )
        )
        if not geometry_ok:
            geometry_diagnostics = await dashboard_page.evaluate(
                """() => ({
                  title: document.title,
                  h1: document.querySelector('h1')?.textContent.trim(),
                  viewport: [innerWidth, innerHeight],
                  htmlSize: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
                  bodySize: [document.body.scrollWidth, document.body.scrollHeight],
                  majorRects: [document.querySelector('.dashboard-header'), ...document.querySelectorAll('.panel')]
                    .filter(Boolean)
                    .map((node) => {
                      const rect = node.getBoundingClientRect();
                      return [rect.left, rect.top, rect.right, rect.bottom];
                    }),
                  messageBoardRect: (() => {
                    const node = document.querySelector('.message-board');
                    if (!node) return null;
                    const rect = node.getBoundingClientRect();
                    return [rect.left, rect.top, rect.right, rect.bottom];
                  })(),
                  weekRects: Array.from(document.querySelectorAll('.week-card')).map((node) => {
                    const rect = node.getBoundingClientRect();
                    return [rect.left, rect.top, rect.right, rect.bottom];
                  }),
                })"""
            )
            raise AssertionError(
                "dashboard_geometry_failed: " + json.dumps(geometry_diagnostics, ensure_ascii=False)
            )

        async def temporary_dashboard_error(route: Any) -> None:
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "data": None,
                        "error": {"code": "temporary_unavailable", "message": "temporary"},
                    }
                ),
            )

        await dashboard_page.route(
            "**/api/get_display_dashboard", temporary_dashboard_error, times=1
        )
        await dashboard_page.evaluate("() => window.__AUTOSTOP_DISPLAY_DASHBOARD__.refresh()")
        await dashboard_page.wait_for_function(
            "() => document.querySelector('#statusBadge')?.textContent.trim() === 'НЕТ ОБНОВЛЕНИЯ'"
        )
        retained_ok = bool(
            await dashboard_page.locator("#messageBoard, #weeksChart").all_inner_texts()
            == initial_dashboard_text
        )
        await dashboard_page.evaluate("() => window.__AUTOSTOP_DISPLAY_DASHBOARD__.refresh()")
        await dashboard_page.wait_for_function(
            "() => document.querySelector('#statusBadge')?.textContent.trim() === 'АКТУАЛЬНО'"
        )
        recovered_ok = bool(
            await dashboard_page.locator("#messageBoard, #weeksChart").all_inner_texts()
            == initial_dashboard_text
        )
        await dashboard_page.wait_for_timeout(950)

        artifact_dir = ROOT / "output" / "playwright"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await dashboard_page.screenshot(
            path=str(artifact_dir / "tv-dashboard-1920x1080.png"),
            full_page=True,
        )
        return bool(
            geometry_ok
            and whole_ruble_display_ok
            and retained_ok
            and recovered_ok
            and not popup_errors
            and not popup_failed_requests
        )
    finally:
        await dashboard_page.close()
        await page.click('[data-close="settings"]')
        await _wait_modal_closed(page, "#boardSettingsModal")


async def _exercise_move_card_delta(page: Any, runtime: TempRuntime) -> bool:
    snapshot = _api_data(
        _read_json(f"{runtime.base_url}/api/get_board_snapshot?compact=1&include_archive=0")
    )
    card = next(
        (item for item in snapshot.get("cards", []) if item.get("id") == runtime.card_id),
        None,
    )
    if not card:
        return False
    source_column_id = str(card.get("column") or card.get("column_id") or "")
    target_column = next(
        (
            column
            for column in snapshot.get("columns", [])
            if str(column.get("id") or "") != source_column_id
            and not column.get("is_system_locked")
        ),
        None,
    )
    if not source_column_id or not target_column:
        return False
    target_column_id = str(target_column.get("id") or "")
    card_selector = f'.card[data-card-id="{runtime.card_id}"]'
    source_card_selector = (
        f'.column[data-column-id="{source_column_id}"] .column__cards {card_selector}'
    )
    target_selector = f'.column[data-column-id="{target_column_id}"] .column__cards'

    async with page.expect_response(
        lambda response: response.url.split("?", 1)[0].endswith("/api/move_card")
    ) as response_info:
        await page.evaluate(
            """([sourceSelector, targetSelector]) => {
              const source = document.querySelector(sourceSelector);
              const target = document.querySelector(targetSelector);
              if (!source || !target) throw new Error('move_card_smoke_dom_missing');
              const transfer = new DataTransfer();
              source.dispatchEvent(new DragEvent('dragstart', {
                bubbles: true,
                cancelable: true,
                dataTransfer: transfer,
              }));
              const rect = target.getBoundingClientRect();
              target.dispatchEvent(new DragEvent('dragover', {
                bubbles: true,
                cancelable: true,
                dataTransfer: transfer,
                clientX: rect.left + Math.max(1, rect.width / 2),
                clientY: rect.top + Math.max(1, rect.height / 2),
              }));
              target.dispatchEvent(new DragEvent('drop', {
                bubbles: true,
                cancelable: true,
                dataTransfer: transfer,
                clientX: rect.left + Math.max(1, rect.width / 2),
                clientY: rect.top + Math.max(1, rect.height / 2),
              }));
            }""",
            [source_card_selector, target_selector],
        )
    moved_response = await response_info.value
    moved_data = _api_data(await moved_response.json())
    await page.wait_for_timeout(250)
    rendered_column_id = await page.evaluate(
        """(cardId) => document.querySelector(
          '.card[data-card-id="' + cardId + '"]:not([data-virtual-card="true"])'
        )?.closest('.column')?.dataset.columnId || ''""",
        runtime.card_id,
    )
    delta_ok = bool(
        moved_response.ok
        and (moved_data.get("meta") or {}).get("response_mode") == "delta"
        and isinstance(moved_data.get("affected_columns"), list)
        and moved_data.get("card", {}).get("id") == runtime.card_id
        and rendered_column_id == target_column_id
    )
    if not delta_ok:
        raise AssertionError(
            "move_card_delta_failed: "
            + json.dumps(
                {
                    "response_ok": moved_response.ok,
                    "response_mode": (moved_data.get("meta") or {}).get("response_mode"),
                    "has_affected_columns": isinstance(moved_data.get("affected_columns"), list),
                    "response_card_id": moved_data.get("card", {}).get("id"),
                    "request_payload": moved_response.request.post_data_json,
                    "expected_card_id": runtime.card_id,
                    "rendered_column_id": rendered_column_id,
                    "target_column_id": target_column_id,
                },
                ensure_ascii=False,
            )
        )
    return delta_ok


async def _exercise_personal_extra_board_column(page: Any, runtime: TempRuntime) -> bool:
    await page.click("#boardSettingsButton")
    await _wait_modal_open(page, "#boardSettingsModal")
    await page.click("#extraBoardColumnFilterButton")
    await page.wait_for_selector("#extraBoardColumnFilterPanel:not([hidden])")

    async def save_filter(label: str, color: str) -> None:
        await page.fill("#extraBoardColumnTagLabelInput", label)
        await page.select_option("#extraBoardColumnTagColorSelect", color)
        await page.click("#extraBoardColumnFilterSaveButton")
        await page.wait_for_function(
            """([expectedLabel, expectedColor]) => {
              const input = document.querySelector('#extraBoardColumnTagLabelInput');
              const select = document.querySelector('#extraBoardColumnTagColorSelect');
              const save = document.querySelector('#extraBoardColumnFilterSaveButton');
              return input?.value === expectedLabel && select?.value === expectedColor && save && !save.disabled;
            }""",
            arg=[label, color],
        )

    async def wait_for_card_editor(title: str) -> None:
        await page.wait_for_function(
            """(expectedTitle) => {
              const titleInput = document.querySelector('#cardTitle');
              const saveButton = document.querySelector('#saveCardButton');
              return titleInput?.value === expectedTitle && !titleInput.disabled && saveButton && !saveButton.disabled;
            }""",
            arg=title,
        )

    await save_filter("БРАУЗЕР SMOKE ФИЛЬТР", "yellow")
    await save_filter("НАДО ЧТО ТО СДЕЛАТЬ", "red")
    await page.click("#extraBoardColumnToggleButton")
    await page.wait_for_selector('[data-virtual-column="extra"]')
    await page.wait_for_function(
        """() => {
          const toggle = document.querySelector('#extraBoardColumnToggleButton');
          return toggle?.textContent.includes('СКРЫТЬ ДОП. КОЛОНКУ') && !toggle.disabled;
        }"""
    )
    await page.click('[data-close="settings"]')
    await _wait_modal_closed(page, "#boardSettingsModal")

    card_id = runtime.extra_column_card_id
    rendered = bool(
        await page.evaluate(
            """(cardId) => {
              const board = document.querySelector('#board');
              const virtualColumn = board?.querySelector('[data-virtual-column="extra"]');
              const virtualCards = Array.from(virtualColumn?.querySelectorAll('.card[data-card-id="' + cardId + '"]') || []);
              const nativeCards = Array.from(document.querySelectorAll('.card[data-card-id="' + cardId + '"]'))
                .filter((card) => !card.closest('[data-virtual-column]'));
              const children = Array.from(board?.children || []);
              const addColumn = children.find((node) => node.classList.contains('board-add-column'));
              return Boolean(
                virtualColumn &&
                virtualCards.length === 1 &&
                nativeCards.length === 1 &&
                virtualCards[0].getAttribute('draggable') === 'false' &&
                virtualCards[0].textContent.includes('НАДО ЧТО ТО СДЕЛАТЬ') &&
                virtualCards[0].textContent.includes('+1') &&
                children.indexOf(virtualColumn) === children.indexOf(addColumn) - 1
              );
            }""",
            card_id,
        )
    )
    if not rendered:
        return False

    virtual_card_selector = f'[data-virtual-column="extra"] .card[data-card-id="{card_id}"]'
    await page.click(virtual_card_selector)
    await _wait_modal_open(page, "#cardModal")
    await wait_for_card_editor("Browser smoke extra column")
    await page.fill("#cardTitle", "Browser smoke extra clone saved")
    await page.click("#saveCardButton")
    await page.wait_for_function(
        """([cardId, title]) => {
          const cards = Array.from(document.querySelectorAll('.card[data-card-id="' + cardId + '"]'));
          return cards.length === 2 && cards.every((card) => card.textContent.includes(title));
        }""",
        arg=[card_id, "Browser smoke extra clone saved"],
    )
    await _close_card_modal_if_open(page)

    await page.click(virtual_card_selector)
    await _wait_modal_open(page, "#cardModal")
    await wait_for_card_editor("Browser smoke extra clone saved")
    await page.locator('#tagList [data-remove-tag="НАДО ЧТО ТО СДЕЛАТЬ"]').click(force=True)
    await page.click("#saveCardButton")
    await page.wait_for_function(
        """(cardId) => {
          const virtualCards = document.querySelectorAll('[data-virtual-column="extra"] .card[data-card-id="' + cardId + '"]');
          const nativeCards = Array.from(document.querySelectorAll('.card[data-card-id="' + cardId + '"]'))
            .filter((card) => !card.closest('[data-virtual-column]'));
          return virtualCards.length === 0 && nativeCards.length === 1;
        }""",
        arg=card_id,
    )
    await _close_card_modal_if_open(page)

    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_selector("#board")
    await page.wait_for_selector('[data-virtual-column="extra"]')
    persisted = bool(
        await page.evaluate(
            """(cardId) => {
              const virtualColumn = document.querySelector('[data-virtual-column="extra"]');
              const virtualCards = virtualColumn?.querySelectorAll('.card[data-card-id="' + cardId + '"]') || [];
              const nativeCards = Array.from(document.querySelectorAll('.card[data-card-id="' + cardId + '"]'))
                .filter((card) => !card.closest('[data-virtual-column]'));
              return Boolean(virtualColumn && virtualCards.length === 0 && nativeCards.length === 1);
            }""",
            card_id,
        )
    )
    return bool(rendered and persisted)


async def _completion_act_browser_print_html(page: Any, pages: list[dict[str, Any]]) -> str:
    return str(
        await page.evaluate(
            """(pages) => {
              const parser = new DOMParser();
              const parsedPages = (Array.isArray(pages) ? pages : []).map((page) => {
                const documentNode = parser.parseFromString(
                  String(page?.html || ''),
                  'text/html'
                );
                const shell = documentNode.querySelector('.document-shell');
                const bodyFragment = shell?.outerHTML || documentNode.body?.innerHTML || '';
                const headFragments = Array.from(
                  documentNode.head?.querySelectorAll('style, link[rel="stylesheet"]') || []
                ).map((node) => node.outerHTML).filter(Boolean);
                return { bodyFragment, headFragments };
              }).filter((page) => page.bodyFragment.trim());
              const headFragments = Array.from(
                new Set(parsedPages.flatMap((page) => page.headFragments))
              ).join('');
              const body = parsedPages.length
                ? parsedPages.map((page) => page.bodyFragment).join('')
                : '<main>Нет данных акта для печати.</main>';
              return '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                + '<title>Акт выполненных работ</title>'
                + headFragments
                + '<style>.document-shell + .document-shell{break-before:page;page-break-before:always}</style>'
                + '</head><body>' + body + '</body></html>';
            }""",
            pages,
        )
    )


def _completion_act_pdf_fixture(
    base_form: dict[str, Any], *, label: str, maximal_final_block: bool, row_count: int = 26
) -> tuple[dict[str, Any], list[str]]:
    form = json.loads(json.dumps(base_form))
    item_names = [f"Smoke PDF {label} row {index:03d}" for index in range(1, row_count + 1)]
    form["document_number"] = f"SMOKE-PDF-{label.upper()}"
    form["items"] = [
        {
            "id": f"smoke-pdf-{label}-{index:02d}",
            "section": "works",
            "name": name,
            "unit": "ч",
            "quantity": "1",
            "price": str(100 + index),
        }
        for index, name in enumerate(item_names, start=1)
    ]
    if maximal_final_block:
        form["basis"] = ("Договор на выполнение работ и техническое обслуживание; " * 12)[:500]
        form["acceptance_text"] = (
            "Работы выполнены полностью и в срок, качество проверено заказчиком; " * 20
        )[:1000]
        for party_name in ("performer", "customer"):
            party = form.setdefault(party_name, {})
            party["legal_name"] = ("Организация с длинным полным наименованием " * 8)[:240]
            party["address"] = (
                "Красноярский край, город Красноярск, улица Длинная, дом 123, офис 456; " * 6
            )[:320]
            party["bank_name"] = ("Банк с длинным официальным наименованием " * 8)[:240]
            party["signer_position"] = ("Старший руководитель подразделения " * 5)[:120]
            party["signer_name"] = ("Иванов Иван Иванович " * 8)[:160]
    return form, item_names


async def _exercise_completion_act_physical_pdf_regression(
    page: Any, runtime: TempRuntime, artifact_dir: Path
) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    cases = (("short", False, 3), ("long", False, 149), ("max-300", True, 300))
    for label, maximal_final_block, row_count in cases:
        form, item_names = _completion_act_pdf_fixture(
            base_form,
            label=label,
            maximal_final_block=maximal_final_block,
            row_count=row_count,
        )
        request_payload = {
            "card_id": runtime.card_id,
            "selected_document_ids": ["completion_act"],
            "active_document_id": "completion_act",
            "document_overrides": {"completion_act": form},
        }
        preview_response = await page.request.post(
            f"{runtime.base_url}/api/preview_repair_order_print_documents",
            data=request_payload,
        )
        if not preview_response.ok:
            return False
        preview = _api_data(await preview_response.json())
        documents = preview.get("documents") if isinstance(preview, dict) else []
        document = documents[0] if isinstance(documents, list) and documents else {}
        pages = document.get("pages") if isinstance(document, dict) else []
        logical_page_count = int(document.get("page_count") or 0)
        if not isinstance(pages, list) or len(pages) != logical_page_count:
            return False
        if label == "long" and logical_page_count >= 7:
            return False
        if logical_page_count > 40:
            return False

        printable_html = await _completion_act_browser_print_html(page, pages)
        if printable_html.lower().count("<!doctype html>") != 1:
            return False
        chromium_path = artifact_dir / f"completion-act-{label}-chromium.pdf"
        pdf_page = await page.context.new_page()
        try:
            await pdf_page.emulate_media(media="print")
            await pdf_page.set_content(printable_html, wait_until="load")
            await pdf_page.evaluate("() => document.fonts?.ready")
            await pdf_page.pdf(
                path=str(chromium_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            await pdf_page.close()

        export_response = await page.request.post(
            f"{runtime.base_url}/api/export_repair_order_print_pdf",
            data=request_payload,
        )
        if not export_response.ok:
            return False
        exported = _api_data(await export_response.json())
        try:
            qt_pdf_bytes = base64.b64decode(exported.get("content_base64") or "", validate=True)
        except (TypeError, ValueError):
            return False
        qt_path = artifact_dir / f"completion-act-{label}-qt.pdf"
        qt_path.write_bytes(qt_pdf_bytes)

        if not (
            chromium_path.read_bytes().startswith(b"%PDF")
            and qt_pdf_bytes.startswith(b"%PDF")
            and _completion_act_pdf_contract(
                chromium_path,
                expected_page_count=logical_page_count,
                expected_item_names=item_names,
            )
            and _completion_act_pdf_contract(
                qt_path,
                expected_page_count=logical_page_count,
                expected_item_names=item_names,
            )
        ):
            return False
    return True


async def _arm_browser_print_capture(page: Any) -> None:
    await page.evaluate(
        """() => {
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__ = '';
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_OBSERVER__?.disconnect?.();
          const observer = new MutationObserver((records) => {
            records.flatMap((record) => Array.from(record.addedNodes || [])).forEach((node) => {
              if (!(node instanceof HTMLIFrameElement) || node.style.right !== '-12000px') return;
              const installPrintCapture = () => {
                if (!node.isConnected || !node.contentWindow || !node.contentDocument) return;
                node.contentWindow.print = () => {
                  const root = node.contentDocument?.documentElement;
                  window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__ = root
                    ? '<!doctype html>' + root.outerHTML
                    : '';
                  observer.disconnect();
                };
              };
              installPrintCapture();
              const timer = window.setInterval(installPrintCapture, 10);
              window.setTimeout(() => window.clearInterval(timer), 1200);
            });
          });
          observer.observe(document.body, { childList: true });
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_OBSERVER__ = observer;
        }"""
    )


async def _capture_browser_print_html(page: Any, button_selector: str) -> str:
    await _arm_browser_print_capture(page)
    await page.click(button_selector)
    await page.wait_for_function(
        "() => Boolean(window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__)", timeout=20_000
    )
    await page.wait_for_function(
        "(selector) => !document.querySelector(selector)?.disabled",
        arg=button_selector,
    )
    return str(await page.evaluate("() => window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__"))


async def _exercise_completion_act_main_print_regression(
    page: Any, runtime: TempRuntime, artifact_dir: Path
) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    form, item_names = _completion_act_pdf_fixture(
        base_form, label="main-button", maximal_final_block=False
    )
    saved = runtime.service.save_completion_act_form(
        {
            "card_id": runtime.card_id,
            "form": form,
            "expected_version": int((current.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (current.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-main-print-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    draft_version = int((saved.get("draft") or {}).get("version") or 0)
    try:
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(
                    "/api/preview_repair_order_print_documents"
                )
            )
        ) as preview_response_info:
            await page.click('[data-print-document="completion_act"]')
        preview_response = await preview_response_info.value
        preview = _api_data(await preview_response.json())
        documents = preview.get("documents") if isinstance(preview, dict) else []
        document = documents[0] if isinstance(documents, list) and documents else {}
        logical_page_count = int(document.get("page_count") or 0)
        if logical_page_count != 2:
            return False

        gear = page.locator("[data-completion-act-editor-open]")
        async with (
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
                )
            ),
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith(
                        "/api/preview_repair_order_print_documents"
                    )
                )
            ),
        ):
            await gear.click()
        await _wait_modal_open(page, "#completionActEditorModal")
        discarded_marker = "SMOKE-UNSAVED-DISCARD"
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(
                    "/api/preview_repair_order_print_documents"
                )
            )
        ):
            await page.fill("#completionActDocumentNumber", discarded_marker)
        await page.wait_for_function(
            """(marker) => {
              const frame = document.querySelector('#completionActPreviewFrame');
              return (frame?.contentDocument?.body?.innerText || '').includes(marker);
            }""",
            arg=discarded_marker,
        )

        async def delay_preview_response(route: Any) -> None:
            await asyncio.sleep(0.45)
            await route.continue_()

        await page.route("**/api/preview_repair_order_print_documents", delay_preview_response)
        try:
            page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
            printable_html = await _capture_browser_print_html(page, "#repairOrderPrintRunButton")
        finally:
            await page.unroute(
                "**/api/preview_repair_order_print_documents", delay_preview_response
            )
        if (
            printable_html.lower().count("<!doctype html>") != 1
            or printable_html.count('class="document-shell"') != logical_page_count
            or discarded_marker in printable_html
            or form["document_number"] not in printable_html
        ):
            return False
        chromium_path = artifact_dir / "completion-act-main-button-chromium.pdf"
        pdf_page = await page.context.new_page()
        try:
            await pdf_page.emulate_media(media="print")
            await pdf_page.set_content(printable_html, wait_until="load")
            await pdf_page.evaluate("() => document.fonts?.ready")
            await pdf_page.pdf(
                path=str(chromium_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            await pdf_page.close()
        return _completion_act_pdf_contract(
            chromium_path,
            expected_page_count=logical_page_count,
            expected_item_names=item_names,
        )
    finally:
        if draft_version:
            runtime.service.reset_completion_act_form(
                {
                    "card_id": runtime.card_id,
                    "expected_version": draft_version,
                    "idempotency_key": "browser-smoke-main-print-reset",
                    "actor_name": "SMOKE",
                    "source": "browser_smoke",
                }
            )


async def _exercise_completion_act_max_items_ui(page: Any, runtime: TempRuntime, gear: Any) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    form = json.loads(json.dumps(base_form))
    form["items"] = [
        {
            "id": f"smoke-ui-max-{index:03d}",
            "section": "works" if index <= 150 else "materials",
            "name": f"Smoke UI maximum row {index:03d}",
            "unit": "ч" if index <= 150 else "шт",
            "quantity": "1",
            "price": "1",
        }
        for index in range(1, 301)
    ]
    saved = runtime.service.save_completion_act_form(
        {
            "card_id": runtime.card_id,
            "form": form,
            "expected_version": int((current.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (current.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-ui-max-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    draft_version = int((saved.get("draft") or {}).get("version") or 0)
    try:
        async with (
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
                )
            ),
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith(
                        "/api/preview_repair_order_print_documents"
                    )
                )
            ),
        ):
            await gear.click()
        await _wait_modal_open(page, "#completionActEditorModal")
        await page.wait_for_function(
            """() => (
              document.querySelectorAll('#completionActItemRows [data-completion-act-item]').length === 300 &&
              document.querySelector('#completionActAddItemButton')?.disabled === true
            )"""
        )
        names = page.locator('#completionActItemRows [data-completion-act-item-field="name"]')
        rows_preserved = bool(
            await names.count() == 300
            and await names.first.input_value() == "Smoke UI maximum row 001"
            and await names.last.input_value() == "Smoke UI maximum row 300"
        )
        await page.click("#completionActEditorCloseX")
        await _wait_modal_closed(page, "#completionActEditorModal")
        return rows_preserved
    finally:
        if await _is_modal_open(page, "#completionActEditorModal"):
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
        if draft_version:
            runtime.service.reset_completion_act_form(
                {
                    "card_id": runtime.card_id,
                    "expected_version": draft_version,
                    "idempotency_key": "browser-smoke-ui-max-reset",
                    "actor_name": "SMOKE",
                    "source": "browser_smoke",
                }
            )


async def _exercise_completion_act_cross_card_race(page: Any, runtime: TempRuntime) -> bool:
    card_a_id = runtime.card_id
    card_b_id = runtime.extra_column_card_id
    card_a_marker = "SMOKE-CARD-A-ACT"
    card_b_marker = "SMOKE-CARD-B-ACT"
    card_b_edit_marker = "SMOKE-CARD-B-EDIT"
    card_a_customer = "Smoke requisites card A only"
    card_b_customer = "Smoke requisites card B only"
    card_a_before = runtime.service.get_completion_act_form({"card_id": card_a_id})
    card_b_before = runtime.service.get_completion_act_form({"card_id": card_b_id})
    if card_a_before.get("draft", {}).get("exists") or card_b_before.get("draft", {}).get("exists"):
        return False

    def marked_form(source: dict[str, Any], *, marker: str, customer_marker: str) -> dict[str, Any]:
        form = json.loads(json.dumps(source.get("form") or {}))
        form["document_number"] = marker
        customer = form.setdefault("customer", {})
        customer["legal_name"] = customer_marker
        customer["address"] = f"{customer_marker}, address"
        customer["inn"] = "2400000000" if marker == card_a_marker else "2400000001"
        form["items"] = [
            {
                "id": f"{marker.lower()}-row",
                "section": "works",
                "name": f"{marker} work",
                "unit": "ч",
                "quantity": "1",
                "price": "100",
            }
        ]
        return form

    saved_a = runtime.service.save_completion_act_form(
        {
            "card_id": card_a_id,
            "form": marked_form(
                card_a_before, marker=card_a_marker, customer_marker=card_a_customer
            ),
            "expected_version": int((card_a_before.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (card_a_before.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-cross-card-a-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    saved_b = runtime.service.save_completion_act_form(
        {
            "card_id": card_b_id,
            "form": marked_form(
                card_b_before, marker=card_b_marker, customer_marker=card_b_customer
            ),
            "expected_version": int((card_b_before.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (card_b_before.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-cross-card-b-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )

    def completion_request_card_id(request: Any) -> str:
        try:
            payload = request.post_data_json
        except (TypeError, ValueError):
            return ""
        return str(payload.get("card_id") or "") if isinstance(payload, dict) else ""

    def completion_get_response_for(response: Any, card_id: str) -> bool:
        return bool(
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
            and completion_request_card_id(response.request) == card_id
        )

    async def delay_card_a_completion_get(route: Any) -> None:
        if completion_request_card_id(route.request) == card_a_id:
            await asyncio.sleep(2.5)
        await route.continue_()

    async def close_repair_order_and_card() -> None:
        if await _is_modal_open(page, "#repairOrderModal"):
            await page.click('[data-close="repair-order"]')
            await _wait_modal_closed(page, "#repairOrderModal")
        await _close_card_modal_if_open(page)

    async def open_card_repair_order(card_id: str) -> None:
        selector = f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        await page.wait_for_selector(selector)
        await page.click(selector)
        await _wait_modal_open(page, "#cardModal")
        await page.wait_for_function(
            "() => !document.querySelector('#repairOrderButton')?.disabled"
        )
        await page.click("#repairOrderButton")
        await _wait_modal_open(page, "#repairOrderModal")

    await page.route("**/api/get_completion_act_form", delay_card_a_completion_get)
    cross_card_ok = False
    try:
        await page.click("#repairOrderPrintButton")
        await _wait_modal_open(page, "#repairOrderPrintModal")
        gear = page.locator("[data-completion-act-editor-open]")
        async with page.expect_response(
            lambda response: completion_get_response_for(response, card_a_id)
        ) as late_card_a_response_info:
            await gear.click()
            await _wait_modal_open(page, "#completionActEditorModal")
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
            await page.click("#repairOrderPrintCloseX")
            await _wait_modal_closed(page, "#repairOrderPrintModal")
            await close_repair_order_and_card()

            await open_card_repair_order(card_b_id)
            await page.click("#repairOrderPrintButton")
            await _wait_modal_open(page, "#repairOrderPrintModal")
            async with page.expect_response(
                lambda response: completion_get_response_for(response, card_b_id)
            ):
                await page.click("[data-completion-act-editor-open]")
            await _wait_modal_open(page, "#completionActEditorModal")
            await page.wait_for_function(
                "(marker) => document.querySelector('#completionActDocumentNumber')?.value === marker",
                arg=card_b_marker,
            )
        late_card_a_response = await late_card_a_response_info.value
        await late_card_a_response.body()
        await page.wait_for_timeout(100)

        card_b_form_survived = bool(
            await page.input_value("#completionActDocumentNumber") == card_b_marker
            and await page.input_value("#completionActCustomerLegalName") == card_b_customer
            and card_a_customer not in await page.input_value("#completionActCustomerLegalName")
            and card_a_marker not in await page.input_value("#completionActDocumentNumber")
        )
        await page.fill("#completionActDocumentNumber", card_b_edit_marker)
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith("/api/save_completion_act_form")
            )
        ) as card_b_save_response_info:
            await page.click("#completionActSaveButton")
        card_b_save_response = await card_b_save_response_info.value
        card_b_save_card_id = completion_request_card_id(card_b_save_response.request)
        await page.wait_for_function(
            "() => !document.querySelector('#completionActSaveButton')?.disabled"
        )
        card_a_after = runtime.service.get_completion_act_form({"card_id": card_a_id})
        card_b_after = runtime.service.get_completion_act_form({"card_id": card_b_id})
        card_b_print_html = await _capture_browser_print_html(page, "#completionActPrintButton")
        cross_card_ok = bool(
            card_b_form_survived
            and card_b_save_card_id == card_b_id
            and str((card_a_after.get("form") or {}).get("document_number") or "") == card_a_marker
            and str((card_b_after.get("form") or {}).get("document_number") or "")
            == card_b_edit_marker
            and card_b_edit_marker in card_b_print_html
            and card_b_customer in card_b_print_html
            and card_a_marker not in card_b_print_html
            and card_a_customer not in card_b_print_html
        )

        await page.click("#completionActEditorCloseX")
        await _wait_modal_closed(page, "#completionActEditorModal")
        await page.click("#repairOrderPrintCloseX")
        await _wait_modal_closed(page, "#repairOrderPrintModal")
        await close_repair_order_and_card()
        await open_card_repair_order(card_a_id)
    finally:
        await page.unroute("**/api/get_completion_act_form", delay_card_a_completion_get)
        for card_id, key in (
            (card_a_id, "browser-smoke-cross-card-a-reset"),
            (card_b_id, "browser-smoke-cross-card-b-reset"),
        ):
            current = runtime.service.get_completion_act_form({"card_id": card_id})
            version = int((current.get("draft") or {}).get("version") or 0)
            if version:
                runtime.service.reset_completion_act_form(
                    {
                        "card_id": card_id,
                        "expected_version": version,
                        "idempotency_key": key,
                        "actor_name": "SMOKE",
                        "source": "browser_smoke",
                    }
                )
    return bool(
        cross_card_ok
        and int((saved_a.get("draft") or {}).get("version") or 0) > 0
        and int((saved_b.get("draft") or {}).get("version") or 0) > 0
    )


async def _exercise_completion_act_editor(page: Any, runtime: TempRuntime) -> bool:
    repair_order_before = runtime.service.get_repair_order({"card_id": runtime.card_id})[
        "repair_order"
    ]
    screenshot_dir = str(os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR") or "").strip()
    artifact_dir = (
        Path(screenshot_dir).expanduser().resolve()
        if screenshot_dir
        else Path(runtime.temp_dir.name) / "playwright"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    await page.click("#repairOrderPrintButton")
    await _wait_modal_open(page, "#repairOrderPrintModal")
    main_print_regression_ok = await _exercise_completion_act_main_print_regression(
        page, runtime, artifact_dir
    )
    gear = page.locator("[data-completion-act-editor-open]")
    await gear.wait_for(state="visible")
    gear_geometry_ok = bool(
        await gear.evaluate(
            """(button) => {
              const rect = button.getBoundingClientRect();
              return rect.width >= 44 && rect.height >= 44 && !button.querySelector('button');
            }"""
        )
    )
    max_items_ui_ok = await _exercise_completion_act_max_items_ui(page, runtime, gear)
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
        )
    ) as get_form_response_info:
        await gear.focus()
        await page.keyboard.press("Enter")
    get_form_response = await get_form_response_info.value
    expected_date = str(
        (_api_data(await get_form_response.json()).get("form") or {}).get("document_date") or ""
    )
    await _wait_modal_open(page, "#completionActEditorModal")
    date_value = await page.input_value("#completionActDocumentDate")
    date_preserved_ok = date_value == expected_date
    await page.fill("#completionActDocumentNumber", "SMOKE-ACT-1")
    await page.click('[data-completion-act-section="items"]')
    await page.click("#completionActAddItemButton")
    last_row = page.locator("#completionActItemRows [data-completion-act-item]").last
    await last_row.locator('[data-completion-act-item-field="name"]').fill("Smoke completion work")
    await last_row.locator('[data-completion-act-item-field="quantity"]').fill("2")
    await last_row.locator('[data-completion-act-item-field="unit"]').fill("ч")
    row_count = await page.locator("#completionActItemRows [data-completion-act-item]").count()
    await last_row.locator('[data-completion-act-item-action="duplicate"]').click()
    await page.wait_for_function(
        "(expected) => document.querySelectorAll('#completionActItemRows [data-completion-act-item]').length === expected",
        arg=row_count + 1,
    )
    await (
        page.locator("#completionActItemRows [data-completion-act-item]")
        .last.locator('[data-completion-act-item-action="up"]')
        .click()
    )
    await (
        page.locator("#completionActItemRows [data-completion-act-item]")
        .last.locator('[data-completion-act-item-action="remove"]')
        .click()
    )
    row_actions_ok = bool(
        await page.locator("#completionActItemRows [data-completion-act-item]").count() == row_count
        and await page.locator(
            '#completionActItemRows [data-completion-act-item-field="name"]'
        ).last.input_value()
        == "Smoke completion work"
    )
    last_row = page.locator("#completionActItemRows [data-completion-act-item]").last
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/preview_repair_order_print_documents")
        )
    ):
        await last_row.locator('[data-completion-act-item-field="quantity"]').fill("")
    await page.wait_for_selector("#completionActLiveWarnings:not([hidden])")
    partial_row_warning_ok = (
        "заполните количество и цену"
        in (await page.locator("#completionActLiveWarnings").inner_text()).lower()
        and await page.locator("#completionActStaleWarning").is_hidden()
    )
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/preview_repair_order_print_documents")
        )
    ):
        await last_row.locator('[data-completion-act-item-field="quantity"]').fill("2")
        await last_row.locator('[data-completion-act-item-field="price"]').fill("50")
    await page.wait_for_function(
        r"""() => {
          const compact = (value) => String(value || '').replace(/[\s\u00a0\u202f]/g, '');
          return compact(document.querySelector('#completionActBaseTotal')?.textContent).includes('100,00') &&
            compact(document.querySelector('#completionActVatTotal')?.textContent).includes('5,00') &&
            compact(document.querySelector('#completionActGrossTotal')?.textContent).includes('105,00') &&
            compact(document.querySelector('#completionActItemRows .completion-act-item:last-child .completion-act-item__total')?.textContent).includes('100,00');
        }"""
    )
    preview_ok = bool(
        await page.evaluate(
            """() => {
              const frame = document.querySelector('#completionActPreviewFrame');
              const text = frame?.contentDocument?.body?.innerText || '';
              return text.includes('SMOKE-ACT-1') && text.includes('Smoke completion work');
            }"""
        )
    )

    async def delay_editor_preview(route: Any) -> None:
        await asyncio.sleep(0.45)
        await route.continue_()

    await page.click('[data-completion-act-section="document"]')
    editor_print_marker = "SMOKE-EDITOR-PRINT-FRESH"
    await page.route("**/api/preview_repair_order_print_documents", delay_editor_preview)
    try:
        await page.fill("#completionActDocumentNumber", editor_print_marker)
        editor_print_html = await _capture_browser_print_html(page, "#completionActPrintButton")
    finally:
        await page.unroute("**/api/preview_repair_order_print_documents", delay_editor_preview)
    editor_print_logical_pages = editor_print_html.count('class="document-shell"')
    editor_print_path = artifact_dir / "completion-act-editor-immediate-print.pdf"
    editor_print_page = await page.context.new_page()
    try:
        await editor_print_page.emulate_media(media="print")
        await editor_print_page.set_content(editor_print_html, wait_until="load")
        await editor_print_page.evaluate("() => document.fonts?.ready")
        await editor_print_page.pdf(
            path=str(editor_print_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
        )
    finally:
        await editor_print_page.close()
    editor_print_page_count = _pdfinfo_page_count(editor_print_path)
    editor_print_text = "\n".join(_pdf_page_texts(editor_print_path, editor_print_page_count))
    editor_print_race_ok = bool(
        editor_print_html.lower().count("<!doctype html>") == 1
        and editor_print_logical_pages > 0
        and editor_print_marker in editor_print_html
        and "SMOKE-ACT-1" not in editor_print_html
        and editor_print_marker in editor_print_text
        and _completion_act_pdf_contract(
            editor_print_path,
            expected_page_count=editor_print_logical_pages,
            expected_item_names=["Smoke completion work"],
        )
    )

    editor_export_marker = "SMOKE-EDITOR-EXPORT-FRESH"
    await page.route("**/api/preview_repair_order_print_documents", delay_editor_preview)
    try:
        await page.fill("#completionActDocumentNumber", editor_export_marker)
        async with page.expect_download() as editor_export_info:
            await page.click("#completionActExportButton")
        editor_export = await editor_export_info.value
    finally:
        await page.unroute("**/api/preview_repair_order_print_documents", delay_editor_preview)
    editor_export_path = artifact_dir / "completion-act-editor-immediate-export.pdf"
    await editor_export.save_as(str(editor_export_path))
    editor_export_page_count = _pdfinfo_page_count(editor_export_path)
    editor_export_text = "\n".join(_pdf_page_texts(editor_export_path, editor_export_page_count))
    editor_export_race_ok = bool(
        editor_export.suggested_filename.lower().endswith(".pdf")
        and editor_export_path.read_bytes().startswith(b"%PDF")
        and _pdf_file_is_parseable(editor_export_path)
        and editor_export_page_count == editor_print_logical_pages
        and editor_export_marker in editor_export_text
        and editor_print_marker not in editor_export_text
        and _completion_act_pdf_contract(
            editor_export_path,
            expected_page_count=editor_print_logical_pages,
            expected_item_names=["Smoke completion work"],
        )
    )

    async def delay_inflight_print_preview(route: Any) -> None:
        await asyncio.sleep(0.15)
        await route.continue_()

    inflight_print_old_marker = "SMOKE-PRINT-INFLIGHT-OLD"
    inflight_print_new_marker = "SMOKE-PRINT-INFLIGHT-NEW"
    await page.fill("#completionActDocumentNumber", inflight_print_old_marker)
    await page.route("**/api/preview_repair_order_print_documents", delay_inflight_print_preview)
    try:
        await _arm_browser_print_capture(page)
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(
                    "/api/preview_repair_order_print_documents"
                )
            )
        ):
            await page.click("#completionActPrintButton")
            await page.fill("#completionActDocumentNumber", inflight_print_new_marker)
    finally:
        await page.unroute(
            "**/api/preview_repair_order_print_documents",
            delay_inflight_print_preview,
        )
    await page.wait_for_function(
        "() => !document.querySelector('#completionActPrintButton')?.disabled"
    )
    await page.wait_for_function(
        """(marker) => {
          const frame = document.querySelector('#completionActPreviewFrame');
          return (frame?.contentDocument?.body?.innerText || '').includes(marker);
        }""",
        arg=inflight_print_new_marker,
    )
    stale_inflight_print_aborted_ok = bool(
        not await page.evaluate("() => Boolean(window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__)")
        and "изменились во время подготовки печати"
        in (await page.locator("#completionActEditorFooterMeta").inner_text()).lower()
    )

    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/preview_repair_order_print_documents")
        )
    ):
        await page.fill("#completionActDocumentNumber", "SMOKE-ACT-1")
    await page.wait_for_function(
        """() => {
          const frameText = document.querySelector('#completionActPreviewFrame')
            ?.contentDocument?.body?.innerText || '';
          const totalText = document.querySelector('#completionActBaseTotal')?.textContent || '';
          return frameText.includes('SMOKE-ACT-1') && !totalText.includes('Пересчёт');
        }"""
    )
    await page.click('[data-completion-act-section="items"]')

    screenshot_path = artifact_dir / "completion-act-editor.png"
    await page.screenshot(path=str(screenshot_path), full_page=False)
    screenshot_ok = screenshot_path.is_file() and screenshot_path.stat().st_size > 0
    physical_pdf_regression_ok = await _exercise_completion_act_physical_pdf_regression(
        page, runtime, artifact_dir
    )
    async with page.expect_download() as download_info:
        await page.click("#completionActExportButton")
    download = await download_info.value
    download_path = await download.path()
    pdf_download_ok = bool(
        download.suggested_filename.lower().endswith(".pdf")
        and download_path
        and Path(download_path).read_bytes().startswith(b"%PDF")
        and _pdf_file_is_parseable(Path(download_path))
    )

    async def delay_completion_act_save(route: Any) -> None:
        await asyncio.sleep(0.45)
        await route.continue_()

    save_snapshot_marker = "SMOKE-SAVE-SNAPSHOT"
    save_newer_marker = "SMOKE-ACT-1"
    await page.click('[data-completion-act-section="document"]')
    await page.fill("#completionActDocumentNumber", save_snapshot_marker)
    await page.route("**/api/save_completion_act_form", delay_completion_act_save)
    try:
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith("/api/save_completion_act_form")
            )
        ) as delayed_save_response_info:
            await page.click("#completionActSaveButton")
            await page.fill("#completionActDocumentNumber", save_newer_marker)
        delayed_save_response = _api_data(await (await delayed_save_response_info.value).json())
    finally:
        await page.unroute("**/api/save_completion_act_form", delay_completion_act_save)
    delayed_save_version = int((delayed_save_response.get("draft") or {}).get("version") or 0)
    await page.wait_for_function(
        "() => !document.querySelector('#completionActSaveButton')?.disabled"
    )
    saved_snapshot_form = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    save_response_race_ok = bool(
        await page.input_value("#completionActDocumentNumber") == save_newer_marker
        and save_snapshot_marker
        == str((saved_snapshot_form.get("form") or {}).get("document_number") or "")
        and "более новые несохранённые изменения"
        in (await page.locator("#completionActEditorFooterMeta").inner_text()).lower()
        and f"версия {delayed_save_version}"
        in await page.locator("#completionActEditorMeta").inner_text()
    )
    await page.click('[data-completion-act-section="items"]')

    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/save_completion_act_form")
        )
    ) as save_response_info:
        await page.click("#completionActSaveButton")
    save_response = _api_data(await (await save_response_info.value).json())
    saved_draft_version = int((save_response.get("draft") or {}).get("version") or 0)
    await page.wait_for_function(
        "(version) => document.querySelector('#completionActEditorMeta')?.textContent.includes('версия ' + String(version))",
        arg=saved_draft_version,
    )
    await page.click("#completionActEditorCloseX")
    await _wait_modal_closed(page, "#completionActEditorModal")
    await page.wait_for_function(
        "() => document.activeElement?.hasAttribute('data-completion-act-editor-open')"
    )
    focus_return_ok = True
    fresh_work_name = "Smoke fresh CRM source work"
    changed_works = json.loads(json.dumps(repair_order_before.get("works") or []))
    changed_works.append(
        {
            "name": fresh_work_name,
            "quantity": "3",
            "price": "75",
            "unit": "ч",
        }
    )
    runtime.service.update_repair_order(
        {
            "card_id": runtime.card_id,
            "repair_order": {"works": changed_works},
            "actor_name": "SMOKE SOURCE",
            "source": "browser_smoke",
        }
    )
    repair_order_form_source_ok = bool(
        await page.evaluate(
            """([name, quantity, price]) => {
          document.querySelector('#repairOrderAddWorkRowButton')?.click();
          const rows = document.querySelectorAll(
            '#repairOrderWorksBody tr[data-repair-order-row]'
          );
          const row = rows.item(rows.length - 1);
          const setValue = (field, value) => {
            const input = row?.querySelector(
              '[data-repair-order-cell="' + field + '"]'
            );
            if (!(input instanceof HTMLInputElement)) return false;
            input.value = value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          return Boolean(
            row &&
            setValue('name', name) &&
            setValue('quantity', quantity) &&
            setValue('price', price)
          );
        }""",
            [fresh_work_name, "3", "75"],
        )
    )
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
        )
    ):
        await page.click("[data-completion-act-editor-open]")
    await _wait_modal_open(page, "#completionActEditorModal")
    await page.wait_for_selector("#completionActStaleWarning:not([hidden])")
    stale_warning_ok = (
        "Данные CRM изменились" in await page.locator("#completionActStaleWarning").inner_text()
    )
    await page.click('[data-completion-act-section="items"]')
    await page.wait_for_selector("#completionActFreshItems:not([hidden])")
    expected_fresh_item_count = len(changed_works) + len(repair_order_before.get("materials") or [])
    fresh_items_text = await page.locator("#completionActFreshItems").inner_text()
    manual_item_names = await page.locator(
        '#completionActItemRows [data-completion-act-item-field="name"]'
    ).evaluate_all("(inputs) => inputs.map((input) => input.value)")
    fresh_items_source_ok = bool(
        fresh_work_name in fresh_items_text
        and f"Строк в CRM: {expected_fresh_item_count}" in fresh_items_text
        and await page.locator("[data-completion-act-fresh-item]").count()
        == expected_fresh_item_count
        and fresh_work_name not in manual_item_names
        and "Smoke completion work" in manual_item_names
    )
    stale_screenshot_path = artifact_dir / "completion-act-editor-stale-items.png"
    await page.screenshot(path=str(stale_screenshot_path), full_page=False)
    stale_screenshot_ok = (
        stale_screenshot_path.is_file() and stale_screenshot_path.stat().st_size > 0
    )
    draft_restored_ok = bool(
        await page.input_value("#completionActDocumentNumber") == "SMOKE-ACT-1"
        and await page.locator(
            '#completionActItemRows [data-completion-act-item-field="name"]'
        ).last.input_value()
        == "Smoke completion work"
    )
    await page.set_viewport_size({"width": 900, "height": 800})
    await page.wait_for_selector("#completionActMobilePreviewButton", state="visible")
    await page.click("#completionActMobilePreviewButton")
    await page.wait_for_function(
        "() => document.querySelector('#completionActEditorLayout')?.dataset.mobileView === 'preview'"
    )
    mobile_layout_ok = bool(
        await page.evaluate(
            """() => {
              const dialog = document.querySelector('.dialog--completion-act-editor');
              const layout = document.querySelector('#completionActEditorLayout');
              const rect = dialog?.getBoundingClientRect();
              return Boolean(
                dialog && layout && rect &&
                rect.left >= -1 && rect.right <= innerWidth + 1 &&
                document.documentElement.scrollWidth <= innerWidth + 1 &&
                layout.scrollWidth <= layout.clientWidth + 1
              );
            }"""
        )
    )
    await page.click("#completionActMobileDataButton")
    page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/reset_completion_act_form")
        )
    ):
        await page.click("#completionActResetButton")
    await page.wait_for_function(
        "() => !document.querySelector('#completionActEditorMeta')?.textContent.includes('Сохранён черновик')"
    )
    reset_ok = await page.input_value("#completionActDocumentNumber") != "SMOKE-ACT-1"
    fresh_items_reset_ok = await page.locator("#completionActFreshItems").is_hidden()
    await page.set_viewport_size({"width": 1440, "height": 960})
    await page.keyboard.press("Escape")
    await _wait_modal_closed(page, "#completionActEditorModal")
    await page.wait_for_function(
        "() => document.activeElement?.hasAttribute('data-completion-act-editor-open')"
    )
    escape_focus_ok = True
    runtime.service.update_repair_order(
        {
            "card_id": runtime.card_id,
            "repair_order": {"works": repair_order_before.get("works") or []},
            "actor_name": "SMOKE SOURCE RESTORE",
            "source": "browser_smoke",
        }
    )
    await page.click("#repairOrderPrintCloseX")
    await _wait_modal_closed(page, "#repairOrderPrintModal")
    repair_order_after = runtime.service.get_repair_order({"card_id": runtime.card_id})[
        "repair_order"
    ]
    checks = {
        "gear_geometry": gear_geometry_ok,
        "main_print_regression": main_print_regression_ok,
        "max_items_ui": max_items_ui_ok,
        "date_preserved": date_preserved_ok,
        "row_actions": row_actions_ok,
        "partial_row_warning": partial_row_warning_ok,
        "preview": preview_ok,
        "editor_print_race": editor_print_race_ok,
        "editor_export_race": editor_export_race_ok,
        "stale_inflight_print_aborted": stale_inflight_print_aborted_ok,
        "screenshot": screenshot_ok,
        "physical_pdf_regression": physical_pdf_regression_ok,
        "pdf_download": pdf_download_ok,
        "save_response_race": save_response_race_ok,
        "focus_return": focus_return_ok,
        "repair_order_form_source": repair_order_form_source_ok,
        "stale_warning": stale_warning_ok,
        "fresh_items_source": fresh_items_source_ok,
        "stale_screenshot": stale_screenshot_ok,
        "draft_restored": draft_restored_ok,
        "mobile_layout": mobile_layout_ok,
        "reset": reset_ok,
        "fresh_items_reset": fresh_items_reset_ok,
        "escape_focus": escape_focus_ok,
        "repair_order_unchanged": repair_order_after == repair_order_before,
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        print(
            _json_dumps({"completion_act_editor_failed_checks": failed_checks}),
            file=sys.stderr,
            flush=True,
        )
    return not failed_checks


async def _desktop_scenarios(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    scenarios = {name: False for name in DESKTOP_SMOKE_SCENARIOS}
    await page.wait_for_selector("#board")
    scenarios["payroll_chain_reaches_reports_and_reconciliation"] = (
        _payroll_chain_reaches_reports_and_reconciliation(runtime)
    )
    admin_binding_ok = await _exercise_operator_admin_employee_binding(page)
    scenarios["operator_admin_employee_binding_returns_to_users"] = bool(admin_binding_ok)

    scenarios["display_dashboard_popup_1920x1080"] = await _exercise_display_dashboard(page)

    controls_ok, roundtrip_ok, timer_ok = await _exercise_card_modal_roundtrip(page, runtime)
    scenarios["card_long_description_controls_reachable"] = bool(controls_ok)
    scenarios["desktop_board_card_roundtrip"] = bool(roundtrip_ok)
    scenarios["card_timer_start_stop"] = bool(timer_ok)
    scenarios["move_card_delta_roundtrip"] = await _exercise_move_card_delta(page, runtime)
    scenarios["personal_extra_board_column"] = await _exercise_personal_extra_board_column(
        page, runtime
    )

    await page.click("#cashboxesButton")
    await _wait_modal_open(page, "#cashboxesModal")
    await page.wait_for_selector("#cashboxJournalDownloadButton")
    await page.wait_for_selector("#cashboxesList [data-cashbox-id]")
    await page.wait_for_selector("#cashboxTransactions [data-cashbox-transaction-cancel]")
    selected_transaction_id = await page.locator(
        "#cashboxTransactions [data-cashbox-transaction-cancel]"
    ).first.get_attribute("data-cashbox-transaction-cancel")
    await page.locator("#cashboxTransactions [data-cashbox-transaction-cancel]").first.click()
    await page.wait_for_selector("#cashboxCancelPopover:not([hidden])")
    await page.fill("#cashboxCancelReasonInput", "Browser smoke cancellation reason")
    await page.click("#cashboxCancelConfirmButton")
    await page.wait_for_function(
        """() => {
          const statusText = document.querySelector('#statusLine')?.textContent || '';
          const popover = document.querySelector('#cashboxCancelPopover');
          return statusText.includes('ОПЕРАЦИЯ ОТМЕНЕНА') && popover?.hidden === true;
        }"""
    )
    cashbox_after_cancellation = runtime.service.get_cashbox(
        {"cashbox_id": runtime.cashbox_id, "transaction_limit": 500}
    )
    cancelled_transaction = next(
        (
            item
            for item in cashbox_after_cancellation["transactions"]
            if item["id"] == selected_transaction_id
        ),
        None,
    )
    cancellation_transaction = next(
        (
            item
            for item in cashbox_after_cancellation["transactions"]
            if item.get("related_transaction_id") == selected_transaction_id
            and item.get("transaction_kind") == "cashbox_cancellation"
        ),
        None,
    )
    scenarios["cashbox_transaction_cancellation"] = bool(
        cancelled_transaction
        and cancelled_transaction.get("transaction_kind") == "cashbox_cancelled"
        and cancellation_transaction
    )
    journal_open_started = time.perf_counter()
    await page.click("#cashboxJournalButton")
    await _wait_modal_open(page, "#cashboxJournalModal")
    await page.wait_for_selector(".cashbox-journal-operation-head")
    await page.wait_for_selector(".cashbox-journal-operation-row")
    journal_first_rows_ms = (time.perf_counter() - journal_open_started) * 1000
    scenarios["cashbox_journal_first_render_budget"] = (
        journal_first_rows_ms <= CASHBOX_JOURNAL_FIRST_RENDER_BUDGET_MS
    )
    scenarios["cashbox_journal_compact_cleanup"] = bool(
        await page.evaluate(
            """() => {
              const toolbar = document.querySelector('.cashbox-journal-toolbar');
              const balanceStrip = document.querySelector('.cashbox-journal-balance-strip');
              const balanceToggle = document.querySelector('[data-cash-journal-toggle-balances]');
              const reset = document.querySelector('[data-cash-journal-reset]');
              const filters = document.querySelector('[data-cash-journal-filter]');
              const activeFilters = document.querySelector('[data-cash-journal-region="active-filters"]');
              const bodyRegion = document.querySelector('[data-cash-journal-region="body"]');
              const firstNote = document.querySelector('.cashbox-journal-operation-row__note')?.textContent || '';
              const firstType = document.querySelector('.cashbox-journal-operation-row__type')?.textContent || '';
              const dayMeta = document.querySelector('[data-cash-journal-compact-day]')?.textContent || '';
              const visibleNoPairTags = Array.from(document.querySelectorAll('.cashbox-journal-operation-tag'))
                .filter((tag) => tag.textContent.trim() === 'нет пары');
              const transferRowsWithoutDiagnosticChips = Array.from(
                document.querySelectorAll('.cashbox-journal-operation-row--transfer')
              ).every((row) => !row.querySelector('.cashbox-journal-operation-tag'));
              return Boolean(
                bodyRegion &&
                !toolbar &&
                !balanceStrip &&
                !balanceToggle &&
                !reset &&
                !filters &&
                !activeFilters &&
                !/^(Поступление|Списание)\\s*:/.test(firstNote.trim()) &&
                /^(Приход|Расход|Перевод)$/.test(firstType.trim()) &&
                /[+-]?\\d/.test(dayMeta) &&
                dayMeta.includes('оп.') &&
                visibleNoPairTags.length === 0 &&
                transferRowsWithoutDiagnosticChips
              );
            }"""
        )
    )
    await page.click("#cashboxJournalStatsButton")
    await page.wait_for_selector(".cashbox-journal-view--stats")
    await page.wait_for_selector("[data-cash-journal-period-kind][data-cash-journal-period-key]")
    await page.click("[data-cash-journal-period-kind][data-cash-journal-period-key]")
    await page.wait_for_selector(".cashbox-journal-operation-head")
    await page.wait_for_function(
        """() => document.querySelector('#cashboxJournalLedgerButton')?.getAttribute('aria-pressed') === 'true'"""
    )
    await page.wait_for_function(
        """() => document.querySelectorAll('.cashbox-journal-operation-row').length >= 1"""
    )
    scenarios["cashbox_journal_filters_and_no_audit"] = bool(
        await page.evaluate(
            """() => {
              const filters = document.querySelector('[data-cash-journal-filter]');
              const reset = document.querySelector('[data-cash-journal-reset]');
              const toolbar = document.querySelector('.cashbox-journal-toolbar');
              const activeFilters = document.querySelector('[data-cash-journal-region="active-filters"]');
              const rows = Array.from(document.querySelectorAll('.cashbox-journal-operation-row'));
              const loadMore = document.querySelector('[data-cash-journal-load-more]');
              return Boolean(
                !filters &&
                !reset &&
                !toolbar &&
                !activeFilters &&
                document.querySelector('#cashboxJournalLedgerButton')?.getAttribute('aria-pressed') === 'true' &&
                document.querySelector('#cashboxJournalStatsButton')?.getAttribute('aria-pressed') === 'false' &&
                document.querySelector('.cashbox-journal-operation-head') &&
                (!loadMore || !/из\\s+\\d+/.test(loadMore.textContent || '')) &&
                !document.querySelector('#cashboxFinanceAuditButton') &&
                !document.querySelector('#cashboxJournalAuditButton') &&
                !document.body.textContent.includes('Финансовая сверка') &&
                rows.length >= 1 &&
                rows.every((row) => row.getAttribute('aria-label')?.startsWith('Операция кассы '))
              );
            }"""
        )
    )
    scenarios["cashbox_journal_mode_and_period_navigation"] = bool(
        await page.evaluate(
            """() => Boolean(
              document.querySelector('#cashboxJournalLedgerButton')?.getAttribute('aria-pressed') === 'true' &&
              document.querySelector('#cashboxJournalStatsButton')?.getAttribute('aria-pressed') === 'false' &&
              !document.querySelector('.cashbox-journal-view--stats') &&
              !document.querySelector('[data-cash-journal-clear-filter="period"]') &&
              document.querySelector('.cashbox-journal-operation-head') &&
              document.querySelectorAll('.cashbox-journal-operation-row').length >= 1
            )"""
        )
    )
    await page.click('[data-close="cashbox-journal"]')
    await _wait_modal_closed(page, "#cashboxJournalModal")
    scenarios["cashbox_journal_workspace"] = await _is_modal_open(page, "#cashboxesModal")
    await page.click("#cashboxTransferButton")
    await _wait_modal_open(page, "#cashboxTransferModal")
    await page.fill("#cashboxTransferAmountInput", "100")
    await page.wait_for_function(
        """() => document.querySelector('#cashboxTransferPreview')?.textContent.includes('Откуда после')"""
    )
    await page.click('[data-close="cashbox-transfer"]')
    await _wait_modal_closed(page, "#cashboxTransferModal")
    scenarios["cashboxes_journal_transfer_returns_to_cashbox"] = await _is_modal_open(
        page, "#cashboxesModal"
    )
    await page.click('[data-close="cashboxes"]')
    await _wait_modal_closed(page, "#cashboxesModal")

    await page.click(f'[data-card-id="{runtime.card_id}"]')
    await _wait_modal_open(page, "#cardModal")
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/get_repair_order")
        )
    ) as repair_order_response_info:
        await page.click("#repairOrderButton")
    repair_order_response = await repair_order_response_info.value
    repair_order_payload = _api_data(await repair_order_response.json())
    repair_order_number = str(
        (repair_order_payload.get("repair_order") or {}).get("number") or ""
    ).strip()
    await _wait_modal_open(page, "#repairOrderModal")
    if repair_order_number:
        await page.wait_for_function(
            """(number) => document.querySelector('#repairOrderNumber')?.textContent.trim() === number""",
            arg=repair_order_number,
        )
    material_rows_before = int(
        await page.evaluate(
            "() => document.querySelectorAll('#repairOrderMaterialsBody tr[data-repair-order-row]').length"
        )
    )
    await page.click("#repairOrderAddMaterialRowButton")
    await page.wait_for_function(
        """(expected) => document.querySelectorAll('#repairOrderMaterialsBody tr[data-repair-order-row]').length === expected""",
        arg=material_rows_before + 1,
    )
    material_default_ok = bool(
        await page.evaluate(
            """(employeeId) => {
              const rows = Array.from(document.querySelectorAll('#repairOrderMaterialsBody tr[data-repair-order-row]'));
              const select = rows.at(-1)?.querySelector('[data-repair-order-cell="executor_id"]');
              return select?.value === employeeId;
            }""",
            runtime.employee_id,
        )
    )
    await page.select_option(
        '#repairOrderMaterialsBody tr[data-repair-order-row]:last-child [data-repair-order-cell="executor_id"]',
        "",
    )
    await page.click("#repairOrderAddMaterialRowButton")
    await page.wait_for_function(
        """(expected) => document.querySelectorAll('#repairOrderMaterialsBody tr[data-repair-order-row]').length === expected""",
        arg=material_rows_before + 2,
    )
    material_manual_preserved_ok = bool(
        await page.evaluate(
            """(employeeId) => {
              const rows = Array.from(document.querySelectorAll('#repairOrderMaterialsBody tr[data-repair-order-row]'));
              const previous = rows.at(-2)?.querySelector('[data-repair-order-cell="executor_id"]');
              const current = rows.at(-1)?.querySelector('[data-repair-order-cell="executor_id"]');
              return previous?.value === '' && current?.value === employeeId;
            }""",
            runtime.employee_id,
        )
    )
    scenarios["repair_order_material_executor_defaults_to_operator_employee"] = bool(
        material_default_ok and material_manual_preserved_ok
    )
    completion_act_editor_ok = await _exercise_completion_act_editor(page, runtime)
    completion_act_cross_card_ok = await _exercise_completion_act_cross_card_race(page, runtime)
    scenarios["completion_act_editor_draft_roundtrip"] = bool(
        completion_act_editor_ok and completion_act_cross_card_ok
    )
    await page.wait_for_selector("#repairOrderPaymentsButton")
    await page.click("#repairOrderPaymentsButton")
    await _wait_modal_open(page, "#repairOrderPaymentsModal")
    scenarios["repair_order_payments_modal"] = True
    await page.keyboard.press("Escape")
    await _wait_modal_closed(page, "#repairOrderPaymentsModal")
    scenarios["escape_closes_top_modal_only"] = await _is_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    await _close_card_modal_if_open(page)

    await page.click("#employeesButton")
    await _wait_modal_open(page, "#employeesModal")
    await page.click(f'[data-employee-id="{runtime.employee_id}"]')
    await page.wait_for_selector(
        f'#employeesDetailTable [data-card-id="{runtime.payroll_card_id}"]'
    )
    await page.click("#employeeShiftAccrualButton")
    await page.wait_for_selector("#employeeShiftAccrualDialog:not([hidden])")
    await page.fill("#employeeShiftAccrualAmountInput", "1234")
    await page.click("#employeeShiftAccrualConfirmButton")
    await page.wait_for_function(
        """() => document.querySelector('#employeeShiftAccrualDialog')?.hidden === true"""
    )
    await page.wait_for_function(
        """() => {
          const rows = Array.from(document.querySelectorAll('#employeesDetailTable tr'));
          return rows.some((row) =>
            row.textContent.includes('Выплата за смены за текущую неделю') &&
            row.textContent.includes('1234')
          );
        }"""
    )
    shift_query = urllib.parse.urlencode({"employee_id": runtime.employee_id, "months": 6})
    shift_ledger = _api_data(
        _read_json(f"{runtime.base_url}/api/get_employee_salary_ledger?{shift_query}")
    )
    scenarios["employee_shift_accrual_manual_salary"] = bool(
        any(row.get("kind") == "shift_accrual" for row in shift_ledger.get("journal_rows") or [])
    )
    await page.click(f'#employeesDetailTable [data-card-id="{runtime.payroll_card_id}"]')
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    scenarios["employees_repair_order_returns_to_employee"] = bool(
        await _is_modal_open(page, "#employeesModal")
        and await page.evaluate(
            """(employeeId) => {
              const row = document.querySelector('[data-employee-id="' + employeeId + '"]');
              return Boolean(row?.closest('.employees-row')?.classList.contains('is-active'));
            }""",
            runtime.employee_id,
        )
    )
    await page.click('[data-close="employees"]')
    await _wait_modal_closed(page, "#employeesModal")

    await page.click("#clientsButton")
    await _wait_modal_open(page, "#clientsModal")
    await page.wait_for_selector("#clientNewButton")
    scenarios["clients_modal"] = True
    await page.fill("#clientsSearchInput", "Smoke")
    await _wait_clients_search_ready(page, client_id=runtime.client_id, query="Smoke")
    scenarios["clients_search_selects_realistic_row"] = bool(
        await page.evaluate(
            """(clientId) => {
              const row = document.querySelector('[data-client-id="' + clientId + '"]');
              return Boolean(row?.getAttribute('aria-label')?.startsWith('Клиент '));
            }""",
            runtime.client_id,
        )
    )
    await page.click(f'[data-client-id="{runtime.client_id}"]')
    await page.wait_for_selector(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    await page.click(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    scenarios["clients_repair_order_returns_to_client"] = await _is_modal_open(
        page, "#clientsModal"
    )
    await page.click('[data-close="clients"]')
    await _wait_modal_closed(page, "#clientsModal")

    await page.click("#repairOrdersButton")
    await _wait_modal_open(page, "#repairOrdersModal")
    await page.wait_for_selector(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    toolbar_stays_available = bool(
        await page.evaluate(
            """() => {
              const scrollPort = document.querySelector('#repairOrdersModal .repair-orders-body-scroll');
              const toolbar = document.querySelector('#repairOrdersModal .repair-orders-toolbar');
              const closeButton = document.querySelector('#repairOrdersModal [data-close="repair-orders"]');
              const activeTab = document.querySelector('#repairOrdersModal #repairOrdersOpenTab');
              const search = document.querySelector('#repairOrdersModal #repairOrdersSearchInput');
              const tableHead = document.querySelector('#repairOrdersModal #repairOrdersTableHead');
              if (!scrollPort || !toolbar || !closeButton || !activeTab || !search || !tableHead) return false;
              const isVisible = (element) => {
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0
                  && rect.top >= 0 && rect.bottom <= window.innerHeight
                  && rect.left >= 0 && rect.right <= window.innerWidth;
              };
              const toolbarTop = toolbar.getBoundingClientRect().top;
              const hasOverflow = scrollPort.scrollHeight > scrollPort.clientHeight + 1;
              scrollPort.scrollTop = scrollPort.scrollHeight;
              const scrollPortRect = scrollPort.getBoundingClientRect();
              const tableHeadRect = tableHead.getBoundingClientRect();
              return hasOverflow
                && scrollPort.scrollTop > 0
                && Math.abs(toolbar.getBoundingClientRect().top - toolbarTop) <= 1
                && isVisible(closeButton)
                && isVisible(activeTab)
                && isVisible(search)
                && Math.abs(tableHeadRect.top - scrollPortRect.top) <= 2;
            }"""
        )
    )
    await page.click("#repairOrdersReadyTab")
    await page.wait_for_selector("#repairOrdersReadyTab.is-active")
    await page.click("#repairOrdersOpenTab")
    await page.wait_for_selector("#repairOrdersOpenTab.is-active")
    await page.wait_for_selector(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    scenarios["repair_orders_toolbar_stays_available_while_list_scrolls"] = toolbar_stays_available
    await page.click(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    scenarios["repair_orders_list_returns_to_list"] = await _is_modal_open(
        page, "#repairOrdersModal"
    )
    await page.wait_for_selector(
        f'[data-open-repair-order-card="{runtime.salary_override_card_id}"]'
    )
    await page.click(f'[data-open-repair-order-card="{runtime.salary_override_card_id}"]')
    await _wait_modal_open(page, "#repairOrderModal")
    await page.wait_for_selector("#repairOrderWorksBody [data-repair-order-work-salary-gear]")
    await page.click("#repairOrderWorksBody [data-repair-order-work-salary-gear]")
    await page.wait_for_selector("#repairOrderWorkSalaryPopover.is-open")
    await page.fill("#repairOrderWorkSalaryGuarantee", "5000")
    await page.fill("#repairOrderWorkSalaryPercent", "45")
    override_45_ok = bool(
        await page.evaluate(
            """() => {
              const amount = (document.querySelector('#repairOrderWorkSalaryAmount')?.textContent || '').replace(/\\s+/g, ' ');
              const servicePercent = document.querySelector('#repairOrderWorkSalaryServicePercent')?.textContent || '';
              const popover = document.querySelector('#repairOrderWorkSalaryPopover');
              const rect = popover?.getBoundingClientRect();
              const fitsViewport = Boolean(rect && rect.left >= 0 && rect.right <= window.innerWidth && rect.top >= 0 && rect.bottom <= window.innerHeight);
              return amount.includes('11 750') && servicePercent.trim() === '55%' && fitsViewport;
            }"""
        )
    )
    await page.click("[data-repair-order-work-salary-apply]")
    override_applied_ok = bool(
        await page.evaluate(
            """() => {
              const row = document.querySelector('#repairOrderWorksBody tr[data-repair-order-row]');
              const gear = row?.querySelector('[data-repair-order-work-salary-gear]');
              return Boolean(
                row?.dataset.repairOrderWorkSalaryOverrideEnabled === 'true' &&
                row?.dataset.repairOrderWorkSalaryGuarantee === '5000' &&
                row?.dataset.repairOrderWorkSalaryPercentOverride === '45' &&
                gear?.classList.contains('is-active') &&
                gear?.getAttribute('aria-pressed') === 'true'
              );
            }"""
        )
    )
    await page.click("#repairOrderWorksBody [data-repair-order-work-salary-gear]")
    await page.wait_for_selector("#repairOrderWorkSalaryPopover.is-open")
    await page.fill("#repairOrderWorkSalaryPercent", "0")
    override_zero_ok = bool(
        await page.evaluate(
            """() => {
              const amount = (document.querySelector('#repairOrderWorkSalaryAmount')?.textContent || '').replace(/\\s+/g, ' ');
              const servicePercent = document.querySelector('#repairOrderWorkSalaryServicePercent')?.textContent || '';
              return amount.includes('5 000') && servicePercent.trim() === '100%';
            }"""
        )
    )
    await page.click("[data-repair-order-work-salary-apply]")
    await page.click("#repairOrderWorksBody [data-repair-order-work-salary-gear]")
    await page.wait_for_selector("#repairOrderWorkSalaryPopover.is-open")
    override_reopened_zero_ok = bool(
        await page.evaluate(
            """() => {
              const percent = document.querySelector('#repairOrderWorkSalaryPercent');
              const row = document.querySelector('#repairOrderWorksBody tr[data-repair-order-row]');
              return Boolean(
                percent?.value === '0' &&
                row?.dataset.repairOrderWorkSalaryOverrideEnabled === 'true' &&
                row?.dataset.repairOrderWorkSalaryPercentOverride === '0'
              );
            }"""
        )
    )
    await page.click("[data-repair-order-work-salary-reset]")
    override_reset_ok = bool(
        await page.evaluate(
            """() => {
              const row = document.querySelector('#repairOrderWorksBody tr[data-repair-order-row]');
              const gear = row?.querySelector('[data-repair-order-work-salary-gear]');
              const popoverOpen = document.querySelector('#repairOrderWorkSalaryPopover')?.classList.contains('is-open');
              return Boolean(
                row &&
                !row.dataset.repairOrderWorkSalaryOverrideEnabled &&
                !row.dataset.repairOrderWorkSalaryGuarantee &&
                !row.dataset.repairOrderWorkSalaryPercentOverride &&
                !gear?.classList.contains('is-active') &&
                gear?.getAttribute('aria-pressed') === 'false' &&
                !popoverOpen
              );
            }"""
        )
    )
    scenarios["repair_order_salary_override_popover"] = bool(
        scenarios["repair_orders_list_returns_to_list"]
        and override_45_ok
        and override_applied_ok
        and override_zero_ok
        and override_reopened_zero_ok
        and override_reset_ok
    )
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    await page.click('[data-close="repair-orders"]')
    await _wait_modal_closed(page, "#repairOrdersModal")

    await page.click("#archiveButton")
    await _wait_modal_open(page, "#archiveModal")
    await page.wait_for_selector("#archiveSearchInput")
    await page.fill("#archiveSearchInput", "Archive Filter")
    await page.wait_for_function(
        """() => document.querySelectorAll('#archiveList .archive-row').length === 1"""
    )
    scenarios["archive_search_filters_visible_rows"] = bool(
        await page.evaluate(
            """(cardId) => {
              const row = document.querySelector('#archiveList .archive-row');
              const button = row?.querySelector('[data-restore-card]');
              return Boolean(
                button?.getAttribute('data-restore-card') === cardId &&
                button?.getAttribute('aria-label')?.startsWith('Вернуть карточку ')
              );
            }""",
            runtime.archived_card_id,
        )
    )
    await page.click('[data-close="archive"]')
    await _wait_modal_closed(page, "#archiveModal")

    await page.click("#sharedFilesButton")
    await _wait_modal_open(page, "#sharedFilesModal")
    await page.wait_for_selector("#sharedFilesDesktop")
    scenarios["files_modal"] = True
    await page.wait_for_selector(".shared-file-icon")
    scenarios["shared_files_scanability_markup"] = bool(
        await page.evaluate(
            """() => {
              const icon = document.querySelector('.shared-file-icon');
              return Boolean(
                icon?.getAttribute('aria-label')?.startsWith('Файл ') &&
                icon.querySelectorAll('.shared-file-icon__meta-chip').length >= 2
              );
            }"""
        )
    )
    await page.click('[data-close="shared-files"]')
    await _wait_modal_closed(page, "#sharedFilesModal")
    return scenarios


async def _mobile_has_no_horizontal_overflow(page: Any) -> bool:
    return bool(
        await page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    )


async def _mobile_select_view(page: Any, view: str) -> None:
    await page.click(f'[data-mobile-view="{view}"]')
    await page.wait_for_selector(f'[data-mobile-panel="{view}"].is-active')


async def _mobile_open_more_module(page: Any, module_name: str, panel_selector: str) -> None:
    await _mobile_select_view(page, "more")
    if not await page.locator(f'[data-mobile-open="{module_name}"]').count():
        for selector in (
            "#mobileClientsBackButton",
            "#mobileEmployeesBackButton",
            "#mobileArchiveBackButton",
            "#mobileSharedFilesBackButton",
        ):
            button = page.locator(selector)
            if await button.count() and await button.first.is_visible():
                await button.first.click()
                break
    await page.wait_for_selector(f'[data-mobile-open="{module_name}"]')
    await page.click(f'[data-mobile-open="{module_name}"]')
    await page.wait_for_selector(f"{panel_selector}:not([hidden])")


async def _mobile_scenarios(
    browser: Any,
    runtime: TempRuntime,
    *,
    console_errors: list[str],
    page_errors: list[str],
    failed_requests: list[str],
) -> dict[str, bool]:
    base_url = runtime.base_url
    context = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = await context.new_page()
    _set_page_timeouts(page)
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(format_failed_request(request)),
    )
    scenarios = {name: False for name in MOBILE_SMOKE_SCENARIOS}
    try:
        await _goto_with_retry(page, base_url)
        await _login(page)
        await page.wait_for_selector("#board", state="attached")
        await page.wait_for_selector("#mobileAppShell")
        await page.wait_for_function("() => document.body.classList.contains('is-mobile-lite')")
        await page.wait_for_selector('[data-mobile-panel="board"].is-active')
        await page.wait_for_selector("#mobileBoardColumns .mobile-column-card")
        scenarios["mobile_board_load"] = bool(
            await page.locator("#mobileBoardColumns [data-mobile-card-id]").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        scenarios["mobile_personal_extra_column"] = bool(
            await page.locator('#mobileBoardColumns [data-mobile-virtual-column="extra"]').count()
        ) and await _mobile_has_no_horizontal_overflow(page)

        await page.locator("#mobileBoardColumns [data-mobile-card-id]").first.click()
        await page.wait_for_selector("#mobileCardDetail:not([hidden])")
        await page.wait_for_selector("#mobileCardTitleInput")
        await page.click('[data-mobile-card-tab="vehicle"]')
        await page.wait_for_selector('[data-mobile-card-page="vehicle"].is-active')
        scenarios["mobile_card_detail"] = bool(
            await page.locator("#mobileCardVehicleProfile [data-mobile-vehicle-field]").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileCardBackButton")
        await page.wait_for_selector("#mobileBoardColumns:not([hidden])")

        await _mobile_select_view(page, "cashboxes")
        await page.wait_for_selector("#mobileCashboxList [data-mobile-cashbox-id]")
        await page.wait_for_selector("#mobileCashboxDetail .mobile-cashbox-detail__title")
        await page.click("#mobileCashboxIncomeButton")
        await page.wait_for_selector("#mobileCashboxActionPanel:not([hidden])")
        scenarios["mobile_cashboxes_workspace"] = bool(
            await page.locator("#mobileCashboxAmountInput").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileCashboxActionCancelButton")

        await _mobile_select_view(page, "repair-orders")
        await page.wait_for_selector("#mobileRepairOrdersList [data-open-repair-order-card]")
        await page.locator("#mobileRepairOrdersList [data-open-repair-order-card]").first.click()
        await page.wait_for_selector("#mobileRepairOrderDetail:not([hidden])")
        await page.click('[data-mobile-repair-order-tab="works"]')
        await page.wait_for_selector('[data-mobile-repair-order-page="works"].is-active')
        scenarios["mobile_repair_orders_workspace"] = bool(
            await page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileRepairOrderBackButton")
        await page.wait_for_selector("#mobileRepairOrdersList:not([hidden])")

        await _mobile_open_more_module(page, "clients", "#mobileClientsPanel")
        await page.wait_for_selector("#mobileClientsSearchInput")
        await page.wait_for_function(
            """() => {
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              return (
                document.querySelectorAll('#mobileClientsList [data-mobile-client-id]').length > 0 &&
                !meta.includes('ЗАГРУЗКА')
              );
            }"""
        )
        await page.fill("#mobileClientsSearchInput", "Smoke")
        await _wait_clients_search_ready(page, client_id="", mobile=True, query="Smoke")
        await page.locator("#mobileClientsList [data-mobile-client-id]").first.click()
        await page.wait_for_selector("#mobileClientDetail .mobile-client-detail__name")
        scenarios["mobile_clients_panel"] = bool(
            await page.locator("#mobileClientDetail .mobile-client-mini").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileClientsBackButton")
        await page.wait_for_selector("#mobileMoreGrid:not([hidden])")

        await _mobile_open_more_module(page, "employees", "#mobileEmployeesPanel")
        await page.wait_for_selector("#mobileEmployeesList [data-mobile-employee-id]")
        await page.locator("#mobileEmployeesList [data-mobile-employee-id]").first.click()
        await page.wait_for_selector("#mobileEmployeeDetail .mobile-employee-detail__name")
        scenarios["mobile_employees_panel"] = bool(
            await page.locator("#mobileEmployeeDetail .mobile-employee-kpi").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileEmployeesBackButton")
        await page.wait_for_selector("#mobileMoreGrid:not([hidden])")

        await _mobile_open_more_module(page, "archive", "#mobileArchivePanel")
        await page.wait_for_selector("#mobileArchiveSearchInput")
        await page.fill("#mobileArchiveSearchInput", "Archive Filter")
        await page.wait_for_function(
            """(cardId) => {
              const rows = Array.from(document.querySelectorAll('#mobileArchiveList [data-mobile-archive-card]'));
              return rows.length === 1 && rows.some((row) => row.getAttribute('data-mobile-archive-card') === cardId);
            }""",
            arg=runtime.archived_card_id,
        )
        scenarios["mobile_archive_panel"] = bool(
            await page.locator("#mobileArchiveList [data-mobile-archive-restore]").count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileArchiveBackButton")
        await page.wait_for_selector("#mobileMoreGrid:not([hidden])")

        await _mobile_open_more_module(page, "files", "#mobileSharedFilesPanel")
        await page.wait_for_selector("#mobileSharedFilesList [data-mobile-shared-file-id]")
        scenarios["mobile_files_panel"] = bool(
            await page.locator(
                '#mobileSharedFilesList [data-mobile-shared-file-action="rename"]'
            ).count()
        ) and await _mobile_has_no_horizontal_overflow(page)
        await page.click("#mobileSharedFilesBackButton")
        return scenarios
    finally:
        await _close_with_timeout(context.close())


async def _goto_with_retry(page: Any, url: str) -> None:
    for attempt in range(2):
        try:
            await page.goto(url, wait_until="domcontentloaded")
            return
        except Exception as exc:
            if attempt == 0 and any(
                marker in str(exc)
                for marker in ("ERR_CONNECTION_RESET", "ERR_CONNECTION_TIMED_OUT")
            ):
                await asyncio.sleep(0.2)
                continue
            raise


def _set_page_timeouts(page: Any) -> None:
    page.set_default_timeout(SMOKE_ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(SMOKE_NAVIGATION_TIMEOUT_MS)


async def _close_with_timeout(awaitable: Any) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS)
    except Exception:
        pass


async def _launch_chromium(playwright: Any, *, headless: bool) -> Any:
    try:
        return await playwright.chromium.launch(headless=headless)
    except Exception as bundled_error:
        fallback_errors = [f"bundled chromium: {bundled_error}"]
        for channel in ("chrome", "msedge"):
            try:
                return await playwright.chromium.launch(channel=channel, headless=headless)
            except Exception as channel_error:
                fallback_errors.append(f"{channel}: {channel_error}")
        raise RuntimeError("; ".join(fallback_errors)) from bundled_error


async def run_browser_smoke(runtime: TempRuntime, *, headless: bool = True) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return {"ok": False, "error": "playwright_missing", "message": str(exc)}

    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    first_render_ms = 0.0
    scenarios: dict[str, bool] = {}

    async with async_playwright() as playwright:
        browser = await _launch_chromium(playwright, headless=headless)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 960})
            page = await context.new_page()
            _set_page_timeouts(page)
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(format_failed_request(request)),
            )
            try:
                started_at = time.perf_counter()
                await _goto_with_retry(page, runtime.base_url)
                scenarios[
                    "login_gate_hides_board_until_operator_login"
                ] = await _login_gate_hides_board(page)
                await _login(page)
                await page.wait_for_selector("#board")
                first_render_ms = round((time.perf_counter() - started_at) * 1000, 1)
                scenarios.update(await _desktop_scenarios(page, runtime))
            finally:
                await _close_with_timeout(context.close())
            scenarios.update(
                await _mobile_scenarios(
                    browser,
                    runtime,
                    console_errors=console_errors,
                    page_errors=page_errors,
                    failed_requests=failed_requests,
                )
            )
        finally:
            await _close_with_timeout(browser.close())

    events = summarize_browser_events(
        console_errors=console_errors,
        page_errors=page_errors,
        failed_requests=failed_requests,
        first_render_ms=first_render_ms,
    )
    return {
        "ok": bool(events["ok"] and all(scenarios.get(name) for name in SMOKE_SCENARIOS)),
        "base_url": runtime.base_url,
        "scenarios": scenarios,
        "events": events,
    }


async def run_temp_smoke(*, headless: bool = True, start_port: int = 42731) -> dict[str, Any]:
    runtime = start_temp_runtime(start_port=start_port)
    try:
        return await run_browser_smoke(runtime, headless=headless)
    finally:
        runtime.close()


def main() -> int:
    configure_stdout_utf8()
    parser = argparse.ArgumentParser(description="Run AutoStop CRM browser smoke on temp data.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible window.")
    parser.add_argument("--start-port", default=42731)
    parser.add_argument(
        "--browser-timeout-seconds",
        default=DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS,
    )
    parser.add_argument("--attempts", default=4)
    args = parser.parse_args()

    attempts = _browser_attempts(args.attempts)
    start_port = _browser_start_port(args.start_port)
    attempt_results: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        try:
            result = asyncio.run(
                asyncio.wait_for(
                    run_temp_smoke(headless=not args.headed, start_port=start_port),
                    timeout=_browser_timeout_seconds(args.browser_timeout_seconds),
                )
            )
        except Exception as exc:
            result = {
                "ok": False,
                "error": "browser_smoke_failed",
                "message": str(exc),
                "scenarios": {},
                "events": {
                    "ok": False,
                    "console_errors": [],
                    "page_errors": [],
                    "failed_requests": [],
                },
            }
        result["attempt"] = attempt
        attempt_results.append(result)
        if result.get("ok") or result.get("error") == "playwright_missing":
            break
    if not result.get("ok") and attempts > 1:
        result = {**result, "attempts": attempt_results}
    print(_json_dumps(result))
    if result.get("error") == "playwright_missing":
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
