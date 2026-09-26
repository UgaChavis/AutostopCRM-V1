from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "state_size_report.py"


def load_state_size_report_module() -> ModuleType:
    return load_module_from_file("state_size_report", SCRIPT_PATH)


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

            with (
                patch.object(self.module, "STATE_SIZE_REPORT_STATE_MAX_BYTES", 8),
                self.assertRaisesRegex(ValueError, "state size report state file is too large"),
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

    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "state_size_report"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_state_size_report_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

    def test_benchmark_logger_adds_one_null_handler(self) -> None:
        logger = self.module.logging.Logger("state-size-report-test")

        with patch.object(self.module.logging, "getLogger", return_value=logger):
            first = self.module._benchmark_logger()
            second = self.module._benchmark_logger()

        null_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, self.module.logging.NullHandler)
        ]
        self.assertIs(first, logger)
        self.assertIs(second, logger)
        self.assertEqual(len(null_handlers), 1)


if __name__ == "__main__":
    unittest.main()
