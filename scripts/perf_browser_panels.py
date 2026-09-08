"""Supplementary, process-owned browser benchmarks; identical harness for both revisions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import perf_workflows as perf

PANELS = ("printing", "inventory", "payroll", "cash_journal")
DESKTOP = {"width": 1440, "height": 960}
MOBILE = {"width": 390, "height": 844}


class MeasurementFailure(RuntimeError):
    def __init__(self, scenario: str, error_type: str) -> None:
        super().__init__("browser measurement failed")
        self.scenario = scenario
        self.error_type = error_type


def positive_count(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("count must be between 1 and 100")
    return number


def application_environment(source_root: str) -> dict[str, Any]:
    # Do this before importing fixtures: they import the selected application.
    result = perf.configure_source_root(source_root)
    result["measurement_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for name in ("browser_smoke.py", "browser_smoke_runtime.py", "browser_smoke_support.py"):
        digest.update((Path(__file__).parent / name).read_bytes())
    result["fixture_sha256"] = digest.hexdigest()
    return result


async def board_ready(page: Any, runtime: Any, *, mobile: bool = False) -> None:
    await perf.goto_with_retry(page, runtime.browser_url, wait_until="domcontentloaded")
    selector = (
        "#mobileBoardColumns [data-mobile-card-id]"
        if mobile
        else f'#board .card[data-card-id="{runtime.card_id}"]'
    )
    await page.wait_for_selector(selector, timeout=30000)
    await page.wait_for_function("() => window.__AUTOSTOP_UI_BOUND__ === true")


async def prepare_panel(page: Any, runtime: Any, panel: str) -> None:
    from browser_smoke_support import _wait_modal_open

    if panel == "printing":
        await page.click(f'#board .card[data-card-id="{runtime.client_card_id}"]')
        await _wait_modal_open(page, "#cardModal")
        await page.click("#repairOrderButton")
        await _wait_modal_open(page, "#repairOrderModal")
        await page.wait_for_selector("#repairOrderWorksBody [data-repair-order-row]")
    elif panel == "cash_journal":
        await page.click("#cashboxesButton")
        await _wait_modal_open(page, "#cashboxesModal")
        await page.wait_for_selector("#cashboxJournalButton")


async def open_panel(page: Any, panel: str) -> None:
    from browser_smoke_support import _wait_modal_open

    if panel == "printing":
        await page.click("#repairOrderPrintButton")
        await _wait_modal_open(page, "#repairOrderPrintModal")
        await page.wait_for_function(
            """() => (document.querySelector('#repairOrderPrintPreviewFrame')
              ?.contentDocument?.body?.innerText || '').includes('Smoke client work')"""
        )
    elif panel == "inventory":
        async with page.expect_response(
            lambda response: (
                urllib.parse.urlsplit(response.url).path == "/api/list_inventory_items"
                and response.status == 200
            )
        ) as pending:
            await page.click("#inventoryButton")
        await (await pending.value).json()
        await _wait_modal_open(page, "#inventoryModal")
        await page.wait_for_function(
            """() => {
              const node = document.querySelector('#inventoryTableBody')
                || document.querySelector('#inventoryItemsList');
              return node?.children.length && !node.textContent.includes('ЗАГРУЖАЮ');
            }"""
        )
    elif panel == "payroll":
        await page.click("#employeesButton")
        await _wait_modal_open(page, "#employeesModal")
        await page.wait_for_selector("#employeesList [data-employee-id]")
    elif panel == "cash_journal":
        await page.click("#cashboxJournalButton")
        await _wait_modal_open(page, "#cashboxJournalModal")
        await page.wait_for_selector("#cashboxJournalText .cashbox-journal-operation-row")
    else:
        raise ValueError("unknown panel")


def is_query_response(response: Any, query: str) -> bool:
    parsed = urllib.parse.urlsplit(response.url)
    return (
        parsed.path == "/api/search_clients"
        and response.status == 200
        and urllib.parse.parse_qs(parsed.query).get("query") == [query]
    )


async def query_client(page: Any, runtime: Any, index: int) -> None:
    query = ("Smoke", "Клиент")[index % 2]
    async with page.expect_response(lambda response: is_query_response(response, query)) as pending:
        await page.fill("#clientsSearchInput", query)
    await (await pending.value).json()
    await page.wait_for_function(
        """([clientId, query]) => {
          const row = document.querySelector('#clientsList [data-client-id="' + clientId + '"]');
          const meta = document.querySelector('#clientsMeta')?.textContent || '';
          return document.querySelector('#clientsSearchInput')?.value === query
            && meta.includes('НАЙДЕНО')
            && row?.textContent.toLowerCase().includes(query.toLowerCase());
        }""",
        arg=[runtime.client_id, query],
    )


def observe_page(page: Any, responses: list[dict[str, Any]], events: dict[str, int]) -> None:
    def record_response(response: Any) -> None:
        if urllib.parse.urlsplit(response.url).path.startswith("/api/"):
            responses.append(
                {
                    "bytes": int(response.headers.get("content-length") or 0),
                    "server_timing": response.headers.get("server-timing") or "",
                }
            )
        if response.status >= 400:
            events["http_error_count"] += 1

    def increment(kind: str) -> None:
        events[kind] += 1

    page.on("response", record_response)
    page.on("pageerror", lambda _error: increment("page_error_count"))
    page.on(
        "console",
        lambda message: increment("console_error_count") if message.type == "error" else None,
    )
    page.on(
        "requestfailed",
        lambda request: (
            increment("failed_request_count")
            if request.failure not in perf.BENIGN_FAILED_REQUEST_CODES
            else None
        ),
    )


async def sample_action(
    page: Any,
    scenario: str,
    action: Callable[[int], Awaitable[None]],
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    row = await perf.measure_browser_action(
        page, scenario=scenario, iterations=1, responses=responses, action=action
    )
    if row.get("failed"):
        # Never serialize browser exceptions: they may contain authenticated navigation URLs.
        raise MeasurementFailure(scenario, row["error_type"])
    sample = {
        "duration_ms": row["p50_ms"],
        "request_count": row["request_count"],
        "payload_bytes": row["payload_bytes"],
        "server_timing": row["server_timing"],
        **row["resources"],
    }
    sample.update(
        await page.evaluate(
            """() => {
              const scripts = performance.getEntriesByType('resource')
                .filter(entry => new URL(entry.name).pathname.endsWith('.js'));
              return {js_resource_count: scripts.length,
                js_encoded_bytes: scripts.reduce((sum, entry) => sum + entry.encodedBodySize, 0),
                js_decoded_bytes: scripts.reduce((sum, entry) => sum + entry.decodedBodySize, 0)};
            }"""
        )
    )
    return sample


async def cold_sample(
    browser: Any, session: dict[str, Any], runtime: Any, scenario: str, events: dict[str, int]
) -> dict[str, Any]:
    mobile = scenario == "startup.mobile_cold_authenticated"
    context = await browser.new_context(
        viewport=MOBILE if mobile else DESKTOP, storage_state=session
    )
    try:
        page = await context.new_page()
        responses: list[dict[str, Any]] = []
        observe_page(page, responses, events)
        if not mobile:
            await board_ready(page, runtime)
            panel = scenario.rsplit(".", 1)[1]
            await prepare_panel(page, runtime, panel)

        async def action(_index: int) -> None:
            if mobile:
                await board_ready(page, runtime, mobile=True)
            else:
                await open_panel(page, panel)

        return await sample_action(page, scenario, action, responses)
    finally:
        await perf.close_with_timeout(context.close())


def summarize(scenario: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    result = perf.summarize_samples(samples, scenario=scenario)
    result["samples"] = samples
    result["api_request_scope"] = "action_page_only"
    return result


async def run_browser(args: argparse.Namespace) -> dict[str, Any]:
    # Imports deliberately follow application_environment in main.
    from browser_smoke import _launch_chromium
    from browser_smoke_runtime import start_temp_runtime
    from browser_smoke_support import _wait_modal_open
    from playwright.async_api import async_playwright

    runtime = start_temp_runtime(start_port=args.start_port)
    events = dict.fromkeys(
        ("page_error_count", "console_error_count", "failed_request_count", "http_error_count"), 0
    )
    report: dict[str, Any] = {"events": events, "series": []}
    try:
        report["fixture"] = (
            perf.seed_browser_scale(runtime)
            if args.synthetic_state_profile == perf.SYNTHETIC_STATE_PROFILE
            else {"profile": "smoke"}
        )
        async with async_playwright() as playwright:
            browser = await _launch_chromium(playwright, headless=not args.headed)
            report["browser_version"] = browser.version
            try:
                context = await browser.new_context(viewport=DESKTOP)
                try:
                    page = await context.new_page()
                    await perf.goto_with_retry(page, runtime.browser_url)
                    await perf.login_browser(page)
                    await page.wait_for_selector(f'#board .card[data-card-id="{runtime.card_id}"]')
                    session = await context.storage_state()
                finally:
                    await perf.close_with_timeout(context.close())
                for series in range(args.series):
                    rows = []
                    report["series"].append({"number": series + 1, "rows": rows})
                    for scenario in [
                        *(f"cold_panel.{panel}" for panel in PANELS),
                        "startup.mobile_cold_authenticated",
                    ]:
                        samples = []
                        for _index in range(args.iterations):
                            samples.append(
                                await cold_sample(browser, session, runtime, scenario, events)
                            )
                        rows.append(summarize(scenario, samples))
                    context = await browser.new_context(viewport=DESKTOP, storage_state=session)
                    try:
                        page = await context.new_page()
                        responses: list[dict[str, Any]] = []
                        observe_page(page, responses, events)
                        await board_ready(page, runtime)
                        await page.click("#clientsButton")
                        await _wait_modal_open(page, "#clientsModal")
                        await page.wait_for_selector("#clientsList [data-client-id]")
                        samples = []
                        for index in range(args.iterations):
                            samples.append(
                                await sample_action(
                                    page,
                                    "clients.query",
                                    lambda _unused, index=index: query_client(page, runtime, index),
                                    responses,
                                )
                            )
                        rows.append(summarize("clients.query", samples))
                    finally:
                        await perf.close_with_timeout(context.close())
            finally:
                await perf.close_with_timeout(browser.close())
    finally:
        runtime.close()
    report["ok"] = not any(events.values())
    return report


def main() -> int:
    perf.configure_stdout_utf8()
    parser = perf.RedactingArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default="")
    parser.add_argument("--iterations", type=positive_count, default=20)
    parser.add_argument("--series", type=positive_count, default=1)
    parser.add_argument(
        "--synthetic-state-profile",
        choices=("smoke", perf.SYNTHETIC_STATE_PROFILE),
        default=perf.SYNTHETIC_STATE_PROFILE,
    )
    parser.add_argument("--start-port", type=int, default=44791)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    try:
        environment = application_environment(args.source_root)
    except ValueError:
        parser.error("invalid source root")
    try:
        report = asyncio.run(run_browser(args))
    except Exception as error:
        report = {"ok": False, "error_type": type(error).__name__}
        if isinstance(error, MeasurementFailure):
            report.update(scenario=error.scenario, error_type=error.error_type)
    report["environment"] = environment
    report["method"] = (
        "fresh context per cold sample; authenticated session; unmodified polling; input-to-response-and-render query"
    )
    print(perf.serialize_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
