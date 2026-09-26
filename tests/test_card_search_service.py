from __future__ import annotations

from unittest.mock import Mock

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.card_service import ServiceError


class CardServiceSearchTests(CardServiceCase):
    def test_search_cards_matches_repair_order_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Nissan Teana J32",
                "title": "АКПП",
                "description": "Госномер В003НК124",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "license_plate": "В003НК124",
                    "works": [
                        {"name": "Диагностика АКПП", "quantity": "1", "price": "2000", "total": ""}
                    ],
                },
            }
        )

        by_number = self.service.search_cards({"query": "1", "limit": 10})
        self.assertTrue(any(card["id"] == card_id for card in by_number["cards"]))

        by_client = self.service.search_cards({"query": "Иван Иванов", "limit": 10})
        self.assertEqual(by_client["cards"][0]["id"], card_id)
        self.assertIn("repair_order_client", by_client["cards"][0]["match"]["fields"])

        by_plate = self.service.search_cards({"query": "В003НК124", "limit": 10})
        self.assertEqual(by_plate["cards"][0]["id"], card_id)
        self.assertIn("repair_order_license_plate", by_plate["cards"][0]["match"]["fields"])

    def test_search_cards_matches_vehicle_profile_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Проверка поиска по техкарте",
                "description": "Карточка без явного текста в описании по VIN",
                "deadline": {"hours": 4},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                },
            }
        )
        card_id = created["card"]["id"]

        by_vin = self.service.search_cards({"query": "JSAZC72S001234567", "limit": 5})
        self.assertEqual(by_vin["meta"]["total_matches"], 1)
        self.assertEqual(by_vin["cards"][0]["id"], card_id)
        self.assertIn("vin", by_vin["cards"][0]["match"]["fields"])

        by_engine = self.service.search_cards({"query": "K12B", "limit": 5})
        self.assertEqual(by_engine["meta"]["total_matches"], 1)
        self.assertEqual(by_engine["cards"][0]["id"], card_id)
        self.assertIn("engine_code", by_engine["cards"][0]["match"]["fields"])

    def test_search_cards_skips_event_count_build_when_no_matches(self) -> None:
        self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "SEARCH MISS",
                "description": "Card for empty search result optimization check.",
                "deadline": {"hours": 2},
            }
        )
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        found = self.service.search_cards({"query": "totally-missing-query", "limit": 5})

        self.assertEqual(found["cards"], [])
        self.assertEqual(found["meta"]["total_matches"], 0)
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_search_cards_normalizes_punctuation_and_service_markers(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST-CAR",
                "title": "[MCP TEST] Поиск по маркерам",
                "description": "Проверка поиска по mcp-test, скобкам и дефисам.",
                "tags": ["MCP_TEST", "SEARCH-CHECK"],
                "deadline": {"hours": 3},
            }
        )
        card_id = created["card"]["id"]

        by_plain_text = self.service.search_cards({"query": "mcp test", "limit": 5})
        self.assertEqual(by_plain_text["meta"]["total_matches"], 1)
        self.assertEqual(by_plain_text["cards"][0]["id"], card_id)

        by_hyphenated = self.service.search_cards({"query": "mcp-test", "limit": 5})
        self.assertEqual(by_hyphenated["meta"]["total_matches"], 1)
        self.assertEqual(by_hyphenated["cards"][0]["id"], card_id)

        by_tag_variant = self.service.search_cards({"query": "search check", "limit": 5})
        self.assertEqual(by_tag_variant["meta"]["total_matches"], 1)
        self.assertEqual(by_tag_variant["cards"][0]["id"], card_id)
        self.assertIn("tags", by_tag_variant["cards"][0]["match"]["fields"])

    def test_search_cards_matches_cyrillic_and_latin_vehicle_variants(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Ниссан Тиида",
                "title": "Диагностика",
                "description": "Проверка поиска по смешанным латинским и кириллическим формам.",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        by_latin = self.service.search_cards({"query": "Nissan Tiida", "limit": 5})
        self.assertEqual(by_latin["meta"]["total_matches"], 1)
        self.assertEqual(by_latin["cards"][0]["id"], card_id)

        by_short_latin = self.service.search_cards({"query": "Tiida", "limit": 5})
        self.assertEqual(by_short_latin["meta"]["total_matches"], 1)
        self.assertEqual(by_short_latin["cards"][0]["id"], card_id)

        by_cyrillic = self.service.search_cards({"query": "Тиида", "limit": 5})
        self.assertEqual(by_cyrillic["meta"]["total_matches"], 1)
        self.assertEqual(by_cyrillic["cards"][0]["id"], card_id)

    def test_search_cards_supports_query_filters_and_archive(self) -> None:
        created_column = self.service.create_column({"label": "ЭЛЕКТРИКИ"})
        column_id = created_column["column"]["id"]

        active = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "column": column_id,
                "tags": ["СРОЧНО", "ДИАГНОСТИКА"],
                "deadline": {"hours": 12},
            }
        )
        archived = self.service.create_card(
            {
                "vehicle": "LADA VESTA",
                "title": "АРХИВНАЯ ПРОВЕРКА",
                "description": "Старый кейс для возврата из архива",
                "tags": ["АРХИВ"],
                "deadline": {"hours": 4},
            }
        )
        self.service.archive_card({"card_id": archived["card"]["id"]})

        found = self.service.search_cards(
            {
                "query": "rio дроссель",
                "column": column_id,
                "tag": "срочно",
                "limit": 10,
            }
        )
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertFalse(found["meta"]["has_more"])
        self.assertEqual(found["cards"][0]["id"], active["card"]["id"])
        self.assertEqual(found["cards"][0]["column_label"], "ЭЛЕКТРИКИ")
        self.assertEqual(found["cards"][0]["heading"], "KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД")
        self.assertIn("vehicle", found["cards"][0]["match"]["fields"])

        archived_found = self.service.search_cards({"query": "архивная", "include_archived": True})
        self.assertEqual(archived_found["meta"]["total_matches"], 1)
        self.assertTrue(archived_found["cards"][0]["archived"])

        with self.assertRaises(ServiceError) as empty_search:
            self.service.search_cards({})
        self.assertEqual(empty_search.exception.code, "validation_error")
        self.assertIn("Для поиска нужно передать query", empty_search.exception.message)
