from __future__ import annotations

import http.client
import io
import json
import logging
import os
import socket
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import (  # noqa: E402
    ApiServer,
    AuthenticationPolicy,
    OperatorLoginLimiter,
)
from minimal_kanban.operator_permissions import (  # noqa: E402
    SALARY_BALANCE_RESET_PERMISSION,
)
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ApiTransportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_output = io.StringIO()
        logger = logging.getLogger(f"test.api.transport.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.StreamHandler(self.log_output))
        logger.propagate = False
        self.service = CardService(
            JsonStore(
                state_file=Path(self.temp_dir.name) / "state.json",
                logger=logger,
            ),
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.server = ApiServer(
            self.service,
            logger,
            start_port=0,
            fallback_limit=1,
            bearer_token="transport-test-token",
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def _request(
        self,
        path: str,
        payload: dict,
        *,
        authorization: str = "Bearer transport-test-token",
    ) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def _raw_post(
        self,
        path: str,
        *,
        declared_length: str,
        body: bytes = b"",
        authorization: str = "Bearer transport-test-token",
    ) -> tuple[int, dict]:
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as sock:
            sock.settimeout(5)
            request = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.server.port}\r\n"
                f"Authorization: {authorization}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {declared_length}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(request + body)
            sock.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response.status, json.loads(response.read().decode("utf-8"))

    def _get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_post_rejects_non_decimal_content_length_before_dispatch(self) -> None:
        status, payload = self._raw_post(
            "/api/get_board_revision",
            declared_length="invalid",
        )

        self.assertEqual(400, status)
        self.assertEqual("validation_error", payload["error"]["code"])

    def test_truncated_json_body_is_rejected_before_dispatch(self) -> None:
        status, payload = self._raw_post(
            "/api/get_board_revision",
            declared_length="4",
            body=b"{}",
        )

        self.assertEqual(400, status)
        self.assertEqual("invalid_json", payload["error"]["code"])

    def test_empty_post_body_preserves_legacy_empty_object_dispatch(self) -> None:
        status, payload = self._raw_post(
            "/api/get_board_revision",
            declared_length="0",
        )

        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])

    def test_bearer_auth_precedes_maintenance_and_body_parsing(self) -> None:
        marker = Path(self.temp_dir.name) / ".agent-gateway-maintenance"
        marker.touch()
        with patch.dict(os.environ, {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}):
            status, payload = self._raw_post(
                "/api/create_card",
                declared_length="9",
                body=b"{not-json",
                authorization="Bearer wrong-token",
            )

        self.assertEqual(401, status)
        self.assertEqual("unauthorized", payload["error"]["code"])

    def test_registry_get_and_protected_get_preserve_auth_order(self) -> None:
        headers = {
            "Authorization": "Bearer wrong-token",
            "X-Forwarded-For": "203.0.113.10",
        }

        registry_status, registry_payload = self._get("/api/get_cards", headers=headers)
        protected_status, protected_payload = self._get(
            "/api/attachment?card_id=missing&attachment_id=missing",
            headers=headers,
        )

        self.assertEqual(401, registry_status)
        self.assertEqual("unauthorized", registry_payload["error"]["code"])
        self.assertEqual(503, protected_status)
        self.assertEqual(
            "operator_auth_unavailable",
            protected_payload["error"]["code"],
        )

    def test_service_error_preserves_public_envelope(self) -> None:
        handler = self.server._server.RequestHandlerClass  # type: ignore[union-attr]

        def fail_with_service_error(_payload: dict) -> dict:
            raise ServiceError(
                "characterized_conflict",
                "Characterized public message.",
                status_code=409,
                details={"field": "revision"},
            )

        handler.ROUTES["/api/get_board_revision"] = fail_with_service_error
        status, payload = self._request("/api/get_board_revision", {})

        self.assertEqual(409, status)
        self.assertEqual("characterized_conflict", payload["error"]["code"])
        self.assertEqual("Characterized public message.", payload["error"]["message"])
        self.assertEqual({"field": "revision"}, payload["error"]["details"])

    def test_route_without_route_spec_fails_closed_before_dispatch(self) -> None:
        handler = self.server._server.RequestHandlerClass  # type: ignore[union-attr]
        dispatched = False

        def unclassified_route(_payload: dict) -> dict:
            nonlocal dispatched
            dispatched = True
            return {"unexpected": True}

        handler.ROUTES["/api/unclassified-test-route"] = unclassified_route
        status, payload = self._request("/api/unclassified-test-route", {})

        self.assertEqual(500, status)
        self.assertEqual("internal_error", payload["error"]["code"])
        self.assertFalse(dispatched)

    def test_unexpected_error_envelope_and_logs_do_not_leak_exception_message(self) -> None:
        handler = self.server._server.RequestHandlerClass  # type: ignore[union-attr]
        secret_marker = "sensitive-business-payload-marker"

        def fail_unexpectedly(_payload: dict) -> dict:
            raise RuntimeError(secret_marker)

        handler.ROUTES["/api/get_board_revision"] = fail_unexpectedly
        status, payload = self._request("/api/get_board_revision", {})
        response_text = json.dumps(payload, ensure_ascii=False)
        log_text = self.log_output.getvalue()

        self.assertEqual(500, status)
        self.assertEqual("internal_error", payload["error"]["code"])
        self.assertNotIn(secret_marker, response_text)
        self.assertNotIn(secret_marker, log_text)


class AuthenticationPolicyUnitTests(unittest.TestCase):
    @staticmethod
    def _policy() -> AuthenticationPolicy:
        return AuthenticationPolicy(
            bearer_token="",
            operator_service=None,
            readonly_routes=set(),
            operator_session_routes=set(),
            admin_only_routes=set(),
            maintenance_technical_write_routes=set(),
        )

    @staticmethod
    def _handler(peer_host: str, *, real_ip: str = "") -> SimpleNamespace:
        headers = Message()
        if real_ip:
            headers["X-Real-IP"] = real_ip
        return SimpleNamespace(
            client_address=(peer_host, 41731),
            headers=headers,
            ROUTES={},
            _send_error_response=Mock(),
        )

    def test_operator_permission_route_uses_resolved_session_not_request_body(self) -> None:
        route = "/api/reset_employee_salary_balance"

        class OperatorService:
            def __init__(self, session: dict) -> None:
                self.session = session

            def resolve_session(self, _token: str) -> dict:
                return dict(self.session)

        denied_handler = self._handler("127.0.0.1")
        denied_handler.headers["X-Operator-Session"] = "denied-session"
        denied_policy = AuthenticationPolicy(
            bearer_token="",
            operator_service=OperatorService(
                {"username": "CODEX", "is_admin": True, "permissions": []}
            ),
            readonly_routes=set(),
            operator_session_routes={route},
            admin_only_routes=set(),
            maintenance_technical_write_routes={route},
            operator_permission_routes={route: SALARY_BALANCE_RESET_PERMISSION},
        )
        denied = denied_policy.operator_context_payload(
            denied_handler,
            route,
            {
                "_operator_session": {
                    "username": "SPOOF",
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                }
            },
            "permission-denied",
        )
        self.assertIsNone(denied)
        denied_handler._send_error_response.assert_called_once()
        self.assertEqual(denied_handler._send_error_response.call_args.args[1], 403)

        allowed_handler = self._handler("127.0.0.1")
        allowed_handler.headers["X-Operator-Session"] = "allowed-session"
        allowed_policy = AuthenticationPolicy(
            bearer_token="",
            operator_service=OperatorService(
                {
                    "username": "MARIA",
                    "is_admin": False,
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                }
            ),
            readonly_routes=set(),
            operator_session_routes={route},
            admin_only_routes=set(),
            maintenance_technical_write_routes={route},
            operator_permission_routes={route: SALARY_BALANCE_RESET_PERMISSION},
        )
        allowed = allowed_policy.operator_context_payload(
            allowed_handler,
            route,
            {"_operator_session": {"username": "SPOOF"}},
            "permission-allowed",
        )
        self.assertEqual(allowed["_operator_session"]["username"], "MARIA")
        self.assertEqual(
            allowed["_operator_session"]["permissions"],
            [SALARY_BALANCE_RESET_PERMISSION],
        )
        allowed_handler._send_error_response.assert_not_called()

    def test_login_client_key_trusts_real_ip_only_from_loopback_or_private_peer(self) -> None:
        self.assertEqual(
            "1.1.1.1",
            OperatorLoginLimiter.client_key(self._handler("127.0.0.1", real_ip="1.1.1.1")),
        )
        self.assertEqual(
            "1.1.1.1",
            OperatorLoginLimiter.client_key(self._handler("10.0.0.4", real_ip="1.1.1.1")),
        )
        self.assertEqual(
            "8.8.8.8",
            OperatorLoginLimiter.client_key(self._handler("8.8.8.8", real_ip="1.1.1.1")),
        )

    def test_login_reservation_is_released_after_non_auth_failures(self) -> None:
        policy = self._policy()
        handler = self._handler("127.0.0.1")

        def fail_validation(_payload: dict) -> dict:
            raise ServiceError("validation_error", "invalid", status_code=400)

        def fail_unexpected(_payload: dict) -> dict:
            raise RuntimeError("unexpected")

        with patch(
            "minimal_kanban.api.server.OPERATOR_LOGIN_FAILURE_LIMIT_PER_CLIENT",
            1,
        ):
            handler.ROUTES["/api/login_operator"] = fail_validation
            for request_id in ("first", "second"):
                with self.assertRaises(ServiceError) as raised:
                    policy.login_operator(handler, {}, request_id)
                self.assertEqual("validation_error", raised.exception.code)

            handler.ROUTES["/api/login_operator"] = fail_unexpected
            for request_id in ("third", "fourth"):
                with self.assertRaises(RuntimeError):
                    policy.login_operator(handler, {}, request_id)


class ApiRuntimeShutdownTests(unittest.TestCase):
    def test_readiness_failure_retains_handles_when_cleanup_is_uncertain(self) -> None:
        logger = logging.getLogger(f"test.api.start_cleanup.{self._testMethodName}")
        server = ApiServer(Mock(), logger, start_port=41731, fallback_limit=1)
        http_server = Mock()
        http_server.server_address = ("127.0.0.1", 41731)
        thread = Mock()
        thread.is_alive.return_value = True

        with (
            patch.object(server, "_make_handler", return_value=Mock()),
            patch.object(
                server,
                "_wait_until_accepting",
                side_effect=RuntimeError("readiness failed"),
            ),
            patch(
                "minimal_kanban.api.server.ReusableThreadingHTTPServer",
                return_value=http_server,
            ),
            patch("minimal_kanban.api.server.threading.Thread", return_value=thread),
            self.assertRaises(RuntimeError),
        ):
            server.start()

        self.assertIs(http_server, server._server)
        self.assertIs(thread, server._thread)
        http_server.server_close.assert_not_called()

    def test_stop_retains_handles_when_thread_remains_alive(self) -> None:
        logger = logging.getLogger(f"test.api.stop.{self._testMethodName}")
        server = ApiServer(Mock(), logger, start_port=41731, fallback_limit=1)
        http_server = Mock()
        thread = Mock()
        thread.is_alive.return_value = True
        server._server = http_server
        server._thread = thread

        with self.assertRaisesRegex(RuntimeError, "не остановился"):
            server.stop()

        http_server.shutdown.assert_called_once_with()
        http_server.server_close.assert_not_called()
        thread.join.assert_called_once_with(timeout=5)
        self.assertIs(http_server, server._server)
        self.assertIs(thread, server._thread)


if __name__ == "__main__":
    unittest.main()
