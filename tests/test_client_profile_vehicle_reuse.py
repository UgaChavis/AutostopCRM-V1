"""Profile reuse preserves vehicle order, totals, and independent payloads."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import Card, ClientProfile


class ClientProfileVehicleReuseTests(CardServiceCase):
    def test_profile_reuses_vehicle_calculation_without_sharing_mutable_values(self):
        client = ClientProfile.from_dict(
            {
                "id": "synthetic-client",
                "display_name": "Synthetic client",
                "vehicles": [{"vehicle": "Stored car", "vin": "SYNTHETIC-STORED"}],
            }
        )
        cards = [
            Card.from_dict(
                {
                    "id": f"synthetic-card-{index}",
                    "title": f"Synthetic card {index}",
                    "client_id": client.id,
                    "vehicle": f"Synthetic vehicle {index}",
                    "vehicle_profile": {"vin": f"SYNTHETIC-{index}"},
                }
            )
            for index in range(3)
        ]
        deleted = self.service._client_vehicle_payload_from_card(cards[-1])
        client.deleted_vehicle_keys.append(
            self.service._client_vehicle_identity_key(
                deleted["vehicle"], deleted["vin"], deleted["license_plate"], deleted["year"]
            )
        )
        related = list(cards)
        vehicles = self.service._client_vehicles(client, cards, related_cards=related)
        self.assertEqual(len(vehicles), 3)
        expected_client = self.service._serialize_client(
            client, cards, include_stats=True, related_cards=related
        )
        with patch.object(
            self.service, "_client_vehicles", wraps=self.service._client_vehicles
        ) as build_vehicles:
            result = self.service._client_profile_payload(
                client, cards, order_limit=30, related_cards=related
            )
        self.assertEqual(build_vehicles.call_count, 1)
        self.assertEqual(result["client"], expected_client)
        self.assertEqual(result["vehicles"], vehicles)
        self.assertEqual(result["meta"]["vehicles_total"], len(vehicles))
        self.assertEqual(result["client"]["stats"]["vehicles_total"], len(vehicles))
        self.assertEqual(result["client"]["vehicles_preview"], vehicles[:2])
        original_vehicle = deepcopy(result["vehicles"][0])
        result["client"]["vehicles_preview"][0]["vehicle"] = "Changed preview"
        self.assertEqual(result["vehicles"][0], original_vehicle)
        self.assertEqual(client.vehicles[0].vehicle, "Stored car")
