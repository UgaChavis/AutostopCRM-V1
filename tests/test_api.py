from __future__ import annotations

import base64
import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
        self.assertEqual(_success_log_level("/api/create_card"), logging.INFO)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(self.store, logger)
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
        self.assertIn("MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD", profile["data"]["security"]["warning"])

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

    def test_admin_user_report_uses_last_15_days_window(self) -> None:
        status, logged_in = self.request("/api/login_operator", {"username": "admin", "password": "admin"})
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

        status, report = self.request("/api/get_operator_user_report?username=worker", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["meta"]["window_days"], 15)
        text = report["data"]["text"]
        self.assertIn("последние 15 дней", text)
        self.assertIn("Переместил карточку.", text)
        self.assertIn("Открыл карточку.", text)
        self.assertNotIn("Старое архивирование.", text)
        self.assertNotIn("Старое открытие.", text)

    def test_snapshot_marks_card_as_updated_for_viewer_after_other_operator_edit(self) -> None:
        status, admin_login = self.request("/api/login_operator", {"username": "admin", "password": "admin"})
        self.assertEqual(status, 200)
        admin_token = admin_login["data"]["session"]["token"]
        admin_headers = {"X-Operator-Session": admin_token}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request("/api/login_operator", {"username": "worker", "password": "1234"})
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

        status, snapshot = self.request("/api/get_board_snapshot", method="GET", headers=admin_headers)
        self.assertEqual(status, 200)
        admin_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertTrue(admin_card["has_unseen_update"])
        self.assertFalse(admin_card["is_unread"])

        status, marked = self.request("/api/mark_card_seen", {"card_id": card_id}, headers=admin_headers)
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
        migrated_admin = next(user for user in migrated_state["users"] if user["username"] == "ADMIN")
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
            {"title": "Карточка в новом столбце", "column": column_id, "deadline": {"days": 0, "hours": 6}},
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

        status, yellow = self.request("/api/set_card_indicator", {"card_id": card_id, "indicator": "yellow"})
        self.assertEqual(status, 200)
        self.assertEqual(yellow["data"]["card"]["indicator"], "yellow")
        self.assertEqual(yellow["data"]["card"]["status"], "warning")

        status, red = self.request("/api/set_card_indicator", {"card_id": card_id, "indicator": "red"})
        self.assertEqual(status, 200)
        self.assertEqual(red["data"]["card"]["status"], "expired")

        status, overdue = self.request("/api/list_overdue_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in overdue["data"]["cards"]))

    def test_move_card_route_can_reorder_within_column(self) -> None:
        status, first = self.request("/api/create_card", {"title": "First", "column": "inbox", "deadline": {"hours": 2}})
        self.assertEqual(status, 200)
        status, second = self.request("/api/create_card", {"title": "Second", "column": "inbox", "deadline": {"hours": 2}})
        self.assertEqual(status, 200)
        status, third = self.request("/api/create_card", {"title": "Third", "column": "inbox", "deadline": {"hours": 2}})
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
            [first["data"]["card"]["id"], third["data"]["card"]["id"], second["data"]["card"]["id"]],
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
            [first["data"]["card"]["id"], third["data"]["card"]["id"], second["data"]["card"]["id"]],
        )

    def test_cashbox_routes_create_list_transaction_get_and_delete(self) -> None:
        status, created = self.request("/api/create_cashbox", {"name": "Касса 1", "actor_name": "ADMIN"})
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        cashbox = created["data"]["cashbox"]

        status, listed = self.request("/api/list_cashboxes?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["total"], 1)
        self.assertEqual(listed["data"]["cashboxes"][0]["id"], cashbox["id"])

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

        status, details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertEqual(details["data"]["transactions"][0]["note"], "Оплата клиента")

        status, deleted = self.request("/api/delete_cashbox", {"cashbox_id": cashbox["id"], "actor_name": "ADMIN"})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["meta"]["deleted"])

    def test_snapshot_compact_query_returns_board_friendly_cards(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "LEXUS IS F", "title": "Compact API snapshot", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, snapshot = self.request("/api/get_board_snapshot?archive_limit=30&compact=1", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["data"]["meta"]["compact_cards"])
        compact_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)
        self.assertTrue(snapshot["data"]["meta"]["revision"])

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
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")

        status, listed = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["repair_orders"][0]["card_id"], card_id)
        self.assertTrue(listed["data"]["repair_orders"][0]["file_name"].endswith(".txt"))

        request = urllib.request.Request(
            f"{self.base_url}/api/repair_order_text?card_id={card_id}",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/plain")
            self.assertIn("ЗАКАЗ-НАРЯД", body)
            self.assertIn("API заказ-наряд", body)
            self.assertIn("Итого работы: 1000", body)
            self.assertIn("Стоимость заказ-наряда: 1000", body)
            self.assertIn("Итого по заказ-наряду: 1000", body)
            self.assertIn("К доплате: 1000", body)

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

    def test_rename_column_route_updates_label_and_preserves_id(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "OLD LABEL"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]
        status, sibling_column = self.request("/api/create_column", {"label": "SIBLING LABEL"})
        self.assertEqual(status, 200)

        status, renamed = self.request("/api/rename_column", {"column_id": column_id, "label": "NEW LABEL"})
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

    def test_bulk_move_cards_route_moves_cards_and_reports_partial_failures(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "MCP TEST COLUMN"})
        self.assertEqual(status, 200)
        target_column = created_column["data"]["column"]["id"]

        status, first = self.request("/api/create_card", {"title": "Bulk one", "column": "inbox", "deadline": {"hours": 2}})
        self.assertEqual(status, 200)
        status, second = self.request("/api/create_card", {"title": "Bulk two", "column": "in_progress", "deadline": {"hours": 2}})
        self.assertEqual(status, 200)
        status, archived = self.request("/api/create_card", {"title": "Bulk archived", "column": "done", "deadline": {"hours": 2}})
        self.assertEqual(status, 200)
        archived_id = archived["data"]["card"]["id"]
        status, _ = self.request("/api/archive_card", {"card_id": archived_id})
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/bulk_move_cards",
            {
                "card_ids": [first["data"]["card"]["id"], second["data"]["card"]["id"], archived_id, "missing-card"],
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
        self.assertTrue(all(card["column"] == target_column for card in moved["data"]["moved_cards"]))

        status, first_after = self.request(f"/api/get_card?card_id={first['data']['card']['id']}", method="GET")
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
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.assertEqual(created["data"]["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["data"]["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(created["data"]["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567")

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "vehicle_profile": {
                    "engine_code": "K12C",
                    "gearbox_model": "A6GF1",
                    "manual_fields": ["engine_code"],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["engine_code"], "K12C")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["gearbox_model"], "A6GF1")
        self.assertEqual(updated["data"]["card"]["vehicle_profile_compact"]["gearbox_model"], "A6GF1")

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
                    "prepayment": "500",
                    "client_information": "Краткая история ремонта для клиента",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")
        self.assertEqual(updated["data"]["card"]["repair_order"]["client"], "Иван Иванов")
        self.assertEqual(updated["data"]["card"]["repair_order"]["client_information"], "Краткая история ремонта для клиента")
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["name"], "Диагностика")
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_method"], "cashless")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_method_label"], "Безналичный")
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment"], "500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment_display"], "500")
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
        self.assertEqual(patched["data"]["repair_order"]["comment"], "Согласовать дальнейшую диагностику")

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
        self.assertIn("Стоимость заказ-наряда: 2000", text_payload["data"]["text"])
        self.assertIn("Итого по заказ-наряду: 2000", text_payload["data"]["text"])
        self.assertIn("К доплате: 2000", text_payload["data"]["text"])

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
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2500", "total": ""}],
                    "materials": [{"name": "ATF", "quantity": "6", "price": "950", "total": ""}],
                },
            },
        )
        self.assertEqual(status, 200)

        status, workspace = self.request("/api/get_repair_order_print_workspace", {"card_id": card_id})
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
                "content": "<div class=\"document-page\"><h1>{{client.name_display}}</h1></div>",
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

        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes", return_value=b"%PDF-1.4 route-test"):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order", "invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF-1.4"))
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
                    "works": [{"name": "Замена масла", "quantity": "1", "price": "2500", "total": ""}],
                    "materials": [{"name": "Масло 5W-30", "quantity": "6", "price": "950", "total": ""}],
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
        self.assertEqual(saved["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM")

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

        status, workspace = self.request("/api/get_repair_order_print_workspace", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(workspace["data"]["settings"]["copies"], 2)
        self.assertEqual(workspace["data"]["settings"]["paper_size"], "A5")
        self.assertEqual(workspace["data"]["settings"]["orientation"], "landscape")
        self.assertEqual(workspace["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM")

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
        self.assertEqual(autofilled["data"]["repair_order"]["works"][0]["name"], "ТО DSG/АКПП")
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
                        "works": [{"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}],
                        "materials": [{"name": "ATF", "quantity": "6", "price": "950", "total": ""}],
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
        self.assertEqual(autofilled["data"]["repair_order"]["works"][0]["price"], "2500")
        self.assertEqual(autofilled["data"]["repair_order"]["materials"][0]["name"], "ATF")
        self.assertEqual(autofilled["data"]["repair_order"]["materials"][0]["price"], "950")
        self.assertIn("Выполнены работы", autofilled["data"]["repair_order"]["client_information"])
        self.assertEqual(len(autofilled["data"]["meta"]["autofill_report"]["prices_applied"]), 2)

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
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["status"], "open")

        status, active = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(active["data"]["meta"]["status"], "open")
        self.assertTrue(any(item["card_id"] == card_id for item in active["data"]["repair_orders"]))

        status, closed = self.request("/api/set_repair_order_status", {"card_id": card_id, "status": "closed"})
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["status"], "closed")
        self.assertTrue(closed["data"]["repair_order"]["closed_at"])

        status, active_after = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertFalse(any(item["card_id"] == card_id for item in active_after["data"]["repair_orders"]))

        status, archived = self.request("/api/list_repair_orders", {"status": "closed"})
        self.assertEqual(status, 200)
        self.assertEqual(archived["data"]["meta"]["status"], "closed")
        self.assertTrue(any(item["card_id"] == card_id for item in archived["data"]["repair_orders"]))

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
                    "works": [{"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}],
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
                    "works": [{"name": "Замена масла", "quantity": "1", "price": "1500", "total": ""}],
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
        with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None):
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
        with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None):
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

        status, response = self.request("/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 0}})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request("/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 24}})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, created = self.request("/api/create_card", {"title": "Карточка", "deadline": {"hours": 1}})
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, response = self.request("/api/set_card_indicator", {"card_id": card_id, "indicator": "blue"})
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

        status, log = self.request(f"/api/get_card_log?card_id={card_id}", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(log["data"]["events"][0]["actor_name"], "ИНСПЕКТОР")

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, archive_list = self.request("/api/list_archived_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in archive_list["data"]["cards"]))

        status, restored = self.request("/api/restore_card", {"card_id": card_id, "column": column_id})
        self.assertEqual(status, 200)
        self.assertFalse(restored["data"]["card"]["archived"])

        status, searched = self.request(
            "/api/search_cards",
            {"query": "rio дроссель", "column": column_id, "tag": "срочно", "limit": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched["data"]["meta"]["total_matches"], 1)
        self.assertEqual(searched["data"]["cards"][0]["id"], card_id)

        status, searched_by_short_id = self.request("/api/search_cards", {"query": card_short_id, "limit": 5})
        self.assertEqual(status, 200)
        self.assertEqual(searched_by_short_id["data"]["cards"][0]["id"], card_id)


        status, wall = self.request("/api/get_gpt_wall", {"include_archived": True, "event_limit": 50})
        self.assertEqual(status, 200)
        self.assertIn(card_short_id, wall["data"]["text"])
        self.assertIn("sections", wall["data"])
        self.assertIn("board_content", wall["data"]["sections"])
        self.assertIn("event_log", wall["data"]["sections"])
        self.assertIn("СТЕНА GPT", wall["data"]["text"])
        self.assertTrue(any(card["id"] == card_id for card in wall["data"]["cards"]))
        wall_card = next(card for card in wall["data"]["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertTrue(any(event["card_id"] == card_id for event in wall["data"]["events"]))
        self.assertIn(card_short_id, wall["data"]["sections"]["board_content"]["text"])
        self.assertTrue(any(event["card_id"] == card_id for event in wall["data"]["sections"]["event_log"]["events"]))

        status, board_settings = self.request("/api/update_board_settings", {"board_scale": 1.25, "actor_name": "ИНСПЕКТОР"})
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
        self.service = CardService(self.store, logger)
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

    def request(self, path: str, payload: dict | None = None, *, method: str = "POST", token: str | None = None):
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
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

        status, snapshot = self.request("/api/get_board_snapshot?archive_limit=10&access_token=secret-token", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in snapshot["data"]["cards"]))

        request = urllib.request.Request(
            f"{self.base_url}/api/attachment?card_id={card_id}&attachment_id={attachment_id}&access_token=secret-token",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"hello")

    def test_board_context_route_describes_single_board_scope(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "КЛИЕНТСКИЙ ЗАЛ"}, token="secret-token")
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
        self.assertTrue(any(column["id"] == column_id for column in context["data"]["context"]["columns"]))
        self.assertIn("[BOARD CONTEXT]", context["data"]["text"])


if __name__ == "__main__":
    unittest.main()
