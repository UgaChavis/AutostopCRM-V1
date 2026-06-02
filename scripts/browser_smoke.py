from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import asyncio
import base64
import json
import logging
import socket
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
from minimal_kanban.operator_activity import OperatorActivityService
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.shared_files_service import SharedFilesService
from minimal_kanban.storage.json_store import JsonStore

SMOKE_SCENARIOS = (
    "login_gate_hides_board_until_operator_login",
    "desktop_board_card_roundtrip",
    "card_long_description_controls_reachable",
    "cashbox_journal_workspace",
    "cashbox_journal_filters_and_no_audit",
    "cashbox_journal_compact_cleanup",
    "cashbox_journal_mode_and_period_navigation",
    "cashbox_journal_first_render_budget",
    "repair_order_payments_modal",
    "repair_order_material_executor_defaults_to_operator_employee",
    "clients_modal",
    "clients_search_selects_realistic_row",
    "files_modal",
    "shared_files_scanability_markup",
    "employees_repair_order_returns_to_employee",
    "employee_shift_accrual_manual_salary",
    "clients_repair_order_returns_to_client",
    "repair_orders_list_returns_to_list",
    "repair_order_salary_override_popover",
    "payroll_chain_reaches_reports_and_reconciliation",
    "archive_search_filters_visible_rows",
    "cashboxes_journal_transfer_returns_to_cashbox",
    "escape_closes_top_modal_only",
    "operator_admin_employee_binding_returns_to_users",
    "mobile_board_load",
)

BROWSER_READ_RETRY_LIMIT = 1
BROWSER_READ_RETRY_DELAY_SECONDS = 0.15
CASHBOX_JOURNAL_FIRST_RENDER_BUDGET_MS = 2500
DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS = 240.0
PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS = 10.0
SMOKE_ACTION_TIMEOUT_MS = 10000
SMOKE_NAVIGATION_TIMEOUT_MS = 15000
SMOKE_UI_BIND_TIMEOUT_MS = 30000
BENIGN_FAILED_REQUEST_MARKERS = ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "AbortError")


@dataclass
class TempRuntime:
    temp_dir: tempfile.TemporaryDirectory[str]
    api: ApiServer
    service: CardService
    card_id: str
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


def _read_bytes(url: str, *, accept: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": accept}, method="GET")
    for attempt in range(BROWSER_READ_RETRY_LIMIT + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
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


def _read_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    return json.loads(_read_bytes(url, accept="application/json", timeout=timeout).decode("utf-8"))


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
            "deadline": {"hours": 2},
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
        card_id=card["id"],
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
    return {
        "ok": not console_errors and not page_errors and not actionable_failed_requests,
        "first_render_ms": first_render_ms,
        "console_errors": console_errors,
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
    return any(marker in text for marker in BENIGN_FAILED_REQUEST_MARKERS)


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
    if await _is_modal_open(page, "#operatorProfileModal"):
        await page.click('[data-close="operator-profile"]')
        await _wait_modal_closed(page, "#operatorProfileModal")


async def _login_gate_hides_board(page: Any) -> bool:
    await page.wait_for_selector("#identityModal.is-open")
    return bool(
        await page.evaluate(
            """() => {
              const body = document.body;
              const shell = document.querySelector('.shell');
              const modal = document.querySelector('#identityModal');
              const shellStyle = shell ? getComputedStyle(shell) : null;
              return Boolean(
                body.classList.contains('operator-login-gate-open') &&
                modal?.classList.contains('operator-login-gate') &&
                shell?.getAttribute('aria-hidden') === 'true' &&
                shell?.hasAttribute('inert') &&
                shellStyle?.visibility === 'hidden'
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


async def _desktop_scenarios(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    scenarios = {
        name: False
        for name in SMOKE_SCENARIOS
        if name not in {"mobile_board_load", "login_gate_hides_board_until_operator_login"}
    }
    await page.wait_for_selector("#board")
    scenarios["payroll_chain_reaches_reports_and_reconciliation"] = (
        _payroll_chain_reaches_reports_and_reconciliation(runtime)
    )
    await page.click("#operatorButton")
    await _wait_modal_open(page, "#operatorProfileModal")
    await page.click("#operatorAdminButton")
    await _wait_modal_open(page, "#operatorAdminModal")
    await page.click('[data-operator-admin-tab="users"]')
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
    admin_binding_escape_ok = bool(
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
    admin_binding_back_ok = await _is_modal_open(page, "#operatorAdminModal")
    await page.click("#operatorAdminCloseButton")
    await _wait_modal_closed(page, "#operatorAdminModal")
    admin_binding_final_close_ok = not await _is_modal_open(page, "#operatorAdminModal")
    await page.click('[data-close="operator-profile"]')
    await _wait_modal_closed(page, "#operatorProfileModal")
    scenarios["operator_admin_employee_binding_returns_to_users"] = bool(
        admin_binding_escape_ok and admin_binding_back_ok and admin_binding_final_close_ok
    )

    await page.wait_for_selector(f'[data-card-id="{runtime.card_id}"]')
    await page.click(f'[data-card-id="{runtime.card_id}"]')
    await _wait_modal_open(page, "#cardModal")
    await page.wait_for_function(
        """() => {
          const editor = document.querySelector('#cardDescriptionEditor');
          const saveButton = document.querySelector('#saveCardButton');
          return !editor?.classList.contains('is-loading') && !saveButton?.disabled;
        }"""
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
    scenarios["card_long_description_controls_reachable"] = bool(
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
              const repairOrderButton = document.querySelector('#repairOrderButton');
              const bottomClose = document.querySelector('#cardModalCloseButtonBottom');
              const saveButton = document.querySelector('#saveCardButton');
              if (!overview || !editor || !tagInput || !tagAddButton || !tagsPanel || !signalPanel || !signalDaysDecrement || !signalDaysIncrement || !signalHoursDecrement || !signalHoursIncrement || !repairOrderButton || !bottomClose || !saveButton) return false;
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
              return (
                editor.scrollHeight > editor.clientHeight &&
                Math.abs(signalHeight - tagsHeight) <= 2 &&
                visibleInOverview(tagInput) &&
                visibleInOverview(tagAddButton) &&
                visibleInOverview(tagsPanel) &&
                visibleInOverview(signalPanel) &&
                visibleInOverview(signalDaysDecrement) &&
                visibleInOverview(signalDaysIncrement) &&
                visibleInOverview(signalHoursDecrement) &&
                visibleInOverview(signalHoursIncrement) &&
                visibleInOverview(repairOrderButton) &&
                visibleInWindow(bottomClose) &&
                visibleInWindow(saveButton)
              );
            }"""
        )
    )
    await page.fill("#cardTitle", "Browser smoke saved")
    await page.click("#saveCardButton")
    await _wait_modal_closed(page, "#cardModal")
    snapshot = _read_json(f"{runtime.base_url}/api/get_board_snapshot?compact=1&include_archive=0")
    cards = snapshot.get("data", {}).get("cards", [])
    scenarios["desktop_board_card_roundtrip"] = any(
        card.get("id") == runtime.card_id and card.get("title") == "Browser smoke saved"
        for card in cards
    )

    await page.click("#cashboxesButton")
    await _wait_modal_open(page, "#cashboxesModal")
    await page.wait_for_selector("#cashboxJournalDownloadButton")
    await page.wait_for_selector("#cashboxesList [data-cashbox-id]")
    journal_open_started = time.perf_counter()
    await page.click("#cashboxJournalButton")
    await _wait_modal_open(page, "#cashboxJournalModal")
    await page.wait_for_selector('[data-cash-journal-filter="query"]')
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
              const resetVisuallyHidden = reset?.hidden === true
                && window.getComputedStyle(reset).display === 'none';
              const firstNote = document.querySelector('.cashbox-journal-operation-row__note')?.textContent || '';
              const firstType = document.querySelector('.cashbox-journal-operation-row__type')?.textContent || '';
              const dayMeta = document.querySelector('[data-cash-journal-compact-day]')?.textContent || '';
              const bodyTitle = document.querySelector('.cashbox-journal-toolbar__title');
              const visibleNoPairTags = Array.from(document.querySelectorAll('.cashbox-journal-operation-tag'))
                .filter((tag) => tag.textContent.trim() === 'нет пары');
              const transferRowsWithoutDiagnosticChips = Array.from(
                document.querySelectorAll('.cashbox-journal-operation-row--transfer')
              ).every((row) => !row.querySelector('.cashbox-journal-operation-tag'));
              return Boolean(
                toolbar?.querySelector('.cashbox-journal-toolbar__status') &&
                !bodyTitle &&
                balanceStrip &&
                !balanceStrip.classList.contains('is-expanded') &&
                balanceToggle?.textContent.trim() === 'Кассы' &&
                resetVisuallyHidden &&
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
    await page.click("[data-cash-journal-toggle-balances]")
    await page.wait_for_function(
        """() => document.querySelector('.cashbox-journal-balance-strip')?.classList.contains('is-expanded')"""
    )
    await page.click("[data-cash-journal-toggle-balances]")
    await page.wait_for_function(
        """() => !document.querySelector('.cashbox-journal-balance-strip')?.classList.contains('is-expanded')"""
    )
    await page.click("#cashboxJournalStatsButton")
    await page.wait_for_selector(".cashbox-journal-view--stats")
    await page.wait_for_selector("[data-cash-journal-period-kind][data-cash-journal-period-key]")
    await page.click("[data-cash-journal-period-kind][data-cash-journal-period-key]")
    await page.wait_for_selector(".cashbox-journal-operation-head")
    await page.wait_for_function(
        """() => document.querySelector('#cashboxJournalLedgerButton')?.getAttribute('aria-pressed') === 'true'"""
    )
    await page.fill('[data-cash-journal-filter="query"]', "Smoke")
    await page.wait_for_function(
        """() => document.querySelectorAll('.cashbox-journal-operation-row').length >= 1"""
    )
    scenarios["cashbox_journal_filters_and_no_audit"] = bool(
        await page.evaluate(
            """() => {
              const query = document.querySelector('[data-cash-journal-filter="query"]');
              const rows = Array.from(document.querySelectorAll('.cashbox-journal-operation-row'));
              const loadMore = document.querySelector('[data-cash-journal-load-more]');
              return Boolean(
                query?.value === 'Smoke' &&
                document.querySelector('#cashboxJournalLedgerButton')?.getAttribute('aria-pressed') === 'true' &&
                document.querySelector('#cashboxJournalStatsButton')?.getAttribute('aria-pressed') === 'false' &&
                document.querySelector('.cashbox-journal-operation-head') &&
                document.querySelector('[data-cash-journal-region="active-filters"]')?.textContent.includes('Период:') &&
                document.querySelector('[data-cash-journal-reset]')?.textContent.trim() === 'Сбросить' &&
                document.querySelector('[data-cash-journal-reset]')?.hidden === false &&
                loadMore &&
                !/из\\s+\\d+/.test(loadMore.textContent || '') &&
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
              document.querySelector('[data-cash-journal-clear-filter="period"]') &&
              document.querySelector('.cashbox-journal-operation-head')
            )"""
        )
    )
    await page.click("[data-cash-journal-reset]")
    await page.wait_for_function(
        """() => document.querySelector('[data-cash-journal-filter="query"]')?.value === ''"""
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
    await page.click("#repairOrderButton")
    await _wait_modal_open(page, "#repairOrderModal")
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
    await page.wait_for_selector("#repairOrderPaymentsButton")
    await page.click("#repairOrderPaymentsButton")
    await _wait_modal_open(page, "#repairOrderPaymentsModal")
    scenarios["repair_order_payments_modal"] = True
    await page.keyboard.press("Escape")
    await _wait_modal_closed(page, "#repairOrderPaymentsModal")
    scenarios["escape_closes_top_modal_only"] = await _is_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    await page.click("#cardModalCloseButtonTop")
    await _wait_modal_closed(page, "#cardModal")

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
    await page.wait_for_selector(f'[data-client-id="{runtime.client_id}"]')
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


async def _mobile_scenario(browser: Any, base_url: str) -> bool:
    context = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = await context.new_page()
    _set_page_timeouts(page)
    try:
        await _goto_with_retry(page, base_url)
        await _login(page)
        await page.wait_for_selector("#board")
        await page.wait_for_function("() => document.body.classList.contains('is-mobile-lite')")
        return True
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
            scenarios["mobile_board_load"] = await _mobile_scenario(browser, runtime.base_url)
        finally:
            await _close_with_timeout(context.close())
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
    parser.add_argument("--start-port", type=int, default=42731)
    parser.add_argument(
        "--browser-timeout-seconds",
        type=float,
        default=DEFAULT_BROWSER_SMOKE_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            asyncio.wait_for(
                run_temp_smoke(headless=not args.headed, start_port=args.start_port),
                timeout=max(30.0, float(args.browser_timeout_seconds or 0.0)),
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
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("error") == "playwright_missing":
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
