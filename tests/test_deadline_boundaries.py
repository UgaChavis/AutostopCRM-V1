from __future__ import annotations

from datetime import UTC, datetime, timedelta

# This fixture adds the application source to sys.path.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class DeadlineBoundaryTests(CardServiceCase):
    def test_valid_minimum_and_maximum_for_update_and_timer_paths(self) -> None:
        card_id = self.service.create_card({"title": "Initial"})["card"]["id"]
        sticky_id = self.service.create_sticky(
            {"text": "Initial", "x": 0, "y": 0, "deadline": {"hours": 1}}
        )["sticky"]["id"]
        for seconds in (1, 31_536_000):
            deadline = {"total_seconds": seconds}
            operations = (
                lambda: self.service.update_card({"card_id": card_id, "deadline": deadline})[
                    "card"
                ],
                lambda: self.service.set_card_deadline({"card_id": card_id, "deadline": deadline})[
                    "card"
                ],
                lambda: self.service.start_card_timer({"card_id": card_id, "deadline": deadline})[
                    "card"
                ],
                lambda: self.service.update_sticky({"sticky_id": sticky_id, "deadline": deadline})[
                    "sticky"
                ],
            )
            for operation in operations:
                with self.subTest(seconds=seconds, operation=operation):
                    self.assertEqual(operation()["deadline_total_seconds"], seconds)

    def test_oversized_combined_deadlines_do_not_commit_any_changes(self) -> None:
        card_id = self.service.create_card({"title": "Initial"})["card"]["id"]
        sticky_id = self.service.create_sticky(
            {"text": "Initial", "x": 0, "y": 0, "deadline": {"hours": 1}}
        )["sticky"]["id"]
        operations = {
            "card create": lambda deadline: self.service.create_card(
                {"title": "Extra", "deadline": deadline}
            ),
            "card update": lambda deadline: self.service.update_card(
                {"card_id": card_id, "title": "Changed", "deadline": deadline}
            ),
            "card deadline": lambda deadline: self.service.set_card_deadline(
                {"card_id": card_id, "deadline": deadline}
            ),
            "timer start": lambda deadline: self.service.start_card_timer(
                {"card_id": card_id, "deadline": deadline}
            ),
            "sticky create": lambda deadline: self.service.create_sticky(
                {"text": "Extra", "x": 0, "y": 0, "deadline": deadline}
            ),
            "sticky update": lambda deadline: self.service.update_sticky(
                {"sticky_id": sticky_id, "text": "Changed", "deadline": deadline}
            ),
        }
        before = self.state_file.read_bytes()
        for deadline in ({"days": 365, "seconds": 1}, {"total_seconds": 31_536_000, "days": 1}):
            for name, operation in operations.items():
                with self.subTest(operation=name, deadline=deadline):
                    with self.assertRaises(ServiceError) as error:
                        operation(deadline)
                    self.assertEqual(error.exception.code, "validation_error")
                    self.assertEqual(error.exception.status_code, 400)
                    self.assertEqual(error.exception.details, {"field": "deadline"})
                    self.assertEqual(before, self.state_file.read_bytes())
        self.assertEqual(self.service.get_card({"card_id": card_id})["card"]["title"], "Initial")
        self.assertEqual(self.store.read_stickies()[0].text, "Initial")

    def test_maximum_combined_deadline_survives_fresh_store_read(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        deadline = {"days": 364, "hours": 23, "minutes": 59, "seconds": 58, "total_seconds": 2}
        expected_timestamp = (now + timedelta(days=365)).isoformat()
        patches = self._patch_time(now)
        with patches[0], patches[1], patches[2]:
            card = self.service.create_card({"title": "Limit", "deadline": deadline})["card"]
            sticky = self.service.create_sticky(
                {"text": "Limit", "x": 0, "y": 0, "deadline": deadline}
            )["sticky"]
        fresh = JsonStore(state_file=self.state_file, logger=self.logger)
        stored_card = next(item for item in fresh.read_cards() if item.id == card["id"])
        stored_sticky = next(item for item in fresh.read_stickies() if item.id == sticky["id"])
        for actual in (card, sticky):
            self.assertEqual(actual["deadline_total_seconds"], 31_536_000)
            self.assertEqual(actual["deadline_timestamp"], expected_timestamp)
        for actual in (stored_card, stored_sticky):
            self.assertEqual(actual.deadline_total_seconds, 31_536_000)
            self.assertEqual(actual.deadline_timestamp, expected_timestamp)
