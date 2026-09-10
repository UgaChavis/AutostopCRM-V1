from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ruff: noqa: E402
from minimal_kanban.services.card_service import ServiceError
from tests.services_case import CardServiceCase


class ColumnDeleteIntegrityTests(CardServiceCase):
    def test_archived_card_blocks_column_delete_without_corrupting_references(self) -> None:
        column_id = self.service.create_column({"label": "ARCHIVED DELETE"})["column"]["id"]
        card_id = self.service.create_card(
            {
                "title": "ARCHIVED BOUND CARD",
                "deadline": {"hours": 2},
                "column": column_id,
            }
        )["card"]["id"]
        self.service.archive_card({"card_id": card_id})

        with self.assertRaises(ServiceError) as raised:
            self.service.delete_column({"column_id": column_id})

        self.assertEqual(raised.exception.code, "column_not_empty")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["cards_total"], 1)
        self.assertEqual(raised.exception.details["archived_cards_total"], 1)
        persisted = self.service.get_card({"card_id": card_id})["card"]
        self.assertTrue(persisted["archived"])
        self.assertEqual(persisted["column"], column_id)
        self.assertIn(column_id, {item["id"] for item in self.service.list_columns()["columns"]})


if __name__ == "__main__":
    unittest.main()
