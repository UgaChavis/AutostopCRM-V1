from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import ClientVehicle, normalize_text
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from tests.services_case import CardServiceCase


class ClientSearchPhoneNameTests(CardServiceCase):
    def test_phone_like_client_name_is_found_by_phone_digits_without_phone_field(self) -> None:
        client = self.service.create_client({"display_name": "89504235457"})["client"]
        competitor = self.service.create_client({"display_name": "История с таким телефоном"})[
            "client"
        ]
        for index in range(15):
            self.service.create_card(
                {
                    "title": f"Косвенная история по телефону {index}",
                    "vehicle": "Toyota Corolla",
                    "client_id": competitor["id"],
                    "vehicle_profile": {"customer_phone": "8 950 423-54-57"},
                    "deadline": {"hours": 1},
                }
            )

        search = self.service.search_clients({"query": "89504235457", "limit": 5})

        self.assertTrue(search["clients"])
        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertIn(competitor["id"], {item["id"] for item in search["clients"]})

    def test_search_index_reused_and_invalidated_for_saves_and_external_reload(self) -> None:
        client = self.service.create_client({"display_name": "Первый клиент"})["client"]
        with patch.object(
            self.service,
            "_client_search_index_for",
            wraps=self.service._client_search_index_for,
        ) as build_index:
            self.assertEqual(
                self.service.search_clients({"query": "Первый"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 1)
            self.service.search_clients({"query": "клиент"})
            self.assertEqual(build_index.call_count, 1)

            self.service.update_client({"client_id": client["id"], "display_name": "Второй клиент"})
            self.assertEqual(self.service.search_clients({"query": "Первый"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Второй"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 2)

            external = CardService(
                JsonStore(state_file=self.state_file, logger=self.logger), self.logger
            )
            external.update_client({"client_id": client["id"], "display_name": "Третий клиент"})
            self.assertEqual(self.service.search_clients({"query": "Второй"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Третий"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 3)
            external.create_card(
                {
                    "title": "Внешняя карточка",
                    "vehicle": "Subaru Forester",
                    "client_id": client["id"],
                    "deadline": {"hours": 1},
                }
            )
            self.assertEqual(
                self.service.search_clients({"query": "Forester"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 4)

    def test_cached_search_preserves_original_ranking_for_name_phone_and_vehicle(self) -> None:
        first = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-57"}
        )["client"]
        self.service.create_client({"display_name": "Анна Петрова", "phone": "+7 950 423-54-58"})
        self.service.create_client({"display_name": "Третье лицо"})
        self.service.create_card(
            {
                "title": "Toyota Camry",
                "vehicle": "Toyota Camry",
                "client_id": first["id"],
                "vehicle_profile": {"vin": "JTNBE46K403123456"},
                "deadline": {"hours": 1},
            }
        )
        for query in ("Анна", "anna", "9504235457", "Toyota", "JTNBE46K403123456", ""):
            with self.subTest(query=query):
                bundle = self.service._store.read_bundle()
                clients = self.service._ordered_clients(bundle["clients"])
                original = self.service._rank_client_matches(clients, query, bundle["cards"])
                actual = self.service.search_clients({"query": query, "limit": 100})
                self.assertEqual(
                    [client.id for _, client in original],
                    [client["id"] for client in actual["clients"]],
                )
                self.assertEqual(actual["meta"]["total"], len(original))

    def test_weighted_search_fields_preserve_legacy_scores_and_order(self) -> None:
        first = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-57"}
        )["client"]
        tied = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-58"}
        )["client"]
        vehicle_owner = self.service.create_client(
            {"display_name": "Владелец Toyota", "phone": "+7 999 111-22-33"}
        )["client"]

        bundle = self.service._store.read_bundle()
        tied_at = "2025-01-01T00:00:00+00:00"
        for client in bundle["clients"]:
            if client.id in {first["id"], tied["id"]}:
                client.created_at = tied_at
                client.updated_at = tied_at
            if client.id == vehicle_owner["id"]:
                client.vehicles.append(
                    ClientVehicle.from_value(
                        {
                            "id": "synthetic-toyota-corolla",
                            "vehicle": "Toyota Corolla",
                            "brand": "Toyota",
                            "model": "Corolla",
                            "vin": "JTDBR32E720123456",
                        }
                    )
                )
        self.service._store.write_bundle(**bundle)
        self.service.create_card(
            {
                "title": "Synthetic Corolla service history",
                "vehicle": "Toyota Corolla",
                "client_id": vehicle_owner["id"],
                "vehicle_profile": {
                    "make_display": "Toyota",
                    "model_display": "Corolla",
                    "customer_name": "Владелец Toyota",
                    "customer_phone": "+7 999 111-22-33",
                },
                "deadline": {"hours": 1},
            }
        )

        queries = (
            "Анна",
            "Анна",
            "anna",
            "9504235457",
            "9991112233",
            "Toyota",
            "Corolla",
            "",
            "Иванова",
        )
        for query in queries:
            with self.subTest(query=query):
                bundle = self.service._store.read_bundle()
                clients = self.service._ordered_clients(bundle["clients"])
                expected = self._legacy_rank_clients(clients, bundle["cards"], query)
                actual = self.service._rank_client_matches(clients, query, bundle["cards"])
                self.assertEqual(
                    [(score, client.id) for score, client in actual],
                    expected,
                )

        tied_result = self.service.search_clients({"query": "Анна", "limit": 10})
        self.assertEqual(
            [item["id"] for item in tied_result["clients"][:2]],
            sorted((first["id"], tied["id"])),
        )

    def test_client_matching_treats_plus_seven_and_eight_phone_as_same(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Сидоров",
                "first_name": "Семен",
                "phone": "+7 (913) 333-44-55",
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "Осмотр",
                "description": "Разовая запись",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Сидоров Семен",
                    "customer_phone": "8 913 333-44-55",
                    "vin": "JN1TANT32U0012345",
                },
            }
        )
        card_id = created["card"]["id"]

        suggestion = self.service.suggest_clients_for_card({"card_id": card_id, "limit": 5})
        self.assertEqual(suggestion["clients"][0]["id"], client["id"])

        stats = self.service.get_client_stats({"client_id": client["id"]})
        self.assertEqual(stats["stats"]["cards_total"], 1)

    def test_client_search_uses_related_vehicle_plate_vin_and_phone_formats(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Петров",
                "first_name": "Петр",
                "phone": "+7 (913) 555-66-77",
            }
        )["client"]
        self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Плановое ТО",
                "description": "Тест поиска клиента по автомобилю",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Петров Петр",
                    "customer_phone": "8 913 555 66 77",
                    "vin": "JTDBE32K620654321",
                    "registration_plate": "А555ВС124",
                },
            }
        )

        for query in ("А555ВС124", "а555вс124", "Camry", "JTDBE32K620654321", "89135556677"):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], client["id"])
                self.assertEqual(
                    search["clients"][0]["vehicles_preview"][0]["vehicle"], "Toyota Camry"
                )
                self.assertEqual(
                    search["clients"][0]["vehicles_preview"][0]["vin"], "JTDBE32K620654321"
                )

    def test_client_search_ignores_placeholder_vehicle_vins(self) -> None:
        placeholder = self.service.create_client(
            {
                "display_name": "Плейсхолдер VIN",
                "vehicles": [
                    {
                        "vehicle": "Toyota Placeholder",
                        "vin": "1111111111111",
                    },
                    {
                        "vehicle": "Short Placeholder",
                        "vin": "-",
                    },
                ],
            }
        )["client"]
        valid = self.service.create_client(
            {
                "display_name": "Нормальный VIN",
                "vehicles": [
                    {
                        "vehicle": "Toyota Probox",
                        "vin": "NCP165-0033993",
                    }
                ],
            }
        )["client"]

        by_placeholder = self.service.search_clients({"query": "1111111111111", "limit": 5})
        by_short_placeholder = self.service.search_clients({"query": "-", "limit": 5})
        by_valid = self.service.search_clients({"query": "NCP165-0033993", "limit": 5})

        self.assertFalse(
            any(client["id"] == placeholder["id"] for client in by_placeholder["clients"])
        )
        self.assertFalse(
            any(client["id"] == placeholder["id"] for client in by_short_placeholder["clients"])
        )
        self.assertTrue(by_valid["clients"])
        self.assertEqual(by_valid["clients"][0]["id"], valid["id"])

    def test_client_search_ignores_placeholder_vins_from_related_cards(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с мусорным VIN в истории",
                "phone": "+7 913 111-22-33",
            }
        )["client"]
        self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Связанная карточка с плейсхолдером",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "customer_name": "Клиент с мусорным VIN в истории",
                    "customer_phone": "+7 913 111-22-33",
                    "vin": "1111111111111",
                },
                "repair_order": {
                    "client": "Клиент с мусорным VIN в истории",
                    "phone": "+7 913 111-22-33",
                    "vin": "ABC",
                },
            }
        )

        by_repeated_placeholder = self.service.search_clients(
            {"query": "1111111111111", "limit": 5}
        )
        by_short_placeholder = self.service.search_clients({"query": "ABC", "limit": 5})
        by_phone = self.service.search_clients({"query": "89131112233", "limit": 5})

        self.assertFalse(
            any(
                client_result["id"] == client["id"]
                for client_result in by_repeated_placeholder["clients"]
            )
        )
        self.assertFalse(
            any(
                client_result["id"] == client["id"]
                for client_result in by_short_placeholder["clients"]
            )
        )
        self.assertTrue(by_phone["clients"])
        self.assertEqual(by_phone["clients"][0]["id"], client["id"])

    def test_client_search_uses_secondary_card_customer_phone_for_related_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Федоров",
                "first_name": "Игорь",
                "phone": "+7 901 222-33-44",
            }
        )["client"]
        card = self.service.create_card(
            {
                "vehicle": "Honda Fit",
                "title": "Плановый осмотр",
                "description": "В карточке основной телефон другой",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Другой контакт",
                    "customer_phone": "+7 900 000-00-01",
                    "customer_phones": ["+7 900 000-00-01", "8 901 222-33-44"],
                    "vin": "GD123456789",
                    "registration_plate": "В222ВВ124",
                },
            }
        )["card"]

        by_plate = self.service.search_clients({"query": "В222ВВ124", "limit": 5})
        self.assertTrue(by_plate["clients"])
        self.assertEqual(by_plate["clients"][0]["id"], client["id"])
        self.assertEqual(by_plate["clients"][0]["vehicles_preview"][0]["vehicle"], "Honda Fit")

        suggestions = self.service.suggest_clients_for_card({"card_id": card["id"], "limit": 5})
        self.assertTrue(suggestions["clients"])
        self.assertEqual(suggestions["clients"][0]["id"], client["id"])

    def test_client_search_reuses_related_cards_for_selected_results(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Оптимизация поиска",
                "vehicles": [
                    {
                        "vehicle": "Toyota Prado",
                        "vin": "JTEBU3FJX05027767",
                        "license_plate": "О777ОО124",
                    }
                ],
            }
        )["client"]
        self.service.create_card(
            {
                "title": "Связанная история",
                "vehicle": "Toyota Prado",
                "vehicle_profile": {
                    "customer_name": "Оптимизация поиска",
                    "vin": "JTEBU3FJX05027767",
                    "registration_plate": "О777ОО124",
                },
                "deadline": {"hours": 1},
            }
        )

        with patch.object(
            self.service,
            "_client_related_cards",
            wraps=self.service._client_related_cards,
        ) as related_cards:
            search = self.service.search_clients({"query": "Toyota Prado", "limit": 5})

        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertEqual(related_cards.call_count, 0)

    def test_client_search_builds_related_vehicle_index_once_on_miss(self) -> None:
        self.service.create_client({"display_name": "Клиент без совпадения"})["client"]
        self.service.create_card(
            {
                "title": "История без совпадения",
                "vehicle": "Toyota Corolla",
                "description": "Проверка промаха поиска",
                "deadline": {"hours": 1},
                "vehicle_profile": {"vin": "NOMATCH0000000001"},
            }
        )

        with patch.object(
            self.service,
            "_client_related_vehicle_fields_index",
            wraps=self.service._client_related_vehicle_fields_index,
        ) as related_index:
            search = self.service.search_clients({"query": "ZZZ-UNKNOWN-999", "limit": 5})

        self.assertEqual(search["clients"], [])
        self.assertEqual(related_index.call_count, 1)

    def test_client_search_reuses_related_vehicle_index_between_queries(self) -> None:
        client = self.service.create_client({"display_name": "Клиент с кэшем поиска"})["client"]
        self.service.create_card(
            {
                "title": "История для кэша поиска",
                "vehicle": "Nissan Note",
                "description": "Проверка повторного поиска",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "vin": "SJNFAAE11U0123456",
                    "registration_plate": "К456КК124",
                },
            }
        )

        with patch.object(
            self.service,
            "_client_related_vehicle_fields_index",
            wraps=self.service._client_related_vehicle_fields_index,
        ) as related_index:
            first = self.service.search_clients({"query": "SJNFAAE11U0123456", "limit": 5})
            second = self.service.search_clients({"query": "К456КК124", "limit": 5})

        self.assertEqual(first["clients"][0]["id"], client["id"])
        self.assertEqual(second["clients"][0]["id"], client["id"])
        self.assertEqual(related_index.call_count, 1)

    def test_suggest_clients_for_card_uses_related_card_vehicle_fields(self) -> None:
        client = self.service.create_client({"display_name": "Клиент из истории VIN"})["client"]
        self.service.create_card(
            {
                "title": "Историческая привязка",
                "vehicle": "Subaru Forester",
                "description": "VIN есть только в связанной карточке",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "vin": "JF1SJ5LC5DG012345",
                    "registration_plate": "С123СС124",
                },
            }
        )
        candidate = self.service.create_card(
            {
                "title": "Новая карточка по VIN",
                "vehicle": "Subaru Forester",
                "description": "Клиента еще не выбрали",
                "deadline": {"hours": 1},
                "vehicle_profile": {"vin": "JF1SJ5LC5DG012345"},
            }
        )["card"]

        with patch.object(
            self.service,
            "_client_related_cards",
            side_effect=AssertionError("suggest_clients_for_card must use batched lookup"),
        ):
            suggestions = self.service.suggest_clients_for_card(
                {"card_id": candidate["id"], "limit": 5}
            )

        self.assertTrue(suggestions["clients"])
        self.assertEqual(suggestions["clients"][0]["id"], client["id"])
        self.assertEqual(
            suggestions["clients"][0]["vehicles_preview"][0]["vin"], "JF1SJ5LC5DG012345"
        )

    def test_client_search_matches_common_russian_phone_variants(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Смирнов",
                "first_name": "Илья",
                "phone": "+7 (901) 222-33-44",
            }
        )["client"]
        for query in (
            "+7 901 222 33 44",
            "8 901 222 33 44",
            "89012223344",
            "79012223344",
            "+7(901)222-33-44",
        ):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], client["id"])

        prefix_client = self.service.create_client(
            {
                "last_name": "Кузнецов",
                "first_name": "Павел",
                "phone": "+7 (902) 222-33-44",
            }
        )["client"]
        for query in ("8-902", "902 222 33", "89022223344", "79022223344"):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], prefix_client["id"])

    def test_client_search_uses_explicit_linked_card_phone(self) -> None:
        linked_client = self.service.create_client(
            {"display_name": "Связанный клиент без телефона"}
        )["client"]
        unlinked_same_name = self.service.create_client(
            {"display_name": "Связанный клиент без телефона"}
        )["client"]
        direct_client = self.service.create_client(
            {
                "display_name": "Прямой клиент с тем же телефоном",
                "phone": "+7 961 738-01-11",
            }
        )["client"]
        card = self.service.create_card(
            {
                "title": "Связанная карточка с телефоном",
                "vehicle": "BMW X5",
                "vehicle_profile": {
                    "customer_name": "Связанный клиент без телефона",
                    "customer_phone": "8 961 738-01-11",
                },
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.link_card_to_client(
            {"card_id": card["id"], "client_id": linked_client["id"], "sync_fields": False}
        )

        search = self.service.search_clients({"query": "89617380111", "limit": 10})
        found_ids = {client["id"] for client in search["clients"]}

        self.assertIn(linked_client["id"], found_ids)
        self.assertIn(direct_client["id"], found_ids)
        self.assertNotIn(unlinked_same_name["id"], found_ids)

    def _legacy_rank_clients(self, clients, cards, query: str) -> list[tuple[int, str]]:
        normalized_query = normalize_text(query, default="", limit=500)
        if not normalized_query:
            return [(1, client.id) for client in self.service._ordered_clients(clients)]

        query_variants = self.service._search_text_variants(normalized_query)
        query_digits = re.sub(r"\D+", "", normalized_query)
        query_phone_variants = self.service._phone_search_variants(normalized_query)
        phone_like_query = bool(query_digits) and not re.search(r"[A-Za-zА-Яа-я]", normalized_query)
        client_index = self.service._client_search_index_for(clients)
        related_fields = self.service._client_related_vehicle_fields_index_for(clients, cards)
        related_index = {}
        for client_id, fields in related_fields.items():
            searchable = [self.service._normalize_search_text(value) for value in fields if value]
            related_index[client_id] = {
                "searchable": searchable,
                "compact_searchable": [
                    re.sub(r"[\W_]+", "", value) for value in searchable if value
                ],
                "phone_keys": {
                    key for value in fields for key in self.service._phone_match_keys(value)
                },
            }

        ranked = []
        for client in clients:
            if phone_like_query:
                score = self.service._score_client_phone_like_match(
                    client,
                    query_digits=query_digits,
                    query_phone_variants=query_phone_variants,
                    client_search_index=client_index,
                    related_search_index_by_client_id=related_index,
                    query_variants=query_variants,
                )
            else:
                indexed = client_index.get(client.id, {})
                score = 0
                for variant in query_variants:
                    if not variant:
                        continue
                    parts = tuple(variant.split())
                    compact_variant = re.sub(r"[\W_]+", "", variant)
                    for value in indexed.get("searchable", []):
                        if value == variant:
                            score += 8
                        elif variant in value:
                            score += 4
                        elif all(part in value for part in parts):
                            score += 2
                    for value in indexed.get("vehicle_searchable", []):
                        if value == variant:
                            score += 7
                        elif variant in value:
                            score += 5
                        elif all(part in value for part in parts):
                            score += 3
                    if compact_variant and any(
                        compact_variant in value for value in indexed.get("compact_searchable", [])
                    ):
                        score += 5
                if len(query_digits) >= 4:
                    phone_digits = " ".join(re.sub(r"\D+", "", phone) for phone in client.phones)
                    if query_digits in phone_digits:
                        score += 10
                if query_phone_variants:
                    client_phone_variants = indexed.get("phone_variants", set())
                    if client_phone_variants and any(
                        query_variant in client_variant or client_variant in query_variant
                        for query_variant in query_phone_variants
                        for client_variant in client_phone_variants
                    ):
                        score += 10
                    elif query_phone_variants.intersection(indexed.get("match_keys", set())):
                        score += 10
                score += self.service._score_client_related_search_fields(
                    related_index.get(client.id),
                    query_variants=query_variants,
                    query_digits=query_digits,
                    query_phone_variants=query_phone_variants,
                )
            if score > 0:
                ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return [(score, client.id) for score, client in ranked]


if __name__ == "__main__":
    unittest.main()
