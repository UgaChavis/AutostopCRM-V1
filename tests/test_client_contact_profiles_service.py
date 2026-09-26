from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.models import VehicleProfile


class ClientContactProfileTests(CardServiceCase):
    def test_clients_can_be_created_searched_and_linked_to_card(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "person",
                "last_name": "Иванов",
                "first_name": "Иван",
                "middle_name": "Иванович",
                "phone": "+7 913 000-11-22",
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Диагностика",
                "description": "Первичный осмотр",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Иванов",
                    "customer_phone": "+7 913 000-11-22",
                    "vin": "JTDBE32K620123456",
                    "registration_plate": "А123ВС124",
                },
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "",
                    "phone": "",
                    "vehicle": "Toyota Camry",
                    "vin": "JTDBE32K620123456",
                    "license_plate": "А123ВС124",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
            }
        )

        search = self.service.search_clients({"query": "Иванов", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])

        linked = self.service.link_card_to_client(
            {"card_id": card_id, "client_id": client["id"], "sync_fields": True}
        )
        self.assertEqual(linked["card"]["client_id"], client["id"])
        self.assertEqual(linked["card"]["repair_order"]["client"], "Иванов Иван Иванович")
        self.assertEqual(linked["card"]["repair_order"]["phone"], "+7 913 000-11-22")

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["client"]["stats"]["repair_orders_total"], 1)
        self.assertEqual(profile["vehicles"][0]["vin"], "JTDBE32K620123456")
        self.assertEqual(profile["repair_orders"][0]["card_id"], card_id)

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

    def test_client_profile_supports_up_to_three_phones(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с несколькими телефонами",
                "phone": "+7 900 000-00-01",
                "phones": [
                    "+7 900 000-00-01",
                    "8 901 000-00-02",
                    "+7 902 000-00-03",
                    "+7 903 000-00-04",
                ],
            }
        )["client"]

        self.assertEqual(client["phone"], "+7 900 000-00-01")
        self.assertEqual(
            client["phones"],
            ["+7 900 000-00-01", "8 901 000-00-02", "+7 902 000-00-03"],
        )

        search = self.service.search_clients({"query": "79020000003", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])

    def test_client_profile_supports_up_to_three_emails(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с несколькими email",
                "email": "info@example.com",
                "emails": [
                    "INFO@example.com",
                    "orders@example.com",
                    "parts@example.com",
                    "extra@example.com",
                ],
            }
        )["client"]

        self.assertEqual(client["email"], "info@example.com")
        self.assertEqual(
            client["emails"],
            ["info@example.com", "orders@example.com", "parts@example.com"],
        )

        search = self.service.search_clients({"query": "parts@example.com", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])

    def test_client_profile_deduplicates_russian_phone_formats(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с дублем телефона",
                "phone": "89535868635",
                "phones": ["+7 953 586-86-35", "+7 913 000-00-01"],
            }
        )["client"]

        self.assertEqual(client["phone"], "89535868635")
        self.assertEqual(client["phones"], ["89535868635", "+7 913 000-00-01"])

    def test_card_vehicle_profile_keeps_three_customer_phones(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент для карточки",
                "phones": [
                    "+7 900 111-11-11",
                    "+7 901 222-22-22",
                    "+7 902 333-33-33",
                ],
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Осмотр",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_name": "Клиент для карточки",
                    "customer_phones": [
                        "+7 999 111-11-11",
                        "+7 999 222-22-22",
                        "+7 999 333-33-33",
                        "+7 999 444-44-44",
                    ],
                },
            }
        )["card"]

        self.assertEqual(created["vehicle_profile"]["customer_phone"], "+7 999 111-11-11")
        self.assertEqual(
            created["vehicle_profile"]["customer_phones"],
            ["+7 999 111-11-11", "+7 999 222-22-22", "+7 999 333-33-33"],
        )

        empty_card = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Пустой телефон",
                "deadline": {"hours": 1},
            }
        )["card"]
        linked = self.service.link_card_to_client(
            {"card_id": empty_card["id"], "client_id": client["id"], "sync_fields": True}
        )["card"]
        self.assertEqual(linked["vehicle_profile"]["customer_phone"], "+7 900 111-11-11")
        self.assertEqual(
            linked["vehicle_profile"]["customer_phones"],
            ["+7 900 111-11-11", "+7 901 222-22-22", "+7 902 333-33-33"],
        )

    def test_card_vehicle_profile_deduplicates_russian_customer_phone_formats(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Дубли телефонов",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_phone": "8 913 222-33-44",
                    "customer_phones": [
                        "+7 913 222-33-44",
                        "+7 914 222-33-44",
                    ],
                },
            }
        )["card"]

        self.assertEqual(created["vehicle_profile"]["customer_phone"], "8 913 222-33-44")
        self.assertEqual(
            created["vehicle_profile"]["customer_phones"],
            ["8 913 222-33-44", "+7 914 222-33-44"],
        )

    def test_client_api_payload_accepts_nested_client_and_patch(self) -> None:
        created = self.service.create_client(
            {
                "client": {
                    "client_type": "ooo",
                    "legal_name": "ООО Ромашка",
                    "short_name": "Ромашка",
                    "inn": "5400000000",
                    "phone": "+7 913 222-33-44",
                }
            }
        )["client"]

        self.assertEqual(created["client_type"], "ooo")
        self.assertEqual(created["legal_name"], "ООО Ромашка")
        self.assertEqual(created["inn"], "5400000000")

        updated = self.service.update_client(
            {
                "client_id": created["id"],
                "patch": {
                    "contact_person": "Иванов Иван",
                    "comment": "Проверка nested patch",
                },
            }
        )["client"]

        self.assertEqual(updated["contact_person"], "Иванов Иван")
        self.assertEqual(updated["comment"], "Проверка nested patch")

    def test_vehicle_profile_preserves_customer_contact_fields(self) -> None:
        profile = VehicleProfile.from_dict(
            {
                "make_display": "Audi",
                "model_display": "A4",
                "mileage": 185000,
                "customer_phone": "+7 900 123-45-67",
                "customer_name": "Иван Иванов",
            }
        )

        payload = profile.to_dict()
        stored = profile.to_storage_dict()

        self.assertEqual(payload["mileage"], 185000)
        self.assertEqual(payload["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(payload["customer_name"], "Иван Иванов")
        self.assertEqual(stored["mileage"], 185000)
        self.assertEqual(stored["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(stored["customer_name"], "Иван Иванов")
        self.assertTrue(payload["has_any_data"])

    def test_create_client_reuses_exact_duplicate_without_explicit_id(self) -> None:
        first = self.service.create_client(
            {
                "display_name": "Дубль клиента",
                "phone": "8 953 586-86-35",
                "vehicles": [
                    {
                        "vehicle": "Kia Spectra",
                        "vin": "XWKFB227370040491",
                        "license_plate": "Т896ТЕ124",
                        "year": "2007",
                    }
                ],
            }
        )["client"]
        duplicate = self.service.create_client(
            {
                "display_name": "Дубль клиента",
                "phone": "+7 953 586-86-35",
                "vehicles": [
                    {
                        "vehicle": "Kia Spectra",
                        "vin": "XWKFB227370040491",
                        "license_plate": "т896те124",
                        "year": "2007",
                    }
                ],
            }
        )

        self.assertFalse(duplicate["meta"]["created"])
        self.assertTrue(duplicate["meta"]["duplicate"])
        self.assertEqual(duplicate["client"]["id"], first["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 1)

    def test_create_client_allows_same_phone_with_different_name(self) -> None:
        first = self.service.create_client(
            {"display_name": "Первый клиент", "phone": "+7 953 586-86-35"}
        )["client"]
        second = self.service.create_client(
            {"display_name": "Второй клиент", "phone": "8 953 586-86-35"}
        )["client"]

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 2)

    def test_create_client_keeps_new_vehicle_when_existing_has_none(self) -> None:
        first = self.service.create_client(
            {"display_name": "Клиент с новым авто позже", "phone": "+7 953 586-86-35"}
        )["client"]
        second = self.service.create_client(
            {
                "display_name": "Клиент с новым авто позже",
                "phone": "8 953 586-86-35",
                "vehicles": [{"vehicle": "Toyota Camry", "license_plate": "А123ВС124"}],
            }
        )

        self.assertTrue(second["meta"]["created"])
        self.assertNotEqual(first["id"], second["client"]["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 2)
