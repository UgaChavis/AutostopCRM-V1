from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_workflows.py"
QUALITY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "quality.yml"


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

    def test_script_exposes_required_cli_flags_and_local_only_write_gate(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        for flag in (
            "--iterations",
            "--warmup-iterations",
            "--card-id",
            "--state-file",
            "--synthetic-state-profile",
            "--stage1-only",
            "--max-storage-write-ms",
            "--max-revision-server-ms",
            "--max-get-card-direct-ms",
            "--max-list-cashboxes-ms",
            "--max-feed-read-ms",
            "--max-feed-replay-ms",
            "--browser-timeout-seconds",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, script)
        for removed_flag in (
            "--base-url",
            "--operator-token",
            "--operator-username",
            "--operator-password",
        ):
            with self.subTest(removed_flag=removed_flag):
                self.assertNotIn(removed_flag, script)
        self.assertIn("Write workflow skipped.", script)
        self.assertNotIn("--allow-write-workflows", script)
        self.assertNotIn("external_write_workflows_enabled", script)
        self.assertIn('reconfigure(encoding="utf-8")', script)
        self.assertIn("autostop-perf", script)
        self.assertIn('"#cardDescriptionEditor", f"Perf workflow description save {index}"', script)
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
        self.assertIn("await goto_with_retry(page, runtime.browser_url", script)
        self.assertIn("runtime.authenticated_url(", script)
        self.assertIn('f"/employee_salary_reconciliation_print?{query}"', script)
        self.assertNotIn(
            'f"{runtime.base_url}/employee_salary_reconciliation_print?{query}"',
            script,
        )
        navigation_index = script.index("await goto_with_retry(page, runtime.browser_url")
        login_index = script.index("await login_browser(page)", navigation_index)
        board_index = script.index('await page.wait_for_selector("#board"', login_index)
        card_index = script.index("await page.wait_for_selector(card_selector", board_index)
        self.assertLess(navigation_index, login_index)
        self.assertLess(login_index, board_index)
        self.assertLess(board_index, card_index)
        self.assertNotIn("print_page = await context.new_page()", script)
        self.assertIn("browser_url", self.module.BrowserRuntime.__dataclass_fields__)
        self.assertIn("salary_override_card_id", self.module.BrowserRuntime.__dataclass_fields__)
        self.assertIn("employee_id", self.module.BrowserRuntime.__dataclass_fields__)

    def test_browser_write_workflows_require_local_temp_runtime(self) -> None:
        remote_runtime = self.module.BrowserRuntime(
            base_url="https://crm.example",
            browser_url="https://crm.example",
            card_id="remote-card",
            local_temp_server=False,
        )
        local_runtime = self.module.BrowserRuntime(
            base_url="http://127.0.0.1:42999",
            browser_url="http://127.0.0.1:42999/?access_token=temp-token",
            card_id="temp-card",
            local_temp_server=True,
            runtime=object(),
        )
        unowned_runtime = self.module.BrowserRuntime(
            base_url="http://127.0.0.1:42999",
            browser_url="http://127.0.0.1:42999/?access_token=unowned-token",
            card_id="unowned-card",
            local_temp_server=True,
        )

        self.assertFalse(self.module.browser_write_workflows_enabled(remote_runtime))
        self.assertTrue(self.module.browser_write_workflows_enabled(local_runtime))
        self.assertFalse(self.module.browser_write_workflows_enabled(unowned_runtime))
        with self.assertRaisesRegex(ValueError, "process-owned local temp runtime"):
            unowned_runtime.authenticated_url("/employee_salary_reconciliation_print")

    def test_browser_runtime_preserves_private_temp_navigation_contract(self) -> None:
        secret = "BROWSER-NAVIGATION-SECRET"
        base_url = "http://127.0.0.1:42999"
        temp_runtime = SimpleNamespace(
            base_url=base_url,
            browser_url=f"{base_url}/?access_token={secret}",
            card_id="temp-card",
            employee_id="employee-id",
            payroll_card_id="payroll-card-id",
            salary_override_card_id="salary-card-id",
            api_token=secret,
            authenticated_url=lambda path: (
                f"{base_url}{path}{'&' if '?' in path else '?'}access_token={secret}"
            ),
            close=lambda: None,
        )
        smoke_module = ModuleType("browser_smoke")
        smoke_module.start_temp_runtime = lambda *, start_port: temp_runtime
        args = SimpleNamespace(local_temp_server=True, start_port=42999, card_id="")

        with patch.dict(sys.modules, {"browser_smoke": smoke_module}):
            runtime = self.module.start_browser_runtime(args)

        self.assertEqual(runtime.base_url, base_url)
        self.assertEqual(runtime.browser_url, temp_runtime.browser_url)
        self.assertEqual(
            runtime.authenticated_url(
                "/employee_salary_reconciliation_print?employee_id=employee-id"
            ),
            f"{base_url}/employee_salary_reconciliation_print"
            f"?employee_id=employee-id&access_token={secret}",
        )
        self.assertNotIn(secret, repr(runtime))
        self.assertNotIn("api_token", repr(runtime))

    def test_perf_login_uses_success_only_support_helper(self) -> None:
        page = object()
        helper = AsyncMock()
        support_module = ModuleType("browser_smoke_support")
        support_module._login_successfully = helper

        with patch.dict(sys.modules, {"browser_smoke_support": support_module}):
            asyncio.run(self.module.login_browser(page))

        helper.assert_awaited_once_with(page)
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("from browser_smoke_support import _login_successfully", script)
        self.assertNotIn("from browser_smoke import _login", script)

    def test_remote_browser_runtime_fails_closed_before_network_access(self) -> None:
        args = SimpleNamespace(
            local_temp_server=False,
            base_url="https://crm.example",
            card_id="",
            start_port=42999,
        )

        with self.assertRaisesRegex(ValueError, "process-owned --local-temp-server"):
            self.module.start_browser_runtime(args)

        self.assertFalse(hasattr(self.module, "first_card_id_from_base_url"))
        self.assertFalse(hasattr(self.module, "json_request"))
        self.assertFalse(hasattr(self.module, "_urlopen_no_redirect"))

    def test_quality_workflow_enforces_change_feed_performance_budgets(self) -> None:
        workflow = QUALITY_WORKFLOW_PATH.read_text(encoding="utf-8")
        stage1 = workflow.split("- name: Stage 1 production-scale performance gates", 1)[1]
        stage1 = stage1.split("- name: Upload Stage 1 performance artifact", 1)[0]

        self.assertIn("--max-feed-read-ms 50", stage1)
        self.assertIn("--max-feed-replay-ms 20", stage1)
        self.assertIn("--max-list-cashboxes-ms 50", stage1)
        self.assertEqual(1, stage1.count("--max-feed-read-ms"))
        self.assertEqual(1, stage1.count("--max-feed-replay-ms"))

    def test_summarize_samples_returns_required_report_fields(self) -> None:
        summary = self.module.summarize_samples(
            [
                {
                    "duration_ms": 100,
                    "request_count": 2,
                    "payload_bytes": 1200,
                    "server_timing": ["app;dur=10.0;desc=private-customer"],
                    "phase_timings": {"serialize": 60.0, "write": 20.0},
                    "ui_perf_entries": [
                        {
                            "name": "openCardWorkspace",
                            "duration_ms": 90,
                            "detail": {
                                "card_id": "card-secret",
                                "payload": "private-customer",
                                "error": "TypeError",
                            },
                        }
                    ],
                },
                {
                    "duration_ms": 300,
                    "request_count": 4,
                    "payload_bytes": 2400,
                    "server_timing": ["app;dur=20.0"],
                    "phase_timings": {"serialize": 180.0, "write": 40.0},
                    "ui_perf_entries": [{"name": "api:/api/get_card", "duration_ms": 200}],
                },
            ],
            scenario="open_card",
        )

        self.assertEqual(summary["scenario"], "open_card")
        self.assertEqual(summary["avg_ms"], 200.0)
        self.assertEqual(summary["min_ms"], 100.0)
        self.assertEqual(summary["max_ms"], 300.0)
        self.assertEqual(summary["p50_ms"], 100.0)
        self.assertEqual(summary["p95_ms"], 300.0)
        self.assertEqual(summary["request_count"], 3)
        self.assertEqual(summary["payload_bytes"], 1800)
        self.assertEqual(summary["server_timing"][-1], "app;dur=20.0")
        self.assertEqual(len(summary["server_timing"]), 2)
        self.assertEqual(summary["phase_timings"]["serialize"]["p95_ms"], 180.0)
        self.assertEqual(summary["ui_perf_entries"][-1]["name"], "api:/api/get_card")
        self.assertEqual(summary["ui_perf_entries"][0]["detail"], {"error": "TypeError"})
        self.assertNotIn("private-customer", json.dumps(summary))
        self.assertNotIn("card-secret", json.dumps(summary))

    def test_summarize_and_thresholds_tolerate_invalid_numeric_values(self) -> None:
        summary = self.module.summarize_samples(
            [
                {"duration_ms": "bad", "request_count": True, "payload_bytes": "bad"},
                {"duration_ms": "125.5", "request_count": "4", "payload_bytes": "2048"},
            ],
            scenario="open_card",
        )
        args = SimpleNamespace(
            max_open_card_ms=130,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=0,
        )

        violations = self.module.evaluate_thresholds(
            [{"scenario": "open_card", "avg_ms": "bad"}, summary],
            args,
        )

        self.assertEqual(summary["avg_ms"], 62.8)
        self.assertEqual(summary["p95_ms"], 125.5)
        self.assertEqual(summary["request_count"], 2)
        self.assertEqual(summary["payload_bytes"], 1024)
        self.assertEqual(violations, [])

    def test_thresholds_use_p95_instead_of_average(self) -> None:
        args = SimpleNamespace(
            max_open_card_ms=100,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=0,
        )

        violations = self.module.evaluate_thresholds(
            [{"scenario": "open_card", "avg_ms": 80.0, "p95_ms": 120.0}],
            args,
        )

        self.assertEqual(
            violations,
            [
                {
                    "scenario": "open_card",
                    "metric": "p95_ms",
                    "actual": 120.0,
                    "max": 100.0,
                }
            ],
        )

    def test_stage1_thresholds_are_independent(self) -> None:
        args = SimpleNamespace(
            max_open_card_ms=0,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=600,
            max_storage_write_ms=550,
            max_revision_server_ms=20,
            max_get_card_direct_ms=20,
            max_list_cashboxes_ms=50,
            max_feed_read_ms=15,
            max_feed_replay_ms=10,
        )
        rows = [
            {"scenario": "backend.update_card", "p95_ms": 601},
            {"scenario": "storage.write_cached_bundle", "p95_ms": 551},
            {"scenario": "backend.get_board_revision_cached", "p95_ms": 21},
            {"scenario": "backend.get_card", "p95_ms": 19},
            {"scenario": "backend.list_cashboxes", "p95_ms": 51},
            {"scenario": "change_feed.read_page", "p95_ms": 16},
            {"scenario": "change_feed.replay_page", "p95_ms": 9},
        ]

        violations = self.module.evaluate_thresholds(rows, args)

        self.assertEqual(
            [item["scenario"] for item in violations],
            [
                "backend.update_card",
                "storage.write_cached_bundle",
                "backend.get_board_revision_cached",
                "backend.list_cashboxes",
                "change_feed.read_page",
            ],
        )

    def test_synthetic_profile_matches_current_production_scale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            metadata = self.module.write_synthetic_current_production_state(state_file)

            self.assertGreaterEqual(metadata["state_bytes"], self.module.SYNTHETIC_STATE_MIN_BYTES)
            self.assertEqual(metadata["profile"], "current-production")
            self.assertEqual(metadata["counts"]["cards"], 620)
            self.assertEqual(metadata["counts"]["clients"], 4000)
            self.assertEqual(metadata["counts"]["events"], 5000)
            self.assertEqual(metadata["counts"]["cash_transactions"], 1500)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(len(state["cards"]), 620)
            self.assertEqual(len(state["clients"]), 4000)
            self.assertEqual(len(state["events"]), 5000)
            self.assertEqual(len(state["cash_transactions"]), 1500)

    def test_response_payload_bytes_ignores_invalid_values(self) -> None:
        self.assertEqual(
            self.module._response_payload_bytes(
                [
                    {"bytes": "1024"},
                    {"bytes": float("inf")},
                    {"bytes": "10.5"},
                    {"bytes": True},
                    {"bytes": "512"},
                    {"bytes": 1e308},
                ]
            ),
            1_000_001_536,
        )

    def test_cli_numeric_bounds_reject_huge_values(self) -> None:
        self.assertEqual(self.module._bounded_iterations(1e308), 100)
        self.assertEqual(self.module._bounded_iterations("bad"), 3)
        self.assertEqual(self.module._bounded_port(1e308, default=42831), 65535)
        self.assertEqual(self.module._bounded_port(0, default=42831), 42831)
        self.assertEqual(
            self.module._bounded_float(1e308, default=0.0, maximum=3_600_000.0),
            3_600_000.0,
        )
        self.assertEqual(
            self.module._bounded_float("bad", default=5.0, maximum=3_600_000.0),
            5.0,
        )

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
        self.assertNotIn("async def modal_ready_diagnostics(", script)
        self.assertIn("modal did not become ready", script)

    def test_failed_request_formatter_accepts_playwright_string_failure(self) -> None:
        class Request:
            method = "POST"
            url = (
                "http://127.0.0.1:42731/api/update_card"
                "?access_token=browser-secret&card_id=card-secret#fragment"
            )
            failure = "Authorization: Bearer bearer-secret"

        self.assertEqual(
            self.module.format_failed_request(Request()),
            "POST /api/update_card request_failed",
        )

    def test_serialized_report_omits_record_ids_payloads_paths_and_secrets(self) -> None:
        request = SimpleNamespace(
            method="GET",
            url="https://private-user:private-password@crm.example/api/get_card"
            "?card_id=card-secret&access_token=browser-secret",
            failure="Authorization: Bearer bearer-secret",
        )
        report = {
            "scenario": "open_card",
            "base_url": (
                "http://private-user:private-password@127.0.0.1:42731/"
                "?access_token=browser-secret#fragment"
            ),
            "browser_url": "http://127.0.0.1:42731/?access_token=navigation-secret",
            "card_id": "card-secret",
            "id": "record-secret",
            "entity_ids": ["entity-secret"],
            "operator_token": "plain-token-secret",
            "authorization": "plain-authorization-secret",
            "cookie": "plain-cookie-secret",
            "state_file": "C:/private/customer-state.json",
            "rows": [
                {
                    "scenario": "open_card",
                    "p95_ms": 125.0,
                    "ui_perf_entries": [
                        {
                            "name": "openCardWorkspace",
                            "detail": {
                                "employee_id": "employee-secret",
                                "payload": {"customer": "private-customer"},
                            },
                        }
                    ],
                }
            ],
            "events": {
                "console_errors": ["private-console-customer"],
                "page_errors": ["C:/private/page-error.py"],
                "failed_requests": [self.module.format_failed_request(request)],
            },
        }

        encoded = self.module.serialize_report(report)
        decoded = json.loads(encoded)

        self.assertNotIn("base_url", decoded)
        self.assertEqual(decoded["rows"][0]["p95_ms"], 125.0)
        self.assertNotIn("card_id", encoded)
        self.assertNotIn("employee_id", encoded)
        self.assertNotIn("entity_ids", encoded)
        self.assertNotIn('"id"', encoded)
        self.assertNotIn("state_file", encoded)
        self.assertNotIn("payload", encoded)
        for secret in (
            "browser-secret",
            "navigation-secret",
            "card-secret",
            "employee-secret",
            "entity-secret",
            "record-secret",
            "private-customer",
            "bearer-secret",
            "customer-state.json",
            "private-user",
            "private-password",
            "plain-token-secret",
            "plain-authorization-secret",
            "plain-cookie-secret",
            "private-console-customer",
            "page-error.py",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, encoded)
        self.assertIn("GET /api/get_card request_failed", encoded)

    def test_browser_failure_and_events_report_only_safe_categories(self) -> None:
        args = SimpleNamespace(
            base_url="https://private:password@crm.example/?access_token=token-secret",
            local_temp_server=False,
        )
        result = self.module.browser_failure_result(
            args,
            RuntimeError("C:/private/customer-state.json private-customer card-secret"),
        )
        events = self.module.browser_event_report(
            ["private console customer"],
            ["C:/private/page-error.js"],
            ["GET /api/get_card net::ERR_CONNECTION_RESET"],
        )
        result["events"] = events
        encoded = self.module.serialize_report(result)

        self.assertEqual(result["rows"][0]["error"], "workflow_failed")
        self.assertEqual(events["console_error_count"], 1)
        self.assertEqual(events["page_error_count"], 1)
        self.assertEqual(events["failed_request_count"], 1)
        self.assertEqual(events["failed_requests"], ["GET /api/get_card net::ERR_CONNECTION_RESET"])
        for secret in (
            "private console customer",
            "page-error.js",
            "customer-state.json",
            "private-customer",
            "card-secret",
            "token-secret",
            "password@",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, encoded)

    def test_browser_event_errors_fail_the_gate_without_leaking_details(self) -> None:
        sentinels = (
            "private console customer",
            "C:/private/page-error.js",
            "GET /private?access_token=token-secret request_failed",
        )
        browser_result = {
            "rows": [],
            "events": self.module.browser_event_report(
                [sentinels[0]],
                [sentinels[1]],
                [sentinels[2]],
            ),
        }
        with (
            patch.object(sys, "argv", ["perf_workflows.py"]),
            patch.object(
                self.module,
                "run_browser_workflows_with_timeout",
                new=AsyncMock(return_value=browser_result),
            ),
            patch("builtins.print") as print_mock,
        ):
            exit_code = self.module.main()

        encoded = str(print_mock.call_args.args[0])
        decoded = json.loads(encoded)
        self.assertEqual(exit_code, 1)
        self.assertEqual(decoded["threshold_status"], "failed")
        self.assertEqual(
            decoded["violations"],
            [
                {
                    "scenario": "browser",
                    "metric": metric,
                    "actual": 1,
                    "max": 0,
                }
                for metric in (
                    "console_error_count",
                    "page_error_count",
                    "failed_request_count",
                )
            ],
        )
        for sentinel in sentinels:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, encoded)

    def test_browser_event_report_ignores_benign_get_abort(self) -> None:
        events = self.module.browser_event_report(
            [],
            [],
            [
                "GET /api/get_cashbox net::ERR_ABORTED",
                "POST /api/update_card net::ERR_ABORTED",
            ],
        )

        self.assertEqual(events["failed_request_count"], 1)
        self.assertEqual(events["failed_requests"], ["POST /api/update_card net::ERR_ABORTED"])

    def test_state_benchmark_exception_becomes_safe_failed_report(self) -> None:
        private_error = RuntimeError("C:/private/customer-state.json private-customer")
        with (
            patch.object(sys, "argv", ["perf_workflows.py", "--skip-browser", "--state-file", "x"]),
            patch.object(self.module, "run_state_file_benchmark", side_effect=private_error),
            patch("builtins.print") as print_mock,
        ):
            exit_code = self.module.main()

        encoded = str(print_mock.call_args.args[0])
        decoded = json.loads(encoded)
        row = decoded["state_file_benchmark"]["rows"][0]
        self.assertEqual(exit_code, 1)
        self.assertEqual(row["scenario"], "state_file_benchmark")
        self.assertEqual(row["error"], "workflow_failed")
        self.assertNotIn("customer-state.json", encoded)
        self.assertNotIn("private-customer", encoded)

    def test_removed_secret_flags_fail_without_echoing_values(self) -> None:
        stderr = io.StringIO()
        sentinels = (
            "private-user:private-password@example.invalid",
            "RECORD-SECRET",
            "TOKEN-SECRET",
            "OPERATOR-TOKEN-SECRET",
            "OPERATOR-USER-SECRET",
            "OPERATOR-PASSWORD-SECRET",
        )
        legacy_url = f"https://{sentinels[0]}/api/card/{sentinels[1]}?access_token={sentinels[2]}"
        with (
            patch.object(
                sys,
                "argv",
                [
                    "perf_workflows.py",
                    "--base-url",
                    legacy_url,
                    "--operator-token",
                    sentinels[3],
                    "--operator-username",
                    sentinels[4],
                    "--operator-password",
                    sentinels[5],
                ],
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            self.module.main()

        error_text = stderr.getvalue()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid command line arguments", error_text)
        for sentinel in sentinels:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, error_text)

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

    def test_ui_perf_errors_are_reported_as_findings_and_violations(self) -> None:
        row = {
            "scenario": "move_card",
            "avg_ms": 90,
            "ui_perf_entries": [
                {"name": "api:/api/get_cashbox", "detail": {"error": "AbortError"}},
                {"name": "api:/api/move_card", "detail": {"error": "TypeError"}},
            ],
        }
        args = SimpleNamespace(
            max_open_card_ms=0,
            max_save_card_ms=0,
            max_move_card_ms=0,
            max_open_modal_ms=0,
            max_backend_write_ms=0,
        )

        findings = self.module.ranked_findings([row])
        violations = self.module.evaluate_thresholds([row], args)

        self.assertEqual(findings[0]["area"], "workflow reliability")
        self.assertIn("api:/api/move_card: TypeError", findings[0]["error"])
        self.assertEqual(violations[0]["metric"], "ui_perf_error")
        self.assertIn("api:/api/move_card: TypeError", violations[0]["actual"])

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

    def test_browser_timeout_seconds_rejects_invalid_values(self) -> None:
        self.assertEqual(
            self.module.browser_timeout_seconds(
                SimpleNamespace(browser_timeout_seconds=float("inf"))
            ),
            self.module.DEFAULT_BROWSER_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            self.module.browser_timeout_seconds(SimpleNamespace(browser_timeout_seconds="bad")),
            self.module.DEFAULT_BROWSER_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            self.module.browser_timeout_seconds(SimpleNamespace(browser_timeout_seconds=0)),
            30.0,
        )
        self.assertEqual(
            self.module.browser_timeout_seconds(SimpleNamespace(browser_timeout_seconds=1e308)),
            3600.0,
        )

    def test_response_size_sanitizes_non_finite_numbers_and_preserves_finite_float(
        self,
    ) -> None:
        payload = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "ratio": 1.25,
            "nested": [float("-inf")],
        }

        encoded = self.module._json_dumps(payload, separators=(",", ":"))

        self.assertEqual(
            json.loads(encoded),
            {"nan": None, "infinity": None, "ratio": 1.25, "nested": [None]},
        )
        self.assertEqual(self.module.response_size(payload), len(encoded.encode("utf-8")))
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = self.module._json_dumps(payload, separators=(",", ":"))
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)


if __name__ == "__main__":
    unittest.main()
