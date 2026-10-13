from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.runtime_suite_runner import RuntimePlan, build_plan, run_plan


def case(module: str, name: str):
    cls = type(name, (unittest.TestCase,), {"__module__": module, "test_ok": lambda self: None})
    return cls("test_ok")


class RuntimeSuiteRunnerTests(unittest.TestCase):
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

    def test_child_failure_propagates_without_retry_or_later_groups(self):
        plan = RuntimePlan(("native.test",), (("browser.one",), ("browser.two",)))
        with patch("tests.runtime_suite_runner.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 7),
            ]
            self.assertEqual(run_plan(plan), 7)
            self.assertEqual(run.call_count, 2)

    def test_success_uses_parallel_coverage_for_all_groups_and_preserves_environment(self):
        plan = RuntimePlan(("native.test",), (("browser.one", "browser.two"),))
        with (
            patch.dict(
                os.environ, {"COVERAGE_FILE": "isolated-coverage", "PYTHONPATH": "existing"}
            ),
            patch("tests.runtime_suite_runner.subprocess.run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertEqual(run_plan(plan, root=Path.cwd()), 0)
            self.assertEqual(run.call_count, 2)
            first, second = run.call_args_list
            self.assertIn("--parallel-mode", first.args[0])
            self.assertEqual(first.args[0][-2:], ["tests.runtime_suite_runner", "--core-only"])
            self.assertEqual(second.args[0][-4:], ["unittest", "browser.one", "browser.two", "-v"])
            self.assertEqual(second.kwargs["env"]["COVERAGE_FILE"], "isolated-coverage")
            self.assertEqual(
                second.kwargs["env"]["PYTHONPATH"],
                str(Path.cwd() / "tests") + os.pathsep + "existing",
            )
