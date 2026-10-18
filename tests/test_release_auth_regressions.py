from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_loader_support import load_module_from_file
from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError  # noqa: E402
from minimal_kanban.mcp.server import (  # noqa: E402
    _authenticate_oauth_owner,
    _nested_oauth_value,
)
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402
from tests.mcp_credentials import MCP_TEST_TOKEN  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class ReleaseAuthRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        environment = patch.dict(
            os.environ,
            {
                "APPDATA": directory.name,
                "AUTOSTOP_DEPLOYMENT_ENV": "development",
                "AUTOSTOP_MAINTENANCE_MARKER": str(root / "absent-maintenance"),
                "MINIMAL_KANBAN_API_BEARER_TOKEN": "",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": MCP_TEST_TOKEN,
                "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0",
                "AUTOSTOP_AGENT_SERVICE_IDENTITY": "release-probe-service",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        logger = logging.Logger(self.id())
        logger.addHandler(logging.NullHandler())
        self.state_file = root / "state.json"
        self.store = JsonStore(state_file=self.state_file, logger=logger)
        self.service = CardService(self.store, logger)
        self.service.create_card({"title": "Synthetic release probe", "vehicle": "Test"})
        self.operators = OperatorAuthService(
            self.store, self.service, users_file=root / "users.json", logger=logger
        )
        self.api = ApiServer(
            self.service,
            logger,
            host="0.0.0.0",
            start_port=0,
            bearer_token="",
            operator_service=self.operators,
        )
        self.api.start()
        self.addCleanup(self.api.stop)
        self.probe = load_module_from_file("release_perf_probe", ROOT / "scripts/perf_probe.py")

    def run_probe(self, *arguments: str) -> tuple[int, dict, str]:
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "perf_probe.py",
                    "--base-url",
                    self.api.base_url,
                    "--iterations",
                    "1",
                    "--warmup-iterations",
                    "1",
                    *arguments,
                ],
            ),
            redirect_stdout(output),
        ):
            exit_code = self.probe.main()
        return exit_code, json.loads(output.getvalue()), output.getvalue()

    def test_live_probe_uses_scoped_service_without_api_bearer_or_writes(self) -> None:
        before = self.state_file.read_bytes()
        exit_code, result, output = self.run_probe("--mcp-service-auth")

        self.assertEqual(exit_code, 0, result)
        self.assertEqual(result["threshold_status"], "passed")
        self.assertEqual(
            [row["label"] for row in result["rows"]],
            ["snapshot.identity", "snapshot.gzip", "revision", "get_card"],
        )
        self.assertTrue(all(row["statuses"] == [200] for row in result["rows"]))
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertNotIn(MCP_TEST_TOKEN, output)
        self.assertNotIn(self.api.agent_bearer_token, output)
        with self.assertRaisesRegex(ValueError, "authentication failed \\(HTTP 401\\)"):
            self.probe.request_json(self.api.base_url, "/api/get_board_revision")

    def test_live_probe_bypasses_environment_proxy_for_service_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "http_proxy": "http://192.0.2.1:9",
                "https_proxy": "http://192.0.2.1:9",
                "HTTP_PROXY": "http://192.0.2.1:9",
                "HTTPS_PROXY": "http://192.0.2.1:9",
                "NO_PROXY": "",
                "no_proxy": "",
            },
        ):
            self.probe = load_module_from_file(
                "release_perf_probe_proxy", ROOT / "scripts/perf_probe.py"
            )
            exit_code, result, output = self.run_probe("--mcp-service-auth")
        self.assertEqual(exit_code, 0, result)
        self.assertNotIn(MCP_TEST_TOKEN, output)

    def test_live_probe_reports_authentication_failure_without_snapshot_shape_error(self) -> None:
        exit_code, result, _ = self.run_probe()
        self.assertEqual(exit_code, 2)
        self.assertIn("authentication failed (HTTP 401)", result["error"])
        self.assertNotIn("snapshot data", result["error"])

    def test_live_probe_rejects_wrong_service_token(self) -> None:
        with patch.dict(os.environ, {"PROBE_WRONG_TOKEN": "wrong-token"}):
            exit_code, result, output = self.run_probe(
                "--mcp-service-auth", "--mcp-token-env", "PROBE_WRONG_TOKEN"
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("authentication failed (HTTP 401)", result["error"])
        self.assertNotIn("wrong-token", output)

    def test_live_probe_retains_separate_api_bearer_when_configured(self) -> None:
        self.api.stop()
        self.api = ApiServer(
            self.service,
            logging.Logger(self.id()),
            start_port=0,
            bearer_token="separate-api-credential",
            operator_service=self.operators,
        )
        self.api.start()
        self.addCleanup(self.api.stop)
        with patch.dict(os.environ, {"MINIMAL_KANBAN_API_BEARER_TOKEN": "separate-api-credential"}):
            exit_code, result, output = self.run_probe("--mcp-service-auth")
        self.assertEqual(exit_code, 0, result)
        self.assertNotIn("separate-api-credential", output)
        self.assertNotIn(MCP_TEST_TOKEN, output)

    def test_live_probe_requires_enabled_gateway_and_reports_missing_credentials(self) -> None:
        for environment, error in (
            ({"AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "0"}, "requires the enabled Gateway"),
            ({"MINIMAL_KANBAN_MCP_BEARER_TOKEN": ""}, "Missing or invalid MCP service credentials"),
        ):
            with self.subTest(environment=list(environment)):
                with patch.dict(os.environ, environment):
                    exit_code, result, output = self.run_probe("--mcp-service-auth")
                self.assertEqual(exit_code, 2)
                self.assertIn(error, result["error"])
                self.assertNotIn(MCP_TEST_TOKEN, output)

    def test_oauth_owner_closes_actual_temporary_operator_session(self) -> None:
        class RecordingClient(BoardApiClient):
            session_token = ""

            def _request(self, path, *args, **kwargs):
                response = super()._request(path, *args, **kwargs)
                if path == "/api/login_operator":
                    self.session_token = _nested_oauth_value(response.get("data"), "token")
                return response

        client = RecordingClient(self.api.base_url, bearer_token=self.api.agent_bearer_token)
        owner = _authenticate_oauth_owner(client, "admin", "admin")

        self.assertEqual((owner or "").casefold(), "admin")
        self.assertTrue(client.session_token)
        self.assertIsNone(self.operators.resolve_session(client.session_token))
        rejected = client._request(
            "/api/logout_operator", {}, extra_headers={"X-Operator-Session": client.session_token}
        )
        self.assertIs(rejected["ok"], False)


class OAuthOwnerCleanupFailureTests(unittest.TestCase):
    def test_consent_requires_successful_session_cleanup_for_admin_and_nonadmin(self) -> None:
        for admin in (True, False):
            for cleanup in (
                {"ok": True},
                {"ok": False, "error": {"code": "unauthorized"}},
                {},
                BoardApiTransportError("Synthetic cleanup failure"),
            ):
                with self.subTest(admin=admin, cleanup=cleanup):
                    login = {
                        "ok": True,
                        "data": {
                            "session": {"token": "session", "username": "OWNER", "is_admin": admin}
                        },
                    }
                    client = Mock(spec=BoardApiClient)
                    client._request.side_effect = [login, cleanup]
                    owner = _authenticate_oauth_owner(client, "owner", "password")
                    self.assertEqual(owner, "OWNER" if admin and cleanup == {"ok": True} else None)
                    self.assertEqual(client._request.call_count, 2)
                    self.assertEqual(
                        client._request.call_args.kwargs["extra_headers"],
                        {"X-Operator-Session": "session"},
                    )

    def test_failed_login_or_missing_session_does_not_approve_owner(self) -> None:
        for login in ({"ok": False}, {"ok": True, "data": {"is_admin": True}}):
            with self.subTest(login=login):
                client = Mock(spec=BoardApiClient)
                client._request.return_value = login
                self.assertIsNone(_authenticate_oauth_owner(client, "owner", "password"))
                client._request.assert_called_once()


class PerfProbeServiceAuthScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = load_module_from_file("scoped_perf_probe", ROOT / "scripts/perf_probe.py")
        self.auth = self.probe.McpServiceAuth("release-probe-service", MCP_TEST_TOKEN)

    def test_service_credentials_cannot_leave_loopback_origin_or_measured_read_routes(self) -> None:
        cases = [
            (url, "/api/get_board_revision", "GET")
            for url in (
                "https://crm.example",
                "http://127.0.0.1.example",
                "http://user:password@127.0.0.1",
                "http://127.0.0.1/prefix",
                "http://127.0.0.1?target=other",
                "http://127.0.0.1#fragment",
                "ftp://127.0.0.1",
            )
        ] + [
            ("http://127.0.0.1", path, method)
            for path, method in (
                ("/api/delete_card", "POST"),
                ("/api/get_card", "GET"),
                ("/api/get_board_revision", "POST"),
                ("//crm.example/api/get_board_revision", "GET"),
                ("https://crm.example/api/get_board_revision", "GET"),
                ("/api/get_board_revision#fragment", "GET"),
            )
        ]
        with patch.object(self.probe._LOCAL_SERVICE_OPENER, "open") as open_request:
            for base_url, path, method in cases:
                with self.subTest(base_url=base_url, path=path, method=method):
                    with self.assertRaises(ValueError):
                        self.probe.request_json(
                            base_url, path, method=method, mcp_service_auth=self.auth
                        )
            open_request.assert_not_called()

    def test_service_request_preserves_read_arguments_and_replaces_source(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        path, payload, headers = self.probe._mcp_service_request(
            "http://[::1]:41731/",
            "/api/get_board_snapshot?compact=1&include_archive=0&source=ui",
            "GET",
            None,
            self.auth,
        )
        self.assertEqual(
            parse_qs(urlsplit(path).query),
            {"compact": ["1"], "include_archive": ["0"], "source": ["mcp_agent_gateway_v2"]},
        )
        self.assertIsNone(payload)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["X-Autostop-Agent-Token"], MCP_TEST_TOKEN)
        self.assertEqual(headers["X-Autostop-Agent-Identity"], "release-probe-service")
        _, payload, _ = self.probe._mcp_service_request(
            "http://127.0.0.1",
            "/api/get_card",
            "POST",
            {"card_id": "c1", "source": "ui"},
            self.auth,
        )
        self.assertEqual(payload, {"card_id": "c1", "source": "mcp_agent_gateway_v2"})

    def test_service_transport_refuses_redirect_without_forwarding_credential(self) -> None:
        seen_paths = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen_paths.append(self.path)
                body = b'{"ok": true}'
                self.send_response(302 if self.path.startswith("/api/") else 200)
                self.send_header("Location", "/leak")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, "API request redirected"):
                self.probe.request_json(
                    f"http://127.0.0.1:{server.server_port}",
                    "/api/get_board_revision",
                    mcp_service_auth=self.auth,
                )
            self.assertEqual(seen_paths, ["/api/get_board_revision?source=mcp_agent_gateway_v2"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_missing_or_invalid_service_credentials_are_rejected_without_disclosure(self) -> None:
        for identity, token in (
            ("", MCP_TEST_TOKEN),
            ("service", ""),
            ("service", "hidden\nvalue"),
        ):
            with self.subTest(identity=identity, token_present=bool(token)):
                with self.assertRaisesRegex(
                    ValueError, "Missing or invalid MCP service credentials"
                ) as error:
                    self.probe.request_json(
                        "http://127.0.0.1",
                        "/api/get_board_revision",
                        mcp_service_auth=self.probe.McpServiceAuth(identity, token),
                    )
                self.assertNotIn(MCP_TEST_TOKEN, str(error.exception))
                self.assertNotIn("hidden", str(error.exception))


if __name__ == "__main__":
    unittest.main()
