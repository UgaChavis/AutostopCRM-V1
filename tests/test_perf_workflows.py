from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

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
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, script)
        self.assertIn("Write workflow skipped.", script)
        self.assertIn("external_write_workflows_enabled", script)
        self.assertIn("autostop-perf", script)

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


if __name__ == "__main__":
    unittest.main()
