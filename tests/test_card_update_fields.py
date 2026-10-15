from __future__ import annotations

# The fixture import sets up the src path before importing application modules.
# ruff: noqa: I001
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class CardFieldUpdateContractTests(CardServiceCase):
    def _create_card(self) -> dict:
        return self.service.create_card(
            {
                "vehicle": "Old vehicle",
                "title": "Исходная заявка",
                "description": "Исходное описание",
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
                "source": "ui",
            }
        )["card"]

    def test_combined_update_preserves_field_precedence_audit_and_reload(self) -> None:
        moment = datetime.now(UTC).replace(microsecond=0)
        ticks = count()
        with (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso",
                side_effect=lambda: (moment + timedelta(microseconds=next(ticks))).isoformat(),
            ),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        ):
            created = self._create_card()
            earlier_events = {event.id for event in self.store.read_bundle()["events"]}
            # Input order deliberately opposes the domain order, including two
            # pairs whose final values depend on which field is applied last.
            payload = {
                "tags": [{"label": "СРОЧНО", "color": "red"}],
                "timer_state": "inactive",
                "deadline": {"minutes": 5, "seconds": 45},
                "description": "  **Описание**\nвторая строка  ",
                "title": "Новая заявка",
                "vehicle_profile": {"make_display": "Toyota", "model_display": "Camry"},
                "vehicle": "",
                "card_id": created["id"],
                "expected_updated_at": created["updated_at"],
                "actor_name": "BOB",
                "source": "ui",
            }
            original_payload = deepcopy(payload)

            result = self.service.update_card(payload)

            fields = [
                "vehicle",
                "vehicle_profile",
                "title",
                "description",
                "deadline",
                "timer_state",
                "tags",
            ]
            self.assertTrue(result["meta"]["changed"])
            self.assertEqual(fields, result["meta"]["changed_fields"])
            self.assertEqual(fields, result["meta"]["verification"]["changed_fields"])
            card = result["card"]
            self.assertEqual("Toyota Camry", card["vehicle"])
            self.assertEqual(payload["title"], card["title"])
            self.assertEqual(payload["description"], card["description"])
            self.assertEqual("inactive", card["timer_state"])
            self.assertEqual(345, card["deadline_total_seconds"])
            self.assertEqual(
                (moment + timedelta(seconds=345)).isoformat(), card["deadline_timestamp"]
            )
            self.assertEqual(["СРОЧНО"], card["tags"])
            self.assertEqual("red", card["tag_items"][0]["color"])
            self.assertEqual(original_payload, payload)
            events = [
                event
                for event in self.store.read_bundle()["events"]
                if event.id not in earlier_events
            ]
            self.assertEqual(
                [
                    "vehicle_changed",
                    "vehicle_profile_updated",
                    "title_changed",
                    "description_changed",
                    "signal_changed",
                    "timer_stopped",
                    "tag_added",
                    "tags_changed",
                ],
                [event.action for event in events],
            )
            self.assertTrue(
                all(event.actor_name == "BOB" and event.source == "ui" for event in events)
            )
            reloaded = CardService(JsonStore(self.state_file, self.logger), self.logger)
            persisted = reloaded.get_card({"card_id": card["id"]})["card"]
            for field in fields + ["deadline_timestamp", "deadline_total_seconds", "tag_items"]:
                if field != "deadline":
                    self.assertEqual(card[field], persisted[field], field)

    def test_unchanged_fields_preserve_state_bytes_cache_and_audit(self) -> None:
        created = self._create_card()
        retained = self.store.read_bundle()
        before = deepcopy(retained)
        state_bytes = self.state_file.read_bytes()

        result = self.service.update_card(
            {
                "card_id": created["id"],
                "vehicle": created["vehicle"],
                "title": created["title"],
                "description": created["description"],
                "timer_state": created["timer_state"],
                "tags": created["tag_items"],
                "actor_name": "BOB",
                "source": "ui",
            }
        )

        self.assertFalse(result["meta"]["changed"])
        self.assertEqual([], result["meta"]["changed_fields"])
        self.assertEqual(created["updated_at"], result["card"]["updated_at"])
        self.assertEqual(before, retained)
        self.assertEqual(state_bytes, self.state_file.read_bytes())

    def test_late_validation_error_preserves_cache_files_and_caller_payload(self) -> None:
        created = self._create_card()
        retained = self.store.read_bundle()
        before = deepcopy(retained)
        root = Path(self.temp_dir.name)
        files_before = {
            path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
        }
        payload = {
            "card_id": created["id"],
            "vehicle": "",
            "vehicle_profile": {"make_display": "Toyota", "model_display": "Camry"},
            "title": "Rejected title",
            "description": "Rejected archived detail " * 100,
            "deadline": {"minutes": 5},
            "timer_state": "inactive",
            "tags": {"invalid": "not an array"},
            "actor_name": "BOB",
            "source": "ui",
        }
        original_payload = deepcopy(payload)

        with self.assertRaises(ServiceError) as rejected:
            self.service.update_card(payload)

        self.assertEqual("validation_error", rejected.exception.code)
        self.assertEqual(400, rejected.exception.status_code)
        self.assertEqual("tags", rejected.exception.details["field"])
        self.assertEqual(before, retained)
        self.assertEqual(original_payload, payload)
        self.assertEqual(
            files_before,
            {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            },
        )
        reloaded = CardService(JsonStore(self.state_file, self.logger), self.logger)
        persisted = reloaded.get_card({"card_id": created["id"]})["card"]
        for field in (
            "vehicle",
            "vehicle_profile",
            "title",
            "description",
            "timer_state",
            "tag_items",
        ):
            self.assertEqual(created[field], persisted[field], field)
