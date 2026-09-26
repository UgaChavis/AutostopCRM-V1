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
SCRIPT_PATH = ROOT / "scripts" / "configure_manager_crm_mcp.py"
STRONG_TOKEN = "aB3_dE5-fG7.hJ9~kL2_mN4-pQ6.rS8~tU1_vW3-xY5.zA7~bC9_dF2-gH4.jK6~mP8"
ROTATED_TOKEN = "zY8_xW7-vU6.tS5~rQ4_pO3-nM2.lK1~jI0_hG9-fE8.dC7~bA6_zY5-xW4.vU3"


def load_module() -> ModuleType:
    return load_module_from_file("configure_manager_crm_mcp", SCRIPT_PATH)


class ConfigureManagerCrmMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "configure_manager_crm_mcp"
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

    def write_server_env(self, path: Path, token: str) -> None:
        path.write_text(
            f"OTHER_SERVER_SETTING=unchanged\nMINIMAL_KANBAN_MCP_BEARER_TOKEN={token}\n",
            encoding="utf-8",
        )

    def test_sync_writes_exact_private_manager_e8_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "crm.env"
            manager_env = root / ".crm-mcp.env"
            self.write_server_env(server_env, STRONG_TOKEN)

            result = self.module.sync(server_env=server_env, manager_env=manager_env)
            check = self.module.check(server_env=server_env, manager_env=manager_env)

            self.assertTrue(result["ok"] and check["ok"])
            self.assertNotIn(STRONG_TOKEN, repr(result) + repr(check))
            self.assertEqual(
                f"AUTOSTOP_CRM_MCP_URL=http://127.0.0.1:8001/mcp\n"
                f"AUTOSTOP_CRM_MCP_BEARER_TOKEN={STRONG_TOKEN}\n",
                manager_env.read_text(encoding="utf-8"),
            )
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(manager_env.stat().st_mode))

    def test_snapshot_restore_restores_previous_manager_file_without_exposing_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "crm.env"
            manager_env = root / ".crm-mcp.env"
            backup_dir = root / "backup"
            self.write_server_env(server_env, STRONG_TOKEN)
            self.module.sync(server_env=server_env, manager_env=manager_env)
            original = manager_env.read_bytes()

            snapshot = self.module.snapshot(manager_env=manager_env, backup_dir=backup_dir)
            self.write_server_env(server_env, ROTATED_TOKEN)
            self.module.sync(server_env=server_env, manager_env=manager_env)
            restored = self.module.restore(manager_env=manager_env, backup_dir=backup_dir)

            self.assertTrue(snapshot["ok"] and restored["ok"])
            self.assertEqual(original, manager_env.read_bytes())
            self.assertNotIn(STRONG_TOKEN, repr(snapshot) + repr(restored))

    def test_sync_refuses_unmanaged_or_weak_credential_file_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "crm.env"
            manager_env = root / ".crm-mcp.env"
            self.write_server_env(server_env, STRONG_TOKEN)
            manager_env.write_text("UNRELATED_SECRET=preserve\n", encoding="utf-8")
            original = manager_env.read_bytes()

            with self.assertRaisesRegex(self.module.ManagerCrmMcpConfigError, "unmanaged"):
                self.module.sync(server_env=server_env, manager_env=manager_env)
            self.assertEqual(original, manager_env.read_bytes())

            self.write_server_env(server_env, "a" * 64)
            manager_env.unlink()
            with self.assertRaisesRegex(self.module.ManagerCrmMcpConfigError, "entropy"):
                self.module.sync(server_env=server_env, manager_env=manager_env)
            self.assertFalse(manager_env.exists())

    def test_restore_rejects_tampered_backup_and_keeps_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "crm.env"
            manager_env = root / ".crm-mcp.env"
            backup_dir = root / "backup"
            self.write_server_env(server_env, STRONG_TOKEN)
            self.module.sync(server_env=server_env, manager_env=manager_env)
            self.module.snapshot(manager_env=manager_env, backup_dir=backup_dir)
            self.write_server_env(server_env, ROTATED_TOKEN)
            self.module.sync(server_env=server_env, manager_env=manager_env)
            current = manager_env.read_bytes()
            (backup_dir / "manager_crm_mcp_env").write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(self.module.ManagerCrmMcpConfigError, "checksum"):
                self.module.restore(manager_env=manager_env, backup_dir=backup_dir)
            self.assertEqual(current, manager_env.read_bytes())


if __name__ == "__main__":
    unittest.main()
