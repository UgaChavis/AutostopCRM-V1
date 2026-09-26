from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.models import ClientVehicle
from minimal_kanban.services.card_service import ServiceError


class ClientVehicleLifecycleTests(CardServiceCase):
    def test_card_can_link_to_specific_client_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Мульти Клиент",
                "phone": "+7 913 777-88-99",
                "vehicles": [
                    {
                        "vehicle": "Toyota Camry 2018",
                        "brand": "Toyota",
                        "model": "Camry",
                        "vin": "JTDBE32K620654321",
                        "license_plate": "А777ВС124",
                        "year": "2018",
                    },
                    {
                        "vehicle": "Mercedes-Benz E200 2014",
                        "brand": "Mercedes-Benz",
                        "model": "E200",
                        "vin": "WDD2120341B009639",
                        "license_plate": "У867РУ124",
                        "year": "2014",
                    },
                ],
            }
        )["client"]
        vehicle_id = client["vehicles"][1]["id"]
        created = self.service.create_card(
            {
                "title": "Выбор автомобиля",
                "description": "Клиент приехал на Mercedes",
                "deadline": {"hours": 2},
                "vehicle_profile": {"customer_name": "Мульти Клиент"},
            }
        )["card"]

        linked = self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "sync_vehicle_fields": True,
            }
        )

        self.assertEqual(linked["card"]["client_id"], client["id"])
        self.assertEqual(linked["card"]["client_vehicle_id"], vehicle_id)
        self.assertEqual(linked["card"]["vehicle_profile"]["vin"], "WDD2120341B009639")
        self.assertEqual(linked["card"]["vehicle_profile"]["registration_plate"], "у867ру124")
        self.assertEqual(linked["card"]["vehicle_profile"]["make_display"], "Mercedes-Benz")

    def test_link_card_to_client_rejects_stale_card_and_client_revisions(self) -> None:
        client = self.service.create_client({"display_name": "Revision Client"})["client"]
        card = self.service.create_card(
            {
                "title": "Revision link",
                "vehicle": "Synthetic Vehicle",
                "deadline": {"hours": 1},
            }
        )["card"]
        payload = {
            "card_id": card["id"],
            "client_id": client["id"],
            "expected_card_updated_at": card["updated_at"],
            "expected_client_updated_at": client["updated_at"],
            "sync_fields": False,
        }

        with self.assertRaises(ServiceError) as stale_card:
            self.service.link_card_to_client(
                {
                    **payload,
                    "expected_card_updated_at": "2000-01-01T00:00:00+00:00",
                }
            )
        self.assertEqual(stale_card.exception.code, "card_update_conflict")
        with self.assertRaises(ServiceError) as stale_client:
            self.service.link_card_to_client(
                {
                    **payload,
                    "expected_client_updated_at": "2000-01-01T00:00:00+00:00",
                }
            )
        self.assertEqual(stale_client.exception.code, "client_update_conflict")
        self.assertEqual(
            self.service.get_card({"card_id": card["id"]})["card"]["client_id"],
            "",
        )

        linked = self.service.link_card_to_client(payload)
        self.assertEqual(linked["card"]["client_id"], client["id"])

    def test_link_card_to_client_can_create_vehicle_from_card_and_sync_back(self) -> None:
        client = self.service.create_client(
            {"display_name": "Клиент с новым авто", "phone": "+7 913 111-22-33"}
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail 2019",
                "title": "Новый автомобиль",
                "description": "Первичный осмотр",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Nissan",
                    "model_display": "X-Trail",
                    "production_year": 2019,
                    "vin": "JN1TANT32U0012345",
                    "registration_plate": "Н111НН124",
                },
            }
        )["card"]

        linked = self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "create_vehicle_from_card": True,
            }
        )
        vehicle_id = linked["card"]["client_vehicle_id"]
        self.assertTrue(vehicle_id)

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"][0]["id"], vehicle_id)
        self.assertEqual(profile["vehicles"][0]["vin"], "JN1TANT32U0012345")

        self.service.update_card(
            {
                "card_id": created["id"],
                "vehicle_profile": {
                    "vin": "JN1TANT32U0099999",
                    "registration_plate": "Н999НН124",
                },
            }
        )
        updated_profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(updated_profile["vehicles"][0]["vin"], "JN1TANT32U0099999")
        self.assertEqual(updated_profile["vehicles"][0]["license_plate"], "н999нн124")

    def test_client_vehicle_crud_syncs_and_hides_deleted_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент CRUD авто",
                "vehicles": [
                    {
                        "vehicle": "ГАЗ 2217 Соболь",
                        "vin": "X96221700G0801473",
                        "license_plate": "А111АА124",
                    }
                ],
            }
        )["client"]
        vehicle_id = client["vehicles"][0]["id"]
        card = self.service.create_card(
            {
                "title": "Связанная машина",
                "vehicle": "ГАЗ 2217 Соболь",
                "vehicle_profile": {"vin": "OLDVIN", "registration_plate": "О111ОО124"},
                "deadline": {"hours": 1},
            }
        )["card"]
        self.service.link_card_to_client(
            {
                "card_id": card["id"],
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "sync_vehicle_fields": True,
            }
        )

        updated = self.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "vehicle": {
                    "vehicle": "ГАЗ 2217 Соболь",
                    "vin": "X96221700G0999999",
                    "license_plate": "В222ВВ124",
                },
            }
        )
        self.assertIn(card["id"], updated["meta"]["synced_card_ids"])
        synced_card = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(synced_card["vehicle_profile"]["vin"], "X96221700G0999999")
        self.assertEqual(synced_card["vehicle_profile"]["registration_plate"], "в222вв124")

        deleted = self.service.delete_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "unlink_cards": True,
            }
        )
        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["linked_cards_unlinked"], 1)
        unlinked_card = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(unlinked_card["client_id"], client["id"])
        self.assertEqual(unlinked_card["client_vehicle_id"], "")
        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"], [])

    def test_client_vehicle_profile_patch_skips_out_of_range_numeric_fields(self) -> None:
        patch = self.service._client_vehicle_profile_patch(
            ClientVehicle(
                brand="Toyota",
                model="Camry",
                year="9999999999999999",
                mileage="999999999999999999",
            )
        )

        self.assertNotIn("production_year", patch)
        self.assertNotIn("mileage", patch)

    def test_create_card_supports_vehicle_profile_and_resolves_vehicle_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Техкарта Swift",
                "description": "Нужно собрать данные по автомобилю",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                    "registration_plate": "А123ВС77",
                    "pts_series": "77AA",
                    "pts_number": "123456",
                },
            }
        )

        self.assertEqual(created["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(created["card"]["vehicle_profile"]["registration_plate"], "а123вс77")
        self.assertEqual(created["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567")
        self.assertEqual(
            created["card"]["vehicle_profile_compact"]["display_name"], "Suzuki Swift 2014"
        )
        self.assertIn("make_display", created["card"]["vehicle_profile"]["manual_fields"])
        self.assertIn("engine_code", created["card"]["vehicle_profile"]["manual_fields"])

    def test_update_card_accepts_vehicle_profile_ui_alias_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Паспорт автомобиля",
                "description": "Проверка сохранения правой панели",
                "deadline": {"hours": 6},
            }
        )

        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "vehicle_profile": {
                    "display_name": "Toyota Camry",
                    "license_plate": "А111АА124",
                    "manual_fields": ["display_name", "license_plate"],
                    "field_sources": {
                        "display_name": "manual_ui",
                        "license_plate": "manual_ui",
                    },
                },
            }
        )

        profile = updated["card"]["vehicle_profile"]
        self.assertEqual(profile["display_name"], "Toyota Camry")
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["registration_plate"], "а111аа124")
        self.assertIn("make_display", profile["manual_fields"])
        self.assertIn("model_display", profile["manual_fields"])
        self.assertIn("registration_plate", profile["manual_fields"])
        self.assertEqual(profile["field_sources"]["make_display"], "manual_ui")
        self.assertEqual(profile["field_sources"]["registration_plate"], "manual_ui")

    def test_update_card_persists_vehicle_profile_display_name_from_full_ui_payload(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry 2014",
                "title": "Паспорт автомобиля",
                "description": "Проверка полного payload формы",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Toyota",
                    "model_display": "Camry",
                    "production_year": 2014,
                },
            }
        )["card"]

        updated = self.service.update_card(
            {
                "card_id": created["id"],
                "actor_name": "UI",
                "source": "ui",
                "vehicle": created["vehicle"],
                "title": created["title"],
                "description": created["description"],
                "deadline": {"hours": 6},
                "tags": [],
                "vehicle_profile": {
                    **created["vehicle_profile"],
                    "display_name": "Honda Fit",
                    "manual_fields": ["display_name"],
                    "field_sources": {"display_name": "manual_ui"},
                },
            }
        )["card"]
        reopened = self.service.get_card({"card_id": created["id"]})["card"]

        self.assertEqual(updated["vehicle_profile"]["display_name"], "Honda Fit 2014")
        self.assertEqual(updated["vehicle_profile"]["make_display"], "Honda")
        self.assertEqual(updated["vehicle_profile"]["model_display"], "Fit")
        self.assertEqual(reopened["vehicle_profile"]["display_name"], "Honda Fit 2014")
        self.assertEqual(reopened["vehicle"], "Honda Fit 2014")
