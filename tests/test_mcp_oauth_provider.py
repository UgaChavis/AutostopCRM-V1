from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.oauth_provider import (  # noqa: E402
    EmbeddedOAuthAuthorizationServerProvider,
    OwnerAuthorizationCode,
    OwnerRefreshToken,
)


class McpOAuthProviderStateTests(unittest.TestCase):
    def _provider(self, state_file: Path) -> EmbeddedOAuthAuthorizationServerProvider:
        return EmbeddedOAuthAuthorizationServerProvider(
            issuer_url="https://agent.example",
            resource_url="https://agent.example/mcp",
            state_file=state_file,
        )

    def _decrypted_state(self, provider, state_file: Path) -> dict:
        raw = provider._cipher.decrypt(state_file.read_bytes()).decode("utf-8")
        return json.loads(raw)

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

            self.assertEqual(provider._default_state(), state)
            backups = sorted(state_file.parent.glob("mcp-oauth-state.corrupted*.json"))
            self.assertTrue(any("NaN" in path.read_text(encoding="utf-8") for path in backups))

    def test_oversized_state_is_treated_as_corrupted_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            state_file.write_text('{"clients":{},"padding":"xxxxxxxx"}', encoding="utf-8")
            provider = self._provider(state_file)

            with patch("minimal_kanban.mcp.oauth_provider.OAUTH_STATE_MAX_BYTES", 8):
                state = provider._read_state()

            self.assertEqual(provider._default_state(), state)
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

            raw = provider._cipher.decrypt(state_file.read_bytes()).decode("utf-8")
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

            payload = self._decrypted_state(provider, state_file)
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

    def test_scope_validation_requires_complete_exact_scope_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")

            self.assertEqual(
                ["kanban:read", "kanban:write"],
                provider._validated_complete_scopes("kanban:write kanban:read"),
            )
            with self.assertRaises(AuthorizeError):
                provider._validated_complete_scopes("kanban:read invalid kanban:write")
            with self.assertRaises(TokenError):
                provider._validated_complete_scopes(["kanban:read"], token_error=True)

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

    def test_register_client_accepts_codex_versioned_loopback_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="codex-cli",
                redirect_uris=["http://127.0.0.1:49152/callback/Abcdef01_-XY"],
                token_endpoint_auth_method="none",
                scope="kanban:read kanban:write",
            )

            asyncio.run(provider.register_client(client))

            self.assertIsNotNone(asyncio.run(provider.get_client("codex-cli")))

    def test_register_client_accepts_issuer_bound_codex_relay_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="codex-cli-relay",
                redirect_uris=["https://agent.example/callback/Abcdef01_-XY"],
                token_endpoint_auth_method="none",
                scope="kanban:read kanban:write",
            )

            asyncio.run(provider.register_client(client))

            self.assertIsNotNone(asyncio.run(provider.get_client("codex-cli-relay")))

    def test_register_client_accepts_issuer_bound_codex_relay_endpoint(self) -> None:
        for suffix in ("", "/"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp_dir:
                provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
                client = OAuthClientInformationFull(
                    client_id=f"codex-cli-relay-endpoint-{len(suffix)}",
                    redirect_uris=[f"https://agent.example/codex-oauth{suffix}"],
                    token_endpoint_auth_method="none",
                    scope="kanban:read kanban:write",
                )

                asyncio.run(provider.register_client(client))

                self.assertIsNotNone(asyncio.run(provider.get_client(client.client_id)))

    def test_authorization_requires_exact_audience_scopes_and_owner_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                client_name="Codex Test",
                redirect_uris=["http://127.0.0.1:18765/callback"],
                scope="kanban:read kanban:write",
            )
            base_params = {
                "state": "state-1",
                "scopes": ["kanban:read", "kanban:write"],
                "code_challenge": "pkce-challenge",
                "redirect_uri": AnyUrl("http://127.0.0.1:18765/callback"),
                "redirect_uri_provided_explicitly": True,
            }

            consent_url = asyncio.run(
                provider.authorize(
                    client,
                    AuthorizationParams(
                        **base_params,
                        resource="https://agent.example/mcp",
                    ),
                )
            )
            request_id = consent_url.rsplit("request_id=", 1)[1]
            self.assertIn("/oauth/authorize?", consent_url)
            self.assertEqual(
                provider.get_pending_authorization(request_id)["client_name"], "Codex Test"
            )
            callback_url = provider.approve_authorization(request_id, subject="admin")
            self.assertIn("code=", callback_url)
            self.assertIn("state=state-1", callback_url)
            self.assertIsNone(provider.get_pending_authorization(request_id))

            with self.assertRaises(AuthorizeError):
                asyncio.run(
                    provider.authorize(
                        client,
                        AuthorizationParams(
                            **base_params,
                            resource="https://wrong.example/mcp",
                        ),
                    )
                )
            with self.assertRaises(AuthorizeError):
                asyncio.run(
                    provider.authorize(
                        client,
                        AuthorizationParams(
                            **{**base_params, "scopes": ["kanban:read"]},
                            resource="https://agent.example/mcp",
                        ),
                    )
                )

    def test_state_file_is_private_and_encrypted_at_rest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "mcp-oauth-state.json"
            provider = self._provider(state_file)
            client = OAuthClientInformationFull(
                client_id="client-1",
                client_secret="private-client-secret",
                redirect_uris=["https://chatgpt.com/connector/oauth/test-callback"],
                scope="kanban:read kanban:write",
            )

            asyncio.run(provider.register_client(client))

            ciphertext = state_file.read_bytes()
            self.assertNotIn(b"private-client-secret", ciphertext)
            self.assertNotIn(b"client-1", ciphertext)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
            decrypted = self._decrypted_state(provider, state_file)
            self.assertEqual(decrypted["clients"]["client-1"]["client_id"], "client-1")

    def test_register_client_rejects_non_chatgpt_redirects(self) -> None:
        blocked_redirects = [
            "https://example.com/connector/oauth/test-callback",
            "https://chatgpt.com.evil.example/connector/oauth/test-callback",
            "http://chatgpt.com/connector/oauth/test-callback",
            "https://chatgpt.com/not-connector/oauth/test-callback",
            "http://127.0.0.1:49152/callback/too-short",
            "http://127.0.0.1:49152/callback/Abcdef01_-XY/extra",
            "http://127.0.0.1:49152/callback/Abcdef01_-XY?redirect=evil",
            "https://example.com/callback/Abcdef01_-XY",
            "https://agent.example/callback/too-short",
            "https://agent.example/callback/Abcdef01_-XY/extra",
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
                provider._is_allowed_redirect_uri(
                    "https://chatgpt.com:bad/connector/oauth/test-callback"
                )
            )
            self.assertFalse(
                provider._is_allowed_redirect_uri(
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
            code = OwnerAuthorizationCode(
                code="code-1",
                scopes=["kanban:read", "kanban:write"],
                expires_at=time.time() + 300,
                client_id="client-1",
                code_challenge="challenge",
                redirect_uri="https://chatgpt.com/callback",
                redirect_uri_provided_explicitly=True,
                resource="https://agent.example/mcp",
                subject="admin",
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "pending_authorizations": {},
                    "authorization_codes": {
                        provider._token_digest(code.code): provider._stored_secret_model(code)
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
            refresh = OwnerRefreshToken(
                token="refresh-1",
                client_id="client-1",
                scopes=["kanban:read", "kanban:write"],
                expires_at=int(time.time()) + 300,
                subject="admin",
                family_id="family-1",
                resource="https://agent.example/mcp",
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "pending_authorizations": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {
                        provider._token_digest(refresh.token): provider._stored_secret_model(
                            refresh
                        )
                    },
                }
            )

            token = asyncio.run(provider.exchange_refresh_token(client, refresh, []))

            self.assertTrue(token.access_token.startswith("mkat_"))
            self.assertTrue(token.refresh_token.startswith("mkrt_"))
            self.assertEqual("kanban:read kanban:write", token.scope)
            with self.assertRaises(TokenError):
                asyncio.run(provider.exchange_refresh_token(client, refresh, []))
            state = provider._read_state()
            self.assertNotIn(provider._token_digest("refresh-1"), state["refresh_tokens"])
            self.assertEqual(1, len(state["access_tokens"]))
            self.assertEqual(1, len(state["refresh_tokens"]))

    def test_exchange_refresh_token_rejects_scope_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(Path(temp_dir) / "mcp-oauth-state.json")
            client = OAuthClientInformationFull(
                client_id="client-1",
                redirect_uris=["https://chatgpt.com/callback"],
            )
            refresh = OwnerRefreshToken(
                token="refresh-1",
                client_id="client-1",
                scopes=["kanban:read", "kanban:write"],
                expires_at=int(time.time()) + 300,
                subject="admin",
                family_id="family-1",
                resource="https://agent.example/mcp",
            )
            provider._write_state_unlocked(
                {
                    "clients": {},
                    "pending_authorizations": {},
                    "authorization_codes": {},
                    "access_tokens": {},
                    "refresh_tokens": {
                        provider._token_digest(refresh.token): provider._stored_secret_model(
                            refresh
                        )
                    },
                }
            )

            with self.assertRaises(TokenError):
                asyncio.run(provider.exchange_refresh_token(client, refresh, ["kanban:read"]))
            state = provider._read_state()
            self.assertIn(provider._token_digest("refresh-1"), state["refresh_tokens"])
            self.assertEqual({}, state["access_tokens"])


if __name__ == "__main__":
    unittest.main()
