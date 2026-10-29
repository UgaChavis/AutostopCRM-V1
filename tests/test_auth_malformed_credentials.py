from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.deployment_security import (  # noqa: E402
    release_smoke_proof,
    release_smoke_proof_matches,
)
from minimal_kanban.mcp.auth import StaticBearerTokenVerifier  # noqa: E402
from minimal_kanban.mcp.oauth_provider import (  # noqa: E402
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    EmbeddedOAuthAuthorizationServerProvider,
)
from tests import test_api  # noqa: E402
from tests.mcp_credentials import MCP_TEST_TOKEN


class MalformedCredentialHttpTests(unittest.TestCase):
    def setUp(self):
        self.api = test_api.ApiServerTests("test_api_server_supports_os_assigned_port")
        self.api.setUp()
        self.addCleanup(self.api.doCleanups)
        environment = patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": MCP_TEST_TOKEN,
                "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
                "AUTOSTOP_AGENT_SERVICE_IDENTITY": "credential-probe",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        status, login = self.api.request(
            "/api/login_operator",
            {
                "username": "admin",
                "password": "admin",
            },
        )
        self.assertEqual(status, 200)
        self.session = login["data"]["session"]["token"]
        self.before_state = (Path(self.api.temp_dir.name) / "state.json").read_bytes()
        self.before_users = self.api.users_file.read_bytes()

    def assert_auth_rejected(self, route, payload, headers):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status, result = self.api.request(route, payload, headers=headers)
        self.assertEqual(status, 401, result)
        self.assertEqual(result["error"]["code"], "unauthorized")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            (Path(self.api.temp_dir.name) / "state.json").read_bytes(), self.before_state
        )
        self.assertEqual(self.api.users_file.read_bytes(), self.before_users)

    def test_operator_session_rejects_non_ascii_in_proxied_and_local_requests(self):
        for malformed in ("\u00e9", "token\u00ff"):
            for method in ("GET", "POST"):
                for proxied in (False, True):
                    with self.subTest(malformed=repr(malformed), method=method, proxied=proxied):
                        headers = {"X-Operator-Session": malformed}
                        if proxied:
                            headers["X-Forwarded-For"] = "192.0.2.50"
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            status, result = self.api.request(
                                "/api/get_operator_profile",
                                {} if method == "POST" else None,
                                method=method,
                                headers=headers,
                            )
                        self.assertEqual(status, 401, result)
                        self.assertEqual(stderr.getvalue(), "")
        status, result = self.api.request(
            "/api/get_operator_profile",
            {},
            headers={
                "X-Operator-Session": self.session,
                "X-Forwarded-For": "192.0.2.50",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["data"]["user"]["is_admin"])

    def test_scoped_automation_bearer_rejects_non_ascii_before_dispatch(self):
        self.assert_auth_rejected(
            "/api/change_feed/readiness",
            {},
            {
                "Authorization": "Bearer \u00e9",
                "X-Autostop-Automation-Protocol": "crm_digest_v1",
            },
        )

    def test_service_identity_and_signed_audit_reject_non_ascii(self):
        valid = {
            "X-Autostop-Agent-Identity": "credential-probe",
            "X-Autostop-Agent-Token": MCP_TEST_TOKEN,
        }
        for changes in (
            {"X-Autostop-Agent-Identity": "\u00e9"},
            {"X-Autostop-Agent-Token": "\u00e9"},
            {OAUTH_AUDIT_ACTOR_HEADER: "ADMIN", OAUTH_AUDIT_ASSERTION_HEADER: "\u00e9"},
        ):
            with self.subTest(headers=list(changes)):
                self.assert_auth_rejected(
                    "/api/get_operator_profile",
                    {
                        "source": "mcp_agent_gateway_v2",
                    },
                    {**valid, **changes},
                )


class MalformedCredentialProtocolTests(unittest.TestCase):
    def test_static_and_legacy_oauth_verifiers_fail_closed_without_changing_state(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"APPDATA": directory}),
        ):
            provider = EmbeddedOAuthAuthorizationServerProvider(
                issuer_url="https://agent.example",
                resource_url="https://agent.example/mcp",
                state_file=Path(directory) / "oauth.json",
                legacy_bearer_token=MCP_TEST_TOKEN,
            )
            verifier = StaticBearerTokenVerifier(
                MCP_TEST_TOKEN, resource_url="https://agent.example/mcp"
            )
            before = provider._state_file.read_bytes()
            for value in ("\u00e9", "\ud800", "\U0001f600", "wrong-token"):
                with self.subTest(value=repr(value)):
                    self.assertIsNone(asyncio.run(verifier.verify_token(value)))
                    self.assertIsNone(asyncio.run(provider.load_access_token(value)))
            self.assertIsNotNone(asyncio.run(verifier.verify_token(MCP_TEST_TOKEN)))
            self.assertIsNotNone(asyncio.run(provider.load_access_token(MCP_TEST_TOKEN)))
            self.assertEqual(provider._state_file.read_bytes(), before)

    def test_release_proof_rejects_non_ascii_and_retains_exact_valid_proof(self):
        revision = "a" * 40
        proof = release_smoke_proof(MCP_TEST_TOKEN, revision)
        self.assertTrue(release_smoke_proof_matches(MCP_TEST_TOKEN, revision, proof))
        for value in ("\u00e9", "\ud800", "\U0001f600", proof + "wrong"):
            with self.subTest(value=repr(value)):
                self.assertFalse(release_smoke_proof_matches(MCP_TEST_TOKEN, revision, value))
