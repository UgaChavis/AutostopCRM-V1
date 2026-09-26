from __future__ import annotations

import unittest

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

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
