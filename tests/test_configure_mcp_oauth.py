from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "configure_mcp_oauth.py"


def load_module() -> ModuleType:
    return load_module_from_file("configure_mcp_oauth", SCRIPT_PATH)


class ConfigureMcpOAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "configure_mcp_oauth"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

    def test_ensure_provisions_stable_private_production_oauth_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("EXISTING=value\n", encoding="utf-8")

            first = self.module.ensure(env_file)
            first_values = self.module._read_values(env_file)
            second = self.module.ensure(env_file)
            second_values = self.module._read_values(env_file)

            self.assertTrue(first["ok"])
            self.assertFalse(first["state_key_reused"])
            self.assertTrue(second["state_key_reused"])
            self.assertEqual(
                first_values[self.module.STATE_KEY], second_values[self.module.STATE_KEY]
            )
            self.assertTrue(self.module.check(env_file)["ok"])
            self.assertEqual(first_values[self.module.OAUTH_ENABLED_KEY], "1")
            self.assertEqual(first_values[self.module.EMBEDDED_OAUTH_KEY], "0")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

    def test_ensure_rejects_existing_invalid_key_instead_of_rotating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(f"{self.module.STATE_KEY}=invalid-existing-key\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, self.module.STATE_KEY):
                self.module.ensure(env_file)

            self.assertIn("invalid-existing-key", env_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
