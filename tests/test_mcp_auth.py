from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.auth import build_auth_settings


class McpAuthSettingsTests(unittest.TestCase):
    def test_build_auth_settings_normalizes_base_url_and_relative_path(self) -> None:
        settings = build_auth_settings("https://agent.example/", path="bridge")

        self.assertEqual(str(settings.issuer_url).rstrip("/"), "https://agent.example")
        self.assertEqual(
            str(settings.resource_server_url).rstrip("/"),
            "https://agent.example/bridge",
        )

    def test_build_auth_settings_uses_explicit_resource_url_without_trailing_slash(self) -> None:
        settings = build_auth_settings(
            "https://agent.example/",
            path="/bridge",
            resource_url="https://public.example/bridge/",
        )

        self.assertEqual(str(settings.issuer_url).rstrip("/"), "https://agent.example")
        self.assertEqual(
            str(settings.resource_server_url).rstrip("/"),
            "https://public.example/bridge",
        )


if __name__ == "__main__":
    unittest.main()
