from __future__ import annotations

import unittest
from pathlib import Path

from tests import test_api as api_fixtures


class ApiVehicleLinkContractTests(unittest.TestCase):
    # setUp registers the original temporary-storage, logger and server cleanups.
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def test_vehicle_link_api_rejects_collision_before_write_and_accepts_unique_alias(self):
        vehicle_ids = (
            "11111111-1111-4111-8111-111111111111",
            "11111111-2222-4222-8222-222222222222",
            "22222222-1111-4111-8111-111111111111",
        )
        client = self.service.create_client(
            {
                "display_name": "Synthetic API identity client",
                "vehicles": [
                    {"id": identifier, "vehicle": f"Synthetic vehicle {index}"}
                    for index, identifier in enumerate(vehicle_ids)
                ],
            }
        )["client"]
        card = self.service.create_card({"title": "Synthetic existing API request"})["card"]
        payload = {
            "card_id": card["id"],
            "client_id": client["id"],
            "expected_card_updated_at": card["updated_at"],
            "expected_client_updated_at": client["updated_at"],
            "sync_fields": False,
        }
        state_file = Path(self.temp_dir.name) / "state.json"
        baseline = state_file.read_bytes()
        status, rejected = self.request(
            "/api/link_card_to_client", payload | {"client_vehicle_id": "CV-11111111"}
        )
        self.assertEqual(status, 409)
        self.assertEqual(rejected["error"]["code"], "client_vehicle_ambiguous")
        self.assertEqual(state_file.read_bytes(), baseline)
        status, linked = self.request(
            "/api/link_card_to_client", payload | {"client_vehicle_id": "cv-22222222"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(linked["data"]["card"]["client_vehicle_id"], vehicle_ids[2])
        status, saved = self.request("/api/get_card", {"card_id": card["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["card"]["client_vehicle_id"], vehicle_ids[2])
