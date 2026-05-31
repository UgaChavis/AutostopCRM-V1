from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_workflows.py"


def load_perf_workflows() -> ModuleType:
    spec = importlib.util.spec_from_file_location("perf_workflows_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load perf_workflows.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PerfWorkflowsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_perf_workflows()

    def test_script_exposes_required_cli_flags_and_safe_write_gate(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        for flag in (
            "--base-url",
            "--iterations",
            "--card-id",
            "--operator-token",
            "--state-file",
            "--allow-write-workflows",
            "--browser-timeout-seconds",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, script)
        self.assertIn("Write workflow skipped.", script)
        self.assertIn("external_write_workflows_enabled", script)
        self.assertIn('reconfigure(encoding="utf-8")', script)
        self.assertIn("autostop-perf", script)
        self.assertIn("open_repair_order_salary_override", script)
        self.assertIn("open_employee_salary_ledger", script)
        self.assertIn("open_employee_salary_reconciliation_print", script)
        self.assertIn("run_browser_workflows_with_timeout", script)
        self.assertIn("asyncio.wait_for(run_browser_workflows(args)", script)
        self.assertIn("browser_failure_result", script)
        self.assertIn("PLAYWRIGHT_CLOSE_TIMEOUT_SECONDS", script)
        self.assertIn("await close_with_timeout(context.close())", script)
        self.assertIn("async def force_close_open_modals(", script)
        self.assertIn("await force_close_open_modals(page)", script)
        self.assertIn("async def close_modal_best_effort(", script)
        self.assertIn("async def goto_with_retry(", script)
        self.assertIn("ERR_CONNECTION_TIMED_OUT", script)
        self.assertIn(
            "await goto_with_retry(\n"
            "                            page,\n"
            '                            f"{runtime.base_url}/employee_salary_reconciliation_print?{query}"',
            script,
        )
        self.assertNotIn("print_page = await context.new_page()", script)
        self.assertIn("salary_override_card_id", self.module.BrowserRuntime.__dataclass_fields__)
        self.assertIn("employee_id", self.module.BrowserRuntime.__dataclass_fields__)

    def test_summarize_samples_returns_required_report_fields(self) -> None:
        summary = self.module.summarize_samples(
            [
                {
                    "duration_ms": 100,
                    "request_count": 2,
                    "payload_bytes": 1200,
                    "server_timing": ["app;dur=10.0"],
                    "ui_perf_entries": [{"name": "openCardWorkspace", "duration_ms": 90}],
                },
                {
                    "duration_ms": 300,
                    "request_count": 4,
                    "payload_bytes": 2400,
                    "server_timing": ["app;dur=20.0"],
                    "ui_perf_entries": [{"name": "api:/api/get_card", "duration_ms": 200}],
                },
            ],
            scenario="open_card",
        )

        self.assertEqual(summary["scenario"], "open_card")
        self.assertEqual(summary["avg_ms"], 200.0)
        self.assertEqual(summary["min_ms"], 100.0)
        self.assertEqual(summary["max_ms"], 300.0)
        self.assertEqual(summary["p95_ms"], 300.0)
        self.assertEqual(summary["request_count"], 3)
        self.assertEqual(summary["payload_bytes"], 1800)
        self.assertEqual(summary["server_timing"][-1], "app;dur=20.0")
        self.assertEqual(summary["ui_perf_entries"][-1]["name"], "api:/api/get_card")

    def test_ranked_findings_maps_slow_rows_to_actionable_areas(self) -> None:
        findings = self.module.ranked_findings(
            [
                {"scenario": "open_card", "avg_ms": 900},
                {"scenario": "backend.update_card", "avg_ms": 1600},
                {"scenario": "open_modal.clients", "avg_ms": 950},
            ],
            limit=3,
        )

        self.assertEqual(findings[0]["scenario"], "backend.update_card")
        self.assertIn("storage", findings[0]["area"])
        self.assertTrue(findings[0]["files"])
        self.assertEqual(len(findings), 3)

    def test_salary_workflow_scenarios_have_performance_targets(self) -> None:
        self.assertGreater(self.module.scenario_target("open_repair_order_salary_override"), 0)
        self.assertGreater(self.module.scenario_target("open_employee_salary_ledger"), 0)
        self.assertGreater(
            self.module.scenario_target("open_employee_salary_reconciliation_print"), 0
        )

    def test_modal_workflows_wait_for_loaded_content(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        ready_selectors = {
            scenario: ready_selector
            for scenario, _button, _modal, ready_selector in self.module.MODAL_WORKFLOWS
        }

        self.assertIn("#cashboxesList [data-cashbox-id]", ready_selectors["open_modal.cashboxes"])
        self.assertIn("#cashboxStats .cashbox-stat-grid", ready_selectors["open_modal.cashboxes"])
        self.assertNotIn("#cashboxTransactions", ready_selectors["open_modal.cashboxes"])
        self.assertIn(".archive-row", ready_selectors["open_modal.archive"])
        self.assertNotEqual("#cashboxJournalButton", ready_selectors["open_modal.cashboxes"])
        self.assertNotEqual("#archiveSearchInput", ready_selectors["open_modal.archive"])
        self.assertNotIn('wait_for_load_state("networkidle"', script)
        self.assertNotIn("modal.textContent", script)
        self.assertIn("root.querySelectorAll(readySelector)", script)
        self.assertIn("async def modal_ready_diagnostics(", script)
        self.assertIn("modal did not become ready", script)

    def test_failed_request_formatter_accepts_playwright_string_failure(self) -> None:
        class Request:
            method = "POST"
            url = "http://127.0.0.1:42731/api/update_card"
            failure = "net::ERR_CONNECTION_RESET"

        self.assertEqual(
            self.module.format_failed_request(Request()),
            "POST http://127.0.0.1:42731/api/update_card net::ERR_CONNECTION_RESET",
        )

    def test_failed_workflow_rows_are_reported_as_findings_and_violations(self) -> None:
        row = self.module.failed_row(
            "open_employee_salary_reconciliation_print",
            TimeoutError("navigation timed out"),
            samples=[
                {
                    "duration_ms": 30000,
                    "request_count": 0,
                    "payload_bytes": 0,
                    "server_timing": [],
                    "ui_perf_entries": [],
                    "error": "navigation timed out",
                }
            ],
        )
        args = SimpleNamespace(
            max_open_card_ms=0,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=0,
        )

        findings = self.module.ranked_findings([row])
        violations = self.module.evaluate_thresholds([row], args)

        self.assertTrue(row["failed"])
        self.assertEqual(findings[0]["area"], "workflow reliability")
        self.assertEqual(violations[0]["metric"], "workflow_error")

    def test_browser_timeout_failure_becomes_reportable_workflow_error(self) -> None:
        args = SimpleNamespace(
            base_url="http://127.0.0.1:42999",
            local_temp_server=True,
            browser_timeout_seconds=1,
            max_open_card_ms=0,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=0,
        )

        result = self.module.browser_failure_result(args, TimeoutError("audit timed out"))
        row = result["rows"][0]
        violations = self.module.evaluate_thresholds(result["rows"], args)

        self.assertTrue(result["local_temp_server"])
        self.assertEqual(row["scenario"], "browser_workflows")
        self.assertTrue(row["failed"])
        self.assertEqual(violations[0]["metric"], "workflow_error")


if __name__ == "__main__":
    unittest.main()
