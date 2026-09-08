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


if __name__ == "__main__":
    unittest.main()
