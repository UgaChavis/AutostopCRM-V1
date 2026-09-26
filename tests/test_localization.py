from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_localization.py"


def load_localization_audit_module() -> ModuleType:
    return load_module_from_file("audit_localization", SCRIPT_PATH)


class LocalizationAuditTests(unittest.TestCase):
    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "audit_localization"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_localization_audit_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

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

            with (
                patch.object(module, "LOCALIZATION_AUDIT_TEXT_MAX_BYTES", 8),
                self.assertRaisesRegex(ValueError, "localization audit file is too large"),
            ):
                module._read_text(path)


if __name__ == "__main__":
    unittest.main()
