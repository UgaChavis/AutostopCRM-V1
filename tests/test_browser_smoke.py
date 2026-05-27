from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "browser_smoke.py"


def load_browser_smoke_module():
    spec = importlib.util.spec_from_file_location("browser_smoke", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("browser_smoke.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BrowserSmokeScriptTests(unittest.TestCase):
    def test_script_is_import_safe_and_targets_temp_local_runtime_only(self) -> None:
        module = load_browser_smoke_module()
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("desktop_board_card_roundtrip", module.SMOKE_SCENARIOS)
        self.assertIn("mobile_board_load", module.SMOKE_SCENARIOS)
        self.assertIn("employees_repair_order_returns_to_employee", module.SMOKE_SCENARIOS)
        self.assertIn("clients_repair_order_returns_to_client", module.SMOKE_SCENARIOS)
        self.assertIn("repair_orders_list_returns_to_list", module.SMOKE_SCENARIOS)
        self.assertIn("cashboxes_journal_transfer_returns_to_cashbox", module.SMOKE_SCENARIOS)
        self.assertIn("cashbox_journal_filters_and_no_audit", module.SMOKE_SCENARIOS)
        self.assertIn("cashbox_journal_compact_cleanup", module.SMOKE_SCENARIOS)
        self.assertIn("escape_closes_top_modal_only", module.SMOKE_SCENARIOS)
        self.assertIn("login_gate_hides_board_until_operator_login", module.SMOKE_SCENARIOS)
        self.assertIn("archive_search_filters_visible_rows", module.SMOKE_SCENARIOS)
        self.assertIn("clients_search_selects_realistic_row", module.SMOKE_SCENARIOS)
        self.assertIn("shared_files_scanability_markup", module.SMOKE_SCENARIOS)
        self.assertIn("repair_order_salary_override_popover", module.SMOKE_SCENARIOS)
        self.assertIn("payroll_chain_reaches_reports_and_reconciliation", module.SMOKE_SCENARIOS)
        self.assertNotIn("crm.autostopcrm.ru", script)
        self.assertTrue(callable(module.start_temp_runtime))
        self.assertTrue(callable(module.run_temp_smoke))
        self.assertTrue(callable(module._first_free_port))
        self.assertIn("start_port = _first_free_port(start_port)", script)

    def test_temp_runtime_seeds_modal_ladder_data(self) -> None:
        module = load_browser_smoke_module()
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("employee_id", module.TempRuntime.__dataclass_fields__)
        self.assertIn("payroll_card_id", module.TempRuntime.__dataclass_fields__)
        self.assertIn("payroll_month", module.TempRuntime.__dataclass_fields__)
        self.assertIn("salary_override_card_id", module.TempRuntime.__dataclass_fields__)
        self.assertIn("client_card_id", module.TempRuntime.__dataclass_fields__)
        self.assertIn("deadline", script)
        self.assertIn("service.save_employee", script)
        self.assertIn("service.set_repair_order_status", script)
        self.assertIn("work_salary_override_enabled", script)
        self.assertIn("repairOrderWorkSalaryAmount", script)
        self.assertIn("employee_salary_reconciliation_print", script)
        self.assertIn("service.archive_card", script)
        self.assertIn("employees_repair_order_returns_to_employee", script)
        self.assertIn("clients_repair_order_returns_to_client", script)
        self.assertIn("archive_search_filters_visible_rows", script)
        self.assertIn("cashbox_journal_filters_and_no_audit", script)
        self.assertIn("data-cash-journal-toggle-balances", script)
        self.assertIn("cashbox_journal_compact_cleanup", script)
        self.assertIn("visibleNoPairTags.length === 0", script)
        self.assertIn("transferRowsWithoutDiagnosticChips", script)
        self.assertIn("cashboxFinanceAuditButton", script)
        self.assertIn("cashboxJournalAuditButton", script)

    def test_summarize_browser_events_reports_console_page_and_network_failures(self) -> None:
        module = load_browser_smoke_module()

        summary = module.summarize_browser_events(
            console_errors=["console failed"],
            page_errors=["page failed"],
            failed_requests=["POST /api/save_card 500"],
            first_render_ms=123.4,
        )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["first_render_ms"], 123.4)
        self.assertEqual(summary["console_errors"], ["console failed"])
        self.assertEqual(summary["page_errors"], ["page failed"])
        self.assertEqual(summary["failed_requests"], ["POST /api/save_card 500"])


if __name__ == "__main__":
    unittest.main()
