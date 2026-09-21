from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.route_registry import policy_for_route  # noqa: E402
from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.automation_center_service import (  # noqa: E402
    AUTOMATION_CONTROL_PROTOCOL,
    AutomationCenterService,
    AutomationControlClient,
    AutomationControlError,
)
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class _FakeClient:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {}
        self.calls: list[tuple[str, dict, dict]] = []

    def request(self, operation: str, payload: dict, *, actor: dict) -> dict:
        self.calls.append((operation, payload, actor))
        return self.result


class AutomationCenterServiceTests(unittest.TestCase):
    def test_status_derives_actor_and_projects_only_safe_fields(self) -> None:
        client = _FakeClient(
            {
                "generated_at": "2026-09-21T00:00:00+00:00",
                "controller": {"state": "ready", "heartbeat_at": "now", "secret": "no"},
                "global_hold": {"enabled": False, "revision": 3, "reason": "private"},
                "jobs": [
                    {
                        "job_id": "crm-digest",
                        "name": "CRM digest",
                        "desired_state": "off",
                        "actual_state": "stopped",
                        "revision": 1,
                        "private_payload": "must-not-pass",
                    }
                ],
                "system_timers": [],
                "readiness": {"checks": [{"id": "socket", "label": "Socket", "state": "ok"}]},
                "templates": [
                    {
                        "template_id": "crm_digest_v1",
                        "name": "CRM digest",
                        "singleton": True,
                        "default_schedule": {
                            "every_minutes": 20,
                            "timezone": "Asia/Krasnoyarsk",
                            "active_window": "24/7",
                        },
                        "limits": {
                            "minimum_every_minutes": 5,
                            "maximum_every_minutes": 1440,
                        },
                    }
                ],
            }
        )
        service = AutomationCenterService(client)  # type: ignore[arg-type]

        result = service.status(
            {
                "actor": {"id": "spoofed"},
                "_operator_session": {"username": "operator-1", "is_admin": False},
            }
        )

        self.assertFalse(result["can_manage"])
        self.assertNotIn("secret", result["controller"])
        self.assertEqual([], result["jobs"])
        self.assertEqual("off", result["controller"]["overall_state"])
        self.assertEqual("operator-1", client.calls[0][2]["id"])
        self.assertEqual("operator", client.calls[0][1]["view"])

        admin_result = service.status(
            {"_operator_session": {"username": "admin-1", "is_admin": True}}
        )
        self.assertNotIn("private_payload", admin_result["jobs"][0])
        self.assertEqual(20, admin_result["templates"][0]["default_every_minutes"])
        self.assertTrue(admin_result["templates"][0]["singleton"])
        self.assertEqual("Asia/Krasnoyarsk", admin_result["templates"][0]["default_timezone"])
        self.assertEqual("24/7", admin_result["templates"][0]["default_active_window"])
        self.assertEqual(5, admin_result["templates"][0]["min_every_minutes"])
        self.assertNotIn("reason", admin_result["controller"]["hold"])

    def test_status_marks_readiness_degradation_and_timer_drift_without_false_green(self) -> None:
        client = _FakeClient(
            {
                "controller": {"state": "ready"},
                "global_hold": {"enabled": False},
                "jobs": [
                    {
                        "job_id": "crm-digest",
                        "desired_state": "on",
                        "actual_state": "idle",
                        "revision": 4,
                        "applied_revision": 4,
                    },
                    {
                        "job_id": "unknown-job",
                        "desired_state": "on",
                        "actual_state": "unknown",
                        "revision": 1,
                        "applied_revision": 1,
                    },
                ],
                "system_timers": [
                    {
                        "timer_id": "managed-health",
                        "control_mode": "managed",
                        "desired_state": "on",
                        "actual_state": "active",
                        "period_minutes": 20,
                        "actual_period_minutes": 15,
                        "reconcile_state": "drift",
                    },
                    {
                        "timer_id": "unknown-timer",
                        "control_mode": "managed",
                        "desired_state": "off",
                        "actual_state": "unknown",
                    },
                ],
                "readiness": {
                    "checks": {
                        "schema": "ready",
                        "crm_change_feed": "not_configured",
                    }
                },
                "templates": [],
            }
        )
        service = AutomationCenterService(client)  # type: ignore[arg-type]

        result = service.status({"_operator_session": {"username": "admin-1", "is_admin": True}})

        self.assertEqual("applying", result["controller"]["overall_state"])
        self.assertEqual(4, result["controller"]["applying_count"])
        self.assertEqual("drift", result["system_timers"][0]["reconcile_state"])
        self.assertEqual(15, result["system_timers"][0]["actual_period_minutes"])

    def test_control_requires_admin_allowlist_command_id_and_ignores_browser_actor(self) -> None:
        client = _FakeClient({"job": {"job_id": "crm-digest", "revision": 2}})
        service = AutomationCenterService(client)  # type: ignore[arg-type]
        with self.assertRaises(ServiceError) as forbidden:
            service.control(
                {
                    "operation": "set_enabled",
                    "command_id": "command-12345",
                    "_operator_session": {"username": "operator", "is_admin": False},
                }
            )
        self.assertEqual("forbidden", forbidden.exception.code)

        result = service.control(
            {
                "operation": "set_enabled",
                "command_id": "command-12345",
                "job_id": "crm-digest",
                "enabled": True,
                "actor": {"id": "spoofed"},
                "source": "browser-spoof",
                "_operator_session": {"username": "real-admin", "is_admin": True},
            }
        )
        self.assertEqual("set_enabled", result["operation"])
        self.assertEqual("real-admin", client.calls[-1][2]["id"])
        self.assertNotIn("actor", client.calls[-1][1])
        self.assertNotIn("source", client.calls[-1][1])

        service.control(
            {
                "operation": "set_schedule",
                "command_id": "command-timer-1",
                "timer_id": "managed-pc-health",
                "expected_revision": 4,
                "schedule": {"kind": "interval", "every_minutes": 10},
                "_operator_session": {"username": "real-admin", "is_admin": True},
            }
        )
        self.assertEqual("managed-pc-health", client.calls[-1][1]["timer_id"])
        self.assertNotIn("job_id", client.calls[-1][1])

        service.control(
            {
                "operation": "preview",
                "command_id": "command-preview-1",
                "target_operation": "set_schedule",
                "target_payload": {
                    "job_id": "crm-digest",
                    "schedule": {"every_minutes": 30},
                },
                "_operator_session": {"username": "real-admin", "is_admin": True},
            }
        )
        self.assertEqual("set_schedule", client.calls[-1][1]["target_operation"])
        self.assertEqual("crm-digest", client.calls[-1][1]["target_payload"]["job_id"])

    def test_route_policy_is_operator_read_and_admin_blocked_write(self) -> None:
        status = policy_for_route("/api/automation_center/status")
        control = policy_for_route("/api/automation_center/control")
        self.assertEqual("operator", status.auth_kind)
        self.assertEqual("read", status.mutation_kind)
        self.assertEqual("admin", control.auth_kind)
        self.assertEqual("write", control.mutation_kind)
        self.assertEqual("blocked", control.maintenance_behavior)


class AutomationControlClientTests(unittest.TestCase):
    def test_bounded_protocol_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control.sock"
            ready = threading.Event()
            received: list[dict] = []

            def serve() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(path))
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        request = json.loads(connection.recv(65536).split(b"\n", 1)[0])
                        received.append(request)
                        response = {
                            "protocol": AUTOMATION_CONTROL_PROTOCOL,
                            "request_id": request["request_id"],
                            "ok": True,
                            "data": {"controller": {"state": "ready"}},
                        }
                        connection.sendall(json.dumps(response).encode("utf-8") + b"\n")

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            client = AutomationControlClient(path, timeout_seconds=1)
            result = client.request(
                "status", {"view": "operator"}, actor={"id": "admin", "is_admin": True}
            )
            thread.join(2)

        self.assertEqual("ready", result["controller"]["state"])
        self.assertEqual(AUTOMATION_CONTROL_PROTOCOL, received[0]["protocol"])
        self.assertEqual("crm_operator", received[0]["actor"]["kind"])
        self.assertNotIn("source", received[0]["actor"])

    def test_command_metadata_is_only_sent_in_protocol_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control.sock"
            ready = threading.Event()
            received: list[dict] = []

            def serve() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(path))
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        request = json.loads(connection.recv(65536).split(b"\n", 1)[0])
                        received.append(request)
                        response = {
                            "protocol": AUTOMATION_CONTROL_PROTOCOL,
                            "request_id": request["request_id"],
                            "ok": True,
                            "data": {"created": True},
                        }
                        connection.sendall(json.dumps(response).encode("utf-8") + b"\n")

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            client = AutomationControlClient(path, timeout_seconds=1)
            result = client.request(
                "create_from_template",
                {
                    "command_id": "command-create-123",
                    "idempotency_key": "browser-must-not-override",
                    "expected_revision": 7,
                    "template_id": "crm_digest_v1",
                    "name": "CRM digest",
                },
                actor={"id": "admin", "is_admin": True},
            )
            thread.join(2)

        self.assertTrue(result["created"])
        self.assertEqual("command-create-123", received[0]["idempotency_key"])
        self.assertEqual(7, received[0]["expected_revision"])
        self.assertEqual(
            {"template_id": "crm_digest_v1", "name": "CRM digest"},
            received[0]["payload"],
        )

    def test_missing_socket_is_safe_unavailable_error(self) -> None:
        client = AutomationControlClient(
            Path("/tmp/does-not-exist-automation.sock"), timeout_seconds=0.1
        )
        with self.assertRaises(AutomationControlError) as error:
            client.request("status", {}, actor={"id": "operator", "is_admin": False})
        self.assertEqual("automation_control_unavailable", error.exception.code)
        self.assertEqual(503, error.exception.status_code)


class AutomationCenterHttpAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ, {"MINIMAL_KANBAN_MCP_BEARER_TOKEN": "feed-manager-token"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        logger = logging.getLogger(f"test.automation.http.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        store = JsonStore(base / "state.json", logger=logger)
        card_service = CardService(store, logger)
        self.operator_service = OperatorAuthService(
            store,
            card_service,
            users_file=base / "users.json",
            logger=logger,
        )
        self.client = _FakeClient(
            {
                "controller": {"state": "ready"},
                "jobs": [],
                "system_timers": [],
                "readiness": [],
                "templates": [],
            }
        )
        self.server = ApiServer(
            card_service,
            logger,
            operator_service=self.operator_service,
            automation_center_service=AutomationCenterService(self.client),  # type: ignore[arg-type]
            bearer_token="automation-test-secret",
            start_port=0,
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        session: str = "",
        proxied: bool = True,
        bearer: str = "automation-test-secret",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }
        if proxied:
            headers["X-Forwarded-For"] = "203.0.113.40"
        if session:
            headers["X-Operator-Session"] = session
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            self.server.base_url + path,
            data=json.dumps(payload or {}).encode("utf-8") if method == "POST" else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_operator_can_view_but_only_admin_can_control(self) -> None:
        admin_session = self.operator_service.login({"username": "admin", "password": "admin"})[
            "session"
        ]["token"]
        self.operator_service.save_user(
            {
                "_operator_session": self.operator_service.resolve_session(admin_session),
                "username": "viewer",
                "password": "viewer-pass",
                "role": "operator",
                "is_active": True,
            }
        )
        viewer_session = self.operator_service.login(
            {"username": "viewer", "password": "viewer-pass"}
        )["session"]["token"]

        missing_status, missing = self.request("/api/automation_center/status", method="GET")
        viewer_status, viewed = self.request(
            "/api/automation_center/status", method="GET", session=viewer_session
        )
        forbidden_status, forbidden = self.request(
            "/api/automation_center/control",
            {
                "operation": "set_enabled",
                "command_id": "command-viewer-1",
                "job_id": "crm-digest",
                "enabled": True,
            },
            session=viewer_session,
        )
        admin_status, applied = self.request(
            "/api/automation_center/control",
            {
                "operation": "set_enabled",
                "command_id": "command-admin-1",
                "job_id": "crm-digest",
                "enabled": False,
                "actor": {"id": "spoofed"},
            },
            session=admin_session,
        )

        self.assertEqual(401, missing_status)
        self.assertEqual("unauthorized", missing["error"]["code"])
        self.assertEqual(200, viewer_status)
        self.assertFalse(viewed["data"]["can_manage"])
        self.assertEqual(403, forbidden_status)
        self.assertEqual("forbidden", forbidden["error"]["code"])
        self.assertEqual(200, admin_status)
        self.assertEqual("set_enabled", applied["data"]["operation"])
        self.assertEqual("ADMIN", self.client.calls[-1][2]["id"])

        reserved_status, reserved = self.request(
            "/api/change_feed/register",
            {"consumer_id": "manager.crm_digest_v1", "start_at": "latest"},
            session=admin_session,
        )
        local_api_status, local_api = self.request(
            "/api/change_feed/register",
            {
                "consumer_id": "manager.crm_digest_v1",
                "start_at": "latest",
                "_automation_service": False,
            },
            proxied=False,
        )
        local_status, local = self.request(
            "/api/change_feed/register",
            {"consumer_id": "manager.crm_digest_v1", "start_at": "latest"},
            proxied=False,
            bearer="feed-manager-token",
            extra_headers={"X-Autostop-Automation-Protocol": "crm_digest_v1"},
        )
        wrong_scope_status, wrong_scope = self.request(
            "/api/change_feed/register",
            {"consumer_id": "another-consumer", "start_at": "latest"},
            proxied=False,
            bearer="feed-manager-token",
            extra_headers={"X-Autostop-Automation-Protocol": "crm_digest_v1"},
        )
        replay_status, replay = self.request(
            "/api/change_feed/register",
            {"consumer_id": "manager.crm_digest_v1", "start_at": "beginning"},
            proxied=False,
            bearer="feed-manager-token",
            extra_headers={"X-Autostop-Automation-Protocol": "crm_digest_v1"},
        )
        self.assertEqual(403, reserved_status)
        self.assertEqual("change_feed_consumer_reserved", reserved["error"]["code"])
        self.assertEqual(403, local_api_status)
        self.assertEqual("change_feed_consumer_reserved", local_api["error"]["code"])
        self.assertEqual(200, local_status)
        self.assertEqual("crm_change_feed_registration_v1", local["data"]["format"])
        self.assertEqual(403, wrong_scope_status)
        self.assertEqual("change_feed_consumer_scope_invalid", wrong_scope["error"]["code"])
        self.assertEqual(400, replay_status)
        self.assertEqual("change_feed_start_at_not_allowed", replay["error"]["code"])


if __name__ == "__main__":
    unittest.main()
