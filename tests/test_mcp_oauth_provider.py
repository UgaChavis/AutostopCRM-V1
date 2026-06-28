from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.auth.provider import (
    AuthorizationCode,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.oauth_provider import (  # noqa: E402
    EmbeddedOAuthAuthorizationServerProvider,
)


class McpOAuthProviderStateTests(unittest.TestCase):
    def _provider(self, state_file: Path) -> EmbeddedOAuthAuthorizationServerProvider:
        return EmbeddedOAuthAuthorizationServerProvider(
            issuer_url="https://agent.example",
            resource_url="https://agent.example/mcp",
            state_file=state_file,
        )

    def test_corrupted_state_backup_does_not_overwrite_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text("{broken json", encoding="utf-8")
            previous_backup = state_file.with_suffix(".corrupted.json")
            previous_backup.write_text("previous corrupt backup", encoding="utf-8")
            provider = self._provider(state_file)

            client = asyncio.run(provider.get_client("missing-client"))

            self.assertIsNone(client)
            self.assertEqual("previous corrupt backup", previous_backup.read_text(encoding="utf-8"))
            backups = sorted(state_file.parent.glob("mcp-oauth-state.corrupted*.json"))
            self.assertGreaterEqual(len(backups), 2)
            self.assertTrue(
                any(path.read_text(encoding="utf-8") == "{broken json" for path in backups)
            )

    def test_prune_state_ignores_malformed_expiration_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "clients": {},
                        "authorization_codes": {"bad-code": {"expires_at": "broken"}},
                        "access_tokens": {"bad-access": {"expires_at": "broken"}},
                        "refresh_tokens": {"bad-refresh": {"expires_at": "broken"}},
                    }
                ),
                encoding="utf-8",
            )
            provider = self._provider(state_file)

            state = provider._read_state()

            self.assertEqual({}, state["authorization_codes"])
            self.assertEqual({}, state["access_tokens"])
            self.assertEqual({}, state["refresh_tokens"])

    def test_load_access_token_accepts_legacy_bearer_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            provider = EmbeddedOAuthAuthorizationServerProvider(
                issuer_url="https://agent.example",
                resource_url="https://agent.example/mcp",
                state_file=state_file,
                legacy_bearer_token="legacy-secret",
            )

            token = asyncio.run(provider.load_access_token("legacy-secret"))

            self.assertIsNotNone(token)
            self.assertEqual(token.client_id, "minimal-kanban-legacy")
            self.assertEqual(token.scopes, ["kanban:read", "kanban:write"])

    def test_prune_state_rejects_non_finite_expiration_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "clients": {},
                        "authorization_codes": {
                            "inf-code": {"expires_at": float("inf")},
                            "huge-code": {"expires_at": 1e308},
                        },
                        "access_tokens": {
                            "inf-access": {"expires_at": float("inf")},
                            "huge-access": {"expires_at": 1e308},
                        },
                        "refresh_tokens": {"nan-refresh": {"expires_at": float("nan")}},
                    }
                ),
                encoding="utf-8",
            )
            provider = self._provider(state_file)

            state = provider._read_state()

            self.assertEqual({}, state["authorization_codes"])
            self.assertEqual({}, state["access_tokens"])
            self.assertEqual({}, state["refresh_tokens"])

    def test_expiration_helpers_reject_bool_and_non_finite_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")

            self.assertEqual(provider._float_or_zero(True), 0.0)
            self.assertEqual(provider._float_or_zero(float("inf")), 0.0)
            self.assertEqual(provider._float_or_zero(""), 0.0)
            self.assertEqual(provider._float_or_zero("12.5"), 12.5)
            self.assertEqual(provider._int_or_zero(True), 0)
            self.assertEqual(provider._int_or_zero(float("inf")), 0)
            self.assertEqual(provider._int_or_zero(""), 0)
            self.assertEqual(provider._int_or_zero("12"), 12)

    def test_non_standard_json_constants_are_treated_as_corrupted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text(
                (
                    '{"clients": {}, "authorization_codes": '
                    '{"bad-code": {"expires_at": NaN}}, '
                    '"access_tokens": {}, "refresh_tokens": {}}'
                ),
                encoding="utf-8",
            )
            provider = self._provider(state_file)

            state = provider._read_state()

            self.assertEqual(
                {
                    "clients": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {},
                },
                state,
            )
            backups = sorted(state_file.parent.glob("mcp-oauth-state.corrupted*.json"))
            self.assertTrue(any("NaN" in path.read_text(encoding="utf-8") for path in backups))

    def test_oversized_state_is_treated_as_corrupted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text('{"clients":{},"padding":"xxxxxxxx"}', encoding="utf-8")
            provider = self._provider(state_file)

            with patch("minimal_kanban.mcp.oauth_provider.OAUTH_STATE_MAX_BYTES", 8):
                state = provider._read_state()

            self.assertEqual(
                {
                    "clients": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {},
                },
                state,
            )
            backups = sorted(state_file.parent.glob("mcp-oauth-state.corrupted*.json"))
            self.assertTrue(any("padding" in path.read_text(encoding="utf-8") for path in backups))

    def test_deeply_nested_state_is_treated_as_corrupted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            deep_json = "[" * 5000 + "]" * 5000
            state_file.write_text(deep_json, encoding="utf-8")
            provider = self._provider(state_file)

            state = provider._read_state()

            self.assertEqual(provider._default_state(), state)
            backups = sorted(state_file.parent.glob("mcp-oauth-state.corrupted*.json"))
            self.assertTrue(any(path.read_text(encoding="utf-8") == deep_json for path in backups))

    def test_write_state_never_persists_non_finite_json_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            provider = self._provider(state_file)

            provider._write_state_unlocked(
                {
                    "clients": {
                        "client-1": {
                            "client_id": "client-1",
                            "bad": float("nan"),
                            "ratio": 1.25,
                        }
                    },
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {},
                }
            )

            raw = state_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
            self.assertNotIn("NaN", raw)
            self.assertIsNone(payload["clients"]["client-1"]["bad"])
            self.assertEqual(payload["clients"]["client-1"]["ratio"], 1.25)

    def test_write_state_rejects_payload_larger_than_reader_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            provider = self._provider(state_file)
            provider._write_state_unlocked(
                {
                    "clients": {"client-1": {"client_id": "client-1"}},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {},
                }
            )
            original = state_file.read_text(encoding="utf-8")

            with patch(
                "minimal_kanban.mcp.oauth_provider.OAUTH_STATE_MAX_BYTES",
                len(original.encode("utf-8")) + 8,
            ):
                with self.assertRaisesRegex(ValueError, "OAuth state file is too large"):
                    provider._write_state_unlocked(
                        {
                            "clients": {
                                "client-1": {
                                    "client_id": "client-1",
                                    "padding": "x" * 128,
                                }
                            },
                            "authorization_codes": {},
                            "access_tokens": {},
                            "refresh_tokens": {},
                        }
                    )

            self.assertEqual(state_file.read_text(encoding="utf-8"), original)
            self.assertEqual(list(state_file.parent.glob("*.tmp")), [])

    def test_write_state_handles_self_referential_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            provider = self._provider(state_file)
            client_payload: dict[str, object] = {"client_id": "client-1"}
            client_payload["self"] = client_payload

            provider._write_state_unlocked(
                {
                    "clients": {"client-1": client_payload},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {},
                }
            )

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            node = payload["clients"]["client-1"]
            for _ in range(6):
                node = node["self"]

            self.assertIsInstance(node, str)

    def test_write_state_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            fixed_tmp = state_file.with_suffix(".tmp")
            fixed_tmp.write_text("sentinel", encoding="utf-8")
            provider = self._provider(state_file)

            provider._write_state_unlocked(provider._default_state())

            self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_loaders_ignore_malformed_model_payloads_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "clients": {"bad-client": {"redirect_uris": "not-a-list"}},
                        "authorization_codes": {
                            "bad-code": {
                                "code": "bad-code",
                                "expires_at": time.time() + 300,
                            }
                        },
                        "access_tokens": {"bad-access": {"token": "bad-access"}},
                        "refresh_tokens": {"bad-refresh": {"token": "bad-refresh"}},
                    }
                ),
                encoding="utf-8",
            )
            provider = self._provider(state_file)
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
            )

            self.assertIsNone(asyncio.run(provider.get_client("bad-client")))
            self.assertIsNone(asyncio.run(provider.load_authorization_code(client, "bad-code")))
            self.assertIsNone(asyncio.run(provider.load_access_token("bad-access")))
            self.assertIsNone(asyncio.run(provider.load_refresh_token(client, "bad-refresh")))

    def test_scope_normalization_filters_invalid_values_and_accepts_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
                scope="kanban:write invalid kanban:read kanban:write",
            )

            self.assertEqual(
                ["kanban:read", "kanban:write"],
                provider._normalize_scopes("kanban:read invalid,kanban:write", client),
            )
            self.assertEqual(
                ["kanban:write", "kanban:read"],
                provider._normalize_scopes(["invalid"], client),
            )
            client.scope = "invalid"
            self.assertEqual(
                ["kanban:read", "kanban:write"],
                provider._normalize_scopes(["invalid"], client),
            )

    def test_register_client_accepts_chatgpt_connector_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=[
                    "https://chatgpt.com/connector/oauth/test-callback",
                    "https://chatgpt.com/connector_platform_oauth_redirect",
                ],
            )

            asyncio.run(provider.register_client(client))

            stored = asyncio.run(provider.get_client("client-1"))
            self.assertIsNotNone(stored)
            self.assertEqual(stored.client_id, "client-1")

    def test_register_client_rejects_non_chatgpt_redirects(self) -> None:
        blocked_redirects = [
            "https://example.com/connector/oauth/test-callback",
            "https://chatgpt.com.evil.example/connector/oauth/test-callback",
            "http://chatgpt.com/connector/oauth/test-callback",
            "https://chatgpt.com/not-connector/oauth/test-callback",
        ]
        for redirect_uri in blocked_redirects:
            with self.subTest(redirect_uri=redirect_uri):
                with tempfile.TemporaryDirectory() as temp_dir:
                    provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
                    client = OAuthClientInformationFull(
                        client_id="client-1",
                        redirect_uris=[redirect_uri],
                    )

                    with self.assertRaises(RegistrationError):
                        asyncio.run(provider.register_client(client))
                    self.assertIsNone(asyncio.run(provider.get_client("client-1")))

    def test_redirect_uri_validator_rejects_invalid_ports_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")

            self.assertFalse(
                provider._is_allowed_chatgpt_redirect_uri(
                    "https://chatgpt.com:bad/connector/oauth/test-callback"
                )
            )
            self.assertFalse(
                provider._is_allowed_chatgpt_redirect_uri(
                    "https://chatgpt.com:99999/connector/oauth/test-callback"
                )
            )

    def test_exchange_authorization_code_consumes_code_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
            )
            code = AuthorizationCode(
                code="code-1",
                scopes=["kanban:read", "kanban:write"],
                expires_at=time.time() + 300,
                client_id="client-1",
                code_challenge="challenge",
                redirect_uri="https://chatgpt.com/callback",
                redirect_uri_provided_explicitly=True,
                resource="https://agent.example/mcp",
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "authorization_codes": {
                        code.code: code.model_dump(mode="json", exclude_none=True)
                    },
                    "access_tokens": {},
                    "refresh_tokens": {},
                }
            )

            token = asyncio.run(provider.exchange_authorization_code(client, code))

            self.assertTrue(token.access_token.startswith("mkat_"))
            self.assertTrue(token.refresh_token.startswith("mkrt_"))
            with self.assertRaises(TokenError):
                asyncio.run(provider.exchange_authorization_code(client, code))
            state = provider._read_state()
            self.assertEqual({}, state["authorization_codes"])
            self.assertEqual(1, len(state["access_tokens"]))
            self.assertEqual(1, len(state["refresh_tokens"]))

    def test_exchange_refresh_token_consumes_refresh_token_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
            )
            refresh = RefreshToken(
                token="refresh-1",
                client_id="client-1",
                scopes=["kanban:read"],
                expires_at=int(time.time()) + 300,
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {
                        refresh.token: refresh.model_dump(mode="json", exclude_none=True)
                    },
                }
            )

            token = asyncio.run(provider.exchange_refresh_token(client, refresh, []))

            self.assertTrue(token.access_token.startswith("mkat_"))
            self.assertTrue(token.refresh_token.startswith("mkrt_"))
            self.assertEqual("kanban:read", token.scope)
            with self.assertRaises(TokenError):
                asyncio.run(provider.exchange_refresh_token(client, refresh, []))
            state = provider._read_state()
            self.assertNotIn("refresh-1", state["refresh_tokens"])
            self.assertEqual(1, len(state["access_tokens"]))
            self.assertEqual(1, len(state["refresh_tokens"]))

    def test_exchange_refresh_token_rejects_scope_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
            )
            refresh = RefreshToken(
                token="refresh-1",
                client_id="client-1",
                scopes=["kanban:read"],
                expires_at=int(time.time()) + 300,
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {
                        refresh.token: refresh.model_dump(mode="json", exclude_none=True)
                    },
                }
            )

            with self.assertRaises(TokenError):
                asyncio.run(provider.exchange_refresh_token(client, refresh, ["kanban:write"]))
            state = provider._read_state()
            self.assertIn("refresh-1", state["refresh_tokens"])
            self.assertEqual({}, state["access_tokens"])


if __name__ == "__main__":
    unittest.main()
