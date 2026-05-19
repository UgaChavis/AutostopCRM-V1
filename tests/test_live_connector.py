from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_live_connector.py"


def load_live_connector_module():
    spec = importlib.util.spec_from_file_location("check_live_connector_for_tests", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load check_live_connector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyConsoleStdout:
    encoding = "cp1251"

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> None:
        self.buffer.write(text.encode(self.encoding))


class LiveConnectorOutputTests(unittest.TestCase):
    def test_emit_output_writes_json_as_utf8_even_on_legacy_console(self) -> None:
        module = load_live_connector_module()
        fake_stdout = LegacyConsoleStdout()
        payload = json.dumps(
            {"ok": True, "message": "Проверка MCP 🚗", "amount": "100 ₽"},
            ensure_ascii=False,
        )

        with patch.object(sys, "stdout", fake_stdout):
            module._emit_output(payload)

        raw = fake_stdout.buffer.getvalue()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw.decode("utf-8")), json.loads(payload))


if __name__ == "__main__":
    unittest.main()
