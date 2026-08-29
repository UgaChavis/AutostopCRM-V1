from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import asyncio
import contextlib
import json
import logging
import math
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402


TARGETS_MS = {
    "open_card": 700.0,
    "save_card": 1200.0,
    "move_card": 1200.0,
    "open_modal": 800.0,
    "payroll_ui": 1200.0,
    "print_act": 1200.0,
    "backend_write": 1200.0,
}

DEFAULT_BROWSER_TIMEOUT_SECONDS = 240.0
PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS = 10.0
BENIGN_UI_PERF_ERRORS = {"AbortError"}
PERF_WORKFLOW_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
PERF_WORKFLOW_STATE_FILE_MAX_BYTES = 100 * 1024 * 1024
SYNTHETIC_STATE_MIN_BYTES = 10 * 1024 * 1024
SYNTHETIC_STATE_PROFILE = "current-production"
SYNTHETIC_STATE_COUNTS = {
    "cards": 620,
    "clients": 4000,
    "events": 5000,
    "cash_transactions": 1500,
}


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


def _json_dumps(
    payload: Any,
    *,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    sort_keys: bool = False,
) -> str:
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        indent=indent,
        separators=separators,
        sort_keys=sort_keys,
        allow_nan=False,
    )


MODAL_WORKFLOWS = (
    (
        "open_modal.clients",
        "#clientsButton",
        "#clientsModal",
        "#clientsList [data-client-id], #clientsList .empty",
    ),
    (
        "open_modal.repair_orders",
        "#repairOrdersButton",
        "#repairOrdersModal",
        "#repairOrdersList [data-open-repair-order-card], #repairOrdersList .log-row__meta",
    ),
    (
        "open_modal.cashboxes",
        "#cashboxesButton",
        "#cashboxesModal",
        "#cashboxesList [data-cashbox-id], #cashboxStats .cashbox-stat-grid, #cashboxesList .cashboxes-empty",
    ),
    (
        "open_modal.archive",
        "#archiveButton",
        "#archiveModal",
        "#archiveList .archive-row, #archiveList .log-row__meta",
    ),
    (
        "open_modal.shared_files",
        "#sharedFilesButton",
        "#sharedFilesModal",
        "#sharedFilesDesktop [data-shared-file-id], #sharedFilesDesktop .shared-files-empty",
    ),
    (
        "open_modal.employees",
        "#employeesButton",
        "#employeesModal",
        "#employeesList [data-employee-id], #employeesList .cashboxes-empty",
    ),
)


@dataclass
class BrowserRuntime:
    base_url: str
    card_id: str
    local_temp_server: bool
    employee_id: str = ""
    payroll_card_id: str = ""
    salary_override_card_id: str = ""
    runtime: Any | None = None

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()


def browser_write_workflows_enabled(runtime: BrowserRuntime) -> bool:
    return bool(runtime.local_temp_server and runtime.runtime is not None)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _bounded_float(
    value: Any,
    *,
    default: float,
    minimum: float = 0.0,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(default if value is None or value == "" else value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _safe_int(value: Any, *, default: int = 0, maximum: int = 1_000_000_000) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed) or not parsed.is_integer():
        return default
    if parsed < 0:
        return default
    if parsed > maximum:
        return maximum
    return int(parsed)


def _bounded_iterations(value: Any) -> int:
    return max(1, _safe_int(value, default=3, maximum=100))


def _bounded_port(value: Any, *, default: int) -> int:
    port = _safe_int(value, default=default, maximum=65535)
    return port if port >= 1 else default


def _response_payload_bytes(responses: list[dict[str, Any]]) -> int:
    return sum(_safe_int(item.get("bytes")) for item in responses if isinstance(item, dict))


def summarize_samples(samples: list[dict[str, Any]], *, scenario: str) -> dict[str, Any]:
    durations = [_safe_float(item.get("duration_ms")) for item in samples]
    request_counts = [_safe_int(item.get("request_count")) for item in samples]
    payload_sizes = [_safe_int(item.get("payload_bytes")) for item in samples]
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
    phase_values: dict[str, list[float]] = {}
    for item in samples:
        phase_timings = item.get("phase_timings")
        if not isinstance(phase_timings, dict):
            continue
        for name, value in phase_timings.items():
            duration_ms = _safe_float(value, default=-1.0)
            if duration_ms < 0:
                continue
            phase_values.setdefault(str(name), []).append(duration_ms)
    return {
        "scenario": scenario,
        "iterations": len(samples),
        "avg_ms": round(statistics.mean(durations), 1) if durations else 0.0,
        "min_ms": round(min(durations), 1) if durations else 0.0,
        "p50_ms": round(percentile(durations, 0.50), 1),
        "max_ms": round(max(durations), 1) if durations else 0.0,
        "p95_ms": round(percentile(durations, 0.95), 1),
        "server_timing": server_timings,
        "phase_timings": {
            name: {
                "samples": len(values),
                "avg_ms": round(statistics.mean(values), 1),
                "p50_ms": round(percentile(values, 0.50), 1),
                "p95_ms": round(percentile(values, 0.95), 1),
                "max_ms": round(max(values), 1),
            }
            for name, values in sorted(phase_values.items())
        },
        "request_count": round(statistics.mean(request_counts), 1) if request_counts else 0.0,
        "payload_bytes": round(statistics.mean(payload_sizes)) if payload_sizes else 0,
        "ui_perf_entries": ui_entries[-20:],
    }


def row_ui_perf_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for entry in row.get("ui_perf_entries") or []:
        if not isinstance(entry, dict):
            continue
        detail = entry.get("detail")
        if not isinstance(detail, dict):
            continue
        error = str(detail.get("error") or "").strip()
        if not error or error in BENIGN_UI_PERF_ERRORS:
            continue
        name = str(entry.get("name") or "ui_perf")
        errors.append(f"{name}: {error}")
    return errors


def _request_failure_text(request: Any) -> str:
    failure = getattr(request, "failure", None)
    if callable(failure):
        failure = failure()
    if isinstance(failure, dict):
        return str(failure.get("errorText") or "").strip()
    return str(failure or "").strip()


def format_failed_request(request: Any) -> str:
    return f"{request.method} {request.url} {_request_failure_text(request)}".strip()


def skipped_row(scenario: str, reason: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "skipped": True,
        "reason": reason,
        "avg_ms": 0.0,
        "min_ms": 0.0,
        "p50_ms": 0.0,
        "max_ms": 0.0,
        "p95_ms": 0.0,
        "server_timing": [],
        "phase_timings": {},
        "request_count": 0,
        "payload_bytes": 0,
        "ui_perf_entries": [],
    }


def _read_response_body(response) -> bytes:
    raw = response.read(PERF_WORKFLOW_RESPONSE_MAX_BYTES + 1)
    if len(raw) > PERF_WORKFLOW_RESPONSE_MAX_BYTES:
        raise ValueError("performance workflow response is too large")
    return raw


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


def failed_row(
    scenario: str, error: BaseException, *, samples: list[dict[str, Any]]
) -> dict[str, Any]:
    row = summarize_samples(samples, scenario=scenario) if samples else skipped_row(scenario, "")
    row.pop("skipped", None)
    row.pop("reason", None)
    row["failed"] = True
    row["error_type"] = type(error).__name__
    row["error"] = str(error)
    row["iterations_completed"] = max(0, len(samples) - 1)
    return row


def configure_stdout_utf8() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


def browser_timeout_seconds(args: argparse.Namespace) -> float:
    return _bounded_float(
        getattr(args, "browser_timeout_seconds", DEFAULT_BROWSER_TIMEOUT_SECONDS),
        default=DEFAULT_BROWSER_TIMEOUT_SECONDS,
        minimum=30.0,
        maximum=3600.0,
    )


async def close_with_timeout(awaitable: Awaitable[Any]) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(awaitable, timeout=PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS)


async def run_browser_workflows_with_timeout(args: argparse.Namespace) -> dict[str, Any]:
    timeout = browser_timeout_seconds(args)
    try:
        return await asyncio.wait_for(run_browser_workflows(args), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"Browser workflows exceeded {timeout:.1f}s") from exc


def browser_failure_result(args: argparse.Namespace, error: BaseException) -> dict[str, Any]:
    return {
        "base_url": args.base_url,
        "local_temp_server": bool(args.local_temp_server),
        "rows": [failed_row("browser_workflows", error, samples=[])],
        "events": {
            "console_errors": [],
            "page_errors": [],
            "failed_requests": [],
        },
    }


def json_request(base_url: str, path: str, *, timeout: float = 15.0) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/" + path.lstrip("/"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            return _load_json_response(_read_response_body(response))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError(f"API request redirected: {path}") from exc
        raise


def first_card_id_from_base_url(base_url: str) -> str:
    payload = json_request(base_url, "/api/get_board_snapshot?compact=1&include_archive=0")
    cards = payload.get("data", {}).get("cards", [])
    if isinstance(cards, list) and cards:
        return str(cards[0].get("id") or "").strip()
    return ""


def start_browser_runtime(args: argparse.Namespace) -> BrowserRuntime:
    if not args.local_temp_server:
        raise ValueError(
            "Browser workflows require a process-owned --local-temp-server; "
            "remote browser workflows are disabled."
        )

    from browser_smoke import start_temp_runtime

    runtime = start_temp_runtime(start_port=args.start_port)
    return BrowserRuntime(
        base_url=runtime.base_url,
        card_id=args.card_id or runtime.card_id,
        local_temp_server=True,
        employee_id=runtime.employee_id,
        payroll_card_id=runtime.payroll_card_id,
        salary_override_card_id=runtime.salary_override_card_id,
        runtime=runtime,
    )


def scenario_target(scenario: str) -> float:
    if scenario.startswith("open_modal."):
        return TARGETS_MS["open_modal"]
    if scenario in {
        "open_repair_order_salary_override",
        "open_employee_salary_ledger",
    }:
        return TARGETS_MS["payroll_ui"]
    if scenario == "open_employee_salary_reconciliation_print":
        return TARGETS_MS["print_act"]
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


def _workflow_reliability_finding(
    *,
    scenario: str,
    avg_ms: float,
    target_ms: float,
    over_by_ms: float,
    area: str,
    next_step: str,
    files: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    finding = {
        "scenario": scenario,
        "area": area,
        "avg_ms": avg_ms,
        "target_ms": target_ms,
        "over_by_ms": over_by_ms,
        "files": files,
        "next_step": next_step,
    }
    if error is not None:
        finding["error"] = error
    return finding


def _workflow_slowdown_context(scenario: str) -> tuple[str, str, list[str]]:
    if scenario.startswith("backend.") or scenario.startswith("storage."):
        return (
            "backend/storage",
            "Profile JsonStore/CardService and avoid full-state writes or unnecessary read-cache misses.",
            [
                "src/minimal_kanban/storage/json_store.py",
                "src/minimal_kanban/services/card_service.py",
            ],
        )
    if scenario.startswith("open_modal."):
        return (
            "modal data/render",
            "Open the modal shell first, then lazy-load heavy lists with compact payloads.",
            ["src/minimal_kanban/web_app_assets/source/"],
        )
    if scenario.startswith("open_employee_salary") or scenario.startswith(
        "open_repair_order_salary"
    ):
        return (
            "payroll UI",
            "Profile employee payroll payloads and keep salary/reconciliation tables compact.",
            [
                "src/minimal_kanban/web_app_assets/source/",
                "src/minimal_kanban/services/card_service.py",
            ],
        )
    if "move_card" in scenario:
        return (
            "board move",
            "Keep optimistic DOM patching on the success path and eliminate fallback full snapshot refreshes.",
            [
                "src/minimal_kanban/web_app_assets/source/",
                "src/minimal_kanban/services/card_service.py",
            ],
        )
    if "save_card" in scenario:
        return (
            "card save",
            "Measure update_card write time and remove post-save refresh or heavy side effects.",
            [
                "src/minimal_kanban/web_app_assets/source/",
                "src/minimal_kanban/services/card_service.py",
            ],
        )
    return (
        "card open",
        "Use compact snapshot immediately and keep journal/files lazy.",
        [
            "src/minimal_kanban/web_app_assets/source/",
            "src/minimal_kanban/services/snapshot_service.py",
        ],
    )


def _workflow_failure_finding(row: dict[str, Any], scenario: str) -> dict[str, Any]:
    return _workflow_reliability_finding(
        scenario=scenario,
        avg_ms=_safe_float(row.get("avg_ms")),
        target_ms=scenario_target(scenario),
        over_by_ms=999999.0,
        area="workflow reliability",
        next_step="Reproduce the scenario failure and fix the app or audit wait condition.",
        files=["scripts/perf_workflows.py"],
        error=str(row.get("error") or ""),
    )


def _workflow_ui_error_finding(
    row: dict[str, Any], scenario: str, ui_errors: list[str]
) -> dict[str, Any]:
    return _workflow_reliability_finding(
        scenario=scenario,
        avg_ms=_safe_float(row.get("avg_ms")),
        target_ms=scenario_target(scenario),
        over_by_ms=999999.0,
        area="workflow reliability",
        next_step="Reproduce the browser workflow and fix the app error hidden inside perf entries.",
        files=[
            "scripts/perf_workflows.py",
            "src/minimal_kanban/web_app_assets/source/",
        ],
        error="; ".join(ui_errors[:3]),
    )


def _workflow_slowdown_finding(
    row: dict[str, Any], scenario: str, avg_ms: float
) -> dict[str, Any] | None:
    target = scenario_target(scenario)
    if target <= 0 or avg_ms <= target:
        return None
    area, next_step, files = _workflow_slowdown_context(scenario)
    return _workflow_reliability_finding(
        scenario=scenario,
        avg_ms=avg_ms,
        target_ms=target,
        over_by_ms=round(avg_ms - target, 1),
        area=area,
        next_step=next_step,
        files=files,
    )


def ranked_findings(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in rows:
        scenario = str(row.get("scenario") or "")
        if row.get("failed"):
            findings.append(_workflow_failure_finding(row, scenario))
            continue
        ui_errors = row_ui_perf_errors(row)
        if ui_errors:
            findings.append(_workflow_ui_error_finding(row, scenario, ui_errors))
            continue
        if row.get("skipped"):
            continue
        avg_ms = _safe_float(row.get("avg_ms"))
        finding = _workflow_slowdown_finding(row, scenario, avg_ms)
        if finding is not None:
            findings.append(finding)
    findings.sort(key=lambda item: _safe_float(item.get("over_by_ms")), reverse=True)
    return findings[:limit]


async def browser_perf_entries(page: Any) -> list[dict[str, Any]]:
    entries = await page.evaluate(
        "() => Array.isArray(window.__AUTOSTOP_PERF__) ? window.__AUTOSTOP_PERF__.slice() : []"
    )
    return [entry for entry in entries if isinstance(entry, dict)]


async def wait_for_modal_ready(page: Any, modal: str, ready_selector: str) -> None:
    await page.wait_for_function(
        """({ modalSelector, readySelector }) => {
          const root = document.querySelector(modalSelector);
          if (!root?.classList?.contains('is-open')) return false;
          const nodes = Array.from(root.querySelectorAll(readySelector));
          return nodes.some((node) => {
            const style = window.getComputedStyle(node);
            const visible = style.display !== 'none'
              && style.visibility !== 'hidden'
              && node.getClientRects().length > 0;
            if (!visible) return false;
            const text = String(node.textContent || '').toUpperCase();
            return !text.includes('ЗАГРУЗКА') && !text.includes('ЗАГРУЖАЮ');
          });
        }""",
        arg={"modalSelector": modal, "readySelector": ready_selector},
        timeout=10000,
    )


async def modal_ready_diagnostics(page: Any, modal: str, ready_selector: str) -> dict[str, Any]:
    result = await page.evaluate(
        """({ modalSelector, readySelector }) => {
          const root = document.querySelector(modalSelector);
          if (!root) return { missing_root: true };
          const nodes = Array.from(root.querySelectorAll(readySelector)).map((node) => {
            const style = window.getComputedStyle(node);
            return {
              tag: node.tagName,
              class_name: String(node.className || ''),
              text: String(node.textContent || '').slice(0, 240),
              display: style.display,
              visibility: style.visibility,
              rects: node.getClientRects().length,
            };
          });
          return {
            open: root.classList.contains('is-open'),
            text_sample: String(root.textContent || '').slice(0, 500),
            nodes,
            perf_entries: Array.isArray(window.__AUTOSTOP_PERF__)
              ? window.__AUTOSTOP_PERF__.slice(-12)
              : [],
          };
        }""",
        arg={"modalSelector": modal, "readySelector": ready_selector},
    )
    return result if isinstance(result, dict) else {"value": result}


async def force_close_open_modals(page: Any) -> None:
    await page.evaluate(
        """() => {
          document.querySelectorAll('.modal.is-open').forEach((modal) => {
            modal.classList.remove('is-open');
            modal.setAttribute('aria-hidden', 'true');
          });
          document.body.classList.remove('modal-open');
        }"""
    )


async def goto_with_retry(page: Any, url: str, *, wait_until: str = "domcontentloaded") -> None:
    for attempt in range(2):
        try:
            await page.goto(url, wait_until=wait_until)
            return
        except Exception as exc:
            if attempt == 0 and "ERR_CONNECTION_TIMED_OUT" in str(exc):
                await asyncio.sleep(0.2)
                continue
            raise


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
        try:
            await action(index)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            try:
                perf_delta = (await browser_perf_entries(page))[perf_start:]
            except Exception:
                perf_delta = []
            with contextlib.suppress(Exception):
                await force_close_open_modals(page)
            response_delta = responses[response_start:]
            samples.append(
                {
                    "duration_ms": duration_ms,
                    "request_count": len(response_delta),
                    "payload_bytes": _response_payload_bytes(response_delta),
                    "server_timing": [
                        str(item.get("server_timing") or "")
                        for item in response_delta
                        if str(item.get("server_timing") or "").strip()
                    ],
                    "ui_perf_entries": perf_delta,
                    "error": str(exc),
                }
            )
            return failed_row(scenario, exc, samples=samples)
        duration_ms = (time.perf_counter() - started_at) * 1000
        perf_delta = (await browser_perf_entries(page))[perf_start:]
        response_delta = responses[response_start:]
        samples.append(
            {
                "duration_ms": duration_ms,
                "request_count": len(response_delta),
                "payload_bytes": _response_payload_bytes(response_delta),
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
                token = json.dumps(str(args.operator_token), allow_nan=False)
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
                lambda request: failed_requests.append(format_failed_request(request)),
            )

            def record_response(response: Any) -> None:
                url = str(response.url)
                if "/api/" not in url:
                    return
                headers = response.headers
                raw_length = headers.get("content-length") or "0"
                byte_count = _safe_int(raw_length)
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
                await goto_with_retry(page, runtime.base_url, wait_until="domcontentloaded")
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

                async def close_modal_best_effort(modal: str) -> None:
                    is_open = await page.evaluate(
                        "(selector) => document.querySelector(selector)?.classList.contains('is-open')",
                        modal,
                    )
                    if not is_open:
                        return
                    close_button = await page.query_selector(f"{modal} [data-close]")
                    if close_button:
                        try:
                            await close_button.click(timeout=3000)
                            await asyncio.wait_for(_wait_modal_closed(page, modal), timeout=5)
                        except Exception:
                            await force_close_open_modals(page)
                    else:
                        await force_close_open_modals(page)

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

                can_write = browser_write_workflows_enabled(runtime)
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
                              const moved = await window.moveCard(cardId, targetColumn, '');
                              if (!moved) {
                                throw new Error('moveCard workflow failed');
                              }
                              const nextColumn = document.querySelector('[data-card-id="' + CSS.escape(cardId) + '"]')
                                ?.closest('.column')?.dataset?.columnId || '';
                              if (targetColumn !== currentColumn && nextColumn !== targetColumn) {
                                throw new Error('moveCard workflow did not update the board column');
                              }
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
                            "Write workflow skipped. Writes require a process-owned "
                            "--local-temp-server.",
                        )
                    )
                    rows.append(
                        skipped_row(
                            "move_card",
                            "Write workflow skipped. Writes require a process-owned "
                            "--local-temp-server.",
                        )
                    )

                async def modal_action(
                    scenario: str, button: str, modal: str, ready_selector: str
                ) -> None:
                    await close_modal_best_effort(modal)
                    await page.click(button)
                    await _wait_modal_open(page, modal)
                    try:
                        await wait_for_modal_ready(page, modal, ready_selector)
                    except Exception as exc:
                        diagnostics = await modal_ready_diagnostics(page, modal, ready_selector)
                        encoded = _json_dumps(diagnostics, sort_keys=True)
                        raise RuntimeError(
                            f"{scenario} modal did not become ready: {encoded}"
                        ) from exc
                    await close_modal_best_effort(modal)

                for scenario, button, modal, ready_selector in MODAL_WORKFLOWS:
                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario=scenario,
                            iterations=args.iterations,
                            responses=responses,
                            action=lambda _index, s=scenario, b=button, m=modal, r=ready_selector: (
                                modal_action(s, b, m, r)
                            ),
                        )
                    )

                if runtime.salary_override_card_id:

                    async def open_repair_order_salary_override_action(_: int) -> None:
                        if await page.evaluate(
                            "() => document.querySelector('#repairOrdersModal')?.classList.contains('is-open')"
                        ):
                            await page.click('#repairOrdersModal [data-close="repair-orders"]')
                            await _wait_modal_closed(page, "#repairOrdersModal")
                        await page.click("#repairOrdersButton")
                        await _wait_modal_open(page, "#repairOrdersModal")
                        order_selector = (
                            f'[data-open-repair-order-card="{runtime.salary_override_card_id}"]'
                        )
                        await page.wait_for_selector(order_selector, timeout=10000)
                        await page.click(order_selector)
                        await _wait_modal_open(page, "#repairOrderModal")
                        await page.wait_for_selector(
                            "#repairOrderWorksBody [data-repair-order-work-salary-gear]",
                            timeout=10000,
                        )
                        await page.click(
                            "#repairOrderWorksBody [data-repair-order-work-salary-gear]"
                        )
                        await page.wait_for_selector(
                            "#repairOrderWorkSalaryPopover.is-open", timeout=8000
                        )
                        await page.wait_for_selector("#repairOrderWorkSalaryAmount")
                        await page.keyboard.press("Escape")
                        await page.click('[data-close="repair-order"]')
                        await _wait_modal_closed(page, "#repairOrderModal")
                        await page.click('#repairOrdersModal [data-close="repair-orders"]')
                        await _wait_modal_closed(page, "#repairOrdersModal")

                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario="open_repair_order_salary_override",
                            iterations=args.iterations,
                            responses=responses,
                            action=open_repair_order_salary_override_action,
                        )
                    )
                else:
                    rows.append(
                        skipped_row(
                            "open_repair_order_salary_override",
                            "Salary override repair order is available only in --local-temp-server.",
                        )
                    )

                if runtime.employee_id:

                    async def open_employee_salary_ledger_action(_: int) -> None:
                        if await page.evaluate(
                            "() => document.querySelector('#employeesModal')?.classList.contains('is-open')"
                        ):
                            await page.click('#employeesModal [data-close="employees"]')
                            await _wait_modal_closed(page, "#employeesModal")
                        await page.click("#employeesButton")
                        await _wait_modal_open(page, "#employeesModal")
                        await page.wait_for_selector(
                            f'[data-employee-id="{runtime.employee_id}"]', timeout=10000
                        )
                        await page.click(f'[data-employee-id="{runtime.employee_id}"]')
                        await page.click(f'[data-employee-salary="{runtime.employee_id}"]')
                        await _wait_modal_open(page, "#employeeSalaryModal")
                        await page.wait_for_selector("#employeeSalaryJournalTable tr")
                        await page.click('[data-close="employeeSalary"]')
                        await _wait_modal_closed(page, "#employeeSalaryModal")
                        await page.click('#employeesModal [data-close="employees"]')
                        await _wait_modal_closed(page, "#employeesModal")

                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario="open_employee_salary_ledger",
                            iterations=args.iterations,
                            responses=responses,
                            action=open_employee_salary_ledger_action,
                        )
                    )

                    async def open_employee_salary_reconciliation_print_action(_: int) -> None:
                        query = urllib.parse.urlencode({"employee_id": runtime.employee_id})
                        await goto_with_retry(
                            page,
                            f"{runtime.base_url}/employee_salary_reconciliation_print?{query}",
                            wait_until="domcontentloaded",
                        )
                        await page.wait_for_selector("text=Акт сверки зарплаты", timeout=10000)
                        await page.wait_for_selector("table")

                    rows.append(
                        await measure_browser_action(
                            page,
                            scenario="open_employee_salary_reconciliation_print",
                            iterations=args.iterations,
                            responses=responses,
                            action=open_employee_salary_reconciliation_print_action,
                        )
                    )
                else:
                    rows.append(
                        skipped_row(
                            "open_employee_salary_ledger",
                            "Employee payroll workflow is available only in --local-temp-server.",
                        )
                    )
                    rows.append(
                        skipped_row(
                            "open_employee_salary_reconciliation_print",
                            "Employee reconciliation print workflow is available only in --local-temp-server.",
                        )
                    )
            finally:
                await close_with_timeout(context.close())
                await close_with_timeout(browser.close())
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
    return len(_json_dumps(payload, separators=(",", ":")).encode("utf-8"))


def build_synthetic_current_production_state() -> dict[str, Any]:
    """Build deterministic, non-business data with the current production shape."""

    timestamp = "2099-01-01T00:00:00+00:00"
    columns = [
        {"id": "inbox", "label": "ВХОДЯЩИЕ", "position": 0},
        {"id": "diagnosis", "label": "ДИАГНОСТИКА", "position": 1},
        {"id": "repair", "label": "РЕМОНТ", "position": 2},
        {"id": "done", "label": "ГОТОВО", "position": 3},
    ]
    card_filler = "D" * 6900
    cards = [
        {
            "id": f"perf-card-{index:04d}",
            "vehicle": f"SYNTHETIC-{index:04d}",
            "title": f"Performance card {index:04d}",
            "description": f"Synthetic performance payload {index:04d} {card_filler}",
            "column": columns[index % len(columns)]["id"],
            "position": index // len(columns),
            "archived": False,
            "created_at": timestamp,
            "updated_at": timestamp,
            "notification_updated_at": timestamp,
            "deadline_timestamp": timestamp,
            "deadline_total_seconds": 173700,
            "client_id": f"perf-client-{index % SYNTHETIC_STATE_COUNTS['clients']:04d}",
            "vehicle_profile": {},
            "repair_order": {},
            "tags": [],
            "attachments": [],
            "seen_by_users": {},
        }
        for index in range(SYNTHETIC_STATE_COUNTS["cards"])
    ]
    client_filler = "C" * 850
    clients = [
        {
            "id": f"perf-client-{index:04d}",
            "client_type": "person",
            "display_name": f"Synthetic client {index:04d}",
            "comment": f"Synthetic profile {index:04d} {client_filler}",
            "vehicles": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        for index in range(SYNTHETIC_STATE_COUNTS["clients"])
    ]
    event_filler = "E" * 300
    events = [
        {
            "id": f"perf-event-{index:05d}",
            "timestamp": timestamp,
            "actor_name": "PERF",
            "source": "system",
            "action": "performance_event",
            "message": f"Synthetic performance event {index:05d}",
            "details": {"payload": event_filler, "sequence": index},
            "card_id": f"perf-card-{index % SYNTHETIC_STATE_COUNTS['cards']:04d}",
        }
        for index in range(SYNTHETIC_STATE_COUNTS["events"])
    ]
    cashboxes = [
        {
            "id": f"perf-cashbox-{index}",
            "name": f"PERF CASHBOX {index}",
            "order": index,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        for index in range(5)
    ]
    transaction_filler = "T" * 180
    cash_transactions = [
        {
            "id": f"perf-transaction-{index:05d}",
            "cashbox_id": cashboxes[index % len(cashboxes)]["id"],
            "direction": "income" if index % 2 == 0 else "expense",
            "amount_minor": 10_000 + index,
            "note": f"Perf {index:05d} {transaction_filler}",
            "created_at": timestamp,
            "actor_name": "PERF",
            "source": "system",
            "transaction_kind": "performance",
        }
        for index in range(SYNTHETIC_STATE_COUNTS["cash_transactions"])
    ]
    return {
        "schema_version": 9,
        "columns": columns,
        "cards": cards,
        "clients": clients,
        "stickies": [],
        "cashboxes": cashboxes,
        "cash_transactions": cash_transactions,
        "inventory_items": [],
        "inventory_movements": [],
        "events": events,
        "settings": {
            "has_seen_onboarding": True,
            "board_scale": 1.0,
            "ready_column_id": "done",
            "ai_board_control": {
                "enabled": False,
                "interval_minutes": 20,
                "cooldown_minutes": 60,
            },
        },
    }


def write_synthetic_current_production_state(state_file: Path) -> dict[str, Any]:
    state = build_synthetic_current_production_state()
    state_file.write_text(
        _json_dumps(state, separators=(",", ":")),
        encoding="utf-8",
    )
    state_bytes = state_file.stat().st_size
    if state_bytes < SYNTHETIC_STATE_MIN_BYTES:
        raise ValueError(
            f"synthetic state is too small: {state_bytes} < {SYNTHETIC_STATE_MIN_BYTES}"
        )
    return {
        "profile": SYNTHETIC_STATE_PROFILE,
        "state_bytes": state_bytes,
        "counts": dict(SYNTHETIC_STATE_COUNTS),
    }


def _timed_with_phases(callable_obj: Callable[[], Any]) -> tuple[float, Any, dict[str, float]]:
    from minimal_kanban.performance import request_performance_trace

    with request_performance_trace() as trace:
        started_at = time.perf_counter()
        result = callable_obj()
        duration_ms = (time.perf_counter() - started_at) * 1000
    return duration_ms, result, dict(trace.durations_ms)


def _cached_write_arguments(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "columns": bundle["columns"],
        "cards": bundle["cards"],
        "clients": bundle["clients"],
        "stickies": bundle["stickies"],
        "cashboxes": bundle["cashboxes"],
        "cash_transactions": bundle["cash_transactions"],
        "inventory_items": bundle["inventory_items"],
        "inventory_movements": bundle["inventory_movements"],
        "events": bundle["events"],
        "settings": bundle["settings"],
    }


def _run_stage1_state_file_benchmark(
    *,
    args: argparse.Namespace,
    source: Path,
    state_file: Path,
    store: Any,
    service: Any,
    card_id: str,
) -> dict[str, Any]:
    raw_samples: dict[str, list[dict[str, Any]]] = {
        "backend.get_card": [],
        "backend.get_board_revision_cached": [],
        "backend.list_cashboxes": [],
        "backend.update_card": [],
        "change_feed.read_page": [],
        "change_feed.replay_page": [],
        "storage.write_cached_bundle": [],
    }

    def run_iteration(index: int, *, collect: bool) -> None:
        duration_ms, result, phase_timings = _timed_with_phases(
            lambda: service.get_card({"card_id": card_id})
        )
        if collect:
            raw_samples["backend.get_card"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(result),
                    "phase_timings": phase_timings,
                }
            )

        revision_payload = {
            "compact": True,
            "include_archive": False,
            "actor_name": "PERF",
        }
        service.get_board_revision(revision_payload)
        duration_ms, result, phase_timings = _timed_with_phases(
            lambda: service.get_board_revision(revision_payload)
        )
        if collect:
            raw_samples["backend.get_board_revision_cached"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(result),
                    "phase_timings": phase_timings,
                }
            )

        duration_ms, result, phase_timings = _timed_with_phases(
            lambda: service.list_cashboxes({"actor_name": "PERF"})
        )
        if collect:
            raw_samples["backend.list_cashboxes"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(result),
                    "phase_timings": phase_timings,
                }
            )

        duration_ms, result, phase_timings = _timed_with_phases(
            lambda: service.update_card(
                {
                    "card_id": card_id,
                    "description": f"Stage 1 performance update {index:04d}",
                    "actor_name": "PERF",
                    "source": "api",
                }
            )
        )
        if collect:
            raw_samples["backend.update_card"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(result),
                    "phase_timings": phase_timings,
                }
            )

        bundle = store.read_bundle()
        duration_ms, _, phase_timings = _timed_with_phases(
            lambda: store.write_cached_bundle(
                bundle,
                **_cached_write_arguments(bundle),
            )
        )
        if collect:
            raw_samples["storage.write_cached_bundle"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": state_file.stat().st_size,
                    "phase_timings": phase_timings,
                }
            )

        consumer_id = f"perf-feed-{index:04d}"
        duration_ms, feed_page, phase_timings = _timed_with_phases(
            lambda: store.change_feed_store.read_page(consumer_id, limit=25)
        )
        if collect:
            raw_samples["change_feed.read_page"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(feed_page),
                    "phase_timings": phase_timings,
                }
            )
        duration_ms, replay_page, phase_timings = _timed_with_phases(
            lambda: store.change_feed_store.read_page(
                consumer_id,
                cursor=feed_page.get("replay_cursor"),
                limit=25,
            )
        )
        if collect:
            if replay_page != feed_page:
                raise ValueError("change-feed replay page changed during performance benchmark")
            raw_samples["change_feed.replay_page"].append(
                {
                    "duration_ms": duration_ms,
                    "payload_bytes": response_size(replay_page),
                    "phase_timings": phase_timings,
                }
            )

    for index in range(max(0, args.warmup_iterations)):
        run_iteration(index, collect=False)
    for index in range(max(1, args.iterations)):
        run_iteration(args.warmup_iterations + index, collect=True)

    rows = [
        summarize_samples(samples, scenario=scenario) for scenario, samples in raw_samples.items()
    ]
    return {
        "state_file": str(source),
        "state_copy_bytes": state_file.stat().st_size,
        "card_id": card_id,
        "stage1_only": True,
        "warmup_iterations": args.warmup_iterations,
        "rows": rows,
    }


def run_state_file_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from minimal_kanban.services.card_service import CardService
    from minimal_kanban.storage.json_store import JsonStore
    from minimal_kanban.storage.limited_io import copy_file_limited

    source = Path(args.state_file).expanduser().resolve()
    if not source.exists():
        return {"rows": [skipped_row("state_file", f"State file not found: {source}")]}
    with tempfile.TemporaryDirectory(prefix="autostop-perf-state-") as temp_dir:
        state_file = Path(temp_dir) / "state.json"
        copy_file_limited(
            source,
            state_file,
            max_bytes=PERF_WORKFLOW_STATE_FILE_MAX_BYTES,
            label="perf workflow state file",
        )
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

        if args.stage1_only:
            return _run_stage1_state_file_benchmark(
                args=args,
                source=source,
                state_file=state_file,
                store=store,
                service=service,
                card_id=card_id,
            )

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
                        "response_mode": "delta",
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
        "storage.write_cached_bundle": getattr(args, "max_storage_write_ms", 0.0),
        "backend.get_board_revision_cached": getattr(args, "max_revision_server_ms", 0.0),
        "backend.get_card": getattr(args, "max_get_card_direct_ms", 0.0),
        "backend.list_cashboxes": getattr(args, "max_list_cashboxes_ms", 0.0),
        "change_feed.read_page": getattr(args, "max_feed_read_ms", 0.0),
        "change_feed.replay_page": getattr(args, "max_feed_replay_ms", 0.0),
    }
    violations: list[dict[str, Any]] = []
    for row in rows:
        scenario = str(row.get("scenario") or "")
        if row.get("failed"):
            violations.append(
                {
                    "scenario": scenario,
                    "metric": "workflow_error",
                    "actual": str(row.get("error") or row.get("error_type") or "failed"),
                    "max": "no errors",
                }
            )
            continue
        ui_errors = row_ui_perf_errors(row)
        if ui_errors:
            violations.append(
                {
                    "scenario": scenario,
                    "metric": "ui_perf_error",
                    "actual": "; ".join(ui_errors[:3]),
                    "max": "no app perf errors",
                }
            )
            continue
        if row.get("skipped"):
            continue
        threshold = thresholds.get(scenario)
        if threshold is None and scenario.startswith("open_modal."):
            threshold = args.max_open_modal_ms
        if not threshold or threshold <= 0:
            continue
        actual = _safe_float(row.get("p95_ms"))
        if actual > threshold:
            violations.append(
                {
                    "scenario": scenario,
                    "metric": "p95_ms",
                    "actual": round(actual, 1),
                    "max": float(threshold),
                }
            )
    return violations


def main() -> int:
    configure_stdout_utf8()
    parser = argparse.ArgumentParser(
        description="Run AutoStop CRM performance workflows and state-file benchmarks."
    )
    parser.add_argument("--base-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--iterations", default=3)
    parser.add_argument("--warmup-iterations", default=0)
    parser.add_argument("--card-id", default="")
    parser.add_argument("--operator-token", default="")
    parser.add_argument("--operator-username", default="")
    parser.add_argument("--operator-password", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument(
        "--synthetic-state-profile",
        default="",
        choices=(SYNTHETIC_STATE_PROFILE,),
    )
    parser.add_argument("--local-temp-server", action="store_true")
    parser.add_argument("--stage1-only", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--start-port", default=42831)
    parser.add_argument("--max-open-card-ms", default=0.0)
    parser.add_argument("--max-save-card-ms", default=0.0)
    parser.add_argument("--max-move-card-ms", default=0.0)
    parser.add_argument("--max-open-modal-ms", default=0.0)
    parser.add_argument("--max-backend-write-ms", default=0.0)
    parser.add_argument("--max-storage-write-ms", default=0.0)
    parser.add_argument("--max-revision-server-ms", default=0.0)
    parser.add_argument("--max-get-card-direct-ms", default=0.0)
    parser.add_argument("--max-list-cashboxes-ms", default=0.0)
    parser.add_argument("--max-feed-read-ms", default=0.0)
    parser.add_argument("--max-feed-replay-ms", default=0.0)
    parser.add_argument("--browser-timeout-seconds", default=DEFAULT_BROWSER_TIMEOUT_SECONDS)
    args = parser.parse_args()
    args.iterations = _bounded_iterations(args.iterations)
    args.warmup_iterations = _safe_int(
        args.warmup_iterations,
        default=0,
        maximum=100,
    )
    args.start_port = _bounded_port(args.start_port, default=42831)
    args.max_open_card_ms = _bounded_float(args.max_open_card_ms, default=0.0, maximum=3_600_000.0)
    args.max_save_card_ms = _bounded_float(args.max_save_card_ms, default=0.0, maximum=3_600_000.0)
    args.max_move_card_ms = _bounded_float(args.max_move_card_ms, default=0.0, maximum=3_600_000.0)
    args.max_open_modal_ms = _bounded_float(
        args.max_open_modal_ms, default=0.0, maximum=3_600_000.0
    )
    args.max_backend_write_ms = _bounded_float(
        args.max_backend_write_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_storage_write_ms = _bounded_float(
        args.max_storage_write_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_revision_server_ms = _bounded_float(
        args.max_revision_server_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_get_card_direct_ms = _bounded_float(
        args.max_get_card_direct_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_list_cashboxes_ms = _bounded_float(
        args.max_list_cashboxes_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_feed_read_ms = _bounded_float(
        args.max_feed_read_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.max_feed_replay_ms = _bounded_float(
        args.max_feed_replay_ms,
        default=0.0,
        maximum=3_600_000.0,
    )
    args.browser_timeout_seconds = browser_timeout_seconds(args)
    if args.state_file and args.synthetic_state_profile:
        parser.error("--state-file and --synthetic-state-profile are mutually exclusive")
    if args.stage1_only and not args.state_file and not args.synthetic_state_profile:
        args.synthetic_state_profile = SYNTHETIC_STATE_PROFILE

    output: dict[str, Any] = {
        "iterations": args.iterations,
        "warmup_iterations": args.warmup_iterations,
        "safe_mode": {
            "local_temp_server": bool(args.local_temp_server),
            "write_workflows_enabled": bool(args.local_temp_server),
            "stage1_only": bool(args.stage1_only),
            "synthetic_state_profile": args.synthetic_state_profile,
        },
        "browser": None,
        "state_file_benchmark": None,
        "rows": [],
    }
    if not args.skip_browser:
        try:
            browser_result = asyncio.run(run_browser_workflows_with_timeout(args))
        except Exception as exc:
            browser_result = browser_failure_result(args, exc)
        output["browser"] = browser_result
        output["rows"].extend(browser_result.get("rows") or [])
    if args.state_file:
        state_result = run_state_file_benchmark(args)
        output["state_file_benchmark"] = state_result
        output["rows"].extend(state_result.get("rows") or [])
    elif args.synthetic_state_profile:
        with tempfile.TemporaryDirectory(prefix="autostop-perf-synthetic-") as temp_dir:
            synthetic_state_file = Path(temp_dir) / "state.json"
            synthetic_meta = write_synthetic_current_production_state(synthetic_state_file)
            args.state_file = str(synthetic_state_file)
            state_result = run_state_file_benchmark(args)
            state_result["synthetic"] = synthetic_meta
            output["state_file_benchmark"] = state_result
            output["rows"].extend(state_result.get("rows") or [])
    output["ranked_findings"] = ranked_findings(output["rows"])
    output["violations"] = evaluate_thresholds(output["rows"], args)
    output["threshold_status"] = "failed" if output["violations"] else "passed"
    print(_json_dumps(output, indent=2))
    browser_output = output.get("browser")
    if isinstance(browser_output, dict) and browser_output.get("error") == "playwright_missing":
        return 2
    return 1 if output["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
