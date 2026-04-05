from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalizationAuditTests(unittest.TestCase):
    def test_localization_audit_passes(self) -> None:
        script = ROOT / "scripts" / "audit_localization.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        combined_output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, combined_output)


if __name__ == "__main__":
    unittest.main()
