from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.card_service import ServiceError


class CardServiceStickyNotesTests(CardServiceCase):
    def test_sticky_notes_are_created_moved_updated_and_deleted(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Проверить сход-развал",
                "x": 120,
                "y": 80,
                "deadline": {"hours": 4},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        self.assertTrue(created["sticky"]["short_id"].startswith("S-"))

        snapshot = self.service.get_board_snapshot()
        self.assertIn("stickies", snapshot)
        self.assertTrue(any(item["id"] == sticky_id for item in snapshot["stickies"]))
        self.assertGreater(snapshot["meta"]["stickies_total"], 0)

        moved = self.service.move_sticky(
            {"sticky_id": sticky_id, "x": 240, "y": 160, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertEqual(moved["sticky"]["x"], 240)
        self.assertEqual(moved["sticky"]["y"], 160)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_id,
                "text": "Проверить сход-развал после замены рулевых тяг",
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertIn("после замены", updated["sticky"]["text"])

        deleted = self.service.delete_sticky(
            {"sticky_id": sticky_id, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))

        events = self.store.read_bundle()["events"]
        self.assertTrue(any(event.action == "sticky_created" for event in events))
        self.assertTrue(any(event.action == "sticky_moved" for event in events))
        self.assertTrue(any(event.action == "sticky_text_changed" for event in events))
        self.assertTrue(any(event.action == "sticky_deleted" for event in events))

    def test_sticky_notes_accept_total_seconds_and_short_id_lookup(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Перезвонить клиенту",
                "deadline": {"total_seconds": 3600},
                "x": 10,
                "y": 20,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        sticky_short_id = created["sticky"]["short_id"]
        self.assertGreater(created["sticky"]["remaining_seconds"], 0)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_short_id,
                "text": "Перезвонить клиенту после согласования",
                "deadline": {"minutes": 45},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertEqual(updated["sticky"]["id"], sticky_id)
        self.assertIn("после согласования", updated["sticky"]["text"])

        deleted = self.service.delete_sticky(
            {"sticky_id": sticky_short_id, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))

    def test_sticky_position_rejects_bool_and_fractional_values(self) -> None:
        for value in (True, 12.5, "12.5"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as position_error:
                    self.service.create_sticky(
                        {"text": "Стикер", "x": value, "y": 0, "deadline": {"hours": 1}}
                    )
                self.assertEqual(position_error.exception.code, "validation_error")
                self.assertEqual(position_error.exception.details.get("field"), "x")
