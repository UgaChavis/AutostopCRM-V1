from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class ClientSearchPhoneNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.client_phone_name.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_phone_like_client_name_is_found_by_phone_digits_without_phone_field(self) -> None:
        client = self.service.create_client({"display_name": "89504235457"})["client"]
        competitor = self.service.create_client({"display_name": "История с таким телефоном"})[
            "client"
        ]
        for index in range(15):
            self.service.create_card(
                {
                    "title": f"Косвенная история по телефону {index}",
                    "vehicle": "Toyota Corolla",
                    "client_id": competitor["id"],
                    "vehicle_profile": {"customer_phone": "8 950 423-54-57"},
                    "deadline": {"hours": 1},
                }
            )

        search = self.service.search_clients({"query": "89504235457", "limit": 5})

        self.assertTrue(search["clients"])
        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertIn(competitor["id"], {item["id"] for item in search["clients"]})

    def test_search_index_reused_and_invalidated_for_saves_and_external_reload(self) -> None:
        client = self.service.create_client({"display_name": "Первый клиент"})["client"]
        with patch.object(
            self.service,
            "_client_search_index_for",
            wraps=self.service._client_search_index_for,
        ) as build_index:
            self.assertEqual(
                self.service.search_clients({"query": "Первый"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 1)
            self.service.search_clients({"query": "клиент"})
            self.assertEqual(build_index.call_count, 1)

            self.service.update_client({"client_id": client["id"], "display_name": "Второй клиент"})
            self.assertEqual(self.service.search_clients({"query": "Первый"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Второй"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 2)

            external = CardService(
                JsonStore(state_file=self.state_file, logger=self.logger), self.logger
            )
            external.update_client({"client_id": client["id"], "display_name": "Третий клиент"})
            self.assertEqual(self.service.search_clients({"query": "Второй"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Третий"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 3)
            external.create_card(
                {
                    "title": "Внешняя карточка",
                    "vehicle": "Subaru Forester",
                    "client_id": client["id"],
                    "deadline": {"hours": 1},
                }
            )
            self.assertEqual(
                self.service.search_clients({"query": "Forester"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 4)

    def test_cached_search_preserves_original_ranking_for_name_phone_and_vehicle(self) -> None:
        first = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-57"}
        )["client"]
        self.service.create_client({"display_name": "Анна Петрова", "phone": "+7 950 423-54-58"})
        self.service.create_client({"display_name": "Третье лицо"})
        self.service.create_card(
            {
                "title": "Toyota Camry",
                "vehicle": "Toyota Camry",
                "client_id": first["id"],
                "vehicle_profile": {"vin": "JTNBE46K403123456"},
                "deadline": {"hours": 1},
            }
        )
        for query in ("Анна", "anna", "9504235457", "Toyota", "JTNBE46K403123456", ""):
            with self.subTest(query=query):
                bundle = self.service._store.read_bundle()
                clients = self.service._ordered_clients(bundle["clients"])
                original = self.service._rank_client_matches(clients, query, bundle["cards"])
                actual = self.service.search_clients({"query": query, "limit": 100})
                self.assertEqual(
                    [client.id for _, client in original],
                    [client["id"] for client in actual["clients"]],
                )
                self.assertEqual(actual["meta"]["total"], len(original))


if __name__ == "__main__":
    unittest.main()
