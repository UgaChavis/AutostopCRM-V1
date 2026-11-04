"""A long vehicle ID must not be truncated into another vehicle's ID."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class ClientVehicleIdLengthTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="synthetic-vehicle-id-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.vehicle_id = "v" * 128
        self.client_id = self.service.create_client(
            {
                "display_name": "SYNTHETIC CLIENT",
                "vehicles": [{"id": self.vehicle_id, "vehicle": "ORIGINAL VEHICLE"}],
            }
        )["client"]["id"]
        self.card_id = self.service.create_card({"title": "SYNTHETIC CARD"})["card"]["id"]

    def files(self):
        result = {}
        for label, root in (("state", Path(self.temp_dir.name)), ("app", Path(self.app_data.name))):
            for path in root.rglob("*"):
                stat = path.stat()
                result[(label, str(path.relative_to(root)))] = (
                    stat.st_ino,
                    stat.st_mode,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    path.read_bytes() if path.is_file() else None,
                )
        return result

    def test_long_ids_are_rejected_before_selecting_or_mutating_a_vehicle(self):
        long_id = self.vehicle_id + "x"
        before = self.files()
        for operation, payload in (
            (
                self.service.upsert_client_vehicle,
                {"client_vehicle_id": long_id, "vehicle": {"vehicle": "CHANGED"}},
            ),
            (
                self.service.upsert_client_vehicle,
                {"vehicle_id": long_id, "vehicle": {"vehicle": "CHANGED"}},
            ),
            (self.service.upsert_client_vehicle, {"vehicle": {"id": long_id, "vehicle": "NEW"}}),
            (self.service.delete_client_vehicle, {"client_vehicle_id": long_id}),
            (self.service.delete_client_vehicle, {"vehicle_id": long_id}),
            (self.service.create_card, {"title": "MUST NOT CREATE", "client_vehicle_id": long_id}),
            (
                self.service.link_card_to_client,
                {"card_id": self.card_id, "client_vehicle_id": long_id},
            ),
            (
                self.service.create_card,
                {
                    "title": "MUST NOT CREATE",
                    "client_vehicle_id": long_id,
                    "create_vehicle_from_card": True,
                },
            ),
            (
                self.service.link_card_to_client,
                {
                    "card_id": self.card_id,
                    "client_vehicle_id": long_id,
                    "create_vehicle_from_card": True,
                },
            ),
        ):
            with self.subTest(operation=operation.__name__, payload=payload):
                with self.assertRaises(ServiceError) as failure:
                    operation({"client_id": self.client_id, **payload})
                self.assertEqual(failure.exception.code, "validation_error")
                self.assertEqual(failure.exception.details.get("field"), "client_vehicle_id")
                self.assertEqual(self.files(), before)

    def test_valid_full_and_short_ids_keep_their_existing_meaning(self):
        result = self.service.upsert_client_vehicle(
            {
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
                "vehicle": {"vehicle": "FULL ID"},
            }
        )
        self.assertFalse(result["meta"]["created"])
        self.assertEqual(result["vehicle"]["id"], self.vehicle_id)
        result = self.service.upsert_client_vehicle(
            {
                "client_id": self.client_id,
                "client_vehicle_id": "cv-vvvvvvvv",
                "vehicle": {"vehicle": "SHORT ID"},
            }
        )
        self.assertFalse(result["meta"]["created"])
        self.assertEqual(result["vehicle"]["id"], self.vehicle_id)
        self.assertEqual(result["vehicle"]["vehicle"], "SHORT ID")

    def test_client_create_and_update_reject_long_nested_vehicle_ids(self):
        long_id = self.vehicle_id + "x"
        before = self.files()
        for operation, payload in (
            (
                self.service.create_client,
                {"display_name": "NEW", "vehicles": [{"id": long_id, "vehicle": "NEW"}]},
            ),
            (
                self.service.create_client,
                {
                    "client": {
                        "display_name": "NEW",
                        "vehicles": [{"client_vehicle_id": long_id, "vehicle": "NEW"}],
                    }
                },
            ),
            (
                self.service.update_client,
                {"client_id": self.client_id, "vehicles": [{"id": long_id, "vehicle": "CHANGED"}]},
            ),
            (
                self.service.update_client,
                {
                    "client_id": self.client_id,
                    "patch": {"vehicles": [{"client_vehicle_id": long_id, "vehicle": "CHANGED"}]},
                },
            ),
        ):
            with self.subTest(operation=operation.__name__, payload=payload):
                with self.assertRaises(ServiceError) as failure:
                    operation(payload)
                self.assertEqual(failure.exception.code, "validation_error")
                self.assertEqual(self.files(), before)

    def test_client_update_accepts_vehicle_id_at_the_limit(self):
        result = self.service.update_client(
            {
                "client_id": self.client_id,
                "patch": {"vehicles": [{"id": self.vehicle_id, "vehicle": "UPDATED"}]},
            }
        )
        self.assertEqual(result["client"]["vehicles"][0]["id"], self.vehicle_id)
        self.assertEqual(result["client"]["vehicles"][0]["vehicle"], "UPDATED")

    def test_short_alias_conflicting_with_full_id_of_another_case_fails_closed(self):
        first_id = "12345678-abcd-0000-0000-000000000001"
        second_id = "CV-12345678"
        client = self.service.create_client(
            {
                "display_name": "SYNTHETIC COLLISION",
                "vehicles": [
                    {"id": first_id, "vehicle": "FIRST VEHICLE"},
                    {"id": second_id, "vehicle": "SECOND VEHICLE"},
                ],
            }
        )["client"]
        client_id = client["id"]
        result = self.service.upsert_client_vehicle(
            {
                "client_id": client_id,
                "client_vehicle_id": second_id,
                "vehicle": {"vehicle": "EXACT SECOND"},
            }
        )
        self.assertEqual(result["vehicle"]["id"], second_id)
        before = self.files()
        for operation, payload in (
            (
                self.service.upsert_client_vehicle,
                {"client_vehicle_id": "cv-12345678", "vehicle": {"vehicle": "MUST NOT SAVE"}},
            ),
            (self.service.delete_client_vehicle, {"client_vehicle_id": "cv-12345678"}),
            (
                self.service.create_card,
                {"client_vehicle_id": "cv-12345678", "title": "MUST NOT CREATE"},
            ),
            (
                self.service.link_card_to_client,
                {"client_vehicle_id": "cv-12345678", "card_id": self.card_id},
            ),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ServiceError) as failure:
                    operation({"client_id": client_id, **payload})
                self.assertEqual(failure.exception.code, "client_vehicle_ambiguous")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(self.files(), before)
        with self.assertRaises(ServiceError) as failure:
            self.service.delete_client_vehicle(
                {"client_id": client_id, "client_vehicle_id": first_id.upper()}
            )
        self.assertEqual(failure.exception.code, "not_found")
        self.assertEqual(self.files(), before)
