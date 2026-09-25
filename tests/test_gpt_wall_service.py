from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.models import AuditEvent, utc_now


class GptWallServiceTests(CardServiceCase):
    def test_gpt_wall_returns_full_context_layer(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "tags": ["СРОЧНО"],
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        card_short_id = created["card"]["short_id"]
        self.service.move_card(
            {"card_id": card_id, "column": "in_progress", "actor_name": "МАСТЕР", "source": "api"}
        )

        self.service.archive_card({"card_id": card_id, "actor_name": "MASTER", "source": "api"})
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 50})
        searched = self.service.search_cards(
            {"query": card_short_id, "limit": 5, "include_archived": True}
        )

        self.assertIn("text", wall)
        self.assertTrue(wall["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(wall["meta"]["text_format"], "markdown")
        self.assertEqual(wall["meta"]["section_kind"], "gpt_wall")
        self.assertEqual(wall["meta"]["event_order"], "newest_first")
        self.assertTrue(wall["meta"]["include_archived"])
        self.assertFalse(wall["meta"]["cards_compact"])
        self.assertIn("board_context", wall)
        self.assertIn("sections", wall)
        self.assertIn("board_content", wall["sections"])
        self.assertIn("event_log", wall["sections"])
        self.assertTrue(
            wall["sections"]["board_content"]["text"].startswith("# AutoStop CRM Board Content")
        )
        self.assertTrue(
            wall["sections"]["event_log"]["text"].startswith("# AutoStop CRM Event Log")
        )
        self.assertEqual(wall["sections"]["board_content"]["meta"]["text_format"], "markdown")
        self.assertEqual(wall["sections"]["board_content"]["meta"]["section_kind"], "board_content")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["text_format"], "markdown")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["section_kind"], "event_log")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["event_order"], "newest_first")
        self.assertIn(card_short_id, wall["text"])
        self.assertTrue(any(card["id"] == card_id for card in wall["cards"]))
        wall_card = next(card for card in wall["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertFalse(wall_card["vehicle_profile_compact"]["has_any_data"])
        self.assertTrue(any(event["card_id"] == card_id for event in wall["events"]))
        self.assertIn(card_short_id, wall["sections"]["board_content"]["text"])
        self.assertTrue(
            any(event["card_id"] == card_id for event in wall["sections"]["event_log"]["events"])
        )
        self.assertEqual(
            wall["board_context"]["context"]["board_scope"], "single_local_board_instance"
        )
        self.assertEqual(
            wall["meta"]["active_cards"], wall["board_context"]["context"]["active_cards_total"]
        )
        self.assertEqual(
            wall["meta"]["archived_cards"], wall["board_context"]["context"]["archived_cards_total"]
        )
        self.assertEqual(searched["cards"][0]["id"], card_id)
        self.assertIn("short_id: " + card_short_id, wall["text"])
        self.assertIn(card_short_id, wall["sections"]["event_log"]["text"])
        self.assertIn("## Cards By Column", wall["sections"]["board_content"]["text"])
        self.assertIn("## Archived Cards", wall["sections"]["board_content"]["text"])
        self.assertIn("card_id: " + card_id, wall["sections"]["board_content"]["text"])
        self.assertIn("status:", wall["sections"]["board_content"]["text"])
        self.assertIn("KIA RIO", wall["text"])
        self.assertIn("ПЛАВАЕТ ХОЛОСТОЙ ХОД", wall["text"])
        self.assertIn("МАСТЕР", wall["text"])

        board_content = self.service.get_board_content(
            {"include_archived": True, "view_mode": "agent"}
        )
        self.assertTrue(board_content["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(board_content["meta"]["section_kind"], "board_content")
        self.assertEqual(board_content["meta"]["response_mode"], "agent_context")
        self.assertEqual(board_content["meta"]["view_mode"], "agent")
        self.assertTrue(board_content["meta"]["cards_compact"])
        self.assertIn(card_short_id, board_content["text"])

        board_events = self.service.get_board_events({"include_archived": True, "event_limit": 50})
        self.assertTrue(board_events["text"].startswith("# AutoStop CRM Event Log"))
        self.assertEqual(board_events["meta"]["section_kind"], "event_log")
        self.assertEqual(board_events["meta"]["response_mode"], "audit")
        self.assertEqual(board_events["meta"]["event_limit"], 50)
        self.assertTrue(any(event["card_id"] == card_id for event in board_events["events"]))

    def test_gpt_wall_can_return_compact_cards_for_agent_reads(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "AUDI A6",
                "title": "AGENT COMPACT",
                "description": "Проверка компактного режима стены",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Audi",
                    "model_display": "A6",
                    "customer_name": "Тестовый клиент",
                },
            }
        )
        card_id = created["card"]["id"]

        wall = self.service.get_gpt_wall(
            {"include_archived": True, "event_limit": 20, "compact": True}
        )
        wall_card = next(card for card in wall["cards"] if card["id"] == card_id)

        self.assertTrue(wall["meta"]["cards_compact"])
        self.assertEqual(wall["meta"]["event_limit"], 20)
        self.assertNotIn("vehicle_profile", wall_card)
        self.assertIn("vehicle_profile_compact", wall_card)

    def test_gpt_wall_defaults_to_markdown_and_archived_cards(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "ARCHIVE DEFAULT",
                "title": "DEFAULT WALL INCLUDE",
                "description": "Архивная карточка должна входить в машинный снимок",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        card_short_id = created["card"]["short_id"]
        self.service.archive_card({"card_id": card_id, "actor_name": "MASTER", "source": "api"})

        wall = self.service.get_gpt_wall({})

        self.assertEqual(wall["meta"]["text_format"], "markdown")
        self.assertEqual(wall["meta"]["event_limit"], 100)
        self.assertTrue(wall["meta"]["include_archived"])
        self.assertEqual(wall["sections"]["event_log"]["meta"]["event_limit"], 100)
        self.assertTrue(any(card["id"] == card_id for card in wall["cards"]))
        self.assertIn("## Archived Cards", wall["sections"]["board_content"]["text"])
        self.assertIn("card_id: " + card_id, wall["sections"]["board_content"]["text"])
        self.assertIn("short_id: " + card_short_id, wall["sections"]["board_content"]["text"])

    def test_gpt_wall_event_log_uses_structured_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "LOG FORMAT",
                "description": "Проверка читаемости журнала",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        event_text = wall["sections"]["event_log"]["text"]

        self.assertTrue(event_text.startswith("# AutoStop CRM Event Log"))
        self.assertIn("## Metadata", event_text)
        self.assertIn("text_format: markdown", event_text)
        self.assertIn("section_kind: event_log", event_text)
        self.assertIn("event_order: newest_first", event_text)
        self.assertIn("## Events", event_text)
        self.assertIn("### Event 1", event_text)
        self.assertIn("time:", event_text)
        self.assertIn("actor:", event_text)
        self.assertIn("source:", event_text)
        self.assertIn("action:", event_text)
        self.assertIn("message:", event_text)
        self.assertIn(created["card"]["short_id"], event_text)

    def test_gpt_wall_repairs_mojibake_event_text(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "ENCODING CHECK",
                "description": "Проверка repair для event log",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        broken_message = "CHATGPT_AUDIT удалил столбец".encode("utf-8").decode("cp1251")  # noqa: UP012
        broken_detail = "Диагностика".encode("utf-8").decode("cp1251")  # noqa: UP012
        bundle = self.store.read_bundle()
        bundle["events"].append(
            AuditEvent(
                id="encoding-event",
                timestamp=utc_now().isoformat(),
                actor_name="CHATGPT_AUDIT",
                source="mcp",
                action="column_deleted",
                message=broken_message,
                details={"after": broken_detail},
                card_id=card_id,
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        repaired_event = next(event for event in wall["events"] if event["id"] == "encoding-event")

        self.assertEqual(repaired_event["message"], "CHATGPT_AUDIT удалил столбец")
        self.assertIn("Диагностика", repaired_event["details_text"])
        self.assertIn("CHATGPT_AUDIT удалил столбец", wall["sections"]["event_log"]["text"])

    def test_gpt_wall_includes_customer_contact_fields(self) -> None:
        self.service.create_card(
            {
                "vehicle": "AUDI A4",
                "title": "КЛИЕНТ НА СВЯЗИ",
                "description": "Проверить контакты в стене GPT",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Audi",
                    "model_display": "A4",
                    "customer_phone": "+7 900 123-45-67",
                    "customer_name": "Иван Иванов",
                },
            }
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})

        self.assertIn('"customer_phone":"+7 900 123-45-67"', wall["text"])
        self.assertIn('"customer_name":"Иван Иванов"', wall["text"])

    def test_gpt_wall_text_is_limited_to_3000_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "Много событий",
                "description": "Проверка усечения стены",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        bundle = self.store.read_bundle()
        for index in range(3600):
            bundle["events"].append(
                AuditEvent(
                    id=f"event-{index}",
                    timestamp=f"2026-04-02T12:00:00+00:00#{index:04d}",
                    actor_name="ТЕСТ",
                    source="api",
                    action="bulk_log",
                    message=f"Событие {index}",
                    details={"step": index},
                    card_id=card_id,
                )
            )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 5000})

        self.assertLessEqual(len(wall["text"].splitlines()), 3000)
        self.assertIn("[WALL TRUNCATED]", wall["text"])
        self.assertIn("### Event 1", wall["text"])
        self.assertIn("time:", wall["text"])
        self.assertIn("actor:", wall["text"])
        self.assertIn("action:", wall["text"])
