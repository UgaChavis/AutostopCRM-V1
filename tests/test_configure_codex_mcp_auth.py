from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_rotate_updates_server_codex_and_runtime_files_without_returning_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            runtime_env = root / "runtime.env"
            server_env.write_text("EXISTING=value\n", encoding="utf-8")
            codex_config.write_text(
                '[mcp_servers.autostopcrm]\nurl = "https://crm.autostopcrm.ru/mcp"\n',
                encoding="utf-8",
            )
            token = STRONG_TOKEN

            result = self.module.rotate(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                token=token,
                mcp_url="https://crm.autostopcrm.ru/mcp",
            )
            checked = self.module.check(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
            )

            self.assertTrue(result["ok"])
            self.assertNotIn(token, repr(result))
            self.assertTrue(checked["ok"])
            self.assertTrue(checked["mcp_url_matches"])
            self.assertTrue(checked["token_entropy_valid"])
            self.assertTrue(checked["codex_has_static_auth_fallback"])
            self.assertIn("bearer_token_env_var", codex_config.read_text(encoding="utf-8"))
            self.assertIn("http_headers", codex_config.read_text(encoding="utf-8"))
            for path in (server_env, codex_config, runtime_env):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_rotate_is_idempotent_for_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            runtime_env = root / "runtime.env"
            token = STRONG_TOKEN

            for _ in range(2):
                self.module.rotate(
                    server_env=server_env,
                    codex_config=codex_config,
                    runtime_env=runtime_env,
                    token=token,
                    mcp_url="https://crm.autostopcrm.ru/mcp",
                )

            self.assertEqual(server_env.read_text().count("MINIMAL_KANBAN_MCP_BEARER_TOKEN="), 1)
            self.assertEqual(codex_config.read_text().count("bearer_token_env_var ="), 1)
            self.assertEqual(codex_config.read_text().count("http_headers ="), 1)

    def test_rotate_rejects_shell_metacharacters_in_token_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(self.module.AuthConfigError):
                self.module.rotate(
                    server_env=root / "server.env",
                    codex_config=root / "config.toml",
                    runtime_env=root / "runtime.env",
                    token="safe-prefix$(touch-danger)" + "x" * 40,
                    mcp_url="https://crm.autostopcrm.ru/mcp",
                )

    def test_rotate_rejects_low_entropy_token_and_unsafe_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(self.module.AuthConfigError, "entropy"):
                self.module.rotate(
                    server_env=root / "server.env",
                    codex_config=root / "config.toml",
                    runtime_env=root / "runtime.env",
                    token="a" * 64,
                    mcp_url="https://crm.autostopcrm.ru/mcp",
                )
            with self.assertRaisesRegex(self.module.AuthConfigError, "HTTPS /mcp"):
                self.module.rotate(
                    server_env=root / "server.env",
                    codex_config=root / "config.toml",
                    runtime_env=root / "runtime.env",
                    token=STRONG_TOKEN,
                    mcp_url='https://crm.autostopcrm.ru/mcp"\nunsafe = "true',
                )

    def test_rotate_updates_stale_url_and_check_rejects_wrong_expected_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            runtime_env = root / "runtime.env"
            codex_config.write_text(
                '[mcp_servers.autostopcrm]\nurl = "https://old.example/mcp"\n',
                encoding="utf-8",
            )

            self.module.rotate(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                token=STRONG_TOKEN,
                mcp_url="https://crm.autostopcrm.ru/mcp",
            )

            self.assertIn(
                'url = "https://crm.autostopcrm.ru/mcp"',
                codex_config.read_text(encoding="utf-8"),
            )
            self.assertFalse(
                self.module.check(
                    server_env=server_env,
                    codex_config=codex_config,
                    runtime_env=runtime_env,
                    mcp_url="https://different.example/mcp",
                )["ok"]
            )

    def test_snapshot_restore_supports_deploy_rollback_without_leaking_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            runtime_env = root / "runtime.env"
            backup_dir = root / "auth-backup"
            old_token = STRONG_TOKEN
            new_token = STRONG_TOKEN[::-1]
            self.module.rotate(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                token=old_token,
                mcp_url="https://crm.autostopcrm.ru/mcp",
            )
            snap = self.module.snapshot(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                backup_dir=backup_dir,
            )
            self.module.rotate(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                token=new_token,
                mcp_url="https://crm.autostopcrm.ru/mcp",
            )

            restored = self.module.restore(
                server_env=server_env,
                codex_config=codex_config,
                runtime_env=runtime_env,
                backup_dir=backup_dir,
            )

            self.assertTrue(snap["ok"])
            self.assertTrue(restored["ok"])
            self.assertEqual(
                self.module._env_value(server_env, self.module.SERVER_TOKEN_KEY), old_token
            )
            self.assertNotIn(old_token, repr(snap))
            self.assertNotIn(old_token, repr(restored))
            self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
            self.assertTrue(
                all(
                    stat.S_IMODE(path.stat().st_mode) == 0o600
                    for path in backup_dir.iterdir()
                    if path.is_file()
                )
            )

    def test_rotate_restores_all_files_when_a_late_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server_env = root / "server.env"
            codex_config = root / "config.toml"
            runtime_env = root / "runtime.env"
            server_env.write_text("ORIGINAL=server\n", encoding="utf-8")
            codex_config.write_text("# original config\n", encoding="utf-8")
            runtime_env.write_text("ORIGINAL=runtime\n", encoding="utf-8")
            originals = {
                path: path.read_bytes() for path in (server_env, codex_config, runtime_env)
            }

            with patch.object(
                self.module,
                "_upsert_codex_bearer_config",
                side_effect=OSError("simulated late failure"),
            ):
                with self.assertRaises(OSError):
                    self.module.rotate(
                        server_env=server_env,
                        codex_config=codex_config,
                        runtime_env=runtime_env,
                        token=STRONG_TOKEN,
                        mcp_url="https://crm.autostopcrm.ru/mcp",
                    )

            self.assertEqual(
                {path: path.read_bytes() for path in originals},
                originals,
            )


if __name__ == "__main__":
    unittest.main()
