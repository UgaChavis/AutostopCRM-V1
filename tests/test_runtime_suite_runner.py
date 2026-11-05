from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.runtime_suite_runner import (
    RuntimePlan,
    build_plan,
    complete_browser_coverage,
    iter_cases,
    load_selected,
    run_browser_groups,
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

    def test_parallel_browser_receipts_must_cover_every_group_once(self):
        groups = (("browser.one",), ("browser.two", "browser.three"))

        def receipt(group):
            return {
                "browser_run_id": "parallel",
                "selected_ids": list(group),
                "executed_ids": list(group),
                "successful": True,
                "exact_identity_coverage": True,
                "run": len(group),
                "skipped": [],
            }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "runtime-suite-first.json").write_text(json.dumps(receipt(groups[0])))
            self.assertFalse(complete_browser_coverage(groups, output=output, run_id="parallel"))
            (output / "runtime-suite-second.json").write_text(json.dumps(receipt(groups[1])))
            self.assertTrue(complete_browser_coverage(groups, output=output, run_id="parallel"))
            summary = json.loads((output / "browser-coverage-parallel.json").read_text())
            self.assertEqual(summary["planned_ids"], summary["passed_ids"])
            self.assertEqual(summary["groups"], 2)
            (output / "runtime-suite-duplicate.json").write_text(json.dumps(receipt(groups[1])))
            self.assertFalse(complete_browser_coverage(groups, output=output, run_id="parallel"))

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
        plan = RuntimePlan(
            ("native.test",), (("browser.one",), ("browser.two",), ("browser.three",))
        )
        failed, running = Mock(), Mock()
        failed.poll.return_value = 7
        running.poll.return_value = None
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0),
            patch(
                "tests.runtime_suite_runner.subprocess.Popen", side_effect=[failed, running]
            ) as run,
            patch("tests.runtime_suite_runner._stop_owned") as stop,
        ):
            self.assertEqual(run_plan(plan), 7)
            self.assertEqual(run.call_count, 2)
            stop.assert_called_once_with([running])
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=124),
            patch("tests.runtime_suite_runner.subprocess.Popen") as run,
        ):
            self.assertEqual(run_plan(plan), 124)
            run.assert_not_called()
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0),
            patch("tests.runtime_suite_runner.subprocess.Popen") as run,
            patch("tests.runtime_suite_runner.complete_browser_coverage", return_value=False),
        ):
            run.side_effect = lambda *args, **kwargs: Mock(poll=Mock(return_value=0))
            self.assertEqual(run_plan(plan), 1)

    def test_browser_timeout_propagates_and_stops_other_controller(self):
        timed_out, running = Mock(), Mock()
        timed_out.poll.return_value = 124
        running.poll.return_value = None
        plan = RuntimePlan(
            ("native.test",), (("browser.one",), ("browser.two",), ("browser.three",))
        )
        with (
            patch("tests.runtime_suite_runner.run_core", return_value=0),
            patch(
                "tests.runtime_suite_runner.subprocess.Popen", side_effect=[timed_out, running]
            ) as run,
            patch("tests.runtime_suite_runner._stop_owned") as stop,
            patch("tests.runtime_suite_runner.complete_browser_coverage") as proof,
        ):
            self.assertEqual(run_plan(plan), 124)
            self.assertEqual(run.call_count, 2)
            stop.assert_called_once_with([running])
            proof.assert_not_called()

    def test_spawn_error_stops_existing_controller_and_restores_handlers(self):
        running = Mock()
        previous = signal.getsignal(signal.SIGTERM)
        with (
            patch(
                "tests.runtime_suite_runner.subprocess.Popen",
                side_effect=[running, OSError("spawn")],
            ) as run,
            patch("tests.runtime_suite_runner._stop_owned") as stop,
        ):
            with self.assertRaisesRegex(OSError, "spawn"):
                run_browser_groups(
                    [[sys.executable, "-c", "pass"]] * 2,
                    root=Path.cwd(),
                    environment=dict(os.environ),
                )
            stop.assert_called_once_with([running])
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            self.assertFalse(Path(run.call_args_list[0].kwargs["env"]["TEMP"]).exists())

    def test_success_uses_parallel_coverage_for_all_groups_and_preserves_environment(self):
        plan = RuntimePlan(("native.test",), (("browser.one", "browser.two"),))
        with (
            patch.dict(
                os.environ, {"COVERAGE_FILE": "isolated-coverage", "PYTHONPATH": "existing"}
            ),
            patch("tests.runtime_suite_runner.run_core", return_value=0) as core,
            patch("tests.runtime_suite_runner.subprocess.Popen") as run,
            patch(
                "tests.runtime_suite_runner.complete_browser_coverage", return_value=True
            ) as proof,
        ):
            run.return_value = Mock(poll=Mock(return_value=0))
            self.assertEqual(run_plan(plan, root=Path.cwd()), 0)
            self.assertEqual(run.call_count, 1)
            second = run.call_args
            self.assertIn("--parallel-mode", second.args[0])
            self.assertEqual(
                second.args[0][-4:],
                ["tests.runtime_suite_runner", "--browser-group", "browser.one", "browser.two"],
            )
            self.assertEqual(second.kwargs["env"]["COVERAGE_FILE"], "isolated-coverage")
            self.assertEqual(second.kwargs["env"]["TEMP"], second.kwargs["env"]["TMP"])
            self.assertEqual(second.kwargs["env"]["TEMP"], second.kwargs["env"]["TMPDIR"])
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
            patch("tests.runtime_suite_runner.subprocess.Popen") as run,
            patch("tests.runtime_suite_runner.complete_browser_coverage", return_value=True),
        ):
            run.side_effect = lambda *args, **kwargs: Mock(poll=Mock(return_value=0))
            self.assertEqual(run_plan(plan), 0)
            self.assertEqual(2, run.call_count)
            self.assertEqual(core.call_args.kwargs["budget"], 1800)
            for call, budget in zip(run.call_args_list, (300, 300), strict=True):
                command = call.args[0]
                self.assertEqual("tests.runtime_process", command[2])
                self.assertEqual(str(budget), command[command.index("--timeout-seconds") + 1])
                self.assertEqual("--", command[command.index("--diagnostics-dir") + 2])

    def test_two_browser_controllers_overlap_with_private_temporaries(self):
        with tempfile.TemporaryDirectory(prefix="browser-parallel-test-") as name:
            root = Path(name)
            source = """
import json, os, pathlib, tempfile, time
root = pathlib.Path(ROOT)
(root / ('ready-' + INDEX)).write_text('ready')
deadline = time.monotonic() + 15
while not all((root / ('ready-' + str(i))).exists() for i in range(2)):
    if time.monotonic() >= deadline: raise RuntimeError('controllers did not overlap')
    time.sleep(0.02)
(root / ('result-' + INDEX)).write_text(json.dumps({
    'pid': os.getpid(), 'temporary': tempfile.gettempdir(),
    'temp': os.environ['TEMP'], 'tmp': os.environ['TMP'], 'tmpdir': os.environ['TMPDIR'],
}))
"""
            commands = [
                [sys.executable, "-c", f"ROOT={str(root)!r}; INDEX={str(index)!r}\n" + source]
                for index in range(2)
            ]
            environment = {**os.environ, "AUTOSTOP_REHEARSAL_RECEIPT_DIR": str(root / "receipts")}
            self.assertEqual(
                run_browser_groups(commands, root=Path.cwd(), environment=environment), 0
            )
            results = [json.loads((root / f"result-{index}").read_text()) for index in range(2)]
            self.assertEqual(len({item["pid"] for item in results}), 2)
            self.assertEqual(len({item["temporary"] for item in results}), 2)
            for item in results:
                self.assertEqual(item["temporary"], item["temp"])
                self.assertEqual(item["temp"], item["tmp"])
                self.assertEqual(item["temp"], item["tmpdir"])
                self.assertFalse(Path(item["temp"]).exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux owned-process check")
    def test_failure_stops_running_sibling_without_starting_next_group(self):
        with tempfile.TemporaryDirectory(prefix="browser-parallel-failure-") as name:
            root = Path(name)
            ready = root / "sibling-ready"
            later = root / "later-started"
            sleeper = f"from pathlib import Path; import os,time; Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
            failure = f"""
from pathlib import Path
import sys,time
ready=Path({str(ready)!r}); deadline=time.monotonic()+15
while not ready.exists():
    if time.monotonic()>=deadline: raise RuntimeError('sibling did not start')
    time.sleep(0.02)
sys.exit(7)
"""
            later_code = f"from pathlib import Path; Path({str(later)!r}).touch()"
            commands = [[sys.executable, "-c", code] for code in (failure, sleeper, later_code)]
            environment = {**os.environ, "AUTOSTOP_REHEARSAL_RECEIPT_DIR": str(root / "receipts")}
            self.assertEqual(
                run_browser_groups(commands, root=Path.cwd(), environment=environment), 7
            )
            self.assertFalse(later.exists())
            self.assertFalse(Path("/proc", ready.read_text()).exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux signal/owned-process check")
    def test_termination_stops_both_browser_controllers_and_children(self):
        with tempfile.TemporaryDirectory(prefix="browser-parallel-cancel-") as name:
            root = Path(name)
            child = """
from pathlib import Path
import os,time
Path(READY).write_text(str(os.getpid()))
time.sleep(60)
"""
            commands = [
                [sys.executable, "-c", f"READY={str(root / f'ready-{index}')!r}\n" + child]
                for index in range(2)
            ]
            coordinator = f"""
import os
from pathlib import Path
from tests.runtime_suite_runner import run_browser_groups
environment={{**os.environ, 'AUTOSTOP_REHEARSAL_RECEIPT_DIR': {str(root / "receipts")!r}}}
raise SystemExit(run_browser_groups({commands!r}, root=Path({str(Path.cwd())!r}), environment=environment))
"""
            process = subprocess.Popen([sys.executable, "-c", coordinator], cwd=Path.cwd())
            try:
                deadline = time.monotonic() + 20
                paths = [root / f"ready-{index}" for index in range(2)]
                while not all(path.exists() for path in paths) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(all(path.exists() for path in paths), "both children must start")
                pids = [path.read_text() for path in paths]
                process.terminate()
                self.assertEqual(process.wait(timeout=20), 128 + 15)
                self.assertTrue(all(not Path("/proc", pid).exists() for pid in pids))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)

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
        self.assertIn("timeout-minutes: ${{ matrix.suite == 'unit' && 60 || 30 }}", workflow)
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
