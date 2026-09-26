from __future__ import annotations

from unittest.mock import patch

from tests.services_case import CardServiceCase


class ClientSearchIndexingTests(CardServiceCase):
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

    def test_manager_client_link_query_prefers_one_strong_identifier(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Диагностика",
                "description": "Клиент Тестовый, госномер А123ВС124",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_name": "Клиент Тестовый",
                    "customer_phone": "+7 913 000-11-22",
                    "registration_plate": "А123ВС124",
                },
            }
        )
        card = next(item for item in self.store.read_cards() if item.id == created["card"]["id"])

        query = self.service._manager_client_link_query(card)

        self.assertEqual(query, "+7 913 000-11-22")
        self.assertNotIn("Toyota", query)
        self.assertNotIn("Клиент", query)

    def test_audit_client_links_prepares_search_indexes_once(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "person",
                "last_name": "Тестовый",
                "first_name": "Клиент",
                "phone": "+7 913 000-11-22",
            }
        )["client"]
        for suffix in ("22", "23"):
            self.service.create_card(
                {
                    "vehicle": "Toyota Camry",
                    "title": f"Диагностика {suffix}",
                    "deadline": {"hours": 1},
                    "vehicle_profile": {
                        "customer_phone": f"+7 913 000-11-{suffix}",
                    },
                }
            )

        with (
            patch.object(
                self.service,
                "_client_search_index_for",
                wraps=self.service._client_search_index_for,
            ) as search_index,
            patch.object(
                self.service,
                "_client_related_vehicle_fields_index_for",
                wraps=self.service._client_related_vehicle_fields_index_for,
            ) as related_index,
            patch.object(
                self.service,
                "_client_related_search_index",
                wraps=self.service._client_related_search_index,
            ) as related_search_index,
        ):
            result = self.service.audit_client_links({"limit": 10})

        self.assertEqual(search_index.call_count, 1)
        self.assertEqual(related_index.call_count, 1)
        self.assertEqual(related_search_index.call_count, 1)
        self.assertEqual(result["cards"][0]["candidates"][0]["client"]["id"], client["id"])

    def test_list_clients_batches_related_cards_when_stats_requested(self) -> None:
        clients = [
            self.service.create_client(
                {
                    "display_name": f"Клиент пакетной статистики {index}",
                    "phone": f"+7 900 000-00-0{index}",
                }
            )["client"]
            for index in range(1, 4)
        ]
        for index, client in enumerate(clients, start=1):
            self.service.create_card(
                {
                    "vehicle": f"Toyota Test {index}",
                    "title": f"Работа {index}",
                    "description": "Проверка пакетной клиентской статистики",
                    "deadline": {"hours": 1},
                    "client_id": client["id"],
                    "vehicle_profile": {"vin": f"TESTVIN000000000{index}"},
                }
            )

        with patch.object(
            self.service,
            "_client_related_cards",
            side_effect=AssertionError("list_clients must use batched related-card lookup"),
        ):
            listed = self.service.list_clients({"limit": 10, "include_stats": True})

        listed_by_id = {client["id"]: client for client in listed["clients"]}
        self.assertEqual(listed["meta"]["returned"], 3)
        for client in clients:
            row = listed_by_id[client["id"]]
            self.assertEqual(row["stats"]["cards_total"], 1)
            self.assertEqual(row["stats"]["vehicles_total"], 1)
            self.assertEqual(
                row["vehicles_preview"][0]["vin"],
                "TESTVIN000000000" + client["phone"][-1],
            )
