from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore
from tests.services_case import CardServiceCase


class CardMoveOrderTests(CardServiceCase):
    def _cards(self, column: str, count: int = 3) -> list[str]:
        # Creation prepends, so return the persisted order.
        for index in range(count):
            self.service.create_card({"title": f"Order {column} {index}", "column": column})
        return self._order(column)

    def _order(self, column: str, *, fresh: bool = False) -> list[str]:
        store = JsonStore(self.store._state_file, self.logger) if fresh else self.store
        cards = [c for c in store.read_bundle()["cards"] if c.column == column and not c.archived]
        cards.sort(key=lambda c: c.position)
        self.assertEqual([c.position for c in cards], list(range(len(cards))))
        return [c.id for c in cards]

    def test_explicit_end_same_column_and_restart(self) -> None:
        first, middle, last = self._cards("inbox")
        result = self.service.move_card(
            {"card_id": first, "column": "inbox", "placement": "end", "response_mode": "delta"}
        )
        self.assertTrue(result["meta"]["moved"])
        self.assertEqual(self._order("inbox", fresh=True), [middle, last, first])
        again = self.service.move_card({"card_id": first, "column": "inbox", "placement": "end"})
        self.assertFalse(again["meta"]["moved"])

    def test_delta_orders_active_cards_without_returning_archived_cards(self) -> None:
        first, last = self._cards("inbox", 2)
        archived = self.service.create_card({"title": "Archived", "column": "inbox"})
        self.service.archive_card({"card_id": archived["card"]["id"]})

        result = self.service.move_card(
            {
                "card_id": first,
                "column": "inbox",
                "placement": "end",
                "response_mode": "delta",
            }
        )

        self.assertEqual(result["affected_columns"][0]["ordered_card_ids"], [last, first])

    def test_other_column_top_middle_end_and_empty(self) -> None:
        first, middle, last = self._cards("inbox")
        target_first, target_last = self._cards("in_progress", 2)
        for card_id, extra, expected in (
            (first, {}, [first, target_first, target_last]),
            (middle, {"before_card_id": target_last}, [first, target_first, middle, target_last]),
            (last, {"placement": "end"}, [first, target_first, middle, target_last, last]),
        ):
            self.service.move_card({"card_id": card_id, "column": "in_progress", **extra})
            self.assertEqual(self._order("in_progress", fresh=True), expected)
        self.assertEqual(self._order("inbox"), [])
        self.service.move_card({"card_id": first, "column": "inbox", "placement": "end"})
        self.assertEqual(self._order("inbox", fresh=True), [first])

    def test_omitted_null_and_blank_anchor_keep_legacy_prepend(self) -> None:
        first, middle, last = self._cards("inbox")
        for anchor in ({}, {"before_card_id": None}, {"before_card_id": ""}):
            self.service.move_card({"card_id": last, "column": "inbox", **anchor})
            self.assertEqual(self._order("inbox"), [last, first, middle])
            self.service.move_card({"card_id": first, "column": "inbox"})
            self.service.move_card({"card_id": middle, "column": "inbox"})
            first, middle, last = self._order("inbox")

    def test_invalid_or_ambiguous_placement_does_not_write(self) -> None:
        first, middle, _last = self._cards("inbox")
        before = self.store._state_file.read_bytes()
        for extra in ({"placement": "sideways"}, {"placement": "end", "before_card_id": middle}):
            with self.subTest(extra=extra):
                with self.assertRaises(ServiceError) as raised:
                    self.service.move_card({"card_id": first, "column": "inbox", **extra})
                self.assertEqual(raised.exception.code, "validation_error")
                self.assertEqual(self.store._state_file.read_bytes(), before)

    def test_rejected_end_move_keeps_committed_order(self) -> None:
        first, middle, last = self._cards("inbox")
        with patch.object(self.service, "_save_bundle", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.service.move_card({"card_id": first, "column": "inbox", "placement": "end"})
        self.assertEqual(self._order("inbox", fresh=True), [first, middle, last])

    def test_end_move_cas_conflict_preserves_concurrent_order_in_both_write_paths(self) -> None:
        first, middle, last = self._cards("inbox")
        peer = CardService(JsonStore(self.state_file, self.logger), self.logger)
        for fast in (False, True):
            with self.subTest(fast=fast):
                self.service.move_card({"card_id": first, "column": "inbox"})
                self.service.move_card(
                    {"card_id": middle, "column": "inbox", "before_card_id": last}
                )
                original = self.service._save_bundle

                def save_after_peer(*args, **kwargs):
                    peer.move_card({"card_id": last, "column": "inbox", "before_card_id": first})
                    return original(*args, **kwargs)

                with (
                    patch(
                        "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                        return_value=fast,
                    ),
                    patch.object(self.service, "_save_bundle", side_effect=save_after_peer),
                    self.assertRaises(ServiceError) as raised,
                ):
                    self.service.move_card(
                        {"card_id": first, "column": "inbox", "placement": "end"}
                    )
                self.assertEqual(raised.exception.code, "state_write_conflict")
                self.assertEqual(self._order("inbox", fresh=True), [last, first, middle])
