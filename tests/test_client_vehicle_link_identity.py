from __future__ import annotations

# The fixture selects this checkout's src before loading application modules.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.models import short_entity_id
from minimal_kanban.services.card_service import ServiceError


FIRST_VEHICLE = "11111111-1111-4111-8111-111111111111"
SECOND_VEHICLE = "11111111-2222-4222-8222-222222222222"
UNIQUE_VEHICLE = "22222222-1111-4111-8111-111111111111"


class ClientVehicleLinkIdentityTests(CardServiceCase):
    def _client(self, *, collision: bool = True):
        return self.service.create_client(
            {
                "display_name": "Synthetic vehicle identity client",
                "vehicles": [
                    {"id": FIRST_VEHICLE, "vehicle": "Synthetic A"},
                    {
                        "id": SECOND_VEHICLE if collision else UNIQUE_VEHICLE,
                        "vehicle": "Synthetic B",
                    },
                ],
            }
        )["client"]

    def _card(self):
        return self.service.create_card(
            {
                "title": "Synthetic existing request",
                "description": "Keep the existing conversation and agreement.",
                "vehicle": "Synthetic B",
                "tags": ["Existing tag"],
            }
        )["card"]

    def _json_bytes(self):
        return {
            str(path.relative_to(self.state_file.parent)): path.read_bytes()
            for path in self.state_file.parent.rglob("*.json")
        }

    def test_ambiguous_foreign_and_stale_links_leave_saved_json_unchanged(self):
        client, card = self._client(), self._card()
        other = self.service.create_client(
            {
                "display_name": "Synthetic other client",
                "vehicles": [{"id": UNIQUE_VEHICLE, "vehicle": "Synthetic foreign vehicle"}],
            }
        )["client"]
        self.assertEqual(other["vehicles"][0]["id"], UNIQUE_VEHICLE)
        payload = {
            "card_id": card["id"],
            "client_id": client["id"],
            "expected_card_updated_at": card["updated_at"],
            "expected_client_updated_at": client["updated_at"],
            "client_vehicle_id": SECOND_VEHICLE,
            "sync_fields": False,
        }
        baseline = self._json_bytes()
        for patch, code, status in (
            ({"client_vehicle_id": "CV-11111111"}, "client_vehicle_ambiguous", 409),
            ({"client_vehicle_id": UNIQUE_VEHICLE}, "not_found", 404),
            (
                {"expected_card_updated_at": "2000-01-01T00:00:00+00:00"},
                "card_update_conflict",
                409,
            ),
            (
                {"expected_client_updated_at": "2000-01-01T00:00:00+00:00"},
                "client_update_conflict",
                409,
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaises(ServiceError) as rejected:
                    self.service.link_card_to_client(payload | patch)
                self.assertEqual(rejected.exception.code, code)
                self.assertEqual(rejected.exception.status_code, status)
                self.assertEqual(self._json_bytes(), baseline)
        saved = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(saved["client_id"], "")
        self.assertEqual(saved["client_vehicle_id"], "")
        self.assertEqual(saved["vehicle"], card["vehicle"])

    def test_unique_short_alias_is_saved_canonically_without_losing_card_fields(self):
        client, card = self._client(collision=False), self._card()
        alias = short_entity_id(UNIQUE_VEHICLE, prefix="CV").lower()
        linked = self.service.link_card_to_client(
            {
                "card_id": card["id"],
                "client_id": client["id"],
                "client_vehicle_id": alias,
                "expected_card_updated_at": card["updated_at"],
                "expected_client_updated_at": client["updated_at"],
                "sync_fields": False,
            }
        )
        self.assertEqual(linked["meta"]["client_vehicle_id"], UNIQUE_VEHICLE)
        saved = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(saved["client_vehicle_id"], UNIQUE_VEHICLE)
        self.assertEqual(saved["client_id"], client["id"])
        for key in ("description", "tags", "column", "deadline_timestamp"):
            self.assertEqual(saved[key], card[key])
        self.assertEqual(len(self.service.get_client({"client_id": client["id"]})["vehicles"]), 2)

    def test_full_id_selects_exact_vehicle_despite_shared_short_alias(self):
        client, card = self._client(), self._card()
        linked = self.service.link_card_to_client(
            {
                "card_id": card["id"],
                "client_id": client["id"],
                "client_vehicle_id": SECOND_VEHICLE,
                "expected_card_updated_at": card["updated_at"],
                "expected_client_updated_at": client["updated_at"],
            }
        )["card"]
        self.assertEqual(linked["client_vehicle_id"], SECOND_VEHICLE)
        self.assertEqual(linked["vehicle"], "Synthetic B")
        self.assertEqual(
            self.service.get_card({"card_id": card["id"]})["card"]["client_vehicle_id"],
            SECOND_VEHICLE,
        )

    def test_shared_resolution_rejects_ambiguous_alias_before_other_mutations(self):
        client = self._client()
        baseline = self._json_bytes()
        for operation, payload in (
            (
                self.service.create_card,
                {
                    "title": "Must not be created",
                    "client_id": client["id"],
                    "client_vehicle_id": "CV-11111111",
                },
            ),
            (
                self.service.upsert_client_vehicle,
                {
                    "client_id": client["id"],
                    "client_vehicle_id": "CV-11111111",
                    "vehicle": {"vehicle": "Must not change"},
                },
            ),
            (
                self.service.delete_client_vehicle,
                {"client_id": client["id"], "client_vehicle_id": "CV-11111111"},
            ),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ServiceError) as rejected:
                    operation(payload)
                self.assertEqual(rejected.exception.code, "client_vehicle_ambiguous")
                self.assertEqual(self._json_bytes(), baseline)

    def test_unique_short_alias_remains_usable_for_vehicle_update_and_delete(self):
        client = self._client(collision=False)
        alias = short_entity_id(UNIQUE_VEHICLE, prefix="CV")
        self.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": alias,
                "vehicle": {"vehicle": "Synthetic updated"},
            }
        )
        saved = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(
            [vehicle["id"] for vehicle in saved["vehicles"]], [FIRST_VEHICLE, UNIQUE_VEHICLE]
        )
        self.assertEqual(saved["vehicles"][1]["vehicle"], "Synthetic updated")
        self.service.delete_client_vehicle({"client_id": client["id"], "client_vehicle_id": alias})
        self.assertEqual(
            [
                vehicle["id"]
                for vehicle in self.service.get_client({"client_id": client["id"]})["vehicles"]
            ],
            [FIRST_VEHICLE],
        )
