from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.storage.json_store import JsonStore


class JsonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.storage.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recovers_from_broken_json(self) -> None:
        self.state_file.write_text("{broken json", encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        cards = store.read_cards()

        self.assertEqual(cards, [])
        self.assertTrue(self.state_file.exists())
        self.assertTrue(self.state_file.with_suffix(".corrupted.json").exists())

    def test_repairs_invalid_card_state_and_migrates_legacy_fields(self) -> None:
        raw_state = {
            "schema_version": 2,
            "columns": [
                {"id": "inbox", "label": "Входящие"},
                {"id": "column_1", "label": "Блокеры"},
            ],
            "cards": [
                {
                    "title": "",
                    "description": "x" * 6000,
                    "priority": "urgent",
                    "column": "trash",
                    "archived": "false",
                    "elapsed_seconds": -25,
                    "timer_started_at": "not-a-date",
                    "indicator": "blue",
                }
            ],
            "settings": {"has_seen_onboarding": True},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        cards = store.read_cards()
        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        repaired_card = repaired_state["cards"][0]

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].title, "Без названия")
        self.assertEqual(cards[0].column, "inbox")
        self.assertGreater(cards[0].deadline_total_seconds, 0)
        self.assertIn("deadline_timestamp", repaired_card)
        self.assertIn("deadline_total_seconds", repaired_card)
        self.assertNotIn("priority", repaired_card)
        self.assertNotIn("indicator", repaired_card)
        self.assertNotIn("elapsed_seconds", repaired_card)
        self.assertNotIn("timer_started_at", repaired_card)

    def test_repairs_invalid_columns_and_missing_card_column(self) -> None:
        raw_state = {
            "schema_version": 3,
            "columns": [
                {"id": "inbox", "label": "Входящие"},
                {"id": "column_1", "label": "Блокеры"},
                {"id": "column_1", "label": "Дубль"},
                {"id": "", "label": "Пусто"},
            ],
            "cards": [
                {
                    "title": "Карточка",
                    "description": "",
                    "column": "missing_column",
                    "archived": False,
                    "deadline_timestamp": "2026-03-24T12:00:00+00:00",
                    "deadline_total_seconds": 3600,
                }
            ],
            "settings": {"has_seen_onboarding": False},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        columns = store.read_columns()
        cards = store.read_cards()
        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertTrue(any(column.id == "column_1" for column in columns))
        self.assertEqual(sum(1 for column in columns if column.id == "column_1"), 1)
        self.assertEqual(cards[0].column, "inbox")
        self.assertEqual(repaired_state["cards"][0]["column"], "inbox")


if __name__ == "__main__":
    unittest.main()
