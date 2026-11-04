"""Client vehicle writes optionally require the observed client revision."""

# The service fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class ClientVehicleCasTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.client_id = self.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"][
            "id"
        ]
        result = self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"vehicle": "SYNTHETIC ORIGINAL"}}
        )
        self.vehicle_id = result["vehicle"]["id"]
        self.revision = result["client"]["updated_at"]
        self.card_id = self.service.create_card(
            {
                "title": "SYNTHETIC CARD",
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
            }
        )["card"]["id"]
        self.revision = self.service.get_client({"client_id": self.client_id})["client"][
            "updated_at"
        ]

    def test_stale_update_and_delete_leave_vehicle_and_link_unchanged(self):
        self.service.update_client({"client_id": self.client_id, "comment": "OTHER OPERATOR"})
        before = self.state_file.read_bytes()
        for method, extra in (
            (self.service.upsert_client_vehicle, {"vehicle": {"vehicle": "STALE EDIT"}}),
            (self.service.delete_client_vehicle, {"unlink_cards": True}),
        ):
            with self.subTest(method=method.__name__), self.assertRaises(ServiceError) as error:
                method(
                    {
                        "client_id": self.client_id,
                        "client_vehicle_id": self.vehicle_id,
                        "expected_client_updated_at": self.revision,
                        **extra,
                    }
                )
            self.assertEqual(error.exception.code, "client_update_conflict")
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["client_vehicle_id"],
            self.vehicle_id,
        )

    def test_invalid_or_inexact_revisions_and_legacy_caller(self):
        before = self.state_file.read_bytes()
        for value in (None, "", "  ", 0, False, [], {}):
            with self.subTest(value=value), self.assertRaises(ServiceError) as error:
                self.service.delete_client_vehicle(
                    {
                        "client_id": self.client_id,
                        "client_vehicle_id": self.vehicle_id,
                        "expected_client_updated_at": value,
                    }
                )
            self.assertEqual(error.exception.code, "validation_error")
            self.assertEqual(self.state_file.read_bytes(), before)
        for value in (" " + self.revision, self.revision + "x" * 200):
            with self.subTest(value=value), self.assertRaises(ServiceError) as error:
                self.service.upsert_client_vehicle(
                    {
                        "client_id": self.client_id,
                        "client_vehicle_id": self.vehicle_id,
                        "expected_client_updated_at": value,
                        "vehicle": {"vehicle": "STALE EDIT"},
                    }
                )
            self.assertEqual(error.exception.code, "client_update_conflict")
            self.assertEqual(self.state_file.read_bytes(), before)
        updated = self.service.upsert_client_vehicle(
            {
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
                "vehicle": {"vehicle": "LEGACY EDIT"},
            }
        )
        self.assertEqual(updated["vehicle"]["vehicle"], "LEGACY EDIT")
        deleted = self.service.delete_client_vehicle(
            {"client_id": self.client_id, "client_vehicle_id": self.vehicle_id}
        )
        self.assertTrue(deleted["meta"]["deleted"])

    def test_frozen_clock_and_observed_revision(self):
        instant = datetime.fromisoformat(self.revision)
        with patch("minimal_kanban.services.card_service_clients.utc_now", return_value=instant):
            updated = self.service.upsert_client_vehicle(
                {
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                    "expected_client_updated_at": self.revision,
                    "vehicle": {"vehicle": "FIRST"},
                }
            )
            newer = updated["client"]["updated_at"]
            self.assertGreater(datetime.fromisoformat(newer), instant)
            with self.assertRaises(ServiceError) as error:
                self.service.delete_client_vehicle(
                    {
                        "client_id": self.client_id,
                        "client_vehicle_id": self.vehicle_id,
                        "expected_client_updated_at": self.revision,
                    }
                )
            self.assertEqual(error.exception.code, "client_update_conflict")
            deleted = self.service.delete_client_vehicle(
                {
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                    "expected_client_updated_at": newer,
                }
            )
            self.assertTrue(deleted["meta"]["deleted"])
            self.assertGreater(
                datetime.fromisoformat(deleted["client"]["updated_at"]),
                datetime.fromisoformat(newer),
            )

    def test_create_requires_the_observed_revision_when_supplied(self):
        created = self.service.upsert_client_vehicle(
            {
                "client_id": self.client_id,
                "expected_client_updated_at": self.revision,
                "vehicle": {"vehicle": "SECOND SYNTHETIC CAR"},
            }
        )
        self.assertTrue(created["meta"]["created"])
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.service.upsert_client_vehicle(
                {
                    "client_id": self.client_id,
                    "expected_client_updated_at": self.revision,
                    "vehicle": {"vehicle": "STALE THIRD CAR"},
                }
            )
        self.assertEqual(error.exception.code, "client_update_conflict")
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_distinct_services_update_vs_delete_keep_signature_guard(self):
        other = self._build_service()
        barrier = Barrier(2)

        def synchronized_read(service):
            original = service._read_bundle_for_update

            def read(*args, **kwargs):
                result = original(*args, **kwargs)
                barrier.wait(timeout=10)
                return result

            return read

        def write(service, delete):
            payload = {
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
                "expected_client_updated_at": self.revision,
            }
            try:
                if delete:
                    service.delete_client_vehicle(payload)
                else:
                    service.upsert_client_vehicle({**payload, "vehicle": {"vehicle": "EDIT"}})
                return "saved"
            except ServiceError as error:
                return error.code

        with (
            patch.object(
                self.service, "_read_bundle_for_update", side_effect=synchronized_read(self.service)
            ),
            patch.object(other, "_read_bundle_for_update", side_effect=synchronized_read(other)),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = [pool.submit(write, self.service, False), pool.submit(write, other, True)]
            outcomes = [result.result() for result in results]
        self.assertCountEqual(outcomes, ["saved", "state_write_conflict"])
        vehicles = self.service.get_client({"client_id": self.client_id})["client"]["vehicles"]
        card = self.service.get_card({"card_id": self.card_id})["card"]
        if vehicles:
            self.assertEqual(vehicles[0]["vehicle"], "EDIT")
            self.assertEqual(card["client_vehicle_id"], self.vehicle_id)
        else:
            self.assertEqual(card["client_vehicle_id"], "")
