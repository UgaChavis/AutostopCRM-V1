from __future__ import annotations

import http.client
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.operator_activity import OperatorActivityService  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class PartsStoreApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        logger = logging.getLogger(self.id())
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        store = JsonStore(state_file=root / "state.json", logger=logger)
        service = CardService(
            store,
            logger,
            attachments_dir=root / "attachments",
            repair_orders_dir=root / "repair-orders",
        )
        operator_service = OperatorAuthService(
            store,
            service,
            users_file=root / "users.json",
            activity_service=OperatorActivityService(
                activity_dir=root / "operator-activity", logger=logger
            ),
            logger=logger,
        )
        self.server = ApiServer(
            service,
            logger,
            operator_service=operator_service,
            start_port=0,
            fallback_limit=1,
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
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_store_column_persists_cards_and_is_locked(self) -> None:
        status, columns = self.request("/api/list_columns", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(columns["data"]["columns"][-1]["id"], "parts_store")

        status, created = self.request(
            "/api/create_card",
            {"title": "Тестовый запрос детали", "column": "parts_store", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.assertEqual(created["data"]["card"]["column"], "parts_store")

        status, card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(card["data"]["card"]["column"], "parts_store")
        for route, payload in (
            ("/api/rename_column", {"column_id": "parts_store", "label": "Переименовать"}),
            ("/api/delete_column", {"column_id": "parts_store"}),
            ("/api/move_column", {"column_id": "parts_store"}),
        ):
            status, result = self.request(route, payload)
            self.assertEqual(status, 409)
            self.assertEqual(result["error"]["code"], "system_column_locked")

    def test_store_visibility_is_independent_of_extra_column(self) -> None:
        status, login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        headers = {"X-Operator-Session": login["data"]["session"]["token"]}
        for extra_open, store_open in ((True, False), (False, True)):
            with self.subTest(extra_open=extra_open, store_open=store_open):
                status, saved = self.request(
                    "/api/update_personal_board_preferences",
                    {
                        "board_preferences": {
                            "extra_column": {
                                "is_open": extra_open,
                                "filter": {"tag_label": "ЛИЧНОЕ", "tag_color": "red"},
                            },
                            "parts_store_column": {"is_open": store_open},
                        }
                    },
                    headers=headers,
                )
                self.assertEqual(status, 200)
                preferences = saved["data"]["board_preferences"]
                self.assertEqual(preferences["extra_column"]["is_open"], extra_open)
                self.assertEqual(preferences["parts_store_column"]["is_open"], store_open)
                status, profile = self.request(
                    "/api/get_operator_profile", method="GET", headers=headers
                )
                self.assertEqual(status, 200)
                self.assertEqual(profile["data"]["board_preferences"], preferences)

    def test_move_column_keeps_store_column_at_right_edge(self) -> None:
        columns = []
        for label in ("FIRST", "SECOND", "THIRD"):
            status, created = self.request("/api/create_column", {"label": label})
            self.assertEqual(status, 200)
            columns.append(created["data"]["column"]["id"])
        first, second, third = columns

        status, moved = self.request(
            "/api/move_column", {"column_id": third, "before_column_id": first}
        )
        self.assertEqual(status, 200)
        ordered_ids = [item["id"] for item in moved["data"]["columns"]]
        self.assertEqual(ordered_ids[-4:], [third, first, second, "parts_store"])
        self.assertTrue(moved["data"]["meta"]["changed"])

        status, moved_to_end = self.request("/api/move_column", {"column_id": third})
        self.assertEqual(status, 200)
        ordered_ids = [item["id"] for item in moved_to_end["data"]["columns"]]
        self.assertEqual(ordered_ids[-2:], [third, "parts_store"])
