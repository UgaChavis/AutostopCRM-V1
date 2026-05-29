from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.service.create_card(
            {
                "title": "Косвенная история по телефону",
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


if __name__ == "__main__":
    unittest.main()
