from __future__ import annotations

# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError


class CardTagServiceTests(CardServiceCase):
    def test_colored_tags_roundtrip_and_search_by_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Цветные метки",
                "description": "Проверка цветов",
                "tags": [
                    {"label": "СРОЧНО", "color": "red"},
                    {"label": "СОГЛАСОВАТЬ", "color": "yellow"},
                ],
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        self.assertEqual(created["card"]["tags"], ["СРОЧНО", "СОГЛАСОВАТЬ"])
        self.assertEqual(created["card"]["tag_items"][0]["color"], "red")
        self.assertEqual(created["card"]["tag_items"][1]["color"], "yellow")

        found = self.service.search_cards({"query": "согласовать", "tag": "срочно", "limit": 5})
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertEqual(found["cards"][0]["id"], card_id)

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "tags": [
                    {"label": "СРОЧНО", "color": "yellow"},
                    {"label": "СОГЛАСОВАТЬ", "color": "green"},
                ],
            }
        )
        self.assertEqual(updated["card"]["tag_items"][0]["color"], "yellow")
        self.assertEqual(updated["card"]["tag_items"][1]["color"], "green")
        events = self.service.get_card_log({"card_id": card_id})["events"]
        self.assertTrue(
            any(
                event["action"] == "tag_color_changed" and "изменил цвет метки" in event["message"]
                for event in events
            )
        )

    def test_rejects_more_than_three_tags(self) -> None:
        with self.assertRaises(ServiceError) as tag_limit_error:
            self.service.create_card(
                {
                    "title": "Слишком много меток",
                    "description": "Проверка ограничения",
                    "tags": ["СРОЧНО", "ЖДЁМ", "СОГЛАСОВАТЬ", "ЗАКАЗАТЬ"],
                    "deadline": {"hours": 2},
                }
            )

        self.assertEqual(tag_limit_error.exception.code, "validation_error")
