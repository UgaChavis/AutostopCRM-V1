import importlib.util
import sys
import unittest
from pathlib import Path


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


class PerfComparisonTests(unittest.TestCase):
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
