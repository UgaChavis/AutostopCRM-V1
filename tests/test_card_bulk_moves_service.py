from __future__ import annotations

from tests.services_case import CardServiceCase


class CardServiceBulkMoveTests(CardServiceCase):
    def test_bulk_move_cards_moves_many_cards_and_reports_partial_failures(self) -> None:
        created_column = self.service.create_column({"label": "MCP TEST COLUMN"})
        target_column = created_column["column"]["id"]
        first = self.service.create_card(
            {"vehicle": "CAR-1", "title": "Bulk one", "column": "inbox", "deadline": {"hours": 3}}
        )
        second = self.service.create_card(
            {
                "vehicle": "CAR-2",
                "title": "Bulk two",
                "column": "in_progress",
                "deadline": {"hours": 3},
            }
        )
        already_there = self.service.create_card(
            {
                "vehicle": "CAR-3",
                "title": "Bulk three",
                "column": target_column,
                "deadline": {"hours": 3},
            }
        )
        archived = self.service.create_card(
            {
                "vehicle": "CAR-4",
                "title": "Bulk archived",
                "column": "done",
                "deadline": {"hours": 3},
            }
        )
        self.service.archive_card({"card_id": archived["card"]["id"]})
        moved = self.service.bulk_move_cards(
            {
                "card_ids": [
                    first["card"]["id"],
                    second["card"]["id"],
                    already_there["card"]["id"],
                    archived["card"]["id"],
                    "missing-card",
                    first["card"]["id"],
                ],
                "column": target_column,
                "actor_name": "MCP TEST",
                "source": "mcp",
            }
        )
        self.assertEqual(moved["meta"]["requested"], 5)
        self.assertEqual(moved["meta"]["moved"], 2)
        self.assertEqual(moved["meta"]["unchanged"], 1)
        self.assertEqual(moved["meta"]["errors"], 2)
        self.assertTrue(moved["meta"]["partial_failure"])
        self.assertTrue(all(card["column"] == target_column for card in moved["moved_cards"]))
        self.assertTrue(
            any(card["id"] == already_there["card"]["id"] for card in moved["unchanged_cards"])
        )
        self.assertTrue(
            any(
                item["card_id"] == archived["card"]["id"] and item["code"] == "archived_card"
                for item in moved["errors"]
            )
        )
        self.assertTrue(
            any(
                item["card_id"] == "missing-card" and item["code"] == "not_found"
                for item in moved["errors"]
            )
        )
        first_after = self.service.get_card({"card_id": first["card"]["id"]})["card"]
        second_after = self.service.get_card({"card_id": second["card"]["id"]})["card"]
        self.assertEqual(first_after["column"], target_column)
        self.assertEqual(second_after["column"], target_column)
        persisted_positions = {
            card["id"]: card["position"] for card in self.service.get_cards()["cards"]
        }
        for card in moved["moved_cards"] + moved["unchanged_cards"]:
            self.assertEqual(card["position"], persisted_positions[card["id"]])
        first_log = self.service.get_card_log({"card_id": first["card"]["id"]})["events"]
        self.assertTrue(any(event["action"] == "card_moved" for event in first_log))

    def test_bulk_move_cards_handles_large_batches(self) -> None:
        created_column = self.service.create_column({"label": "BATCH TARGET"})
        target_column = created_column["column"]["id"]
        source_columns = ["inbox", "in_progress", "done"]

        card_ids: list[str] = []
        for index in range(24):
            created = self.service.create_card(
                {
                    "vehicle": f"CAR-{index}",
                    "title": f"Batch {index}",
                    "column": source_columns[index % len(source_columns)],
                    "deadline": {"hours": 2},
                }
            )
            card_ids.append(created["card"]["id"])

        moved = self.service.bulk_move_cards(
            {
                "card_ids": card_ids,
                "column": target_column,
                "actor_name": "MCP TEST",
                "source": "mcp",
            }
        )

        self.assertEqual(moved["meta"]["requested"], 24)
        self.assertEqual(moved["meta"]["moved"], 24)
        self.assertEqual(moved["meta"]["errors"], 0)
        self.assertFalse(moved["meta"]["partial_failure"])

        snapshot_cards = self.service.get_cards()["cards"]
        moved_ids = {card["id"] for card in moved["moved_cards"]}
        self.assertEqual(moved_ids, set(card_ids))
        self.assertTrue(
            all(
                card["column"] == target_column
                for card in snapshot_cards
                if card["id"] in moved_ids
            )
        )
