from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "configure_codex_mcp_auth.py"
STRONG_TOKEN = "aB3_dE5-fG7.hJ9~kL2_mN4-pQ6.rS8~tU1_vW3-xY5.zA7~bC9_dF2-gH4.jK6~mP8"


def load_module():
    spec = importlib.util.spec_from_file_location("configure_codex_mcp_auth", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("configure_codex_mcp_auth.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ConfigureCodexMcpAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_rotate_updates_only_server_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            original_config = '[mcp_servers.autostopcrm]\nurl = "https://crm.autostopcrm.ru/mcp"\n'
            codex_config.write_text(original_config, encoding="utf-8")

            result = self.module.rotate(
                server_env=server_env,
                token=STRONG_TOKEN,
            )

            self.assertTrue(result["ok"])
            self.assertNotIn(STRONG_TOKEN, repr(result))
            self.assertTrue(self.module.check(server_env=server_env)["ok"])
            self.assertEqual(codex_config.read_text(encoding="utf-8"), original_config)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(server_env.stat().st_mode), 0o600)

    def test_rotate_is_idempotent_and_rejects_unsafe_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            for _ in range(2):
                self.module.rotate(
                    server_env=server_env,
                    token=STRONG_TOKEN,
                )
            self.assertEqual(server_env.read_text().count("MINIMAL_KANBAN_MCP_BEARER_TOKEN="), 1)
            with self.assertRaises(self.module.AuthConfigError):
                self.module.rotate(
                    server_env=server_env,
                    token="safe-prefix$(touch-danger)" + "x" * 40,
                )
            with self.assertRaisesRegex(self.module.AuthConfigError, "entropy"):
                self.module.rotate(
                    server_env=server_env,
                    token="a" * 64,
                )

    def test_snapshot_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            backup_dir = root / "auth-backup"
            self.module.rotate(server_env=server_env, token=STRONG_TOKEN)
            snapshot = self.module.snapshot(
                server_env=server_env,
                backup_dir=backup_dir,
            )
            self.module.rotate(
                server_env=server_env,
                token=STRONG_TOKEN[::-1],
            )
            restored = self.module.restore(
                server_env=server_env,
                backup_dir=backup_dir,
            )

            self.assertTrue(snapshot["ok"] and restored["ok"])
            self.assertEqual(
                self.module._env_value(server_env, self.module.SERVER_TOKEN_KEY), STRONG_TOKEN
            )
            self.assertNotIn(STRONG_TOKEN, repr(snapshot) + repr(restored))


if __name__ == "__main__":
    unittest.main()
