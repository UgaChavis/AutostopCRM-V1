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


class CardDescriptionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.description_contract.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_and_update_preserve_exact_description_markdown_and_spaces(self) -> None:
        initial_description = (
            "  **Важно:** проверить течь  \n"
            "  *Комментарий мастера:* ждет диагностику  \n\n"
            "✅ ++Не потерять пробелы++  "
        )
        created = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Точный текст",
                "description": initial_description,
                "deadline": {"hours": 2},
            }
        )

        self.assertEqual(created["card"]["description"], initial_description)

        updated_description = "  первая строка  \n  вторая  строка  \n\n  финал  "
        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "description": updated_description,
            }
        )

        self.assertEqual(updated["card"]["description"], updated_description)
