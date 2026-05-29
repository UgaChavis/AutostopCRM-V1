from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderOpenedAtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.repair_order_opened_at.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch_time(self, moment: datetime):
        return (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso", return_value=moment.isoformat()
            ),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        )

    def test_lazy_repair_order_opened_at_uses_order_creation_time_not_card_time(self) -> None:
        old_time = datetime(2026, 4, 1, 3, 0, tzinfo=UTC)
        new_order_time = datetime(2026, 5, 1, 3, 0, tzinfo=UTC)
        old_order_time = datetime(2026, 5, 2, 3, 0, tzinfo=UTC)
        patches = self._patch_time(old_time)
        with patches[0], patches[1], patches[2]:
            old_card = self.service.create_card(
                {"vehicle": "Toyota", "title": "Старая карточка", "deadline": {"hours": 2}}
            )["card"]
        patches = self._patch_time(new_order_time)
        with patches[0], patches[1], patches[2]:
            new_card = self.service.create_card(
                {"vehicle": "Nissan", "title": "Новая карточка", "deadline": {"hours": 2}}
            )["card"]
            new_order = self.service.get_repair_order({"card_id": new_card["id"]})["repair_order"]
        patches = self._patch_time(old_order_time)
        with patches[0], patches[1], patches[2]:
            old_order = self.service.get_repair_order({"card_id": old_card["id"]})["repair_order"]

        self.assertEqual(new_order["number"], "1")
        self.assertEqual(old_order["number"], "2")
        self.assertTrue(old_order["opened_at"].startswith("02.05.2026 "))
        audit = self.service.get_repair_order_number_audit()
        inversions = [
            issue for issue in audit["issues"] if issue["code"] == "number_time_inversion"
        ]
        self.assertEqual(inversions, [])


if __name__ == "__main__":
    unittest.main()
