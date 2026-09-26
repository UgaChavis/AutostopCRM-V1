import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file


def load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    return load_module_from_file(
        name,
        path,
        load_error=AssertionError(f"Could not load performance script: {name}"),
    )


class PerfComparisonTests(unittest.TestCase):
    def test_invalid_harness_json_keeps_process_diagnostics(self) -> None:
        module = load_script("perf_comparison")
        failed_process = subprocess.CompletedProcess(
            args=["python", "perf_workflows.py"],
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: synthetic missing dependency",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "evidence"
            with patch.object(module.subprocess, "run", return_value=failed_process) as run:
                with self.assertRaisesRegex(
                    RuntimeError, "baseline series 1 did not return valid JSON"
                ):
                    module.main(
                        [
                            "--baseline",
                            temp_dir,
                            "--output-dir",
                            str(output_dir),
                            "--series",
                            "1",
                            "--iterations",
                            "1",
                        ]
                    )
            run.assert_called_once()
            error_log = output_dir / "baseline-1-error.log"
            self.assertTrue(error_log.is_file())
            content = error_log.read_text(encoding="utf-8")
            self.assertIn("process_exit_code: 1", content)
            self.assertIn("ModuleNotFoundError: synthetic missing dependency", content)
            self.assertEqual(list(output_dir.iterdir()), [error_log])

    def test_script_loader_restores_none_sys_modules_entry(self) -> None:
        with patch.dict(sys.modules, {"perf_comparison": None}):
            module = load_script("perf_comparison")

            self.assertEqual(module.__name__, "perf_comparison")
            self.assertTrue(
                "perf_comparison" in sys.modules,
                "module loader should preserve a None sentinel",
            )
            self.assertIsNone(sys.modules["perf_comparison"])

    def test_comparison_uses_median_series_and_small_absolute_tolerance(self):
        module = load_script("perf_comparison")

        def runs(values):
            return [{"rows": [{"scenario": "operation", "p95_ms": value}]} for value in values]

        row = module.compare_rows(runs([1, 2, 100]), runs([3, 4, 200]))[0]
        self.assertEqual(row["baseline_p95_ms"], 2)
        self.assertEqual(row["candidate_p95_ms"], 4)
        self.assertEqual(row["status"], "passed")
        self.assertEqual(module.compare_rows(runs([100]), runs([111]))[0]["status"], "regressed")
        self.assertEqual(module.compare_rows(runs([100]), [])[0]["status"], "missing")
        self.assertEqual(module.compare_rows([], [])[0]["status"], "missing")
        self.assertEqual(
            module.compare_rows(runs([100, 100]), runs([100]) + [{"rows": []}])[0]["status"],
            "missing",
        )

    def test_growing_fixture_preserves_links_and_does_not_change_default_counts(self):
        module = load_script("perf_workflows")
        original = dict(module.SYNTHETIC_STATE_COUNTS)
        grown = module.build_synthetic_current_production_state(scale=2)
        self.assertEqual(
            {key: len(grown[key]) for key in original},
            {key: value * 2 for key, value in original.items()},
        )
        clients = {client["id"] for client in grown["clients"]}
        self.assertTrue(all(card["client_id"] in clients for card in grown["cards"]))
        self.assertEqual(module.SYNTHETIC_STATE_COUNTS, original)
        with self.assertRaises(ValueError):
            module.build_synthetic_current_production_state(scale=3)

    def test_concurrent_printing_requires_relative_targets_for_each_milestone(self):
        module = load_script("perf_comparison")

        def runs(shell, documents, lock, preview=900):
            return [
                {
                    "rows": [
                        {
                            "scenario": "printing.concurrent_payroll",
                            "p95_ms": 1000,
                            "shell_ms": {"p95_ms": shell},
                            "documents_ms": {"p95_ms": documents},
                            "service_lock_wait_ms": {"p95_ms": lock},
                            "service_lock_hold_ms": {"p95_ms": 10},
                            "preview_ms": {"p95_ms": preview},
                        }
                    ]
                }
            ]

        comparisons = module.compare_rows(runs(1000, 1000, 1000), runs(250, 500, 200))
        self.assertTrue(all(row["status"] == "passed" for row in comparisons))
        by_metric = {row["metric"]: row for row in comparisons}
        self.assertEqual(by_metric["shell_ms"]["required_reduction_percent"], 75)
        self.assertEqual(by_metric["documents_ms"]["required_reduction_percent"], 50)
        self.assertEqual(by_metric["service_lock_wait_ms"]["required_reduction_percent"], 80)
        failed = module.compare_rows(runs(1000, 1000, 1000), runs(251, 501, 201, preview=1000))
        self.assertEqual(sum(row["status"] == "regressed" for row in failed), 4)

    def test_printing_comparison_rejects_missing_milestones(self):
        module = load_script("perf_comparison")
        runs = [{"rows": [{"scenario": "printing.immediate", "p95_ms": 100}]}]
        comparisons = module.compare_rows(runs, runs)
        self.assertEqual(sum(row["status"] == "missing" for row in comparisons), 5)

    def test_new_hold_telemetry_is_not_a_zero_baseline(self):
        module = load_script("perf_comparison")
        baseline = [{"rows": [{"scenario": "printing.immediate", "p95_ms": 100}]}]
        candidate = [
            {
                "rows": [
                    {
                        "scenario": "printing.immediate",
                        "p95_ms": 100,
                        "service_lock_hold_ms": {"p95_ms": 35},
                    }
                ]
            }
        ]
        hold = next(
            row
            for row in module.compare_rows(baseline, candidate)
            if row.get("metric") == "service_lock_hold_ms"
        )
        self.assertEqual(hold["status"], "not_comparable")
        self.assertEqual(hold["candidate_p95_ms"], 35)
        self.assertNotIn("baseline_p95_ms", hold)


if __name__ == "__main__":
    unittest.main()
