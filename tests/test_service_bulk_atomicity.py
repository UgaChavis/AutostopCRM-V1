"""Bulk card moves preserve failed cards while committing successful siblings."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ruff: noqa: E402
from minimal_kanban.repair_order import RepairOrder
from tests.services_case import CardServiceCase


class BulkMoveAtomicityTests(CardServiceCase):
    def _write_bundle(self, bundle: dict) -> None:
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            inventory_items=bundle["inventory_items"],
            inventory_movements=bundle["inventory_movements"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    @staticmethod
    def _stored_card(bundle: dict, card_id: str) -> dict:
        return next(card for card in bundle["cards"] if card.id == card_id).to_storage_dict()

    @staticmethod
    def _card_events(bundle: dict, card_id: str) -> list[dict]:
        return [event.to_dict() for event in bundle["events"] if event.card_id == card_id]

    def test_bulk_move_cards_does_not_persist_a_failed_ready_transition(self) -> None:
        numbered = self.service.create_card(
            {"vehicle": "NUMBERED", "title": "Number owner", "deadline": {"hours": 2}}
        )
        numbered_order = self.service.update_card(
            {
                "card_id": numbered["card"]["id"],
                "repair_order": {"client": "Number owner"},
            }
        )["card"]["repair_order"]
        duplicate = self.service.create_card(
            {"vehicle": "DUPLICATE", "title": "Rejected move", "deadline": {"hours": 2}}
        )
        successful = self.service.create_card(
            {
                "vehicle": "SUCCESS",
                "title": "Successful sibling",
                "column": "in_progress",
                "deadline": {"hours": 2},
            }
        )

        injected = self.store.read_bundle()
        duplicate_model = next(
            card for card in injected["cards"] if card.id == duplicate["card"]["id"]
        )
        duplicate_model.repair_order = RepairOrder.from_dict(
            {
                **numbered_order,
                "client": "Duplicate number owner",
                "status": "open",
            }
        )
        self._write_bundle(injected)

        before = self.store.read_bundle()
        ready_column_id = before["settings"]["ready_column_id"]
        failed_before = self._stored_card(before, duplicate["card"]["id"])
        numbered_before = self._stored_card(before, numbered["card"]["id"])
        failed_events_before = self._card_events(before, duplicate["card"]["id"])

        result = self.service.bulk_move_cards(
            {
                "card_ids": [duplicate["card"]["id"], successful["card"]["id"]],
                "column": ready_column_id,
                "actor_name": "BULK TEST",
                "source": "api",
            }
        )

        self.assertEqual(result["meta"]["moved"], 1)
        self.assertEqual(result["meta"]["errors"], 1)
        self.assertEqual(result["errors"][0]["code"], "repair_order_number_duplicate")
        after = self.store.read_bundle()
        failed_after = self._stored_card(after, duplicate["card"]["id"])
        numbered_after = self._stored_card(after, numbered["card"]["id"])
        failed_events_after = self._card_events(after, duplicate["card"]["id"])
        successful_after = next(
            card for card in after["cards"] if card.id == successful["card"]["id"]
        )

        self.assertEqual(failed_after, failed_before)
        self.assertEqual(numbered_after, numbered_before)
        self.assertEqual(failed_events_after, failed_events_before)
        self.assertEqual(successful_after.column, ready_column_id)

    def test_bulk_move_cards_does_not_number_a_failed_ready_transition(self) -> None:
        failed = self.service.create_card(
            {"vehicle": "FAILED", "title": "Missing cashbox", "deadline": {"hours": 2}}
        )
        successful = self.service.create_card(
            {
                "vehicle": "SUCCESS",
                "title": "Successful sibling",
                "column": "in_progress",
                "deadline": {"hours": 2},
            }
        )
        injected = self.store.read_bundle()
        failed_model = next(card for card in injected["cards"] if card.id == failed["card"]["id"])
        failed_model.repair_order = RepairOrder.from_dict(
            {
                "client": "Broken payment",
                "status": "open",
                "number": "",
                "payments": [
                    {
                        "id": "payment-missing-cashbox",
                        "amount": "100",
                        "cashbox_id": "missing-cashbox",
                        "payment_method": "cash",
                    }
                ],
            }
        )
        self._write_bundle(injected)

        before = self.store.read_bundle()
        ready_column_id = before["settings"]["ready_column_id"]
        failed_before = self._stored_card(before, failed["card"]["id"])

        result = self.service.bulk_move_cards(
            {
                "card_ids": [failed["card"]["id"], successful["card"]["id"]],
                "column": ready_column_id,
                "actor_name": "BULK TEST",
                "source": "api",
            }
        )

        self.assertEqual(result["meta"]["moved"], 1)
        self.assertEqual(result["errors"][0]["code"], "not_found")
        after = self.store.read_bundle()
        failed_after = self._stored_card(after, failed["card"]["id"])
        successful_after = next(
            card for card in after["cards"] if card.id == successful["card"]["id"]
        )
        self.assertEqual(failed_after, failed_before)
        self.assertEqual(failed_after["repair_order"]["number"], "")
        self.assertEqual(successful_after.column, ready_column_id)
