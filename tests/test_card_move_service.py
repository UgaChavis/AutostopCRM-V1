from __future__ import annotations

import json

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.models import Card, utc_now
from minimal_kanban.services.card_service import ServiceError


class CardMoveServiceTests(CardServiceCase):
    def test_move_card_can_reorder_within_same_column(self) -> None:
        first = self.service.create_card(
            {"title": "First", "column": "inbox", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"title": "Second", "column": "inbox", "deadline": {"hours": 2}}
        )
        third = self.service.create_card(
            {"title": "Third", "column": "inbox", "deadline": {"hours": 2}}
        )

        moved = self.service.move_card(
            {
                "card_id": first["card"]["id"],
                "column": "inbox",
                "before_card_id": second["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "inbox")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"][:3]],
            [third["card"]["id"], first["card"]["id"], second["card"]["id"]],
        )
        self.assertTrue(all("repair_order" not in card for card in moved["affected_cards"]))
        self.assertTrue(moved["meta"]["changed"])

        snapshot = self.service.get_board_snapshot()
        inbox_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [third["card"]["id"], first["card"]["id"], second["card"]["id"]],
        )

    def test_move_card_can_insert_before_card_in_another_column(self) -> None:
        source = self.service.create_card(
            {"title": "Source", "column": "inbox", "deadline": {"hours": 2}}
        )
        first_target = self.service.create_card(
            {"title": "Target A", "column": "in_progress", "deadline": {"hours": 2}}
        )
        second_target = self.service.create_card(
            {"title": "Target B", "column": "in_progress", "deadline": {"hours": 2}}
        )

        moved = self.service.move_card(
            {
                "card_id": source["card"]["id"],
                "column": "in_progress",
                "before_card_id": first_target["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "in_progress")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox", "in_progress"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"]],
            [second_target["card"]["id"], source["card"]["id"], first_target["card"]["id"]],
        )

        snapshot = self.service.get_board_snapshot()
        target_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "in_progress"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in target_cards[:3]],
            [second_target["card"]["id"], source["card"]["id"], first_target["card"]["id"]],
        )

    def test_move_card_delta_returns_only_ordered_ids_and_preserves_legacy_default(self) -> None:
        source = self.service.create_card(
            {"title": "Delta source", "column": "inbox", "deadline": {"hours": 2}}
        )
        target = self.service.create_card(
            {"title": "Delta target", "column": "in_progress", "deadline": {"hours": 2}}
        )

        delta = self.service.move_card(
            {
                "card_id": source["card"]["id"],
                "column": "in_progress",
                "before_card_id": target["card"]["id"],
                "response_mode": "delta",
            }
        )

        self.assertEqual(delta["meta"]["response_mode"], "delta")
        self.assertNotIn("affected_cards", delta)
        self.assertEqual(delta["affected_column_ids"], ["inbox", "in_progress"])
        self.assertEqual(
            delta["affected_columns"],
            [
                {"column_id": "inbox", "ordered_card_ids": []},
                {
                    "column_id": "in_progress",
                    "ordered_card_ids": [source["card"]["id"], target["card"]["id"]],
                },
            ],
        )
        self.assertTrue(all(not key.startswith("_") for key in delta["card"]))

        legacy = self.service.move_card({"card_id": source["card"]["id"], "column": "inbox"})
        self.assertIn("affected_cards", legacy)
        self.assertNotIn("affected_columns", legacy)
        self.assertNotIn("response_mode", legacy["meta"])

        with self.assertRaises(ServiceError) as invalid:
            self.service.move_card(
                {
                    "card_id": source["card"]["id"],
                    "column": "inbox",
                    "response_mode": "compact",
                }
            )
        self.assertEqual(invalid.exception.details["field"], "response_mode")

    def test_move_card_delta_payload_stays_below_production_scale_budget(self) -> None:
        bundle = self.store.read_bundle()
        now = utc_now().isoformat()
        cards = [
            Card.from_dict(
                {
                    "id": f"perf-card-{index:04d}",
                    "title": f"Production-scale card {index}",
                    "description": "Диагностическое описание " * 30,
                    "column": "inbox" if index < 310 else "in_progress",
                    "position": index if index < 310 else index - 310,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            for index in range(620)
        ]
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=cards,
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            inventory_items=bundle["inventory_items"],
            inventory_movements=bundle["inventory_movements"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        delta = self.service.move_card(
            {
                "card_id": "perf-card-0000",
                "column": "in_progress",
                "response_mode": "delta",
            }
        )
        payload_bytes = len(
            json.dumps(delta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

        self.assertLessEqual(payload_bytes, 100_000)
        self.assertEqual(
            sum(len(item["ordered_card_ids"]) for item in delta["affected_columns"]),
            620,
        )

    def test_move_card_without_before_card_id_inserts_at_top_of_target_column(self) -> None:
        source = self.service.create_card(
            {"title": "Source", "column": "inbox", "deadline": {"hours": 2}}
        )
        first_target = self.service.create_card(
            {"title": "Target A", "column": "in_progress", "deadline": {"hours": 2}}
        )
        second_target = self.service.create_card(
            {"title": "Target B", "column": "in_progress", "deadline": {"hours": 2}}
        )

        moved = self.service.move_card(
            {
                "card_id": source["card"]["id"],
                "column": "in_progress",
            }
        )

        self.assertEqual(moved["card"]["column"], "in_progress")
        self.assertEqual(moved["card"]["position"], 0)
        self.assertEqual(moved["affected_column_ids"], ["inbox", "in_progress"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"][:3]],
            [source["card"]["id"], second_target["card"]["id"], first_target["card"]["id"]],
        )

        snapshot = self.service.get_board_snapshot()
        target_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "in_progress"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in target_cards[:3]],
            [source["card"]["id"], second_target["card"]["id"], first_target["card"]["id"]],
        )
        self.assertEqual([card["position"] for card in target_cards[:3]], [0, 1, 2])

    def test_create_card_inserts_new_cards_at_top_of_column(self) -> None:
        first = self.service.create_card(
            {"title": "First", "column": "inbox", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"title": "Second", "column": "inbox", "deadline": {"hours": 2}}
        )
        third = self.service.create_card(
            {"title": "Third", "column": "inbox", "deadline": {"hours": 2}}
        )

        self.assertEqual(third["card"]["position"], 0)

        snapshot = self.service.get_board_snapshot()
        inbox_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [third["card"]["id"], second["card"]["id"], first["card"]["id"]],
        )
        self.assertEqual([card["position"] for card in inbox_cards[:3]], [0, 1, 2])
