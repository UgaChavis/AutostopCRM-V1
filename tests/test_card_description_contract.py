from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.models import Card


class CardDescriptionContractTests(CardServiceCase):
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

        updated_description = "  первая строка  \n  вторая  строка  \n\n  финал  "
        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "description": updated_description,
            }
        )

        self.assertEqual(updated["card"]["description"], updated_description)

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
