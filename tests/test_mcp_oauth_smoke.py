from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


class FakeAuthorizationResponse:
    def __init__(
        self,
        status_code: int,
        *,
        payload: dict[str, str] | None = None,
        location: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"location": location} if location else {}

    def json(self) -> dict[str, str]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status {self.status_code}")


class FakeAuthorizationClient:
    def __init__(self) -> None:
        self.state = ""
        self.consent_headers: dict[str, str] = {}

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
    ) -> FakeAuthorizationResponse:
        self.state = params["state"]
        return FakeAuthorizationResponse(
            302,
            location="https://crm.autostopcrm.ru/oauth/authorize?request_id=request-1",
        )

    def post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeAuthorizationResponse:
        if url.endswith("/register"):
            return FakeAuthorizationResponse(201, payload={"client_id": "public-client"})
        if "/oauth/authorize" in url:
            self.consent_headers = dict(headers or {})
            return FakeAuthorizationResponse(
                302,
                location=f"http://127.0.0.1:18765/callback?code=code-1&state={self.state}",
            )
        if url.endswith("/token"):
            return FakeAuthorizationResponse(
                200,
                payload={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                },
            )
        raise AssertionError(f"unexpected POST {url}")


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

    def test_owner_consent_post_sends_public_origin(self) -> None:
        client = FakeAuthorizationClient()
        metadata = {
            "registration_endpoint": "https://crm.autostopcrm.ru/register",
            "authorization_endpoint": "https://crm.autostopcrm.ru/authorize",
            "token_endpoint": "https://crm.autostopcrm.ru/token",
        }

        with patch.object(self.module, "_metadata", return_value=metadata):
            state = self.module._new_authorization(
                client,
                mcp_url="https://crm.autostopcrm.ru/mcp",
                username="operator",
                password="secret",
            )

        self.assertEqual(
            client.consent_headers,
            {"Origin": "https://crm.autostopcrm.ru"},
        )
        self.assertEqual(state["access_token"], "access-token")


if __name__ == "__main__":
    unittest.main()
