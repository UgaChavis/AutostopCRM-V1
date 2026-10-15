from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
import json
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.models import CARD_DESCRIPTION_LIMIT, Card
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class CardDescriptionContractTests(CardServiceCase):
    def _assert_persisted_description(self, card_id: str, expected: str) -> None:
        stored = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(card for card in stored["cards"] if card["id"] == card_id)
        self.assertEqual(expected, card["description"], "state.json")
        self.assertEqual(
            expected, self.service.get_card({"card_id": card_id})["card"]["description"], "readback"
        )
        reloaded = CardService(JsonStore(self.state_file, self.logger), self.logger)
        self.assertEqual(
            expected, reloaded.get_card({"card_id": card_id})["card"]["description"], "restart"
        )

    def test_create_and_update_preserve_exact_description_markdown_and_spaces(self) -> None:
        initial_description = (
            "  **Важно:** проверить течь  \n"
            "  *Комментарий мастера:* ждет диагностику  \n\n"
            "✅ ++Не потерять пробелы++  "
        )
        created = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Точный текст",
                "description": initial_description,
                "deadline": {"hours": 2},
            }
        )

        self.assertEqual(created["card"]["description"], initial_description)
        with self.subTest(phase="create"):
            self._assert_persisted_description(created["card"]["id"], initial_description)

        updated_description = "  первая строка  \n  вторая  строка  \n\n  финал  "
        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "description": updated_description,
            }
        )

        self.assertEqual(updated["card"]["description"], updated_description)
        with self.subTest(phase="update"):
            self._assert_persisted_description(created["card"]["id"], updated_description)

    def test_model_round_trip_preserves_authored_text_and_legacy_non_text_values(self) -> None:
        for value, expected in (
            ("  **Важно:** две  строки  \n    код  ", "  **Важно:** две  строки  \n    код  "),
            ("\t \n\n", "\t \n\n"),
            ("\r\n  строка\r\n  ", "\r\n  строка\r\n  "),
            ("\u00a0🚗\u00a0", "\u00a0🚗\u00a0"),
            (None, ""),
            (False, ""),
            (0, ""),
            ([], ""),
            ({}, ""),
            (True, "True"),
            (123, "123"),
            ({"legacy": "value"}, "{'legacy': 'value'}"),
        ):
            with self.subTest(value=value):
                card = Card.from_dict({"id": "description-model", "description": value})
                self.assertEqual(expected, card.description)
                self.assertEqual(expected, Card.from_dict(card.to_storage_dict()).description)

    def test_model_description_limit_counts_whitespace_without_removing_it(self) -> None:
        authored = "  " + "я" * (CARD_DESCRIPTION_LIMIT - 4) + "  "
        for raw in (authored, authored + " extra"):
            with self.subTest(length=len(raw)):
                card = Card.from_dict({"id": "description-limit", "description": raw})
                self.assertEqual(CARD_DESCRIPTION_LIMIT, len(card.description))
                self.assertEqual(raw[:CARD_DESCRIPTION_LIMIT], card.description)

    def test_service_description_limit_and_noop_keep_the_exact_saved_text(self) -> None:
        authored = "  " + "я" * (CARD_DESCRIPTION_LIMIT - 4) + "  "
        created = self.service.create_card({"title": "Description limit", "description": authored})
        card_id = created["card"]["id"]
        self._assert_persisted_description(card_id, authored)
        before = self.state_file.read_bytes()

        repeated = self.service.update_card({"card_id": card_id, "description": authored})

        self.assertFalse(repeated["meta"]["changed"])
        self.assertEqual(before, self.state_file.read_bytes())
        with self.assertRaises(ServiceError) as oversized:
            self.service.update_card({"card_id": card_id, "description": authored + "x"})
        self.assertEqual("validation_error", oversized.exception.code)
        self.assertEqual("description", oversized.exception.details["field"])
        self.assertEqual(before, self.state_file.read_bytes())

    def test_service_normalizes_windows_newlines_and_preserves_surrounding_text(self) -> None:
        created = self.service.create_card(
            {"title": "Windows text", "description": "  первая\r\nвторая\rтретья  "}
        )
        expected = "  первая\nвторая\nтретья  "

        self.assertEqual(expected, created["card"]["description"])
        self._assert_persisted_description(created["card"]["id"], expected)

    def test_both_writer_modes_preserve_the_exact_description(self) -> None:
        for fast_writes in (False, True):
            with (
                self.subTest(fast_writes=fast_writes),
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast_writes,
                ),
            ):
                initial = "  **Строка**\n    код  "
                created = self.service.create_card(
                    {"title": "Writer contract", "description": initial}
                )
                card_id = created["card"]["id"]
                self._assert_persisted_description(card_id, initial)

                updated = initial + "\n  финал  "
                self.service.update_card({"card_id": card_id, "description": updated})
                self._assert_persisted_description(card_id, updated)

    def test_card_description_preview_strips_minimal_formatting_markers(self) -> None:
        formatted_description = (
            "Проверить **подвеску**, *руль* и ++датчик ABS++.\n"
            "Комментарий Codex: ✅ оставить полный текст."
        )
        created = self.service.create_card(
            {
                "vehicle": "FORD FOCUS",
                "title": "Formatting preview",
                "description": formatted_description,
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        full_card = self.service.get_card({"card_id": card_id})["card"]
        snapshot = self.service.get_board_snapshot({"compact": True})
        compact_card = next(card for card in snapshot["cards"] if card["id"] == card_id)

        self.assertEqual(full_card["description"], formatted_description)
        self.assertNotIn("**", compact_card["description_preview"])
        self.assertNotIn("*руль*", compact_card["description_preview"])
        self.assertNotIn("++", compact_card["description_preview"])
        self.assertIn("подвеску", compact_card["description_preview"])
        self.assertIn("руль", compact_card["description_preview"])
        self.assertIn("датчик ABS", compact_card["description_preview"])
        self.assertIn("✅", compact_card["description_preview"])
        self.assertEqual(compact_card["description"], compact_card["description_preview"])

    def test_explicit_empty_vehicle_preserves_title_with_separator(self) -> None:
        card = Card.from_dict(
            {
                "id": "modern-card",
                "vehicle": "",
                "title": "MCP write flow / updated",
                "description": "Smoke test",
                "column": "inbox",
            },
            valid_columns={"inbox"},
        )
        self.assertEqual(card.vehicle, "")
        self.assertEqual(card.title, "MCP write flow / updated")
