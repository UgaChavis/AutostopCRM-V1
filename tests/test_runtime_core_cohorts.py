from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from tests import runtime_core_cohorts as cohorts

ROOT = Path(__file__).resolve().parents[1]


class RuntimeCoreCohortTests(unittest.TestCase):
    def test_class_partition_preserves_every_identity_without_splitting_class_lifetimes(self):
        ids = tuple(
            f"test_synthetic.C{index}.test_{method}"
            for index in range(7)
            for method in range(index + 1)
        )
        groups = cohorts.partition(ids, 4)
        self.assertEqual(Counter(item for group in groups for item in group), Counter(ids))
        for index in range(7):
            self.assertEqual(
                sum(
                    any(item.startswith(f"test_synthetic.C{index}.") for item in group)
                    for group in groups
                ),
                1,
            )
        self.assertLessEqual(max(map(len, groups)) - min(map(len, groups)), 7)
        with self.assertRaises(ValueError):
            cohorts.partition((ids[0], ids[0]), 2)

    def test_changed_immutable_plan_cannot_select_another_test(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name, "plan.json")
            plan = {
                "schema": "autostopcrm.core-cohorts.v1",
                "core_ids": ["test_example.C.test_a"],
                "cohorts": [["test_example.C.test_a"]],
                "deadline_monotonic": time.monotonic() + 10,
                "budget_seconds": 10,
            }
            digest = cohorts.write_new(path, plan)
            self.assertEqual(cohorts.load_plan(path, digest, 0)[1], ("test_example.C.test_a",))
            path.write_bytes(path.read_bytes().replace(b"test_a", b"test_b"))
            with self.assertRaisesRegex(ValueError, "plan bytes changed"):
                cohorts.load_plan(path, digest, 0)

    def _environment(self, root):
        return {
            **os.environ,
            "AUTOSTOP_REHEARSAL_RECEIPT_DIR": str(root / "receipts"),
            "COVERAGE_FILE": str(root / ".coverage"),
        }

    def test_two_real_owned_cohorts_prove_exact_ids_distinct_processes_and_private_temp(self):
        ids = (
            "tests.test_runtime_suite_runner.RuntimeSuiteRunnerTests.test_partition_preserves_every_selected_id_and_nested_order",
            "tests.test_runtime_skip_contract.RuntimeSkipContractTests.test_unknown_platform_and_new_reasons_remain_closed",
        )
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual(
                cohorts.run_core(
                    ids, root=ROOT, environment=self._environment(root), budget=30, workers=2
                ),
                0,
            )
            receipts = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (root / "receipts").glob("runtime-suite-*.json")
            ]
            self.assertEqual(len(receipts), 2)
            self.assertEqual(len({receipt["pid"] for receipt in receipts}), 2)
            self.assertEqual(len({receipt["fixture_temp_root"] for receipt in receipts}), 2)
            self.assertEqual(
                Counter(item for receipt in receipts for item in receipt["executed_ids"]),
                Counter(ids),
            )
            result = json.loads(next((root / "receipts").glob("core-result-*.json")).read_text())
            self.assertTrue(result["exact_disjoint_id_receipts"])
            self.assertTrue(result["successful"])

    def test_missing_test_cannot_be_reported_as_successful_empty_cohort(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            code = cohorts.run_core(
                ("test_missing_cohort_module.C.test_missing",),
                root=ROOT,
                environment=self._environment(root),
                budget=15,
                workers=2,
            )
            self.assertNotEqual(code, 0)
            result = json.loads(next((root / "receipts").glob("core-result-*.json")).read_text())
            self.assertFalse(result["successful"])
            self.assertFalse(result["exact_disjoint_id_receipts"])

    def test_selected_application_import_does_not_depend_on_discovery_order(self):
        identity = "tests.test_cold_manager.ColdManagerTests.test_sealed_source_calls_independent_manager_validator"
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            environment = self._environment(root)
            environment.pop("PYTHONPATH", None)
            self.assertEqual(
                cohorts.run_core(
                    (identity,),
                    root=ROOT,
                    environment=environment,
                    budget=30,
                    workers=1,
                ),
                0,
            )
            result = json.loads(next((root / "receipts").glob("core-result-*.json")).read_text())
            self.assertTrue(result["exact_disjoint_id_receipts"])

    def test_owned_socket_fixture_stays_bounded_under_long_ci_temp_parent(self):
        identity = "tests.test_automation_center.AutomationControlClientTests.test_bounded_protocol_round_trip"
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            long_parent = root / ("long-ci-cache-" + "a" * 100) / "fixtures"
            long_parent.mkdir(parents=True)
            environment = self._environment(root) | {
                "TEMP": str(long_parent),
                "TMP": str(long_parent),
            }
            self.assertEqual(
                cohorts.run_core(
                    (identity,),
                    root=ROOT,
                    environment=environment,
                    budget=30,
                    workers=1,
                ),
                0,
            )
            receipt = json.loads(next((root / "receipts").glob("runtime-suite-*.json")).read_text())
            self.assertLess(len(receipt["fixture_temp_root"].encode()), 80)
            self.assertFalse(Path(receipt["fixture_temp_root"]).exists())

    def test_shared_deadline_stops_real_nested_worker_without_giving_each_a_new_budget(self):
        ids = (
            "tests.test_runtime_suite_runner.RuntimeSuiteRunnerTests.test_primary_failure_is_visible_before_cleanup_times_out",
        )
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            started = time.monotonic()
            code = cohorts.run_core(
                ids, root=ROOT, environment=self._environment(root), budget=1, workers=2
            )
            self.assertEqual(code, 124)
            self.assertLess(time.monotonic() - started, 12)
            result = json.loads(next((root / "receipts").glob("core-result-*.json")).read_text())
            self.assertEqual(result["shared_budget_seconds"], 1)
            self.assertFalse(result["successful"])
            reports = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (ROOT / "output/runtime-process").glob("*.json")
                if path.stat().st_size < 1024**2
            ]
            reports = [
                item for item in reports if item.get("controller_pid") in result["controller_pids"]
            ]
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["owned_after_cleanup"], [])


if __name__ == "__main__":
    unittest.main()
