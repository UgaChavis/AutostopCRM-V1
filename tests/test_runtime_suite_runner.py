from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.runtime_suite_runner import (
    RuntimePlan,
    build_plan,
    complete_browser_coverage,
    iter_cases,
    load_selected,
    run_plan,
)


def case(module: str, name: str):
    cls = type(name, (unittest.TestCase,), {"__module__": module, "test_ok": lambda self: None})
    return cls("test_ok")


class RuntimeSuiteRunnerTests(unittest.TestCase):
    def test_browser_coverage_requires_current_exact_unskipped_receipts(self):
        groups = (("browser.one", "browser.two"),)
        good = {
            "browser_run_id": "current",
            "selected_ids": list(groups[0]),
            "executed_ids": list(groups[0]),
            "successful": True,
            "exact_identity_coverage": True,
            "run": 2,
            "skipped": [],
        }
        variants = (
            ({}, True),
            ({"browser_run_id": "stale"}, False),
            ({"executed_ids": ["browser.one"]}, False),
            ({"selected_ids": ["browser.unknown"]}, False),
            ({"skipped": [{"test": "browser.one", "reason": "missing browser"}]}, False),
            ({"successful": False}, False),
            ({"exact_identity_coverage": False}, False),
            ({"run": 1}, False),
        )
        for changes, expected in variants:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                (output / "runtime-suite-one.json").write_text(json.dumps({**good, **changes}))
                self.assertEqual(
                    complete_browser_coverage(groups, output=output, run_id="current"), expected
                )
                if expected:
                    receipt = json.loads((output / "browser-coverage-current.json").read_text())
                    self.assertEqual(receipt["planned_ids"], receipt["passed_ids"])
                    self.assertEqual(receipt["skipped"], [])
                    (output / "runtime-suite-two.json").write_text(json.dumps(good))
                    self.assertFalse(
                        complete_browser_coverage(groups, output=output, run_id="current")
                    )
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(
                complete_browser_coverage(groups, output=Path(temporary), run_id="current")
            )

    def test_module_level_platform_skip_keeps_real_reason_and_exact_discovered_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = "test_owned_platform_skip"
            (root / (name + ".py")).write_text(
                'import unittest\nraise unittest.SkipTest("synthetic platform restriction")\n',
                encoding="utf-8",
            )
            with patch.object(sys, "path", [str(root), *sys.path]):
                discovered = unittest.TestLoader().discover(str(root), pattern=name + ".py")
                identities = tuple(item.id() for item in iter_cases(discovered))
                selected = load_selected(identities)
                self.assertEqual(tuple(item.id() for item in iter_cases(selected)), identities)
                result = unittest.TestResult()
                selected.run(result)
            self.assertEqual(result.testsRun, 1)
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(result.skipped[0][1], "synthetic platform restriction")
            self.assertEqual(result.errors, [])

    def test_partition_preserves_every_selected_id_and_nested_order(self):
        native = case("native_cases", "Native")
        browser = case("test_example_browser", "Browser")
        suite = unittest.TestSuite([native, unittest.TestSuite([browser])])
        plan = build_plan(suite)
        self.assertEqual(plan.core_ids, (native.id(),))
        self.assertEqual(plan.browser_groups, ((browser.id(),),))
        self.assertEqual(plan.count, suite.countTestCases())

    def test_inherited_browser_setup_is_isolated_even_with_another_module_name(self):
        item = case("test_mobile_draft", "Mobile")

        def setup(cls):
            return None

        setup.__module__ = "browser_ui_support"
        type(item).setUpClass = classmethod(setup)
        plan = build_plan(unittest.TestSuite([item]))
        self.assertEqual(plan.core_ids, ())
        self.assertEqual(plan.browser_groups, ((item.id(),),))

    def test_actual_mobile_draft_runs_in_a_required_browser_group_on_both_os(self):
        from tests.test_mobile_client_search_draft import MobileClientSearchDraftTests

        item = MobileClientSearchDraftTests(
            "test_input_survives_rerender_and_empty_query_fetches_list"
        )
        plan = build_plan(unittest.TestSuite([item]))
        self.assertEqual(plan.core_ids, ())
        self.assertEqual(plan.browser_groups, ((item.id(),),))

    def test_child_failure_propagates_without_retry_or_later_groups(self):
        plan = RuntimePlan(("native.test",), (("browser.one",), ("browser.two",)))
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0),
            patch("tests.runtime_suite_runner.subprocess.run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 7)
            self.assertEqual(run_plan(plan), 7)
            self.assertEqual(run.call_count, 1)
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=124),
            patch("tests.runtime_suite_runner.subprocess.run") as run,
        ):
            self.assertEqual(run_plan(plan), 124)
            run.assert_not_called()
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0),
            patch("tests.runtime_suite_runner.subprocess.run") as run,
            patch("tests.runtime_suite_runner.complete_browser_coverage", return_value=False),
        ):
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertEqual(run_plan(plan), 1)

    def test_success_uses_parallel_coverage_for_all_groups_and_preserves_environment(self):
        plan = RuntimePlan(("native.test",), (("browser.one", "browser.two"),))
        with (
            patch.dict(
                os.environ, {"COVERAGE_FILE": "isolated-coverage", "PYTHONPATH": "existing"}
            ),
            patch("tests.runtime_suite_runner.run_core", return_value=0) as core,
            patch("tests.runtime_suite_runner.subprocess.run") as run,
            patch(
                "tests.runtime_suite_runner.complete_browser_coverage", return_value=True
            ) as proof,
        ):
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertEqual(run_plan(plan, root=Path.cwd()), 0)
            self.assertEqual(run.call_count, 1)
            second = run.call_args
            self.assertIn("--parallel-mode", second.args[0])
            self.assertEqual(
                second.args[0][-4:],
                ["tests.runtime_suite_runner", "--browser-group", "browser.one", "browser.two"],
            )
            self.assertEqual(second.kwargs["env"]["COVERAGE_FILE"], "isolated-coverage")
            self.assertEqual(core.call_args.kwargs["budget"], 1800)
            self.assertEqual(core.call_args.kwargs["environment"]["PYTHONUTF8"], "1")
            self.assertEqual(proof.call_args.args[0], plan.browser_groups)
            self.assertEqual(
                proof.call_args.kwargs["run_id"],
                second.kwargs["env"]["AUTOSTOP_RUNTIME_BROWSER_RUN_ID"],
            )
            self.assertEqual(
                second.kwargs["env"]["PYTHONPATH"],
                os.pathsep.join([str(Path.cwd() / "src"), str(Path.cwd() / "tests"), "existing"]),
            )

    def test_external_controller_has_core_and_browser_budgets_without_retries(self):
        plan = RuntimePlan(("native.test",), (("browser.one",), ("browser.two",)))
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0) as core,
            patch("tests.runtime_suite_runner.subprocess.run") as run,
            patch("tests.runtime_suite_runner.complete_browser_coverage", return_value=True),
        ):
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertEqual(run_plan(plan), 0)
            self.assertEqual(2, run.call_count)
            self.assertEqual(core.call_args.kwargs["budget"], 1800)
            for call, budget in zip(run.call_args_list, (300, 300), strict=True):
                command = call.args[0]
                self.assertEqual("tests.runtime_process", command[2])
                self.assertEqual(str(budget), command[command.index("--timeout-seconds") + 1])
                self.assertEqual("--", command[command.index("--label") + 2])

    def test_ci_entrypoints_keep_required_budgets_and_failure_diagnostics(self):
        root = Path(__file__).resolve().parents[1]
        profile = (root / "scripts/run_checks.ps1").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        bounded_local = dict(
            re.findall(
                r'Invoke-Python -StepName "([^"]+)" -Arguments @\([^)]*\) -TimeoutSeconds (\d+)',
                profile,
                re.DOTALL,
            )
        )
        for name, seconds in (
            ("Release branch coverage", "1800"),
            ("Mandatory core browser smoke", "300"),
        ):
            self.assertEqual(seconds, bounded_local.get(name), name)
        self.assertRegex(
            profile,
            r'Invoke-Python -StepName "Isolated runtime branch coverage" -Arguments @\(\s*'
            r'"-m", "tests.runtime_suite_runner"\s*\)',
        )
        self.assertNotIn("Isolated Windows runtime coverage", profile)
        self.assertIn("python -m tests.runtime_suite_runner", workflow)
        self.assertNotIn("--label core -- python -m coverage run -m unittest discover", workflow)
        for name, seconds in (
            ("Release backup and restore branch coverage", "1800"),
            ("Mandatory core browser smoke", "300"),
            ("Optional full browser smoke", "300"),
        ):
            pattern = rf"^      - name: {re.escape(name)}\n.*?(?=^      - name: |\Z)"
            matches = re.findall(pattern, workflow, re.MULTILINE | re.DOTALL)
            self.assertEqual(1, len(matches), name)
            self.assertIn(f"tests.runtime_process --timeout-seconds {seconds}", matches[0], name)
        self.assertIn("output/runtime-process/browser-coverage-*.json", workflow)
        self.assertIn("path: output/runtime-process/", workflow)
        self.assertRegex(workflow, r"name: runtime-process-.*github\.run_attempt")
        diagnostics_position = workflow.index("- name: Upload runtime process diagnostics")
        self.assertGreater(
            diagnostics_position, workflow.index("- name: Optional full browser smoke")
        )
        self.assertIn("always() && matrix.suite != 'static'", workflow[diagnostics_position:])

    def test_primary_failure_is_visible_before_cleanup_times_out(self):
        with tempfile.TemporaryDirectory(prefix="runtime-primary-failure-") as name:
            probe = """
import time, unittest
from tests.runtime_test_result import ImmediateTracebackResult
class Failing(unittest.TestCase):
    def test_primary(self):
        self.addCleanup(time.sleep, 30)
        self.fail('SYNTHETIC_PRIMARY_FAILURE_BEFORE_NATIVE_CLEANUP')
unittest.TextTestRunner(resultclass=ImmediateTracebackResult, verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(Failing)
)
"""
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.runtime_process",
                    "--timeout-seconds",
                    "3",
                    "--diagnostics-dir",
                    name,
                    "--",
                    sys.executable,
                    "-c",
                    probe,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 124, completed.stdout + completed.stderr)
            self.assertIn("Immediate failure detail:", completed.stderr)
            self.assertIn(
                "AssertionError: SYNTHETIC_PRIMARY_FAILURE_BEFORE_NATIVE_CLEANUP",
                completed.stderr,
            )
