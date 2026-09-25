from __future__ import annotations

from unittest.mock import patch

from tests.services_case import CardServiceCase


class ClientVehicleSearchTests(CardServiceCase):
    def test_client_profile_can_store_imported_vehicles(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "ooo",
                "short_name": "ГрандСервис",
                "legal_name": 'ООО "ГрандСервис"',
                "phone": "+7 923 339-78-84",
                "inn": "2465257740",
                "vehicles": [
                    {
                        "brand": "Toyota",
                        "model": "Probox",
                        "vin": "ncp165-0033993",
                        "year": 2017,
                    }
                ],
            }
        )["client"]

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"][0]["vehicle"], "Toyota Probox")
        self.assertEqual(profile["vehicles"][0]["vin"], "NCP165-0033993")
        self.assertEqual(profile["vehicles"][0]["year"], "2017")

        search = self.service.search_clients({"query": "Probox", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertEqual(search["clients"][0]["vehicles_preview"][0]["vehicle"], "Toyota Probox")
        self.assertTrue(search["clients"][0]["vehicles_preview"][0]["id"])

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
