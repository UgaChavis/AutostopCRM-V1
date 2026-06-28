from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_localization.py"


def load_localization_audit_module():
    spec = importlib.util.spec_from_file_location("audit_localization", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("audit_localization.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LocalizationAuditTests(unittest.TestCase):
    def test_localization_audit_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        combined_output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, combined_output)

    def test_localization_audit_reader_rejects_oversized_file(self) -> None:
        module = load_localization_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "huge.py"
            path.write_text("x" * 16, encoding="utf-8")

            with patch.object(module, "LOCALIZATION_AUDIT_TEXT_MAX_BYTES", 8):
                with self.assertRaisesRegex(ValueError, "localization audit file is too large"):
                    module._read_text(path)


if __name__ == "__main__":
    unittest.main()
