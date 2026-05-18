from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_probe.py"


def load_perf_probe_module():
    spec = importlib.util.spec_from_file_location("perf_probe", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("perf_probe.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PerfProbeTests(unittest.TestCase):
    def test_thresholds_report_named_latency_and_payload_violations(self) -> None:
        module = load_perf_probe_module()
        rows = [
            {"label": "snapshot.gzip", "avg_ms": 480.0, "bytes": 45_020},
            {"label": "revision", "avg_ms": 252.5, "bytes": 637},
            {"label": "get_card", "avg_ms": 235.5, "bytes": 7433},
        ]

        violations = module.evaluate_thresholds(
            rows,
            {
                "snapshot.gzip.avg_ms": 450.0,
                "snapshot.gzip.bytes": 40_000,
                "revision.avg_ms": 300.0,
                "get_card.avg_ms": 250.0,
            },
        )

        self.assertEqual(
            violations,
            [
                {
                    "label": "snapshot.gzip",
                    "metric": "avg_ms",
                    "actual": 480.0,
                    "max": 450.0,
                },
                {
                    "label": "snapshot.gzip",
                    "metric": "bytes",
                    "actual": 45020,
                    "max": 40000.0,
                },
            ],
        )

    def test_main_returns_nonzero_when_thresholds_are_exceeded(self) -> None:
        module = load_perf_probe_module()

        def fake_measure(
            base_url, label, path, *, iterations, method="GET", payload=None, gzip_ok=False
        ):
            _ = (base_url, path, iterations, method, payload, gzip_ok)
            if label == "snapshot.identity":
                return {"data": {"cards": [{"id": "card-1"}]}}, [
                    module.ProbeResult(label, 200, 600.0, 305_000, "", "app;dur=60")
                ]
            return {}, [module.ProbeResult(label, 200, 480.0, 45_020, "gzip", "app;dur=58")]

        stdout = io.StringIO()
        with (
            patch.object(module, "measure", side_effect=fake_measure),
            patch.object(
                sys,
                "argv",
                [
                    "perf_probe.py",
                    "--base-url",
                    "https://crm.autostopcrm.ru",
                    "--iterations",
                    "1",
                    "--max-snapshot-gzip-ms",
                    "450",
                ],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["threshold_status"], "failed")
        self.assertEqual(payload["violations"][0]["label"], "snapshot.gzip")


if __name__ == "__main__":
    unittest.main()
