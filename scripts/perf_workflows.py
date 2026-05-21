from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


TARGETS_MS = {
    "open_card": 700.0,
    "save_card": 1200.0,
    "move_card": 1200.0,
    "open_modal": 800.0,
    "backend_write": 1200.0,
}

MODAL_WORKFLOWS = (
    ("open_modal.clients", "#clientsButton", "#clientsModal", "#clientNewButton"),
    (
        "open_modal.repair_orders",
        "#repairOrdersButton",
        "#repairOrdersModal",
        "#repairOrdersList",
    ),
    ("open_modal.cashboxes", "#cashboxesButton", "#cashboxesModal", "#cashboxJournalButton"),
    ("open_modal.archive", "#archiveButton", "#archiveModal", "#archiveSearchInput"),
    ("open_modal.shared_files", "#sharedFilesButton", "#sharedFilesModal", "#sharedFilesDesktop"),
    ("open_modal.employees", "#employeesButton", "#employeesModal", "#employeesList"),
)


@dataclass
class BrowserRuntime:
    base_url: str
    card_id: str
    local_temp_server: bool
    runtime: Any | None = None

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def summarize_samples(samples: list[dict[str, Any]], *, scenario: str) -> dict[str, Any]:
    durations = [float(item.get("duration_ms") or 0.0) for item in samples]
    request_counts = [int(item.get("request_count") or 0) for item in samples]
    payload_sizes = [int(item.get("payload_bytes") or 0) for item in samples]
    server_timings = [
        str(timing)
        for item in samples
        for timing in (item.get("server_timing") or [])
        if str(timing).strip()
    ]
    ui_entries = [
        entry
        for item in samples
        for entry in (item.get("ui_perf_entries") or [])
        if isinstance(entry, dict)
    ]
    return {
        "scenario": scenario,
        "iterations": len(samples),
        "avg_ms": round(statistics.mean(durations), 1) if durations else 0.0,
        "min_ms": round(min(durations), 1) if durations else 0.0,
        "max_ms": round(max(durations), 1) if durations else 0.0,
        "p95_ms": round(percentile(durations, 0.95), 1),
        "server_timing": server_timings[-5:],
        "request_count": round(statistics.mean(request_counts), 1) if request_counts else 0.0,
        "payload_bytes": round(statistics.mean(payload_sizes)) if payload_sizes else 0,
        "ui_perf_entries": ui_entries[-20:],
    }


def skipped_row(scenario: str, reason: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "skipped": True,
        "reason": reason,
        "avg_ms": 0.0,
        "min_ms": 0.0,
        "max_ms": 0.0,
        "p95_ms": 0.0,
        "server_timing": [],
        "request_count": 0,
        "payload_bytes": 0,
        "ui_perf_entries": [],
    }


def json_request(base_url: str, path: str, *, timeout: float = 15.0) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/" + path.lstrip("/"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def first_card_id_from_base_url(base_url: str) -> str:
    payload = json_request(base_url, "/api/get_board_snapshot?compact=1&include_archive=0")
    cards = payload.get("data", {}).get("cards", [])
    if isinstance(cards, list) and cards:
        return str(cards[0].get("id") or "").strip()
    return ""


def start_browser_runtime(args: argparse.Namespace) -> BrowserRuntime:
    if args.local_temp_server:
        from browser_smoke import start_temp_runtime

        runtime = start_temp_runtime(start_port=args.start_port)
        return BrowserRuntime(
            base_url=runtime.base_url,
            card_id=args.card_id or runtime.card_id,
            local_temp_server=True,
            runtime=runtime,
        )
    card_id = args.card_id or first_card_id_from_base_url(args.base_url)
    return BrowserRuntime(
        base_url=args.base_url,
        card_id=card_id,
        local_temp_server=False,
        runtime=None,
    )


def scenario_target(scenario: str) -> float:
    if scenario.startswith("open_modal."):
        return TARGETS_MS["open_modal"]
    if scenario.startswith("backend.") and scenario in {
        "backend.update_card",
        "backend.move_card",
        "backend.mark_card_seen",
        "storage.write_bundle",
    }:
        return TARGETS_MS["backend_write"]
    for key, value in TARGETS_MS.items():
        if key in scenario:
            return value
    return 0.0


def ranked_findings(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in rows:
        if row.get("skipped"):
            continue
        target = scenario_target(str(row.get("scenario") or ""))
        avg_ms = float(row.get("avg_ms") or 0.0)
        if target <= 0 or avg_ms <= target:
            continue
        scenario = str(row.get("scenario") or "")
        if scenario.startswith("backend.") or scenario.startswith("storage."):
            area = "backend/storage"
            next_step = "Profile JsonStore/CardService and avoid full-state writes or unnecessary read-cache misses."
            files = [
                "src/minimal_kanban/storage/json_store.py",
                "src/minimal_kanban/services/card_service.py",
            ]
        elif scenario.startswith("open_modal."):
            area = "modal data/render"
            next_step = (
                "Open the modal shell first, then lazy-load heavy lists with compact payloads."
            )
            files = ["src/minimal_kanban/web_app_assets/assembler.py"]
        elif "move_card" in scenario:
            area = "board move"
            next_step = "Keep optimistic DOM patching on the success path and eliminate fallback full snapshot refreshes."
            files = [
                "src/minimal_kanban/web_app_assets/assembler.py",
                "src/minimal_kanban/services/card_service.py",
            ]
        elif "save_card" in scenario:
            area = "card save"
            next_step = (
                "Measure update_card write time and remove post-save refresh or heavy side effects."
            )
            files = [
                "src/minimal_kanban/web_app_assets/assembler.py",
                "src/minimal_kanban/services/card_service.py",
            ]
        else:
            area = "card open"
            next_step = "Use compact snapshot immediately and keep journal/files lazy."
            files = [
                "src/minimal_kanban/web_app_assets/assembler.py",
                "src/minimal_kanban/services/snapshot_service.py",
            ]
        findings.append(
            {
                "scenario": scenario,
                "area": area,
                "avg_ms": avg_ms,
                "target_ms": target,
                "over_by_ms": round(avg_ms - target, 1),
                "files": files,
                "next_step": next_step,
            }
        )
    findings.sort(key=lambda item: float(item["over_by_ms"]), reverse=True)
    return findings[:limit]


async def browser_perf_entries(page: Any) -> list[dict[str, Any]]:
    entries = await page.evaluate(
        "() => Array.isArray(window.__AUTOSTOP_PERF__) ? window.__AUTOSTOP_PERF__.slice() : []"
    )
    return [entry for entry in entries if isinstance(entry, dict)]


async def measure_browser_action(
    page: Any,
    *,
    scenario: str,
    iterations: int,
    responses: list[dict[str, Any]],
    action: Callable[[int], Awaitable[None]],
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for index in range(max(1, iterations)):
        response_start = len(responses)
        perf_start = len(await browser_perf_entries(page))
        started_at = time.perf_counter()
        await action(index)
        duration_ms = (time.perf_counter() - started_at) * 1000
        perf_delta = (await browser_perf_entries(page))[perf_start:]
        response_delta = responses[response_start:]
        samples.append(
            {
                "duration_ms": duration_ms,
                "request_count": len(response_delta),
                "payload_bytes": sum(int(item.get("bytes") or 0) for item in response_delta),
                "server_timing": [
                    str(item.get("server_timing") or "")
                    for item in response_delta
                    if str(item.get("server_timing") or "").strip()
                ],
                "ui_perf_entries": perf_delta,
            }
        )
    return summarize_samples(samples, scenario=scenario)


async def login_browser(page: Any, args: argparse.Namespace, runtime: BrowserRuntime) -> bool:
    from browser_smoke import _is_modal_open, _login

    if runtime.local_temp_server:
        await _login(page)
        return True
    if args.operator_username and args.operator_password:
        await page.wait_for_selector("#identityInput", state="visible", timeout=8000)
        await page.fill("#identityInput", args.operator_username)
        await page.fill("#identityPassword", args.operator_password)
        await page.click("#identitySave")
        return True
    if args.operator_token:
        return True
    if await _is_modal_open(page, "#identityModal"):
        return False
    return True


async def run_browser_workflows(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return {
            "rows": [],
            "events": {
                "console_errors": [],
                "page_errors": [],
                "failed_requests": [],
            },
            "error": "playwright_missing",
            "message": str(exc),
        }

    from browser_smoke import _launch_chromium, _wait_modal_closed, _wait_modal_open

    runtime = start_browser_runtime(args)
    rows: list[dict[str, Any]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    responses: list[dict[str, Any]] = []
    try:
        if not runtime.card_id:
            return {
                "base_url": runtime.base_url,
                "rows": [skipped_row("browser", "No card_id was found for browser workflows.")],
                "events": {
                    "console_errors": console_errors,
                    "page_errors": page_errors,
                    "failed_requests": failed_requests,
                },
            }
        async with async_playwright() as playwright:
            browser = await _launch_chromium(playwright, headless=not args.headed)
            context = await browser.new_context(viewport={"width": 1440, "height": 960})
            await context.add_init_script(
                "window.localStorage.setItem('autostop-perf', '1');window.__AUTOSTOP_PERF__ = [];"
            )
            if args.operator_token:
                token = json.dumps(str(args.operator_token))
                await context.add_init_script(
                    f"window.localStorage.setItem('kanban-operator-session', {token});"
                )
            page = await context.new_page()
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(
                    f"{request.method} {request.url} "
                    f"{request.failure.get('errorText') if request.failure else ''}"
                ),
            )

            def record_response(response: Any) -> None:
                url = str(response.url)
                if "/api/" not in url:
                    return
                headers = response.headers
                raw_length = headers.get("content-length") or "0"
                try:
                    byte_count = int(raw_length)
                except (TypeError, ValueError):
                    byte_count = 0
                responses.append(
                    {
                        "url": url,
                        "status": response.status,
                        "bytes": byte_count,
                        "server_timing": headers.get("server-timing") or "",
                    }
                )

            page.on("response", record_response)
            try:
                await page.goto(runtime.base_url, wait_until="domcontentloaded")
                logged_in = await login_browser(page, args, runtime)
                if not logged_in:
                    rows.append(
                        skipped_row(
                            "browser",
                            "Operator session is required. Pass --operator-token, "
                            "--operator-username/--operator-password, or use --local-temp-server.",
                        )
                    )
                    return {
                        "base_url": runtime.base_url,
                        "rows": rows,
                        "events": {
                            "console_errors": console_errors,
                            "page_errors": page_errors,
                            "failed_requests": failed_requests,
                        },
                    }
                await page.wait_for_selector("#board", timeout=15000)
                card_selector = f'[data-card-id="{runtime.card_id}"]'
                await page.wait_for_selector(card_selector, timeout=15000)

                async def close_card_if_open() -> None:
                    if await page.evaluate(
                        "() => document.querySelector('#cardModal')?.classList.contains('is-open')"
                    ):
                        await page.click("#cardModalCloseButtonTop")
                        await _wait_modal_closed(page, "#cardModal")

                async def open_card_action(_: int) -> None:
                    await close_card_if_open()
                    await page.click(card_selector)
                    await _wait_modal_open(page, "#cardModal")
                    await page.wait_for_function(
                        "() => !document.querySelector('#cardDescriptionEditor')?.classList.contains('is-loading')",
                        timeout=8000,
                    )
                    await close_card_if_open()

                rows.append(
                    await measure_browser_action(
                        page,
                        scenario="open_card",
                        iterations=args.iterations,
                        responses=responses,
                        action=open_card_action,
                    )
                )

                async def open_journal_action(_: int) -> None:
                    await close_card_if_open()
                    await page.click(card_selector)
                    await _wait_modal_open(page, "#cardModal")
                    await page.click('[data-tab="journal"]')
                    await page.wait_for_selector(".card-journal-view", timeout=8000)
                    await page.click('[data-tab="overview"]')
                    await close_card_if_open()

                rows.append(
                    await measure_browser_action(
                        page,
                        scenario="open_card_journal",
                        iterations=args.iterations,
                        responses=responses,
                        action=open_journal_action,
                    )
                )

                can_write = bool(runtime.local_temp_server or args.allow_write_workflows)
                if can_write:

                    async def save_card_action(index: int) -> None:
                        await close_card_if_open()
                        await page.click(card_selector)
                        await _wait_modal_open(page, "#cardModal")
                        await page.fill("#cardTitle", f"Perf workflow save {index}")
                        await page.click("#saveCardButton")
                        await _wait_modal_closed(page, "#cardModal")

                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario="save_card",
                            iterations=args.iterations,
                            responses=responses,
                            action=save_card_action,
                        )
                    )

                    async def move_card_action(_: int) -> None:
                        await page.evaluate(
                            """async (cardId) => {
                              const card = document.querySelector('[data-card-id="' + CSS.escape(cardId) + '"]');
                              const currentColumn = card?.closest('.column')?.dataset?.columnId || '';
                              const columns = Array.from(document.querySelectorAll('.column[data-column-id]'))
                                .map((column) => column.dataset.columnId)
                                .filter(Boolean);
                              const targetColumn = columns.find((columnId) => columnId !== currentColumn) || currentColumn;
                              if (!targetColumn || typeof window.moveCard !== 'function') {
                                throw new Error('moveCard workflow is not available');
                              }
                              await window.moveCard(cardId, targetColumn, '');
                            }""",
                            runtime.card_id,
                        )
                        await page.wait_for_selector(card_selector, timeout=8000)

                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario="move_card",
                            iterations=args.iterations,
                            responses=responses,
                            action=move_card_action,
                        )
                    )
                else:
                    rows.append(
                        skipped_row(
                            "save_card",
                            "Write workflow skipped. Use --allow-write-workflows or --local-temp-server.",
                        )
                    )
                    rows.append(
                        skipped_row(
                            "move_card",
                            "Write workflow skipped. Use --allow-write-workflows or --local-temp-server.",
                        )
                    )

                async def modal_action(button: str, modal: str, ready_selector: str) -> None:
                    if await page.evaluate(
                        "(selector) => document.querySelector(selector)?.classList.contains('is-open')",
                        modal,
                    ):
                        await page.click(f"{modal} [data-close]")
                        await _wait_modal_closed(page, modal)
                    await page.click(button)
                    await _wait_modal_open(page, modal)
                    await page.wait_for_selector(ready_selector, timeout=10000)
                    close_button = await page.query_selector(f"{modal} [data-close]")
                    if close_button:
                        await close_button.click()
                        await _wait_modal_closed(page, modal)

                for scenario, button, modal, ready_selector in MODAL_WORKFLOWS:
                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario=scenario,
                            iterations=args.iterations,
                            responses=responses,
                            action=lambda _index, b=button, m=modal, r=ready_selector: modal_action(
                                b, m, r
                            ),
                        )
                    )
            finally:
                await context.close()
                await browser.close()
    finally:
        runtime.close()

    return {
        "base_url": runtime.base_url,
        "local_temp_server": runtime.local_temp_server,
        "card_id": runtime.card_id,
        "rows": rows,
        "events": {
            "console_errors": console_errors,
            "page_errors": page_errors,
            "failed_requests": failed_requests,
        },
    }


def _logger() -> logging.Logger:
    logger = logging.getLogger("autostop.perf_workflows")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def response_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def run_state_file_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from minimal_kanban.services.card_service import CardService
    from minimal_kanban.storage.json_store import JsonStore

    source = Path(args.state_file).expanduser().resolve()
    if not source.exists():
        return {"rows": [skipped_row("state_file", f"State file not found: {source}")]}
    with tempfile.TemporaryDirectory(prefix="autostop-perf-state-") as temp_dir:
        state_file = Path(temp_dir) / "state.json"
        shutil.copy2(source, state_file)
        logger = _logger()
        store = JsonStore(state_file=state_file, logger=logger)
        service = CardService(
            store,
            logger,
            attachments_dir=Path(temp_dir) / "attachments",
            repair_orders_dir=Path(temp_dir) / "repair-orders",
        )
        bundle = store.read_bundle()
        cards = [card for card in bundle["cards"] if not getattr(card, "archived", False)]
        if not cards:
            return {"rows": [skipped_row("state_file", "No active cards in state file copy.")]}
        requested_card_id = str(args.card_id or "").strip()
        benchmark_card = next(
            (card for card in cards if card.id == requested_card_id),
            cards[0],
        )
        card_id = benchmark_card.id
        columns = list(bundle["columns"])
        if len(columns) < 2:
            created_column = service.create_column({"label": "PERF TMP", "actor_name": "PERF"})[
                "column"
            ]
            bundle = store.read_bundle()
            columns = list(bundle["columns"])
            if not any(column.id == str(created_column["id"]) for column in columns):
                columns = list(bundle["columns"])

        def timed(callable_obj: Callable[[], Any]) -> tuple[float, Any]:
            started_at = time.perf_counter()
            result = callable_obj()
            return (time.perf_counter() - started_at) * 1000, result

        raw_samples: dict[str, list[dict[str, Any]]] = {
            "backend.get_card": [],
            "backend.get_card_log_compact": [],
            "backend.update_card": [],
            "backend.move_card": [],
            "backend.mark_card_seen": [],
            "storage.read_bundle_uncached": [],
            "storage.write_bundle": [],
        }
        for index in range(max(1, args.iterations)):
            store._invalidate_read_cache()
            duration_ms, result = timed(lambda: store.read_bundle())
            raw_samples["storage.read_bundle_uncached"].append(
                {"duration_ms": duration_ms, "payload_bytes": 0}
            )

            duration_ms, result = timed(lambda: service.get_card({"card_id": card_id}))
            raw_samples["backend.get_card"].append(
                {"duration_ms": duration_ms, "payload_bytes": response_size(result)}
            )

            duration_ms, result = timed(
                lambda: service.get_card_log({"card_id": card_id, "compact": True, "limit": 50})
            )
            raw_samples["backend.get_card_log_compact"].append(
                {"duration_ms": duration_ms, "payload_bytes": response_size(result)}
            )

            duration_ms, result = timed(
                lambda: service.update_card(
                    {
                        "card_id": card_id,
                        "description": f"Perf workflow state copy update {index}",
                        "actor_name": "PERF",
                        "source": "api",
                    }
                )
            )
            raw_samples["backend.update_card"].append(
                {"duration_ms": duration_ms, "payload_bytes": response_size(result)}
            )

            move_bundle = store.read_bundle()
            current_card = next(
                (card for card in move_bundle["cards"] if card.id == card_id),
                benchmark_card,
            )
            next_column_id = next(
                (
                    column.id
                    for column in move_bundle["columns"]
                    if column.id != current_card.column
                ),
                current_card.column,
            )
            duration_ms, result = timed(
                lambda: service.move_card(
                    {
                        "card_id": card_id,
                        "column": next_column_id,
                        "actor_name": "PERF",
                        "source": "api",
                    }
                )
            )
            raw_samples["backend.move_card"].append(
                {"duration_ms": duration_ms, "payload_bytes": response_size(result)}
            )

            bundle = store.read_bundle()
            card = next(card for card in bundle["cards"] if card.id == card_id)
            card.is_unread = True
            store.write_bundle(
                columns=bundle["columns"],
                cards=bundle["cards"],
                clients=bundle["clients"],
                stickies=bundle["stickies"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=bundle["settings"],
            )
            duration_ms, result = timed(
                lambda: service.mark_card_seen({"card_id": card_id, "actor_name": "PERF"})
            )
            raw_samples["backend.mark_card_seen"].append(
                {"duration_ms": duration_ms, "payload_bytes": response_size(result)}
            )

            bundle = store.read_bundle()
            duration_ms, _ = timed(
                lambda: store.write_bundle(
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    clients=bundle["clients"],
                    stickies=bundle["stickies"],
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=bundle["events"],
                    settings=bundle["settings"],
                )
            )
            raw_samples["storage.write_bundle"].append(
                {"duration_ms": duration_ms, "payload_bytes": state_file.stat().st_size}
            )
        rows = [
            summarize_samples(samples, scenario=scenario)
            for scenario, samples in raw_samples.items()
        ]
        return {
            "state_file": str(source),
            "state_copy_bytes": state_file.stat().st_size,
            "card_id": card_id,
            "rows": rows,
        }


def evaluate_thresholds(
    rows: list[dict[str, Any]], args: argparse.Namespace
) -> list[dict[str, Any]]:
    thresholds = {
        "open_card": args.max_open_card_ms,
        "save_card": args.max_save_card_ms,
        "move_card": args.max_move_card_ms,
        "backend.update_card": args.max_backend_write_ms,
        "backend.move_card": args.max_backend_write_ms,
        "backend.mark_card_seen": args.max_backend_write_ms,
        "storage.write_bundle": args.max_backend_write_ms,
    }
    violations: list[dict[str, Any]] = []
    for row in rows:
        if row.get("skipped"):
            continue
        scenario = str(row.get("scenario") or "")
        threshold = thresholds.get(scenario)
        if threshold is None and scenario.startswith("open_modal."):
            threshold = args.max_open_modal_ms
        if not threshold or threshold <= 0:
            continue
        actual = float(row.get("avg_ms") or 0.0)
        if actual > threshold:
            violations.append(
                {
                    "scenario": scenario,
                    "metric": "avg_ms",
                    "actual": round(actual, 1),
                    "max": float(threshold),
                }
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run AutoStop CRM performance workflows and state-file benchmarks."
    )
    parser.add_argument("--base-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--card-id", default="")
    parser.add_argument("--operator-token", default="")
    parser.add_argument("--operator-username", default="")
    parser.add_argument("--operator-password", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--local-temp-server", action="store_true")
    parser.add_argument("--allow-write-workflows", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--start-port", type=int, default=42831)
    parser.add_argument("--max-open-card-ms", type=float, default=0.0)
    parser.add_argument("--max-save-card-ms", type=float, default=0.0)
    parser.add_argument("--max-move-card-ms", type=float, default=0.0)
    parser.add_argument("--max-open-modal-ms", type=float, default=0.0)
    parser.add_argument("--max-backend-write-ms", type=float, default=0.0)
    args = parser.parse_args()

    output: dict[str, Any] = {
        "iterations": args.iterations,
        "safe_mode": {
            "local_temp_server": bool(args.local_temp_server),
            "allow_write_workflows": bool(args.allow_write_workflows),
            "external_write_workflows_enabled": bool(
                args.allow_write_workflows and not args.local_temp_server
            ),
        },
        "browser": None,
        "state_file_benchmark": None,
        "rows": [],
    }
    if not args.skip_browser:
        browser_result = asyncio.run(run_browser_workflows(args))
        output["browser"] = browser_result
        output["rows"].extend(browser_result.get("rows") or [])
    if args.state_file:
        state_result = run_state_file_benchmark(args)
        output["state_file_benchmark"] = state_result
        output["rows"].extend(state_result.get("rows") or [])
    output["ranked_findings"] = ranked_findings(output["rows"])
    output["violations"] = evaluate_thresholds(output["rows"], args)
    output["threshold_status"] = "failed" if output["violations"] else "passed"
    print(json.dumps(output, ensure_ascii=False, indent=2))
    browser_output = output.get("browser")
    if isinstance(browser_output, dict) and browser_output.get("error") == "playwright_missing":
        return 2
    return 1 if output["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
