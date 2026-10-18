from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.server import ApiServer, AuthenticationPolicy
from minimal_kanban.app import _resolve_api_runtime_config
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.settings_models import IntegrationSettings
from minimal_kanban.storage.json_store import JsonStore


class ApiBindSecurityTests(unittest.TestCase):
    def test_desktop_preserves_loopback_default_and_explicit_host(self) -> None:
        settings = IntegrationSettings()
        with patch.dict(os.environ, {}, clear=True):
            host, _, token = _resolve_api_runtime_config(settings, Mock(), Mock(), Mock())
        self.assertEqual("127.0.0.1", host)
        self.assertIsNone(token)
        with patch.dict(os.environ, {"MINIMAL_KANBAN_API_HOST": "0.0.0.0"}, clear=True):
            host, _, _ = _resolve_api_runtime_config(settings, lambda: "0.0.0.0", Mock(), Mock())
        self.assertEqual("0.0.0.0", host)

    def test_non_loopback_bind_requires_strong_credential_before_side_effects(self) -> None:
        for host in ("0.0.0.0", "::", "[::]", "192.0.2.10", "crm.example", "", "127.1"):
            for token in ("", "short", " " * 32, "x" * 31, "x" * 32, "x" * 32 + "\n"):
                with self.subTest(host=host, token_length=len(token)):
                    api = ApiServer(Mock(), Mock(), host=host, start_port=0, bearer_token=token)
                    with (
                        patch.object(api, "_make_handler") as make_handler,
                        patch("minimal_kanban.api.server.ReusableThreadingHTTPServer") as bind,
                    ):
                        with self.assertRaises(ValueError):
                            api.start()
                    make_handler.assert_not_called()
                    bind.assert_not_called()

    def test_explicit_remote_credential_retains_supported_bind(self) -> None:
        api = ApiServer(
            Mock(),
            Mock(),
            host="0.0.0.0",
            start_port=0,
            bearer_token="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-",
        )
        with (
            patch.object(api, "_make_handler"),
            patch.object(api, "_wait_until_accepting"),
            patch("minimal_kanban.api.server.threading.Thread") as thread,
            patch("minimal_kanban.api.server.ReusableThreadingHTTPServer") as bind,
        ):
            bind.return_value.server_address = ("0.0.0.0", 41731)
            api.start()
            self.assertEqual("0.0.0.0", bind.call_args.args[0][0])
            thread.return_value.is_alive.return_value = False
            api.stop()

    def test_container_bind_preserves_operator_and_scoped_service_authentication(self) -> None:
        from tests.test_deployment_security import valid_production_env

        environment = valid_production_env(
            Path(tempfile.gettempdir()) / "synthetic-api-maintenance"
        )
        environment["MINIMAL_KANBAN_API_BEARER_TOKEN"] = ""
        api = ApiServer(
            Mock(),
            Mock(),
            host="0.0.0.0",
            start_port=0,
            bearer_token="",
            operator_service=Mock(),
        )
        with (
            patch.dict(os.environ, environment),
            patch.object(api, "_make_handler"),
            patch.object(api, "_wait_until_accepting"),
            patch("minimal_kanban.api.server.threading.Thread") as thread,
            patch("minimal_kanban.api.server.ReusableThreadingHTTPServer") as bind,
        ):
            bind.return_value.server_address = ("0.0.0.0", 41731)
            api.start()
            thread.return_value.is_alive.return_value = False
            api.stop()
            self.assertTrue(bind.called)
        for token, operator in (("", None), ("weak-api-token", Mock())):
            with self.subTest(api_token=bool(token), operator=operator is not None):
                api = ApiServer(
                    Mock(),
                    Mock(),
                    host="0.0.0.0",
                    start_port=0,
                    bearer_token=token,
                    operator_service=operator,
                )
                with patch.dict(os.environ, environment):
                    with self.assertRaises(ValueError):
                        api.start()


class ApiIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.state_file = root / "state.json"
        self.logger = logging.getLogger(self.id())
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.addCleanup(self.store.close)
        self.service = CardService(self.store, self.logger)
        self.operators = OperatorAuthService(
            self.store, self.service, users_file=root / "users.json", logger=self.logger
        )
        self.start_api()

    def start_api(self, *, operators: bool = True, token: str = "") -> None:
        if hasattr(self, "api"):
            self.api.stop()
        self.api = ApiServer(
            self.service,
            self.logger,
            operator_service=self.operators if operators else None,
            host="127.0.0.1",
            start_port=0,
            bearer_token=token,
        )
        self.api.start()
        self.addCleanup(self.api.stop)

    def request(self, path: str, payload: dict | None = None, *, headers: dict | None = None):
        request = urllib.request.Request(
            self.api.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST" if payload is not None else "GET",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def test_anonymous_reads_mutations_and_downloads_never_reach_handlers(self) -> None:
        routes = (
            ("/api/get_cards", None),
            ("/api/get_cards?access_token=", None),
            ("/api/list_cashboxes", {}),
            ("/api/create_card", {"title": "Untrusted", "deadline": {"hours": 1}}),
            ("/api/create_cash_transaction", {"cashbox_id": "missing", "amount": 1}),
            ("/api/attachment?card_id=missing&attachment_id=missing", None),
            ("/api/shared_file?file_id=missing", None),
            ("/api/repair_order_text?card_id=missing", None),
        )
        before = self.state_file.read_bytes()
        for path, payload in routes:
            with self.subTest(path=path, method="GET" if payload is None else "POST"):
                status, result = self.request(path, payload)
                self.assertEqual(401, status)
                self.assertEqual("unauthorized", result["error"]["code"])
        self.assertEqual(before, self.state_file.read_bytes())

    def test_absent_auth_service_and_forged_identity_fail_closed(self) -> None:
        self.start_api(operators=False)
        spoof = {"_operator_session": {"is_admin": True}, "_automation_service": True}
        for path, payload in (("/api/get_cards", None), ("/api/create_cashbox", spoof)):
            with self.subTest(path=path):
                self.assertEqual(401, self.request(path, payload)[0])

    def test_operator_login_and_current_session_allow_legitimate_work(self) -> None:
        status, result = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(200, status)
        session = result["data"]["session"]["token"]
        headers = {"X-Operator-Session": session}
        self.assertEqual(200, self.request("/api/get_cards", headers=headers)[0])
        status, created = self.request(
            "/api/create_card",
            {"title": "Authorized", "deadline": {"hours": 1}, "source": "ui"},
            headers=headers,
        )
        self.assertEqual(200, status)
        self.assertEqual("Authorized", created["data"]["card"]["title"])
        self.assertEqual(200, self.request("/api/logout_operator", {}, headers=headers)[0])
        self.assertEqual(401, self.request("/api/get_cards", headers=headers)[0])

    def test_api_service_credential_preserves_scope_and_rejects_body_token(self) -> None:
        self.start_api(operators=False, token="service-token")
        auth = {"Authorization": "Bearer service-token"}
        self.assertEqual(200, self.request("/api/list_cashboxes", {}, headers=auth)[0])
        self.assertEqual(200, self.request("/api/get_cards?access_token=service-token")[0])
        self.assertEqual(
            401, self.request("/api/list_cashboxes", {"access_token": "service-token"})[0]
        )
        self.assertEqual(401, self.request("/api/get_display_dashboard", {}, headers=auth)[0])
        self.assertEqual(
            401, self.request("/api/finance_audit/apply_safe_fixes", {}, headers=auth)[0]
        )

    def test_finance_mutation_is_denied_then_succeeds_with_service_credential(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Synthetic cashbox"})["cashbox"]
        payload = {"cashbox_id": cashbox["id"], "direction": "income", "amount": "1.00"}
        before = self.state_file.read_bytes()
        self.assertEqual(401, self.request("/api/create_cash_transaction", payload)[0])
        self.assertEqual(before, self.state_file.read_bytes())
        self.start_api(token="finance-service-token")
        status, created = self.request(
            "/api/create_cash_transaction",
            payload,
            headers={"Authorization": "Bearer finance-service-token"},
        )
        self.assertEqual(200, status)
        self.assertEqual(100, created["data"]["transaction"]["amount_minor"])

    def test_embedded_agent_credential_is_private_scoped_and_instance_specific(self) -> None:
        from minimal_kanban.mcp.client import BoardApiClient

        for operators in (True, False):
            with self.subTest(operators=operators):
                self.start_api(operators=operators)
                token = self.api.agent_bearer_token
                client = BoardApiClient(
                    self.api.base_url, bearer_token=token, default_source="agent"
                )
                self.assertTrue(client.list_cashboxes()["ok"])
                status, created = self.request(
                    "/api/create_card",
                    {"title": "Embedded agent", "deadline": {"hours": 1}, "source": "agent"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(200, status)
                self.assertEqual("Embedded agent", created["data"]["card"]["title"])
                self.assertEqual(401, self.request("/api/get_cards")[0])
                health = self.request("/api/health")[1]
                self.assertNotIn(token, json.dumps(health))
                self.assertEqual(
                    401,
                    self.request(
                        "/api/get_display_dashboard",
                        {},
                        headers={"Authorization": f"Bearer {token}"},
                    )[0],
                )
                self.start_api(operators=operators)
                self.assertNotEqual(token, self.api.agent_bearer_token)
                self.assertEqual(
                    401,
                    self.request("/api/get_cards", headers={"Authorization": f"Bearer {token}"})[0],
                )

    def test_scoped_feed_credential_cannot_authorize_crm_routes(self) -> None:
        headers = {
            "Authorization": "Bearer feed-service-token",
            "X-Autostop-Automation-Protocol": "crm_digest_v1",
        }
        with patch.dict(os.environ, {"MINIMAL_KANBAN_MCP_BEARER_TOKEN": "feed-service-token"}):
            self.assertEqual(
                200, self.request("/api/change_feed/readiness", {}, headers=headers)[0]
            )
            self.assertEqual(401, self.request("/api/list_cashboxes", {}, headers=headers)[0])
            self.assertEqual(
                401,
                self.request(
                    "/api/list_cashboxes", {}, headers={"Authorization": headers["Authorization"]}
                )[0],
            )

    def test_gateway_service_identity_requires_matching_credentials_and_source(self) -> None:
        environment = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "gateway-service-token",
        }
        headers = {
            "X-Autostop-Agent-Identity": "codex-owner-agent",
            "X-Autostop-Agent-Token": "gateway-service-token",
        }
        payload = {"source": "mcp_agent_gateway_v2"}
        with patch.dict(os.environ, environment):
            self.assertEqual(200, self.request("/api/list_cashboxes", payload, headers=headers)[0])
            self.assertEqual(401, self.request("/api/list_cashboxes", payload)[0])
            self.assertEqual(401, self.request("/api/list_cashboxes", {}, headers=headers)[0])
            self.assertEqual(401, self.request("/api/create_cashbox", payload, headers=headers)[0])


class ApiIdentitySanitizationTests(unittest.TestCase):
    def test_service_principal_cannot_supply_reserved_operator_or_feed_state(self) -> None:
        policy = AuthenticationPolicy(
            bearer_token="service-token",
            operator_service=None,
            readonly_routes=set(),
            operator_session_routes=set(),
            admin_only_routes=set(),
            maintenance_technical_write_routes=set(),
        )
        handler = SimpleNamespace(headers={"Authorization": "Bearer service-token"})
        payload = {"_operator_session": {"is_admin": True}, "_automation_service": True}
        result = policy.operator_context_payload(handler, "/api/list_cashboxes", payload, "test")
        self.assertEqual({}, result)

    def test_embedded_and_standalone_agents_forward_api_credential(self) -> None:
        from minimal_kanban.agent.bootstrap import start_embedded_agent_runtime
        from minimal_kanban.agent.runner import build_board_api_client

        logger = Mock()
        with (
            patch("minimal_kanban.agent.bootstrap.AgentStorage"),
            patch("minimal_kanban.agent.bootstrap.AgentControlService") as control,
        ):
            start_embedded_agent_runtime(
                service=None,
                logger=logger,
                board_api_url="http://127.0.0.1:41731",
                api_bearer_token="configured-api-token",
            )
            control.return_value.start_worker.assert_called_once_with(
                logger=logger,
                board_api_url="http://127.0.0.1:41731",
                api_bearer_token="configured-api-token",
            )
        with (
            patch.dict(os.environ, {"MINIMAL_KANBAN_API_BEARER_TOKEN": "configured-api-token"}),
            patch("minimal_kanban.agent.runner.get_agent_board_api_url", return_value=None),
            patch(
                "minimal_kanban.agent.runner.discover_board_api",
                return_value="http://127.0.0.1:41731",
            ) as discover,
            patch("minimal_kanban.agent.runner.BoardApiClient") as client,
        ):
            client.return_value.health.return_value = {"ok": True}
            build_board_api_client(logger=logger)
            discover.assert_called_once_with(bearer_token="configured-api-token")
            client.assert_called_once_with(
                "http://127.0.0.1:41731",
                bearer_token="configured-api-token",
                logger=logger,
                default_source="agent",
            )


if __name__ == "__main__":
    unittest.main()
