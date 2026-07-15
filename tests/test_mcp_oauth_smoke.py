from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_mcp_oauth.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_mcp_oauth", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("check_mcp_oauth.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status_code = 200


class FakeClient:
    def __init__(self) -> None:
        self.url = ""
        self.data: dict[str, str] = {}

    def post(self, url: str, *, data: dict[str, str]) -> FakeResponse:
        self.url = url
        self.data = data
        return FakeResponse()


class McpOAuthSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_public_client_revocation_sends_sdk_compatible_empty_secret(self) -> None:
        client = FakeClient()

        revoked = self.module._revoke(
            client,
            {
                "mcp_url": "https://crm.autostopcrm.ru/mcp",
                "client_id": "public-client",
                "refresh_token": "opaque-refresh-token",
            },
        )

        self.assertTrue(revoked)
        self.assertEqual(client.url, "https://crm.autostopcrm.ru/revoke")
        self.assertIn("client_secret", client.data)
        self.assertEqual(client.data["client_secret"], "")


if __name__ == "__main__":
    unittest.main()
