from __future__ import annotations

import importlib.util
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROFILE_CORE = "core"
PROFILE_FULL = "full"
SUPPORTED_PROFILES = (PROFILE_CORE, PROFILE_FULL)


@dataclass(frozen=True)
class SmokeScenario:
    name: str
    tags: frozenset[str]


_FULL_SCENARIO_NAMES = (
    "login_gate_hides_board_until_operator_login",
    "anonymous_write_rejected",
    "desktop_board_create_roundtrip",
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
    "repair_order_preview_roundtrip",
    "completion_act_editor_draft_roundtrip",
    "clients_modal",
    "clients_search_selects_realistic_row",
    "files_modal",
    "shared_files_scanability_markup",
    "inventory_item_roundtrip",
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

_CORE_SCENARIO_NAMES = (
    "login_gate_hides_board_until_operator_login",
    "anonymous_write_rejected",
    "desktop_board_create_roundtrip",
    "desktop_board_card_roundtrip",
    "move_card_delta_roundtrip",
    "card_timer_start_stop",
    "clients_search_selects_realistic_row",
    "clients_repair_order_returns_to_client",
    "repair_order_preview_roundtrip",
    "inventory_item_roundtrip",
    "files_modal",
)

_MOBILE_SCENARIO_NAMES = tuple(name for name in _FULL_SCENARIO_NAMES if name.startswith("mobile_"))
_PDF_SCENARIO_NAMES = frozenset({"completion_act_editor_draft_roundtrip"})
_FINANCE_SCENARIO_NAMES = frozenset(
    name
    for name in _FULL_SCENARIO_NAMES
    if name.startswith(("cashbox_", "cashboxes_", "payroll_", "employee_shift_"))
    or name in {"repair_order_payments_modal"}
)


def _scenario_tags(name: str) -> frozenset[str]:
    tags = {PROFILE_FULL}
    if name in _CORE_SCENARIO_NAMES:
        tags.add(PROFILE_CORE)
    if name in _MOBILE_SCENARIO_NAMES:
        tags.add("mobile")
    else:
        tags.add("desktop")
    if name in _PDF_SCENARIO_NAMES:
        tags.add("pdf")
    if name in _FINANCE_SCENARIO_NAMES:
        tags.add("finance")
    return frozenset(tags)


SCENARIO_REGISTRY = tuple(
    SmokeScenario(name=name, tags=_scenario_tags(name)) for name in _FULL_SCENARIO_NAMES
)
if len({scenario.name for scenario in SCENARIO_REGISTRY}) != len(SCENARIO_REGISTRY):
    raise RuntimeError("Browser smoke scenario names must be unique.")

SMOKE_SCENARIOS = tuple(scenario.name for scenario in SCENARIO_REGISTRY)
CORE_SMOKE_SCENARIOS = tuple(
    scenario.name for scenario in SCENARIO_REGISTRY if PROFILE_CORE in scenario.tags
)
MOBILE_SMOKE_SCENARIOS = tuple(
    scenario.name for scenario in SCENARIO_REGISTRY if "mobile" in scenario.tags
)
DESKTOP_SMOKE_SCENARIOS = tuple(
    scenario.name
    for scenario in SCENARIO_REGISTRY
    if "desktop" in scenario.tags
    and scenario.name
    not in {"login_gate_hides_board_until_operator_login", "anonymous_write_rejected"}
)


def scenarios_for_profile(profile: str) -> tuple[str, ...]:
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported browser smoke profile: {profile}")
    return tuple(scenario.name for scenario in SCENARIO_REGISTRY if profile in scenario.tags)


def _probe_python_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _probe_chromium() -> bool:
    if not _probe_python_module("playwright.sync_api"):
        return False
    try:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        try:
            return Path(playwright.chromium.executable_path).is_file()
        finally:
            playwright.stop()
    except Exception:
        return False


def _probe_qt_pdf() -> bool:
    try:
        from PySide6.QtPdf import QPdfDocument

        document = QPdfDocument()
        document.close()
        return True
    except Exception:
        return False


def _probe_command(command: str) -> bool:
    executable = shutil.which(command)
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "-v"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


BROWSER_DEPENDENCY_PROBES: dict[str, Callable[[], bool]] = {
    "playwright": lambda: _probe_python_module("playwright.async_api"),
    "chromium": _probe_chromium,
    "qt_pdf": _probe_qt_pdf,
    "pdfinfo": lambda: _probe_command("pdfinfo"),
    "pdftotext": lambda: _probe_command("pdftotext"),
}


def dependency_names_for_profile(profile: str) -> tuple[str, ...]:
    if profile == PROFILE_CORE:
        return ("playwright", "chromium")
    if profile == PROFILE_FULL:
        return ("playwright", "chromium", "qt_pdf", "pdfinfo", "pdftotext")
    raise ValueError(f"Unsupported browser smoke profile: {profile}")


def missing_browser_dependencies(profile: str) -> list[str]:
    return [
        name
        for name in dependency_names_for_profile(profile)
        if not BROWSER_DEPENDENCY_PROBES[name]()
    ]


def missing_dependency_result(profile: str, missing_dependencies: list[str]) -> dict[str, object]:
    return {
        "ok": False,
        "error": "missing_dependency",
        "profile": profile,
        "missing_dependencies": list(missing_dependencies),
        "scenarios": {},
        "events": {
            "ok": False,
            "console_errors": [],
            "page_errors": [],
            "failed_requests": [],
        },
    }
