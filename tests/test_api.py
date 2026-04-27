# ruff: noqa: I001, E402
from __future__ import annotations

import base64
import json
import logging
import socket
import sys
import tempfile
import http.client
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from attachment_samples import (
    JPEG_1X1_BYTES,
    PNG_1X1_BYTES,
    minimal_docx_bytes,
    minimal_pdf_bytes,
    minimal_text_bytes,
    minimal_xlsx_bytes,
)
from minimal_kanban.api.server import ApiServer
from minimal_kanban.api.server import _success_log_level
from minimal_kanban.models import AuditEvent, utc_now
from minimal_kanban.operator_auth import OperatorAuthService, _password_hash
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiServerTests(unittest.TestCase):
    def test_snapshot_success_route_uses_debug_log_level(self) -> None:
        self.assertEqual(_success_log_level("/api/get_board_snapshot"), logging.DEBUG)
        self.assertEqual(_success_log_level("/api/health"), logging.DEBUG)
        self.assertEqual(_success_log_level("/api/create_card"), logging.INFO)

    def test_api_base_url_normalizes_wildcard_and_formats_ipv6_hosts(self) -> None:
        logger = logging.getLogger(f"test.api.base_url.{self._testMethodName}")
        wildcard = ApiServer(Mock(), logger, host="0.0.0.0", start_port=41731, fallback_limit=1)
        ipv6 = ApiServer(Mock(), logger, host="::1", start_port=41731, fallback_limit=1)
        ipv6_wildcard = ApiServer(Mock(), logger, host="[::]", start_port=41731, fallback_limit=1)
        self.assertEqual(wildcard.base_url, "http://127.0.0.1:41731")
        self.assertEqual(ipv6.base_url, "http://[::1]:41731")
        self.assertEqual(ipv6_wildcard.base_url, "http://127.0.0.1:41731")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(
            self.store,
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.operator_service = OperatorAuthService(
            self.store,
            self.service,
            users_file=Path(self.temp_dir.name) / "users.json",
            logger=logger,
        )
        self.port = reserve_port()
        self.server = ApiServer(
            self.service,
            logger,
            operator_service=self.operator_service,
            start_port=self.port,
            fallback_limit=1,
        )
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict | list | None = None,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        merged_headers = {"Content-Type": "application/json"}
        if headers:
            merged_headers.update(headers)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=merged_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def test_health_and_create_card(self) -> None:
        status, health = self.request("/api/health", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["data"]["base_url"], self.base_url)
        self.assertFalse(health["data"]["auth_required"])

        status, created = self.request(
            "/api/create_card",
            {"title": "API карточка", "deadline": {"days": 1, "hours": 2}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        self.assertEqual(created["data"]["card"]["title"], "API карточка")
        self.assertEqual(created["data"]["card"]["status"], "ok")
        self.assertEqual(created["data"]["card"]["indicator"], "green")
        self.assertIn("remaining_seconds", created["data"]["card"])
        self.assertIn("deadline_timestamp", created["data"]["card"])

    def test_client_routes_accept_documented_nested_payloads(self) -> None:
        status, created = self.request(
            "/api/create_client",
            {
                "client": {
                    "client_type": "ip",
                    "display_name": "ИП Петров Петр",
                    "inn": "540000000001",
                    "phone": "+7 913 555-66-77",
                }
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        client_id = created["data"]["client"]["id"]
        self.assertEqual(created["data"]["client"]["client_type"], "ip")
        self.assertEqual(created["data"]["client"]["inn"], "540000000001")

        status, updated = self.request(
            "/api/update_client",
            {
                "client_id": client_id,
                "patch": {
                    "bank_name": "Тест Банк",
                    "contact_person": "Петров Петр",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["data"]["client"]["bank_name"], "Тест Банк")
        self.assertEqual(updated["data"]["client"]["contact_person"], "Петров Петр")

    def test_get_repair_order_creates_it_lazily_on_first_open(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Ленивый заказ-наряд",
                "description": "Первый вход",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, listed_before = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed_before["data"]["meta"]["total"], 0)

        status, fetched = self.request(
            "/api/get_repair_order",
            {"card_id": card_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(fetched["data"]["meta"]["has_any_data"])
        self.assertTrue(fetched["data"]["meta"]["created"])
        self.assertEqual(fetched["data"]["repair_order"]["reason"], "Ленивый заказ-наряд")
        self.assertEqual(fetched["data"]["card"]["repair_order"]["number"], "1")

        status, listed_after = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed_after["data"]["meta"]["total"], 1)
        self.assertTrue(
            any(item["card_id"] == card_id for item in listed_after["data"]["repair_orders"])
        )

    def test_cleanup_card_content_route_runs_local_cleanup(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Течь антифриза",
                "description": "Клиент: Иван Иванов\nТелефон: 89001112233\nVIN: WAUZZZ8V0JA000001\nПроверить радиатор",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cleaned = self.request("/api/cleanup_card_content", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(cleaned["ok"])
        self.assertTrue(cleaned["data"]["meta"]["changed"])
        self.assertEqual(cleaned["data"]["meta"]["cleanup_mode"], "local_card_cleanup")
        self.assertTrue(cleaned["data"]["meta"]["verify"]["passed"])
        self.assertIn("СУТЬ", cleaned["data"]["card"]["description"])
        self.assertEqual(cleaned["data"]["card"]["vehicle_profile"]["customer_name"], "Иван Иванов")

    def test_agent_routes_and_full_enrichment_launch_when_agent_is_attached(self) -> None:
        agent_status_payload = {
            "agent": {
                "name": "AUTOSTOP SERVER AGENT",
                "enabled": True,
                "available": True,
                "ready": True,
                "availability_reason": "worker_running",
                "configured": True,
                "model": "gpt-test",
                "board_api_url": self.base_url,
            },
            "ai_remodel": {},
            "board_control": {},
            "worker": {
                "embedded": True,
                "running": True,
                "heartbeat_fresh": True,
            },
            "scheduler": {
                "last_run_at": "",
                "last_success_at": "",
                "last_error": "",
            },
            "status": {
                "running": True,
                "current_task_id": None,
                "current_run_id": None,
                "last_heartbeat": utc_now().isoformat(),
                "last_run_started_at": "",
                "last_run_finished_at": "",
                "last_error": "",
                "last_scheduler_run_at": "",
                "last_scheduler_success_at": "",
                "last_scheduler_error": "",
                "board_control": {},
            },
            "queue": {"pending_total": 0, "running_total": 0},
            "scheduled": {"total": 0, "active_total": 0, "paused_total": 0},
            "recent_runs": [],
        }
        agent_control = Mock()
        agent_control.agent_status.return_value = agent_status_payload
        agent_control.agent_tasks.return_value = {
            "tasks": [],
            "meta": {"limit": 20, "statuses": []},
        }
        agent_control.agent_actions.return_value = {
            "actions": [],
            "meta": {"limit": 80, "run_id": None, "task_id": None},
        }
        agent_control.agent_scheduled_tasks.return_value = {"tasks": [], "meta": {"total": 0}}
        agent_control.enqueue_card_autofill_task.return_value = {
            "id": "task-123",
            "created_at": utc_now().isoformat(),
            "status": "pending",
        }
        self.service.attach_agent_control(agent_control)

        status, agent_status = self.request("/api/agent_status", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_status["ok"])
        self.assertTrue(agent_status["data"]["agent"]["enabled"])
        self.assertEqual(agent_status["data"]["agent"]["model"], "gpt-test")

        status, agent_tasks = self.request("/api/agent_tasks?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_tasks["ok"])
        self.assertEqual(agent_tasks["data"]["meta"]["limit"], 20)

        status, agent_scheduled = self.request("/api/agent_scheduled_tasks", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_scheduled["ok"])

        status, created = self.request(
            "/api/create_card",
            {
                "title": "AI карточка",
                "description": "VIN: WAUZZZ8V0JA000001\nПроверить радиатор",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, launched = self.request(
            "/api/run_full_card_enrichment",
            {"card_id": card_id, "actor_name": "AI", "context_packet": {"kind": "compact_context"}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(launched["ok"])
        self.assertTrue(launched["data"]["meta"]["launched"])
        self.assertEqual(launched["data"]["meta"]["task_id"], "task-123")
        agent_control.enqueue_card_autofill_task.assert_called()
        payload = agent_control.enqueue_card_autofill_task.call_args.args[0]
        prompt_text = str(
            payload.get("task_text", payload.get("prompt", payload.get("ai_autofill_prompt", "")))
        )
        self.assertIn("полное заполнение", prompt_text.lower())
        self.assertIn("update_card", prompt_text)
        self.assertIn("update_repair_order", prompt_text)
        self.assertIn("replace_repair_order_works", prompt_text)
        self.assertIn("replace_repair_order_materials", prompt_text)
        self.assertEqual(payload["scenario_id"], "full_card_enrichment")
        self.assertEqual(
            agent_control.enqueue_card_autofill_task.call_args.kwargs["purpose"],
            "full_card_enrichment",
        )
        self.assertEqual(
            agent_control.enqueue_card_autofill_task.call_args.kwargs["source"],
            "ui_full_card_enrichment",
        )
        self.assertEqual(payload["vehicle"], created["data"]["card"]["vehicle"])

    def test_head_root_and_health_are_supported(self) -> None:
        parsed = urlsplit(self.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request("HEAD", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/favicon.ico")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
            self.assertEqual(response.getheader("Content-Type"), "image/x-icon")
            self.assertEqual(response.read(), b"")

            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/favicon.png")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertEqual(response.read(), b"")
        finally:
            connection.close()

    def test_favicon_routes_serve_brand_assets(self) -> None:
        parsed = urlsplit(self.base_url)
        cases = [
            ("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
            ("/favicon.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ]
        for path, content_type, expected_prefix in cases:
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), content_type)
                self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
                body = response.read()
                self.assertGreater(len(body), 0)
                self.assertTrue(body.startswith(expected_prefix))
            finally:
                connection.close()

    def test_review_board_route_returns_summary(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Review route", "description": "For review_board", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)

        status, review = self.request("/api/review_board", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(review["ok"])
        self.assertIn("summary", review["data"])
        self.assertIn("by_column", review["data"])
        self.assertIn("alerts", review["data"])
        self.assertIn("priority_cards", review["data"])
        self.assertIn("recent_events", review["data"])
        self.assertGreaterEqual(review["data"]["summary"]["active_cards"], 1)
        self.assertIn("[BOARD REVIEW]", review["data"]["text"])

    def test_operator_login_profile_and_admin_user_management(self) -> None:
        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, profile = self.request("/api/get_operator_profile", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["user"]["username"], "ADMIN")
        self.assertTrue(profile["data"]["user"]["is_admin"])
        self.assertTrue(profile["data"]["security"]["using_default_admin_credentials"])
        self.assertIn(
            "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD", profile["data"]["security"]["warning"]
        )

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "mekh", "password": "1234", "role": "admin"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["data"]["meta"]["created"])
        self.assertEqual(saved["data"]["user"]["username"], "MEKH")
        self.assertEqual(saved["data"]["user"]["role"], "operator")

        status, listed = self.request("/api/list_operator_users", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["username"] == "MEKH" for item in listed["data"]["users"]))

        status, deleted = self.request(
            "/api/delete_operator_user",
            {"username": "mekh"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])

    def test_ui_write_routes_require_operator_session(self) -> None:
        status, blocked = self.request(
            "/api/create_column",
            {"label": "Protected", "source": "ui"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]

        status, created = self.request(
            "/api/create_column",
            {"label": "Protected", "source": "ui"},
            headers={"X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["column"]["label"], "Protected")

    def test_proxied_write_routes_require_operator_session(self) -> None:
        proxy_headers = {"X-Forwarded-For": "203.0.113.10"}

        status, blocked = self.request(
            "/api/create_sticky",
            {"text": "Proxy write", "x": 1, "y": 1, "deadline": {"hours": 1}},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

        status, blocked_autofill = self.request(
            "/api/autofill_vehicle_data",
            {"raw_text": "VIN WBANV9C57AC136084", "actor_name": "AUDIT"},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked_autofill["error"]["details"]["auth_type"], "operator_session")

        status, blocked_transfer = self.request(
            "/api/create_cashbox_transfer",
            {
                "from_cashbox_id": "CB1",
                "to_cashbox_id": "CB2",
                "amount": "100",
                "actor_name": "AUDIT",
            },
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked_transfer["error"]["details"]["auth_type"], "operator_session")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]

        status, created = self.request(
            "/api/create_sticky",
            {"text": "Proxy write", "x": 1, "y": 1, "deadline": {"hours": 1}},
            headers={**proxy_headers, "X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["sticky"]["text"], "Proxy write")

    def test_update_board_settings_route_is_not_exposed_via_get(self) -> None:
        status, response = self.request("/api/update_board_settings?board_scale=1.25", method="GET")
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "not_found")

    def test_open_card_updates_operator_opened_counter(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Tracked open", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, opened = self.request(
            "/api/open_card",
            {"card_id": card_id},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(opened["data"]["card"]["id"], card_id)

        status, profile = self.request("/api/get_operator_profile", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["stats"]["cards_opened"], 1)

    def test_open_card_requires_operator_session(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Tracked open", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, blocked = self.request("/api/open_card", {"card_id": card_id})
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["code"], "unauthorized")
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

    def test_admin_user_report_uses_last_15_days_window(self) -> None:
        status, logged_in = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["user"]["role"], "operator")

        users_state = self.operator_service._read_normalized_state()
        worker = self.operator_service._find_user(users_state["users"], "WORKER")
        self.assertIsNotNone(worker)
        worker["action_history"] = [
            {
                "timestamp": (utc_now() - timedelta(days=2)).isoformat(),
                "action": "card_opened",
                "message": "Открыл карточку.",
                "card_id": "recent-card",
            },
            {
                "timestamp": (utc_now() - timedelta(days=20)).isoformat(),
                "action": "card_opened",
                "message": "Старое открытие.",
                "card_id": "old-card",
            },
        ]
        self.operator_service._write_state(users_state)

        bundle = self.store.read_bundle()
        bundle["events"].append(
            AuditEvent(
                id="recent-move",
                timestamp=(utc_now() - timedelta(days=1)).isoformat(),
                actor_name="WORKER",
                source="ui",
                action="card_moved",
                message="Переместил карточку.",
                card_id="recent-card",
                details={},
            )
        )
        bundle["events"].append(
            AuditEvent(
                id="old-archive",
                timestamp=(utc_now() - timedelta(days=25)).isoformat(),
                actor_name="WORKER",
                source="ui",
                action="card_archived",
                message="Старое архивирование.",
                card_id="old-card",
                details={},
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        status, listed = self.request("/api/list_operator_users", method="GET", headers=headers)
        self.assertEqual(status, 200)
        worker_row = next(item for item in listed["data"]["users"] if item["username"] == "WORKER")
        self.assertEqual(worker_row["stats"]["cards_opened"], 1)
        self.assertEqual(worker_row["stats"]["card_moves"], 1)
        self.assertEqual(worker_row["stats"]["cards_archived"], 0)

        status, report = self.request(
            "/api/get_operator_user_report?username=worker", method="GET", headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["meta"]["window_days"], 15)
        text = report["data"]["text"]
        self.assertIn("последние 15 дней", text)
        self.assertIn("Переместил карточку.", text)
        self.assertIn("Открыл карточку.", text)
        self.assertNotIn("Старое архивирование.", text)
        self.assertNotIn("Старое открытие.", text)

    def test_snapshot_marks_card_as_updated_for_viewer_after_other_operator_edit(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_token = admin_login["data"]["session"]["token"]
        admin_headers = {"X-Operator-Session": admin_token}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, created = self.request(
            "/api/create_card",
            {"title": "Updated badge", "description": "Initial", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, opened = self.request("/api/open_card", {"card_id": card_id}, headers=admin_headers)
        self.assertEqual(status, 200)
        self.assertFalse(opened["data"]["card"]["has_unseen_update"])

        status, updated = self.request(
            "/api/update_card",
            {"card_id": card_id, "description": "Worker updated card"},
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["data"]["card"]["has_unseen_update"])

        status, snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=admin_headers
        )
        self.assertEqual(status, 200)
        admin_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertTrue(admin_card["has_unseen_update"])
        self.assertFalse(admin_card["is_unread"])

        status, marked = self.request(
            "/api/mark_card_seen", {"card_id": card_id}, headers=admin_headers
        )
        self.assertEqual(status, 200)
        self.assertFalse(marked["data"]["card"]["has_unseen_update"])

    def test_operator_user_listing_reads_board_bundle_once(self) -> None:
        logged_in = self.operator_service.login({"username": "admin", "password": "admin"})
        session = logged_in["session"]
        self.operator_service.save_user(
            {
                "_operator_session": session,
                "username": "mekh",
                "password": "1234",
            }
        )
        bundle = self.store.read_bundle()
        self.store.read_bundle = Mock(return_value=bundle)
        self.operator_service._build_event_activity_index = Mock(
            wraps=self.operator_service._build_event_activity_index
        )

        listed = self.operator_service.list_users({"_operator_session": session})

        self.assertEqual(listed["meta"]["total"], 2)
        self.assertEqual(self.store.read_bundle.call_count, 1)
        self.assertEqual(self.operator_service._build_event_activity_index.call_count, 1)

    def test_default_admin_accepts_admin_password_and_migrates_legacy_hash(self) -> None:
        state = self.operator_service._read_normalized_state()
        admin_user = next(user for user in state["users"] if user["username"] == "ADMIN")
        admin_user["password_hash"] = _password_hash("admin123")
        self.operator_service._write_state(state)

        logged_in = self.operator_service.login({"username": "admin", "password": "admin"})

        self.assertEqual(logged_in["user"]["username"], "ADMIN")
        migrated_state = self.operator_service._read_normalized_state()
        migrated_admin = next(
            user for user in migrated_state["users"] if user["username"] == "ADMIN"
        )
        self.assertNotEqual(migrated_admin["password_hash"], admin_user["password_hash"])

    def test_create_column_move_card_and_update_deadline(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "Блокеры"})
        self.assertEqual(status, 200)
        self.assertTrue(created_column["ok"])
        column_id = created_column["data"]["column"]["id"]

        status, columns = self.request("/api/list_columns", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(column["id"] == column_id for column in columns["data"]["columns"]))

        status, created_card = self.request(
            "/api/create_card",
            {
                "title": "Карточка в новом столбце",
                "column": column_id,
                "deadline": {"days": 0, "hours": 6},
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]
        self.assertEqual(created_card["data"]["card"]["column"], column_id)

        status, updated = self.request(
            "/api/set_card_deadline",
            {"card_id": card_id, "deadline": {"days": 0, "hours": 0, "minutes": 1}},
        )
        self.assertEqual(status, 200)
        self.assertLessEqual(updated["data"]["card"]["remaining_seconds"], 60)

        status, yellow = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "yellow"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(yellow["data"]["card"]["indicator"], "yellow")
        self.assertEqual(yellow["data"]["card"]["status"], "warning")

        status, red = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "red"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(red["data"]["card"]["status"], "expired")

        status, overdue = self.request("/api/list_overdue_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in overdue["data"]["cards"]))

    def test_create_column_accepts_name_alias(self) -> None:
        status, created_column = self.request("/api/create_column", {"name": "Этап по имени"})
        self.assertEqual(status, 200)
        self.assertTrue(created_column["ok"])
        self.assertEqual(created_column["data"]["column"]["label"], "Этап по имени")

    def test_move_card_route_can_reorder_within_column(self) -> None:
        status, first = self.request(
            "/api/create_card", {"title": "First", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, second = self.request(
            "/api/create_card", {"title": "Second", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, third = self.request(
            "/api/create_card", {"title": "Third", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/move_card",
            {
                "card_id": third["data"]["card"]["id"],
                "column": "inbox",
                "before_card_id": second["data"]["card"]["id"],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["data"]["card"]["position"], 1)
        self.assertEqual(moved["data"]["affected_column_ids"], ["inbox"])
        self.assertEqual(
            [card["id"] for card in moved["data"]["affected_cards"][:3]],
            [
                first["data"]["card"]["id"],
                third["data"]["card"]["id"],
                second["data"]["card"]["id"],
            ],
        )
        self.assertTrue(all("repair_order" not in card for card in moved["data"]["affected_cards"]))
        self.assertTrue(moved["data"]["meta"]["changed"])

        status, snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        inbox_cards = sorted(
            [card for card in snapshot["data"]["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [
                first["data"]["card"]["id"],
                third["data"]["card"]["id"],
                second["data"]["card"]["id"],
            ],
        )

    def test_cashbox_routes_create_list_transaction_get_and_delete(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса 1", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        cashbox = created["data"]["cashbox"]

        status, listed = self.request("/api/list_cashboxes?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["total"], 1)
        self.assertEqual(listed["data"]["cashboxes"][0]["id"], cashbox["id"])

        status, another_created = self.request(
            "/api/create_cashbox", {"name": "Касса 2", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        another_cashbox = another_created["data"]["cashbox"]

        status, reordered = self.request(
            "/api/reorder_cashboxes",
            {
                "cashbox_id": another_cashbox["id"],
                "before_cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(reordered["data"]["meta"]["changed"])
        self.assertEqual(
            [item["id"] for item in reordered["data"]["cashboxes"][:2]],
            [another_cashbox["id"], cashbox["id"]],
        )

        status, transaction = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["short_id"],
                "direction": "income",
                "amount": "2500",
                "note": "Оплата клиента",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(transaction["data"]["transaction"]["amount_minor"], 250000)

        destination_cashbox = another_cashbox

        status, transferred = self.request(
            "/api/create_cashbox_transfer",
            {
                "from_cashbox_id": cashbox["id"],
                "to_cashbox_id": destination_cashbox["short_id"],
                "amount": "500",
                "note": "Перевод на запас",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(transferred["data"]["source_transaction"]["direction"], "expense")
        self.assertEqual(transferred["data"]["target_transaction"]["direction"], "income")

        status, details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["transactions_total"], 2)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["balance_minor"], 200000)
        self.assertIn("Перемещение в Касса 2", details["data"]["transactions"][0]["note"])

        status, destination_details = self.request(
            f"/api/get_cashbox?cashbox_id={destination_cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            destination_details["data"]["cashbox"]["statistics"]["transactions_total"], 1
        )
        self.assertEqual(
            destination_details["data"]["cashbox"]["statistics"]["balance_minor"], 50000
        )
        self.assertIn(
            "Перемещение из Касса 1", destination_details["data"]["transactions"][0]["note"]
        )

        status, deleted = self.request(
            "/api/delete_cashbox", {"cashbox_id": cashbox["id"], "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(deleted["ok"])
        self.assertIn("есть движения", deleted["error"]["message"])

        status, empty_created = self.request(
            "/api/create_cashbox", {"name": "Касса 3", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        empty_cashbox = empty_created["data"]["cashbox"]

        status, deleted = self.request(
            "/api/delete_cashbox", {"cashbox_id": empty_cashbox["id"], "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["meta"]["deleted"])

        status, journal = self.request("/api/get_cash_journal?months=3&limit=100", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(journal["ok"])
        self.assertIn("КАССОВЫЙ ЖУРНАЛ", journal["data"]["text"])
        self.assertGreaterEqual(journal["data"]["meta"]["returned"], 1)

    def test_cancel_last_cash_transaction_route_removes_latest_manual_movement(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса API", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = created["data"]["cashbox"]
        status, first = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Старт",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        status, last = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "300",
                "note": "Расход",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)

        status, cancelled = self.request(
            "/api/cancel_last_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": last["data"]["transaction"]["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(cancelled["data"]["meta"]["cancelled"])
        self.assertEqual(
            cancelled["data"]["cancelled_transaction"]["id"], last["data"]["transaction"]["id"]
        )

        status, details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertEqual(
            details["data"]["transactions"][0]["id"], first["data"]["transaction"]["id"]
        )

    def test_employee_salary_ledger_and_cash_routes_work_together(self) -> None:
        status, employee_saved = self.request(
            "/api/save_employee",
            {
                "name": "Сергей Электрик",
                "position": "Электрик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "40000",
                "work_percent": "25",
            },
        )
        self.assertEqual(status, 200)
        employee = employee_saved["data"]["employee"]

        status, cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Наличный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_created["data"]["cashbox"]

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Toyota Camry", "title": "Зарплатный наряд", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "201",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "works": [
                        {
                            "name": "Замена генератора",
                            "quantity": "1",
                            "price": "8000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["salary_amount"], "")

        status, paid = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "201",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "payments": [
                        {
                            "amount": "8000",
                            "paid_at": "16.04.2026 12:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Замена генератора",
                            "quantity": "1",
                            "price": "8000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(paid["data"]["card"]["repair_order"]["payments"][0]["amount"], "8000")

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "2000")

        status, ledger = self.request(
            f"/api/get_employee_salary_ledger?employee_id={employee['id']}&months=6",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ledger["data"]["balance_total"], "2000")
        self.assertTrue(any(row["kind"] == "accrual" for row in ledger["data"]["journal_rows"]))

        status, payout = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "2500",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payout["data"]["transaction"]["employee_id"], employee["id"])
        self.assertEqual(payout["data"]["transaction"]["transaction_kind"], "salary_payout")
        self.assertEqual(payout["data"]["transaction"]["cashbox_id"], cashbox["id"])

        status, advance = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount": "500",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(advance["data"]["transaction"]["transaction_kind"], "salary_advance")

        status, ledger_after = self.request(
            f"/api/get_employee_salary_ledger?employee_id={employee['id']}&months=6",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ledger_after["data"]["balance_total"], "-1000")
        self.assertEqual(ledger_after["data"]["payout_total"], "2500")
        self.assertEqual(ledger_after["data"]["advance_total"], "500")
        self.assertTrue(
            any(row["kind"] == "salary_payout" for row in ledger_after["data"]["journal_rows"])
        )
        self.assertTrue(
            any(row["kind"] == "salary_advance" for row in ledger_after["data"]["journal_rows"])
        )

        status, report = self.request(
            f"/api/get_employee_salary_report?employee_id={employee['id']}&months=2",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["meta"]["months"], 2)
        self.assertIn("ОТЧЕТ ПО ЗАРПЛАТЕ", report["data"]["text"])
        self.assertIn("ПОСЛЕДНИЕ 2 МЕС.", report["data"]["text"])
        self.assertIn("employee-salary-report-", report["data"]["file_name"])

        status, cashbox_details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(
            cashbox_details["data"]["cashbox"]["statistics"]["transactions_total"], 2
        )

    def test_snapshot_compact_query_returns_board_friendly_cards(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "LEXUS IS F", "title": "Compact API snapshot", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, snapshot = self.request(
            "/api/get_board_snapshot?archive_limit=30&compact=1", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["data"]["meta"]["compact_cards"])
        compact_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)
        self.assertTrue(snapshot["data"]["meta"]["revision"])

    def test_get_cards_compact_query_omits_heavy_fields(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW",
                "title": "Compact API cards",
                "description": "VIN test",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cards_payload = self.request("/api/get_cards?compact=1", method="GET")
        self.assertEqual(status, 200)
        compact_card = next(
            card for card in cards_payload["data"]["cards"] if card["id"] == card_id
        )
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)
        self.assertNotIn("ai_autofill_log", compact_card)

    def test_get_cards_compact_query_redacts_phone_and_vin_from_description_preview(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Compact redact",
                "description": "Клиент: +7 (923) 123-45-67\nVIN: X4XKCN81140CY67957\nПроверить запись.",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cards_payload = self.request("/api/get_cards?compact=1", method="GET")
        self.assertEqual(status, 200)
        compact_card = next(
            card for card in cards_payload["data"]["cards"] if card["id"] == card_id
        )
        self.assertNotIn("+7 (923) 123-45-67", compact_card["description"])
        self.assertNotIn("X4XKCN81140CY67957", compact_card["description"])
        self.assertIn("[PHONE]", compact_card["description"])
        self.assertIn("[VIN]", compact_card["description"])
        self.assertEqual(compact_card["description"], compact_card["description_preview"])

    def test_snapshot_can_skip_archive_payload_for_board_refresh(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "MITSUBISHI L200",
                "title": "Archive API snapshot",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, archived = self.request(
            "/api/archive_card", {"card_id": card_id, "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, snapshot = self.request(
            "/api/get_board_snapshot?compact=1&include_archive=0", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["data"]["archive"], [])
        self.assertEqual(snapshot["data"]["meta"]["archived_cards_total"], 1)

    def test_repair_order_routes_list_and_open_text_file(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "KIA RIO", "title": "API заказ-наряд", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить и выдать текстовый файл",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")

        status, listed = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["repair_orders"][0]["card_id"], card_id)
        self.assertEqual(listed["data"]["repair_orders"][0]["paid_total"], "0")
        self.assertEqual(listed["data"]["repair_orders"][0]["payment_status"], "unpaid")
        self.assertTrue(listed["data"]["repair_orders"][0]["file_name"].endswith(".txt"))

        request = urllib.request.Request(
            f"{self.base_url}/api/repair_order_text?card_id={card_id}",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/plain")
            self.assertIn("1", body)
            self.assertIn("1000", body)
            self.assertIn("+7 900 123-45-67", body)
            self.assertIn("JSON:", body)

    def test_update_card_route_derives_repair_order_taxes_from_selected_cashbox(self) -> None:
        status, cashless_cashbox = self.request(
            "/api/create_cashbox", {"name": "Безналичный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        status, maria_cashbox = self.request(
            "/api/create_cashbox", {"name": "Карта Мария", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "AUDI A4", "title": "API оплата", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        cashless_card_id = created["data"]["card"]["id"]

        status, updated_cashless = self.request(
            "/api/update_card",
            {
                "card_id": cashless_card_id,
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "cashbox_id": cashless_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        cashless_order = updated_cashless["data"]["card"]["repair_order"]
        self.assertEqual(cashless_order["payment_method"], "cashless")
        self.assertEqual(cashless_order["taxes_total"], "150")
        self.assertEqual(cashless_order["grand_total"], "1150")
        self.assertEqual(cashless_order["due_total"], "650")

        status, cash_cashbox = self.request(
            "/api/create_cashbox", {"name": "Наличный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        status, mixed_paid = self.request(
            "/api/update_card",
            {
                "card_id": cashless_card_id,
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "cashbox_id": cashless_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        },
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:10",
                            "note": "Доплата",
                            "payment_method": "cash",
                            "cashbox_id": cash_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        },
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        mixed_order = mixed_paid["data"]["card"]["repair_order"]
        self.assertEqual(mixed_order["taxes_total"], "150")
        self.assertEqual(mixed_order["grand_total"], "1150")
        self.assertEqual(mixed_order["paid_total"], "1000")
        self.assertEqual(mixed_order["due_total"], "150")

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "BMW X5", "title": "API карта", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        maria_card_id = created["data"]["card"]["id"]

        status, updated_maria = self.request(
            "/api/update_card",
            {
                "card_id": maria_card_id,
                "repair_order": {
                    "works": [{"name": "Осмотр", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:05",
                            "note": "Оплата",
                            "payment_method": "cashless",
                            "cashbox_id": maria_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        maria_order = updated_maria["data"]["card"]["repair_order"]
        self.assertEqual(maria_order["payment_method"], "card")
        self.assertEqual(maria_order["taxes_total"], "0")
        self.assertEqual(maria_order["grand_total"], "1000")
        self.assertEqual(maria_order["due_total"], "500")

    def test_create_card_accepts_colored_tags(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Цветная карточка",
                "description": "Проверка API",
                "tags": [
                    {"label": "СРОЧНО", "color": "red"},
                    {"label": "ЖДЁМ", "color": "yellow"},
                ],
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["card"]["tags"], ["СРОЧНО", "ЖДЁМ"])
        self.assertEqual(created["data"]["card"]["tag_items"][0]["color"], "red")
        self.assertEqual(created["data"]["card"]["tag_items"][1]["color"], "yellow")

    def test_create_card_rejects_more_than_three_tags(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Слишком много меток",
                "description": "Проверка ограничения",
                "tags": ["СРОЧНО", "ЖДЁМ", "СОГЛАСОВАТЬ", "ЗАКАЗАТЬ"],
                "deadline": {"hours": 2},
            },
        )

        self.assertEqual(status, 400)
        self.assertFalse(created["ok"])
        self.assertEqual(created["error"]["code"], "validation_error")

    def test_delete_column_route_removes_empty_column_and_rejects_non_empty(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "DELETE ME"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, deleted = self.request("/api/delete_column", {"column_id": column_id})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["data"]["deleted_column"]["id"], column_id)
        self.assertTrue(all(column["id"] != column_id for column in deleted["data"]["columns"]))

        status, created_again = self.request("/api/create_column", {"label": "NOT EMPTY"})
        self.assertEqual(status, 200)
        blocked_column_id = created_again["data"]["column"]["id"]
        status, _ = self.request(
            "/api/create_card",
            {"title": "Busy column", "column": blocked_column_id, "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        status, blocked = self.request("/api/delete_column", {"column_id": blocked_column_id})
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], "column_not_empty")

    def test_delete_column_route_allows_native_column_with_only_archived_cards(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Archived done card", "column": "done", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, deleted = self.request("/api/delete_column", {"column_id": "done"})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["data"]["deleted_column"]["id"], "done")

        status, card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(card["data"]["card"]["archived"])
        self.assertEqual(card["data"]["card"]["column"], "inbox")

    def test_archive_card_route_rejects_open_repair_order(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Open order",
                "description": "Проверить подвеску",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "18",
                    "status": "open",
                    "client": "Иван Иванов",
                    "vehicle": "KIA RIO",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["status"], "open")

        status, blocked = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], "repair_order_open_archive_blocked")
        self.assertIn("открыт заказ-наряд", blocked["error"]["message"])

    def test_employee_routes_and_payroll_report(self) -> None:
        status, saved_employee = self.request(
            "/api/save_employee",
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            },
        )
        self.assertEqual(status, 200)
        employee_id = saved_employee["data"]["employee"]["id"]

        status, employees = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == employee_id for item in employees["data"]["employees"]))

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "Mitsubishi L200", "title": "Payroll API", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "31",
                    "status": "open",
                    "client": "Клиент",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee_id,
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "1500")

        status, report = self.request("/api/get_payroll_report", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(
            any(item["employee_id"] == employee_id for item in report["data"]["summary"])
        )

        status, reopened = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "open"}
        )
        self.assertEqual(status, 200)
        reopened_row = reopened["data"]["repair_order"]["works"][0]
        self.assertEqual(reopened_row["salary_amount"], "")
        self.assertEqual(reopened_row["salary_accrued_at"], "")

    def test_employee_routes_create_multiple_and_delete(self) -> None:
        status, first = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)
        self.assertTrue(first["data"]["created"])

        status, second = self.request(
            "/api/save_employee", {"name": "Пётр", "position": "Приёмщик"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["data"]["created"])
        self.assertNotEqual(first["data"]["employee"]["id"], second["data"]["employee"]["id"])

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), 2)

        status, deleted = self.request(
            "/api/delete_employee", {"employee_id": second["data"]["employee"]["id"]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])

        status, listed_after = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed_after["data"]["employees"]), 1)
        self.assertEqual(
            listed_after["data"]["employees"][0]["id"], first["data"]["employee"]["id"]
        )

    def test_employee_routes_toggle_active_state(self) -> None:
        status, saved = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)
        employee_id = saved["data"]["employee"]["id"]

        status, toggled_off = self.request("/api/toggle_employee", {"employee_id": employee_id})
        self.assertEqual(status, 200)
        self.assertFalse(toggled_off["data"]["employee"]["is_active"])

        status, toggled_on = self.request("/api/toggle_employee", {"employee_id": employee_id})
        self.assertEqual(status, 200)
        self.assertTrue(toggled_on["data"]["employee"]["is_active"])

    def test_employee_routes_support_up_to_fifteen_and_reject_sixteenth(self) -> None:
        checkpoints = {1, 2, 3, 10, 15}
        seen_ids: set[str] = set()
        for index in range(15):
            status, saved = self.request(
                "/api/save_employee",
                {
                    "name": f"Сотрудник {index + 1}",
                    "position": f"Пост {index + 1}",
                    "salary_mode": "salary_plus_percent",
                    "base_salary": str((index + 1) * 1000),
                    "work_percent": str(index + 3),
                },
            )
            self.assertEqual(status, 200)
            seen_ids.add(saved["data"]["employee"]["id"])
            if (index + 1) in checkpoints:
                status, listed = self.request("/api/list_employees", method="GET")
                self.assertEqual(status, 200)
                self.assertEqual(len(listed["data"]["employees"]), index + 1)
                self.assertEqual(
                    len({item["id"] for item in listed["data"]["employees"]}), index + 1
                )

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), 15)
        self.assertEqual({item["id"] for item in listed["data"]["employees"]}, seen_ids)

        status, overflow = self.request("/api/save_employee", {"name": "Сотрудник 16"})
        self.assertEqual(status, 400)
        self.assertEqual(overflow["error"]["code"], "validation_error")
        self.assertIn("15", overflow["error"]["message"])

    def test_save_employee_create_mode_ignores_stale_employee_id(self) -> None:
        status, first = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)

        status, second = self.request(
            "/api/save_employee",
            {
                "employee_id": first["data"]["employee"]["id"],
                "create_mode": True,
                "name": "Пётр",
                "position": "Приёмщик",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["data"]["created"])
        self.assertNotEqual(first["data"]["employee"]["id"], second["data"]["employee"]["id"])

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), 2)
        self.assertCountEqual(
            [item["name"] for item in listed["data"]["employees"]], ["Иван", "Пётр"]
        )

    def test_save_employee_requires_name(self) -> None:
        status, response = self.request(
            "/api/save_employee",
            {
                "name": "",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")
        self.assertEqual(response["error"]["details"]["field"], "name")

    def test_rename_column_route_updates_label_and_preserves_id(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "OLD LABEL"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]
        status, sibling_column = self.request("/api/create_column", {"label": "SIBLING LABEL"})
        self.assertEqual(status, 200)

        status, renamed = self.request(
            "/api/rename_column", {"column_id": column_id, "label": "NEW LABEL"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["data"]["column"]["id"], column_id)
        self.assertEqual(renamed["data"]["column"]["label"], "NEW LABEL")
        self.assertTrue(renamed["data"]["meta"]["changed"])

        status, duplicate = self.request(
            "/api/rename_column",
            {"column_id": column_id, "label": sibling_column["data"]["column"]["label"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(duplicate["error"]["code"], "validation_error")

    def test_move_column_route_reorders_columns(self) -> None:
        status, first = self.request("/api/create_column", {"label": "FIRST"})
        self.assertEqual(status, 200)
        status, second = self.request("/api/create_column", {"label": "SECOND"})
        self.assertEqual(status, 200)
        status, third = self.request("/api/create_column", {"label": "THIRD"})
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/move_column",
            {
                "column_id": third["data"]["column"]["id"],
                "before_column_id": first["data"]["column"]["id"],
            },
        )
        self.assertEqual(status, 200)
        ordered_ids = [item["id"] for item in moved["data"]["columns"]]
        self.assertEqual(
            ordered_ids[-3:],
            [
                third["data"]["column"]["id"],
                first["data"]["column"]["id"],
                second["data"]["column"]["id"],
            ],
        )
        self.assertTrue(moved["data"]["meta"]["changed"])

    def test_bulk_move_cards_route_moves_cards_and_reports_partial_failures(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "MCP TEST COLUMN"})
        self.assertEqual(status, 200)
        target_column = created_column["data"]["column"]["id"]

        status, first = self.request(
            "/api/create_card", {"title": "Bulk one", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, second = self.request(
            "/api/create_card",
            {"title": "Bulk two", "column": "in_progress", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        status, archived = self.request(
            "/api/create_card",
            {"title": "Bulk archived", "column": "done", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        archived_id = archived["data"]["card"]["id"]
        status, _ = self.request("/api/archive_card", {"card_id": archived_id})
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/bulk_move_cards",
            {
                "card_ids": [
                    first["data"]["card"]["id"],
                    second["data"]["card"]["id"],
                    archived_id,
                    "missing-card",
                ],
                "column": target_column,
                "actor_name": "MCP TEST",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["data"]["meta"]["moved"], 2)
        self.assertEqual(moved["data"]["meta"]["errors"], 2)
        self.assertTrue(any(item["code"] == "archived_card" for item in moved["data"]["errors"]))
        self.assertTrue(any(item["code"] == "not_found" for item in moved["data"]["errors"]))
        self.assertTrue(
            all(card["column"] == target_column for card in moved["data"]["moved_cards"])
        )

        status, first_after = self.request(
            f"/api/get_card?card_id={first['data']['card']['id']}", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(first_after["data"]["card"]["column"], target_column)

    def test_vehicle_profile_can_be_created_and_updated_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "API vehicle profile",
                "deadline": {"hours": 5},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                    "registration_plate": "A123BC77",
                    "pts_series": "77AA",
                    "pts_number": "123456",
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.assertEqual(created["data"]["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["data"]["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(
            created["data"]["card"]["vehicle_profile"]["registration_plate"], "A123BC77"
        )
        self.assertEqual(
            created["data"]["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567"
        )

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "vehicle_profile": {
                    "engine_code": "K12C",
                    "gearbox_model": "A6GF1",
                    "manual_fields": ["engine_code"],
                    "pts_series": "77AA",
                    "pts_number": "765432",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["engine_code"], "K12C")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["gearbox_model"], "A6GF1")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["pts_series"], "77AA")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["pts_number"], "765432")
        self.assertEqual(
            updated["data"]["card"]["vehicle_profile_compact"]["gearbox_model"], "A6GF1"
        )

    def test_vehicle_profile_ui_alias_fields_are_saved_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "API vehicle aliases",
                "deadline": {"hours": 5},
            },
        )
        self.assertEqual(status, 200)

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": created["data"]["card"]["id"],
                "vehicle_profile": {
                    "display_name": "Toyota Camry",
                    "license_plate": "А111АА124",
                    "manual_fields": ["display_name", "license_plate"],
                    "field_sources": {
                        "display_name": "manual_ui",
                        "license_plate": "manual_ui",
                    },
                },
            },
        )

        self.assertEqual(status, 200)
        profile = updated["data"]["card"]["vehicle_profile"]
        self.assertEqual(profile["display_name"], "Toyota Camry")
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["registration_plate"], "А111АА124")
        self.assertIn("registration_plate", profile["manual_fields"])

    def test_cards_can_be_marked_seen_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Unread from MCP",
                "description": "Новая карточка от GPT",
                "deadline": {"hours": 2},
                "source": "mcp",
            },
        )
        self.assertEqual(status, 200)
        card = created["data"]["card"]
        self.assertTrue(card["is_unread"])
        card_id = card["id"]
        updated_at = card["updated_at"]

        status, marked = self.request("/api/mark_card_seen", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(marked["data"]["meta"]["changed"])
        self.assertFalse(marked["data"]["card"]["is_unread"])
        self.assertEqual(marked["data"]["card"]["updated_at"], updated_at)

    def test_update_card_accepts_repair_order_payload(self) -> None:
        status, cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Безналичный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_created["data"]["cashbox"]
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Repair order API",
                "description": "Клиент ожидает звонка",
                "deadline": {"hours": 3},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "payment_method": "cashless",
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 12:00",
                            "note": "Аванс",
                            "payment_method": "cashless",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashbox["id"],
                        }
                    ],
                    "client_information": "Краткая история ремонта для клиента",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")
        self.assertEqual(updated["data"]["card"]["repair_order"]["client"], "Иван Иванов")
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["client_information"],
            "Краткая история ремонта для клиента",
        )
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["name"], "Диагностика")
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_method"], "cashless")
        self.assertTrue(updated["data"]["card"]["repair_order"]["payment_method_label"])
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment"], "500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment_display"], "500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["paid_total"], "500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_status"], "unpaid")
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payment_status_label"], "Не оплачен"
        )
        self.assertEqual(len(updated["data"]["card"]["repair_order"]["payments"]), 1)
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payments"][0]["actor_name"], "ADMIN"
        )
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payments"][0]["cashbox_name"], cashbox["name"]
        )
        self.assertTrue(
            updated["data"]["card"]["repair_order"]["payments"][0]["cash_transaction_id"]
        )
        self.assertEqual(updated["data"]["card"]["repair_order"]["works_total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["materials_total"], "0")
        self.assertEqual(updated["data"]["card"]["repair_order"]["subtotal_total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["taxes_total"], "225")
        self.assertEqual(updated["data"]["card"]["repair_order"]["grand_total"], "1725")
        self.assertEqual(updated["data"]["card"]["repair_order"]["due_total"], "1225")

    def test_repair_order_context_and_patch_routes(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW 320i",
                "title": "Ошибка двигателя",
                "description": "Госномер В003НК124",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "client_information": "Согласовать дальнейшую диагностику",
                    "license_plate": "В003НК124",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["client"], "Иван Иванов")
        self.assertEqual(
            patched["data"]["repair_order"]["comment"], "Согласовать дальнейшую диагностику"
        )

        status, materials = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "materials": [
                        {
                            "name": "Радиатор",
                            "catalog_number": "1300A123",
                            "quantity": "1",
                            "price": "12000",
                            "total": "",
                        }
                    ]
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            materials["data"]["repair_order"]["materials"][0]["catalog_number"], "1300A123"
        )

        status, works = self.request(
            "/api/replace_repair_order_works",
            {
                "card_id": card_id,
                "rows": [{"name": "Диагностика", "quantity": "1", "price": "2000", "total": ""}],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(works["data"]["repair_order"]["works_total"], "2000")

        status, order = self.request("/api/get_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(order["data"]["repair_order"]["license_plate"], "В003НК124")
        self.assertEqual(
            order["data"]["repair_order"]["materials"][0]["catalog_number"], "1300A123"
        )

        status, context = self.request(
            "/api/get_card_context",
            {"card_id": card_id, "event_limit": 10, "include_repair_order_text": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(context["data"]["card"]["id"], card_id)
        self.assertTrue(context["data"]["meta"]["has_repair_order"])
        self.assertIn("ЗАКАЗ-НАРЯД", context["data"]["repair_order_text"]["text"])

        status, text_payload = self.request("/api/get_repair_order_text", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(text_payload["data"]["card_id"], card_id)
        self.assertIn("Стоимость заказ-наряда: 14000", text_payload["data"]["text"])
        self.assertIn("Итого по заказ-наряду: 14000", text_payload["data"]["text"])
        self.assertIn("К доплате: 14000", text_payload["data"]["text"])

    def test_repair_order_print_module_routes_preview_export_and_template_crud(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry XV70",
                "title": "Print module API",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "Toyota Camry XV70",
                    "vin": "JTNB11HK103456789",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "2500", "total": ""}
                    ],
                    "materials": [{"name": "ATF", "quantity": "6", "price": "950", "total": ""}],
                },
            },
        )
        self.assertEqual(status, 200)

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace", {"card_id": card_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(workspace["data"]["documents"][0]["id"], "repair_order")
        self.assertIn("repair_order", workspace["data"]["templates"])

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["repair_order"],
                "active_document_id": "repair_order",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["data"]["documents"][0]["id"], "repair_order")
        self.assertIn("Заказ-наряд", preview["data"]["documents"][0]["pages"][0]["html"])

        status, saved_template = self.request(
            "/api/save_print_template",
            {
                "document_type": "repair_order",
                "name": "API template",
                "content": '<div class="document-page"><h1>{{client.name_display}}</h1></div>',
            },
        )
        self.assertEqual(status, 200)
        template_id = saved_template["data"]["template"]["id"]
        self.assertTrue(template_id.startswith("custom:repair_order:"))

        status, defaulted = self.request(
            "/api/set_default_print_template",
            {"document_type": "repair_order", "template_id": template_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["is_default"] for item in defaulted["data"]["templates"]))

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 route-test",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order", "invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(
            base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF-1.4")
        )
        self.assertEqual(exported["data"]["meta"]["documents"][0]["id"], "repair_order")

        with patch("minimal_kanban.printing.service.print_html") as print_backend:
            status, printed = self.request(
                "/api/print_repair_order_documents",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order"],
                    "printer_name": "Office Printer",
                    "print_settings": {"default_printer": "Office Printer", "copies": 2},
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(printed["data"]["printer_name"], "Office Printer")
        self.assertEqual(printed["data"]["copies"], 2)
        print_backend.assert_called_once()

    def test_inspection_sheet_form_routes_save_preview_and_autofill(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Mazda CX-3",
                "title": "Fill inspection sheet",
                "description": "Suspension noise, inspection required.",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Nina Yarulina",
                    "vehicle": "Mazda CX-3",
                    "vin": "DK5FW106086",
                    "license_plate": "A123AA124",
                    "reason": "Suspension noise",
                    "comment": "Client asked for chassis inspection",
                    "note": "Stabilizer link play found",
                    "works": [
                        {
                            "name": "Suspension diagnosis",
                            "quantity": "1",
                            "price": "1800",
                            "total": "",
                        }
                    ],
                    "materials": [
                        {"name": "Stabilizer link", "quantity": "2", "price": "900", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, loaded = self.request("/api/get_inspection_sheet_form", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertIn("complaint_summary", loaded["data"]["form"])
        self.assertIn("planned_work_rows", loaded["data"]["form"])
        self.assertIn("planned_material_rows", loaded["data"]["form"])

        status, saved = self.request(
            "/api/save_inspection_sheet_form",
            {
                "card_id": card_id,
                "form_data": {
                    "client": "Nina Yarulina",
                    "vehicle": "Mazda CX-3",
                    "vin_or_plate": "DK5FW106086 ? A123AA124",
                    "complaint_summary": "Suspension noise",
                    "findings": "Stabilizer link play",
                    "recommendations": "Replace stabilizer links",
                    "planned_works": "Replace stabilizer links",
                    "planned_materials": "Stabilizer link",
                    "planned_work_rows": [
                        {"name": "Replace stabilizer links", "quantity": "1"},
                        {"name": "Check bushings", "quantity": "1"},
                    ],
                    "planned_material_rows": [
                        {"name": "Stabilizer link", "quantity": "2"},
                    ],
                    "master_comment": "Approve estimate",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["form"]["vehicle"], "Mazda CX-3")
        self.assertEqual(saved["data"]["form"]["planned_work_rows"][1]["name"], "Check bushings")

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["inspection_sheet"],
                "active_document_id": "inspection_sheet",
            },
        )
        self.assertEqual(status, 200)
        html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("Mazda CX-3", html)
        self.assertIn("Stabilizer link play", html)
        self.assertIn("Replace stabilizer links", html)
        self.assertIn("Check bushings", html)
        self.assertIn("Stabilizer link", html)

        with patch("minimal_kanban.services.card_service.OpenAIJsonAgentClient") as client_cls:
            client = client_cls.return_value
            client.model = "gpt-5.4-mini"
            client.complete_json.return_value = {
                "client": "Nina Yarulina",
                "vehicle": "Mazda CX-3",
                "vin_or_plate": "DK5FW106086 ? A123AA124",
                "complaint_summary": "Suspension noise and vibration",
                "findings": ["Stabilizer link play", "Bushing wear"],
                "recommendations": ["Replace links", "Check bushings"],
                "planned_works": ["Replace stabilizer links", "Check bushings"],
                "planned_materials": ["Stabilizer link", "Stabilizer bushing"],
                "planned_work_rows": [
                    {"name": "Replace stabilizer links", "quantity": "1"},
                    {"name": "Check bushings", "quantity": "1"},
                ],
                "planned_material_rows": [
                    {"name": "Stabilizer link", "quantity": "2"},
                    {"name": "Stabilizer bushing", "quantity": "2"},
                ],
                "master_comment": "Prepare estimate",
                "confidence_notes": ["Part of the data came from the card description"],
            }
            status, autofilled = self.request(
                "/api/autofill_inspection_sheet_form", {"card_id": card_id}
            )
        self.assertEqual(status, 200)
        self.assertEqual(autofilled["data"]["form"]["master_comment"], "Prepare estimate")
        self.assertEqual(autofilled["data"]["autofill"]["model"], "gpt-5.4-mini")
        self.assertEqual(
            autofilled["data"]["form"]["planned_material_rows"][1]["name"], "Stabilizer bushing"
        )
        self.assertIn(
            "Part of the data came from the card description",
            autofilled["data"]["autofill"]["confidence_notes"][0],
        )

    def test_repair_order_print_pdf_export_works_from_http_thread(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Lexus IS F",
                "title": "Threaded PDF export",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "Lexus IS F",
                    "vin": "USE205004751",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "2500", "total": ""}
                    ],
                    "materials": [
                        {"name": "Масло 5W-30", "quantity": "6", "price": "950", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, exported = self.request(
            "/api/export_repair_order_print_pdf",
            {
                "card_id": card_id,
                "selected_document_ids": ["repair_order"],
                "active_document_id": "repair_order",
            },
        )
        self.assertEqual(status, 200)
        content = base64.b64decode(exported["data"]["content_base64"])
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(exported["data"]["meta"]["documents"][0]["id"], "repair_order")

    def test_save_print_module_settings_route_persists_workspace_settings(self) -> None:
        status, saved = self.request(
            "/api/save_print_module_settings",
            {
                "print_settings": {
                    "default_printer": "",
                    "copies": 2,
                    "paper_size": "A5",
                    "orientation": "landscape",
                    "service_profile": {
                        "company_name": "AutoStop CRM",
                        "phone": "+7 900 123-45-67",
                    },
                }
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["settings"]["copies"], 2)
        self.assertEqual(saved["data"]["settings"]["paper_size"], "A5")
        self.assertEqual(saved["data"]["settings"]["orientation"], "landscape")
        self.assertEqual(
            saved["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM"
        )

        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Workspace settings reuse",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace", {"card_id": card_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(workspace["data"]["settings"]["copies"], 2)
        self.assertEqual(workspace["data"]["settings"]["paper_size"], "A5")
        self.assertEqual(workspace["data"]["settings"]["orientation"], "landscape")
        self.assertEqual(
            workspace["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM"
        )

    def test_autofill_repair_order_route_uses_card_and_vehicle_profile(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "ТО DSG/АКПП",
                "description": "Госномер А123АА124. Выполнить обслуживание и замену расходников.",
                "deadline": {"hours": 5},
                "vehicle_profile": {
                    "customer_name": "Петров Пётр",
                    "customer_phone": "+7 999 000-11-22",
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, autofilled = self.request("/api/autofill_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(autofilled["ok"])
        self.assertEqual(autofilled["data"]["repair_order"]["number"], "1")
        self.assertEqual(autofilled["data"]["repair_order"]["client"], "Петров Пётр")
        self.assertEqual(autofilled["data"]["repair_order"]["phone"], "+7 999 000-11-22")
        self.assertEqual(autofilled["data"]["repair_order"]["license_plate"], "А123АА124")
        self.assertEqual(autofilled["data"]["repair_order"]["works"], [])
        self.assertIn("Заявка принята", autofilled["data"]["repair_order"]["client_information"])
        self.assertIn("autofill_report", autofilled["data"]["meta"])

    def test_autofill_repair_order_route_returns_structured_rows_and_history_prices(self) -> None:
        vin = "WVWZZZ1KZBP123456"
        for index in range(2):
            status, created = self.request(
                "/api/create_card",
                {
                    "vehicle": "Volkswagen Tiguan II",
                    "title": f"История DSG {index}",
                    "description": "Ранее выполненные работы",
                    "deadline": {"hours": 4},
                    "vehicle_profile": {"vin": vin},
                },
            )
            self.assertEqual(status, 200)
            history_id = created["data"]["card"]["id"]
            status, _ = self.request(
                "/api/update_repair_order",
                {
                    "card_id": history_id,
                    "repair_order": {
                        "works": [
                            {
                                "name": "Диагностика DSG",
                                "quantity": "1",
                                "price": "2500",
                                "total": "",
                            }
                        ],
                        "materials": [
                            {"name": "ATF", "quantity": "6", "price": "950", "total": ""}
                        ],
                    },
                },
            )
            self.assertEqual(status, 200)

        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Жалоба DSG",
                "description": "VIN WVWZZZ1KZBP123456\nЖалоба: пинки DSG.\nРаботы: Диагностика DSG\nМатериалы: ATF 6 л",
                "deadline": {"hours": 4},
                "vehicle_profile": {"vin": vin},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, autofilled = self.request("/api/autofill_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(autofilled["data"]["repair_order"]["works"], [])
        self.assertEqual(autofilled["data"]["repair_order"]["materials"], [])
        self.assertIn("Заявка принята", autofilled["data"]["repair_order"]["client_information"])
        self.assertIn(
            "В ходе проверки выявлено", autofilled["data"]["repair_order"]["client_information"]
        )
        self.assertIn("filled_fields", autofilled["data"]["meta"]["autofill_report"])

    def test_repair_order_status_route_moves_order_between_active_list_and_archive(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan",
                "title": "Repair order API status",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "payments": [
                        {"amount": "1500", "paid_at": "06.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["status"], "open")

        status, active = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(active["data"]["meta"]["status"], "open")
        self.assertTrue(any(item["card_id"] == card_id for item in active["data"]["repair_orders"]))

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["status"], "closed")
        self.assertTrue(closed["data"]["repair_order"]["closed_at"])

        status, active_after = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertFalse(
            any(item["card_id"] == card_id for item in active_after["data"]["repair_orders"])
        )

        status, archived = self.request("/api/list_repair_orders", {"status": "closed"})
        self.assertEqual(status, 200)
        self.assertEqual(archived["data"]["meta"]["status"], "closed")
        self.assertTrue(
            any(item["card_id"] == card_id for item in archived["data"]["repair_orders"])
        )

    def test_repair_order_status_route_rejects_unpaid_close(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Неоплаченный наряд",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["payment_status"], "unpaid")

        status, response = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "repair_order_payment_required")
        self.assertIn("выполнить оплату", response["error"]["message"].lower())

    def test_repair_order_update_route_rejects_unpaid_closed_status(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Обход закрытия",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, response = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "status": "closed",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )

        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "repair_order_payment_required")

    def test_repair_order_list_route_supports_query_sort_and_tags(self) -> None:
        status, first = self.request(
            "/api/create_card",
            {
                "vehicle": "Audi A6",
                "title": "Диагностика DSG",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        first_id = first["data"]["card"]["id"]

        status, second = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW X5",
                "title": "Замена масла",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        second_id = second["data"]["card"]["id"]

        status, patched_first = self.request(
            "/api/update_repair_order",
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить DSG и согласовать диагностику",
                    "tags": [
                        {"label": "Срочно", "color": "yellow"},
                        {"label": "DSG", "color": "green"},
                    ],
                    "works": [
                        {"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            patched_first["data"]["repair_order"]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": second_id,
                "repair_order": {
                    "client": "Петр Петров",
                    "phone": "+7 901 000-11-22",
                    "comment": "Стандартное ТО",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, listed = self.request(
            "/api/list_repair_orders",
            {
                "status": "all",
                "query": "срочно иван dsg",
                "sort_by": "number",
                "sort_dir": "asc",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["query"], "срочно иван dsg")
        self.assertEqual(listed["data"]["meta"]["sort_by"], "number")
        self.assertEqual(listed["data"]["meta"]["sort_dir"], "asc")
        self.assertEqual([item["card_id"] for item in listed["data"]["repair_orders"]], [first_id])
        self.assertEqual(
            listed["data"]["repair_orders"][0]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

    def test_autofill_vehicle_data_route_returns_card_draft_and_profile(self) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ):
            status, autofilled = self.request(
                "/api/autofill_vehicle_data",
                {
                    "raw_text": "Suzuki Swift 2014 VIN JSAZC72S001234567",
                    "existing_profile": {
                        "engine_code": "CUSTOM",
                        "manual_fields": ["engine_code"],
                    },
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(autofilled["ok"])
        self.assertEqual(autofilled["data"]["vehicle_profile"]["make_display"], "Suzuki")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["model_display"], "Swift")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["engine_code"], "CUSTOM")
        self.assertIn("vehicle", autofilled["data"]["card_draft"])

    def test_autofill_vehicle_data_route_uses_card_fields_without_raw_text(self) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ):
            status, autofilled = self.request(
                "/api/autofill_vehicle_data",
                {
                    "vehicle": "Suzuki Swift 2014",
                    "title": "Suzuki Swift 2014 / подбор запчастей",
                    "description": "VIN JSAZC72S001234567\nДвигатель: K12B\nКоробка: Aisin\nПередний привод.",
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(autofilled["ok"])
        self.assertEqual(autofilled["data"]["vehicle_profile"]["make_display"], "Suzuki")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["model_display"], "Swift")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["gearbox_model"], "Aisin")
        self.assertEqual(autofilled["data"]["vehicle_profile"]["drivetrain"], "FWD")

    def test_rejects_invalid_json_and_payload_type(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/create_card",
            data=b"{broken",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_json:
            urllib.request.urlopen(request, timeout=5)
        try:
            payload = json.loads(invalid_json.exception.read().decode("utf-8"))
        finally:
            invalid_json.exception.close()
        self.assertEqual(payload["error"]["code"], "invalid_json")

        status, wrong_type = self.request("/api/create_card", payload=["not", "object"])  # type: ignore[arg-type]
        self.assertEqual(status, 400)
        self.assertEqual(wrong_type["error"]["code"], "validation_error")

    def test_validation_error_is_returned_for_wrong_input(self) -> None:
        status, response = self.request("/api/get_cards", {"include_archived": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request("/api/create_column", {"label": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request(
            "/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 0}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request(
            "/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 24}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, created = self.request(
            "/api/create_card", {"title": "Карточка", "deadline": {"hours": 1}}
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, response = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "blue"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

    def test_snapshot_log_restore_and_search_routes(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "Электрики"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, created_sticky = self.request(
            "/api/create_sticky",
            {
                "text": "Проверить втулки стабилизатора",
                "x": 120,
                "y": 90,
                "deadline": {"hours": 4},
                "actor_name": "ИНСПЕКТОР",
                "source": "api",
            },
        )
        self.assertEqual(status, 200)
        sticky_id = created_sticky["data"]["sticky"]["id"]
        self.assertTrue(created_sticky["data"]["sticky"]["short_id"].startswith("S-"))

        status, created_card = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "column": column_id,
                "tags": ["СРОЧНО", "ДИАГНОСТИКА"],
                "deadline": {"hours": 8},
                "actor_name": "ИНСПЕКТОР",
                "source": "api",
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]
        card_short_id = created_card["data"]["card"]["short_id"]
        self.assertEqual(created_card["data"]["card"]["heading"], "KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД")
        self.assertEqual(created_card["data"]["card"]["column_label"], "Электрики")

        status, snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in snapshot["data"]["cards"]))
        self.assertTrue(any(sticky["id"] == sticky_id for sticky in snapshot["data"]["stickies"]))
        self.assertGreater(snapshot["data"]["meta"]["stickies_total"], 0)
        self.assertIn("cards_returned", snapshot["data"]["meta"])
        self.assertIn("archive_returned", snapshot["data"]["meta"])

        status, log = self.request(f"/api/get_card_log?card_id={card_id}&limit=1", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(log["data"]["events"][0]["actor_name"], "ИНСПЕКТОР")
        self.assertEqual(log["data"]["meta"]["limit"], 1)
        self.assertEqual(log["data"]["meta"]["events_returned"], 1)
        self.assertIn("has_more", log["data"]["meta"])

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, archive_list = self.request("/api/list_archived_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in archive_list["data"]["cards"]))
        self.assertGreaterEqual(archive_list["data"]["meta"]["total"], 1)
        self.assertGreaterEqual(archive_list["data"]["meta"]["returned"], 1)

        status, restored = self.request(
            "/api/restore_card", {"card_id": card_id, "column": column_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(restored["data"]["card"]["archived"])

        status, searched = self.request(
            "/api/search_cards",
            {"query": "rio дроссель", "column": column_id, "tag": "срочно", "limit": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched["data"]["meta"]["total_matches"], 1)
        self.assertFalse(searched["data"]["meta"]["has_more"])
        self.assertEqual(searched["data"]["cards"][0]["id"], card_id)

        status, searched_by_short_id = self.request(
            "/api/search_cards", {"query": card_short_id, "limit": 5}
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched_by_short_id["data"]["cards"][0]["id"], card_id)

        status, wall = self.request(
            "/api/get_gpt_wall", {"include_archived": True, "event_limit": 50}
        )
        self.assertEqual(status, 200)
        self.assertIn(card_short_id, wall["data"]["text"])
        self.assertIn("sections", wall["data"])
        self.assertIn("board_content", wall["data"]["sections"])
        self.assertIn("event_log", wall["data"]["sections"])
        self.assertTrue(wall["data"]["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(wall["data"]["meta"]["text_format"], "markdown")
        self.assertEqual(
            wall["data"]["sections"]["board_content"]["meta"]["text_format"], "markdown"
        )
        self.assertEqual(wall["data"]["sections"]["event_log"]["meta"]["text_format"], "markdown")
        self.assertTrue(any(card["id"] == card_id for card in wall["data"]["cards"]))
        wall_card = next(card for card in wall["data"]["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertTrue(any(event["card_id"] == card_id for event in wall["data"]["events"]))
        self.assertIn(card_short_id, wall["data"]["sections"]["board_content"]["text"])
        self.assertTrue(
            any(
                event["card_id"] == card_id
                for event in wall["data"]["sections"]["event_log"]["events"]
            )
        )

        status, board_settings = self.request(
            "/api/update_board_settings", {"board_scale": 1.25, "actor_name": "ИНСПЕКТОР"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(board_settings["data"]["settings"]["board_scale"], 1.25)

        status, updated_snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(updated_snapshot["data"]["settings"]["board_scale"], 1.25)


class ApiServerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.auth.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(
            self.store,
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.port = reserve_port()
        self.server = ApiServer(
            self.service,
            logger,
            start_port=self.port,
            fallback_limit=1,
            bearer_token="secret-token",
        )
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        token: str | None = None,
    ):
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def test_mutating_routes_require_bearer_token(self) -> None:
        status, health = self.request("/api/health", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(health["data"]["auth_required"])

        status, unauthorized = self.request(
            "/api/create_card",
            {"title": "Закрыто", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")

        status, authorized = self.request(
            "/api/create_card",
            {"title": "Открыто", "deadline": {"hours": 1}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertTrue(authorized["ok"])

    def test_query_access_token_supports_browser_share_flow(self) -> None:
        status, created = self.request(
            "/api/create_card?access_token=secret-token",
            {"title": "РџРѕ СЃСЃС‹Р»РєРµ", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, attachment = self.request(
            "/api/add_card_attachment?access_token=secret-token",
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            },
        )
        self.assertEqual(status, 200)
        attachment_id = attachment["data"]["attachment"]["id"]

        status, snapshot = self.request(
            "/api/get_board_snapshot?archive_limit=10&access_token=secret-token", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in snapshot["data"]["cards"]))

        request = urllib.request.Request(
            f"{self.base_url}/api/attachment?card_id={card_id}&attachment_id={attachment_id}&access_token=secret-token",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"hello")

    def test_attachment_api_roundtrip_preserves_headers_for_required_formats(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment headers", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        samples = [
            ("фото клиента.png", "image/png", PNG_1X1_BYTES),
            ("фото клиента.jpg", "image/jpeg", JPEG_1X1_BYTES),
            (
                "отчёт клиента.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                minimal_docx_bytes(),
            ),
            (
                "смета клиента.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                minimal_xlsx_bytes(),
            ),
            ("заметки клиента.txt", "text/plain", minimal_text_bytes()),
            ("договор.final.pdf", "application/pdf", minimal_pdf_bytes()),
        ]

        for file_name, mime_type, payload in samples:
            with self.subTest(file_name=file_name):
                status, upload = self.request(
                    "/api/add_card_attachment",
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    },
                    token="secret-token",
                )
                self.assertEqual(status, 200)
                attachment_id = upload["data"]["attachment"]["id"]

                request = urllib.request.Request(
                    f"{self.base_url}/api/attachment?card_id={card_id}&attachment_id={attachment_id}",
                    headers={"Authorization": "Bearer secret-token"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    header = response.headers["Content-Disposition"]
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), payload)
                    self.assertEqual(response.headers.get_content_type(), mime_type)
                    self.assertIn('filename="', header)
                    self.assertIn("filename*=", header)
                    self.assertIn(quote(file_name, safe=""), header)
                    self.assertIn("X-Content-Type-Options", response.headers)

    def test_attachment_read_routes_return_bounded_agent_payload(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment agent read", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, upload = self.request(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": "agent-note.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            },
            token="secret-token",
        )
        self.assertEqual(status, 200)
        attachment_id = upload["data"]["attachment"]["id"]

        status, listed = self.request(
            "/api/list_card_attachments",
            {"card_id": card_id},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["attachments"][0]["content_kind"], "text")

        status, metadata = self.request(
            "/api/get_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(metadata["data"]["attachment"]["id"], attachment_id)
        self.assertIn("sha256", metadata["data"]["attachment"])

        status, read = self.request(
            "/api/read_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id, "max_chars": 12},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(read["data"]["content"]["text"], "Привет, влож")
        self.assertTrue(read["data"]["content"]["text_truncated"])

    def test_attachment_api_rejects_disallowed_and_mismatched_files(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment validation", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        cases = [
            ("malware.exe", "application/x-msdownload", b"MZ\x90\x00"),
            ("script.js", "application/javascript", b"alert(1);"),
            ("report.exe.pdf", "application/pdf", minimal_pdf_bytes()),
            ("report.pdf", "application/pdf", b"MZ\x00\x02\x03\x00"),
        ]

        for file_name, mime_type, payload in cases:
            with self.subTest(file_name=file_name):
                status, response = self.request(
                    "/api/add_card_attachment",
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    },
                    token="secret-token",
                )
                self.assertEqual(status, 400)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "validation_error")

    def test_board_context_route_describes_single_board_scope(self) -> None:
        status, created_column = self.request(
            "/api/create_column", {"label": "КЛИЕНТСКИЙ ЗАЛ"}, token="secret-token"
        )
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, _ = self.request(
            "/api/create_card",
            {"title": "Перезвонить владельцу", "column": column_id, "deadline": {"hours": 4}},
            token="secret-token",
        )
        self.assertEqual(status, 200)

        status, context = self.request("/api/get_board_context", method="GET", token="secret-token")
        self.assertEqual(status, 200)
        self.assertTrue(context["ok"])
        self.assertEqual(context["data"]["context"]["board_name"], "Current AutoStop CRM Board")
        self.assertEqual(context["data"]["context"]["board_scope"], "single_local_board_instance")
        self.assertIn("Do not use it for Trello, YouGile", context["data"]["context"]["scope_rule"])
        self.assertTrue(
            any(column["id"] == column_id for column in context["data"]["context"]["columns"])
        )
        self.assertIn("[BOARD CONTEXT]", context["data"]["text"])


if __name__ == "__main__":
    unittest.main()
