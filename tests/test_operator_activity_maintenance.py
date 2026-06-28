from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "operator_activity_maintenance.py"


def load_operator_activity_maintenance_module():
    spec = importlib.util.spec_from_file_location("operator_activity_maintenance", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("operator_activity_maintenance.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OperatorActivityMaintenanceScriptTests(unittest.TestCase):
    def test_main_json_sanitizes_nonfinite_values(self) -> None:
        module = load_operator_activity_maintenance_module()
        output = StringIO()

        with (
            patch.object(
                module,
                "compact_operator_activity",
                return_value={"ok": True, "value": float("nan"), "ratio": 1.25},
            ),
            redirect_stdout(output),
        ):
            exit_code = module.main(["--dry-run", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"ok": True, "value": None, "ratio": 1.25})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_operator_activity_maintenance_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_retention_days_bounds_reject_huge_values(self) -> None:
        module = load_operator_activity_maintenance_module()

        self.assertEqual(module._bounded_retention_days(1e308), 3650)
        self.assertEqual(module._bounded_retention_days(0), 1)
        self.assertEqual(
            module._bounded_retention_days("bad"), module.DEFAULT_DETAIL_RETENTION_DAYS
        )

    def test_main_reports_json_error_without_traceback(self) -> None:
        module = load_operator_activity_maintenance_module()
        output = StringIO()

        with (
            patch.object(
                module,
                "compact_operator_activity",
                side_effect=ValueError("bad activity storage"),
            ),
            redirect_stdout(output),
        ):
            exit_code = module.main(["--dry-run", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("bad activity storage", payload["error"])


if __name__ == "__main__":
    unittest.main()
