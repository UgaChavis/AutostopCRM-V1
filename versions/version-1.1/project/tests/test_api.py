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
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(self.store, logger)
        self.port = reserve_port()
        self.server = ApiServer(self.service, logger, start_port=self.port, fallback_limit=1)
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
            return exc.code, json.loads(exc.read().decode("utf-8"))

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

    def test_rejects_invalid_json_and_payload_type(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/create_card",
            data=b"{broken",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_json:
            urllib.request.urlopen(request, timeout=5)
        payload = json.loads(invalid_json.exception.read().decode("utf-8"))
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
                "source": "ui",
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
        self.assertIn("СТЕНА GPT", wall["data"]["text"])
        self.assertTrue(any(card["id"] == card_id for card in wall["data"]["cards"]))
        self.assertTrue(any(event["card_id"] == card_id for event in wall["data"]["events"]))

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
            return exc.code, json.loads(exc.read().decode("utf-8"))

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
        self.assertEqual(context["data"]["context"]["board_name"], "Current Minimal Kanban Board")
        self.assertEqual(context["data"]["context"]["board_scope"], "single_local_board_instance")
        self.assertIn("Do not use it for Trello, YouGile", context["data"]["context"]["scope_rule"])
        self.assertTrue(any(column["id"] == column_id for column in context["data"]["context"]["columns"]))
        self.assertIn("[BOARD CONTEXT]", context["data"]["text"])


if __name__ == "__main__":
    unittest.main()
