from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore

SMOKE_SCENARIOS = (
    "desktop_board_card_roundtrip",
    "cashbox_journal_workspace",
    "repair_order_payments_modal",
    "clients_modal",
    "files_modal",
    "employees_repair_order_returns_to_employee",
    "clients_repair_order_returns_to_client",
    "repair_orders_list_returns_to_list",
    "cashboxes_journal_transfer_returns_to_cashbox",
    "escape_closes_top_modal_only",
    "mobile_board_load",
)


@dataclass
class TempRuntime:
    temp_dir: tempfile.TemporaryDirectory[str]
    api: ApiServer
    service: CardService
    card_id: str
    employee_id: str
    payroll_card_id: str
    client_id: str
    client_card_id: str

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


def _read_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def start_temp_runtime(*, start_port: int = 42731) -> TempRuntime:
    temp_dir = tempfile.TemporaryDirectory(prefix="autostop-browser-smoke-")
    base_dir = Path(temp_dir.name)
    logger = _logger()
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
                        "amount": "4000",
                        "paid_at": "18.05.2026 10:00",
                        "payment_method": "cash",
                    }
                ],
                "works": [
                    {
                        "name": "Smoke payroll work",
                        "quantity": "1",
                        "price": "4000",
                        "executor_id": employee["id"],
                    }
                ],
            },
            "actor_name": "SMOKE",
        }
    )
    service.set_repair_order_status(
        {"card_id": payroll_card["id"], "status": "closed", "actor_name": "SMOKE"}
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
    operator_service = OperatorAuthService(
        store,
        service,
        users_file=base_dir / "users.json",
        logger=logger,
    )
    api = ApiServer(
        service,
        logger,
        operator_service=operator_service,
        host="127.0.0.1",
        start_port=start_port,
        fallback_limit=50,
        bearer_token="",
    )
    api.start()
    return TempRuntime(
        temp_dir=temp_dir,
        api=api,
        service=service,
        card_id=card["id"],
        employee_id=employee["id"],
        payroll_card_id=payroll_card["id"],
        client_id=client["id"],
        client_card_id=client_card["id"],
    )


def summarize_browser_events(
    *,
    console_errors: list[str],
    page_errors: list[str],
    failed_requests: list[str],
    first_render_ms: float,
) -> dict[str, Any]:
    return {
        "ok": not console_errors and not page_errors and not failed_requests,
        "first_render_ms": first_render_ms,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed_requests": failed_requests,
    }


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
    await page.fill("#identityInput", "admin")
    await page.fill("#identityPassword", "admin")
    await page.click("#identitySave")
    await _wait_modal_closed(page, "#identityModal")
    if await _is_modal_open(page, "#operatorProfileModal"):
        await page.click('[data-close="operator-profile"]')
        await _wait_modal_closed(page, "#operatorProfileModal")


async def _desktop_scenarios(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    scenarios = {name: False for name in SMOKE_SCENARIOS if name != "mobile_board_load"}
    await page.wait_for_selector("#board")
    await page.wait_for_selector(f'[data-card-id="{runtime.card_id}"]')
    await page.click(f'[data-card-id="{runtime.card_id}"]')
    await _wait_modal_open(page, "#cardModal")
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
    await page.click("#cashboxJournalButton")
    await _wait_modal_open(page, "#cashboxJournalModal")
    await page.click('[data-close="cashbox-journal"]')
    await _wait_modal_closed(page, "#cashboxJournalModal")
    scenarios["cashbox_journal_workspace"] = await _is_modal_open(page, "#cashboxesModal")
    await page.click("#cashboxTransferButton")
    await _wait_modal_open(page, "#cashboxTransferModal")
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
    await page.click('[data-close="repair-orders"]')
    await _wait_modal_closed(page, "#repairOrdersModal")

    await page.click("#sharedFilesButton")
    await _wait_modal_open(page, "#sharedFilesModal")
    await page.wait_for_selector("#sharedFilesDesktop")
    scenarios["files_modal"] = True
    await page.click('[data-close="shared-files"]')
    await _wait_modal_closed(page, "#sharedFilesModal")
    return scenarios


async def _mobile_scenario(browser: Any, base_url: str) -> bool:
    context = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = await context.new_page()
    try:
        await page.goto(base_url, wait_until="domcontentloaded")
        await _login(page)
        await page.wait_for_selector("#board")
        await page.wait_for_function("() => document.body.classList.contains('is-mobile-lite')")
        return True
    finally:
        await context.close()


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
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url} {request.failure.get('errorText') if request.failure else ''}"
            ),
        )
        try:
            started_at = time.perf_counter()
            await page.goto(runtime.base_url, wait_until="domcontentloaded")
            await _login(page)
            await page.wait_for_selector("#board")
            first_render_ms = round((time.perf_counter() - started_at) * 1000, 1)
            scenarios.update(await _desktop_scenarios(page, runtime))
            scenarios["mobile_board_load"] = await _mobile_scenario(browser, runtime.base_url)
        finally:
            await context.close()
            await browser.close()

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
    parser = argparse.ArgumentParser(description="Run AutoStop CRM browser smoke on temp data.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible window.")
    parser.add_argument("--start-port", type=int, default=42731)
    args = parser.parse_args()

    result = asyncio.run(run_temp_smoke(headless=not args.headed, start_port=args.start_port))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("error") == "playwright_missing":
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
