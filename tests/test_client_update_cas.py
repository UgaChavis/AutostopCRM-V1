"""Client profile revisions protect updates without changing legacy callers."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class ClientUpdateCasTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.client = self.service.create_client(
            {
                "display_name": "SYNTHETIC CLIENT",
                "comment": "ORIGINAL",
                "phone": "79990000001",
                "phones": ["79990000001", "79990000002"],
                "email": "a@example.test",
                "emails": ["a@example.test", "b@example.test"],
            }
        )["client"]
        self.client_id = self.client["id"]

    def test_stale_version_rejects_without_file_or_event_changes(self):
        version = self.client["updated_at"]
        latest = self.service.update_client(
            {"client_id": self.client_id, "comment": "OTHER OPERATOR"}
        )["client"]
        self.assertNotEqual(version, latest["updated_at"])
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.service.update_client(
                {"client_id": self.client_id, "expected_updated_at": version, "comment": "STALE"}
            )
        self.assertEqual(error.exception.code, "client_update_conflict")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(
            self.service.get_client({"client_id": self.client_id})["client"]["comment"],
            "OTHER OPERATOR",
        )

    def test_bad_versions_are_rejected_without_writes_or_truncation(self):
        before = self.state_file.read_bytes()
        for value in (None, "", " \t", 0, True, [], {}):
            with self.subTest(value=value), self.assertRaises(ServiceError) as error:
                self.service.update_client(
                    {"client_id": self.client_id, "expected_updated_at": value}
                )
            self.assertEqual(error.exception.code, "validation_error")
            self.assertEqual(self.state_file.read_bytes(), before)
        for value in (" " + self.client["updated_at"], self.client["updated_at"] + "x" * 200):
            with self.subTest(value=value), self.assertRaises(ServiceError) as error:
                self.service.update_client(
                    {"client_id": self.client_id, "expected_updated_at": value}
                )
            self.assertEqual(error.exception.code, "client_update_conflict")
            self.assertEqual(self.state_file.read_bytes(), before)

    def test_legacy_and_matching_revision_keep_vehicles_and_contact_semantics(self):
        self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"vehicle": "SYNTHETIC CAR"}}
        )
        latest = self.service.get_client({"client_id": self.client_id})["client"]
        updated = self.service.update_client(
            {
                "client_id": self.client_id,
                "expected_updated_at": latest["updated_at"],
                "phone": "",
                "phones": [],
                "email": "",
                "emails": [],
            }
        )["client"]
        self.assertEqual(updated["phones"], [])
        self.assertEqual(updated["emails"], [])
        self.assertEqual(len(updated["vehicles"]), 1)
        legacy = self.service.update_client(
            {"client_id": self.client_id, "comment": "LEGACY UPDATE"}
        )["client"]
        self.assertEqual(legacy["comment"], "LEGACY UPDATE")
        self.assertEqual(len(legacy["vehicles"]), 1)

    def test_parallel_writes_same_revision_allow_only_one(self):
        barrier = Barrier(2)

        def update(comment):
            barrier.wait(timeout=10)
            try:
                self.service.update_client(
                    {
                        "client_id": self.client_id,
                        "expected_updated_at": self.client["updated_at"],
                        "comment": comment,
                    }
                )
                return "saved"
            except ServiceError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(update, ("OPERATOR A", "OPERATOR B")))
        self.assertCountEqual(outcomes, ["saved", "client_update_conflict"])
        self.assertIn(
            self.service.get_client({"client_id": self.client_id})["client"]["comment"],
            ("OPERATOR A", "OPERATOR B"),
        )

    def test_distinct_service_locks_keep_file_signature_guard(self):
        other = self._build_service()
        barrier = Barrier(2)

        def synchronized_read(service):
            original = service._read_bundle_for_update

            def read(*args, **kwargs):
                result = original(*args, **kwargs)
                barrier.wait(timeout=10)
                return result

            return read

        def update(service, comment):
            try:
                service.update_client(
                    {
                        "client_id": self.client_id,
                        "expected_updated_at": self.client["updated_at"],
                        "comment": comment,
                    }
                )
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
            first = pool.submit(update, self.service, "OPERATOR A")
            second = pool.submit(update, other, "OPERATOR B")
            outcomes = [first.result(), second.result()]
        self.assertCountEqual(outcomes, ["saved", "state_write_conflict"])

    def test_frozen_and_backward_clock_do_not_repeat_version(self):
        version = self.client["updated_at"]
        original_time = datetime.fromisoformat(version)
        with patch(
            "minimal_kanban.services.card_service_clients.utc_now", return_value=original_time
        ):
            first = self.service.update_client(
                {"client_id": self.client_id, "expected_updated_at": version, "comment": "FIRST"}
            )["client"]
        self.assertGreater(datetime.fromisoformat(first["updated_at"]), original_time)
        with patch(
            "minimal_kanban.services.card_service_clients.utc_now",
            return_value=original_time - timedelta(days=1),
        ):
            second = self.service.update_client(
                {
                    "client_id": self.client_id,
                    "expected_updated_at": first["updated_at"],
                    "comment": "SECOND",
                }
            )["client"]
            with self.assertRaises(ServiceError) as error:
                self.service.update_client(
                    {
                        "client_id": self.client_id,
                        "expected_updated_at": version,
                        "comment": "STALE",
                    }
                )
        self.assertGreater(
            datetime.fromisoformat(second["updated_at"]),
            datetime.fromisoformat(first["updated_at"]),
        )
        self.assertEqual(error.exception.code, "client_update_conflict")

    def test_vehicle_and_card_creation_advance_client_version_with_frozen_clock(self):
        version = self.client["updated_at"]
        instant = datetime.fromisoformat(version)
        with patch("minimal_kanban.services.card_service_clients.utc_now", return_value=instant):
            vehicle = self.service.upsert_client_vehicle(
                {"client_id": self.client_id, "vehicle": {"vehicle": "SYNTHETIC CAR"}}
            )
            updated = vehicle["client"]
            self.assertGreater(datetime.fromisoformat(updated["updated_at"]), instant)
            with self.assertRaises(ServiceError) as error:
                self.service.update_client(
                    {
                        "client_id": self.client_id,
                        "expected_updated_at": version,
                        "comment": "STALE",
                    }
                )
            self.assertEqual(error.exception.code, "client_update_conflict")
            card = self.service.create_card(
                {
                    "title": "SYNTHETIC CAR",
                    "client_id": self.client_id,
                    "create_vehicle_from_card": True,
                    "vehicle_profile": {
                        "make_display": "SYNTHETIC",
                        "model_display": "MODEL",
                        "registration_plate": "A123BC",
                    },
                }
            )
            self.assertEqual(card["card"]["client_id"], self.client_id)
            latest = self.service.get_client({"client_id": self.client_id})["client"]
            self.assertGreater(
                datetime.fromisoformat(latest["updated_at"]),
                datetime.fromisoformat(updated["updated_at"]),
            )
            with self.assertRaises(ServiceError) as error:
                self.service.update_client(
                    {
                        "client_id": self.client_id,
                        "expected_updated_at": updated["updated_at"],
                        "comment": "STALE",
                    }
                )
            self.assertEqual(error.exception.code, "client_update_conflict")

    def test_maximum_revision_is_rejected_without_write(self):
        bundle = self.store.read_bundle()
        client = next(client for client in bundle["clients"] if client.id == self.client_id)
        client.updated_at = datetime.max.replace(tzinfo=UTC).isoformat()
        self.store.write_bundle(**bundle)
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.service.update_client(
                {
                    "client_id": self.client_id,
                    "expected_updated_at": client.updated_at,
                    "comment": "MUST NOT SAVE",
                }
            )
        self.assertEqual(error.exception.code, "client_revision_invalid")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_unrepresentable_offset_is_rejected_without_write(self):
        bundle = self.store.read_bundle()
        client = next(client for client in bundle["clients"] if client.id == self.client_id)
        client.updated_at = "0001-01-01T00:00:00+14:00"
        self.store.write_bundle(**bundle)
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.service.update_client(
                {
                    "client_id": self.client_id,
                    "expected_updated_at": client.updated_at,
                    "comment": "MUST NOT SAVE",
                }
            )
        self.assertEqual(error.exception.code, "client_revision_invalid")
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_unparseable_existing_revision_uses_current_clock(self):
        bundle = self.store.read_bundle()
        client = next(client for client in bundle["clients"] if client.id == self.client_id)
        client.updated_at = "SYNTHETIC INVALID DATE"
        self.store.write_bundle(**bundle)
        now = datetime(2026, 1, 2, tzinfo=UTC)
        with patch("minimal_kanban.services.card_service_clients.utc_now", return_value=now):
            result = self.service.update_client(
                {
                    "client_id": self.client_id,
                    "expected_updated_at": client.updated_at,
                    "comment": "REPAIRED",
                }
            )["client"]
        self.assertEqual(result["updated_at"], now.isoformat())
