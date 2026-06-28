from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "state_size_report.py"


def load_state_size_report_module():
    spec = importlib.util.spec_from_file_location("state_size_report", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("state_size_report.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StateSizeReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_state_size_report_module()

    def test_main_reports_invalid_json_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("{broken", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = self.module.main(["--state-file", str(state_file), "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("Expecting property name", payload["error"])

    def test_load_state_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("[]", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = self.module.main(["--state-file", str(state_file)])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "state file must contain a JSON object")

    def test_load_state_rejects_nonstandard_json_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text('{"events":[{"details":{"score":NaN}}]}', encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = self.module.main(["--state-file", str(state_file), "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("Unsupported JSON constant", payload["error"])

    def test_load_state_rejects_deeply_nested_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
                self.module.load_state(state_file)

    def test_load_state_rejects_oversized_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("x" * 16, encoding="utf-8")

            with patch.object(self.module, "STATE_SIZE_REPORT_STATE_MAX_BYTES", 8):
                with self.assertRaisesRegex(
                    ValueError, "state size report state file is too large"
                ):
                    self.module.load_state(state_file)

    def test_json_size_helpers_emit_standard_json_for_non_finite_values(self) -> None:
        payload = {"score": float("inf"), "items": [float("nan")]}

        encoded = json.dumps(
            self.module._json_safe_value(payload),
            separators=(",", ":"),
            allow_nan=False,
        )

        self.assertEqual(json.loads(encoded), {"score": None, "items": [None]})
        self.assertEqual(self.module.json_bytes(payload), len(encoded.encode("utf-8")))

    def test_benchmark_iterations_are_bounded_before_running(self) -> None:
        self.assertEqual(self.module._bounded_iterations(1e308), 1000)
        self.assertEqual(self.module._bounded_iterations(-1e308), 0)
        self.assertEqual(self.module._bounded_iterations("bad"), 0)


if __name__ == "__main__":
    unittest.main()
