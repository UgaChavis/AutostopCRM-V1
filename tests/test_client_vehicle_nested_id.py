"""An embedded vehicle ID must not create a duplicate or modify linked cards."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class ClientVehicleNestedIdTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="synthetic-nested-vehicle-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.vehicle_id = "12345678-abcd-0000-0000-000000000001"
        self.client_id = self.service.create_client(
            {
                "display_name": "SYNTHETIC CLIENT",
                "vehicles": [{"id": self.vehicle_id, "vehicle": "ORIGINAL VEHICLE"}],
            }
        )["client"]["id"]
        self.card_id = self.service.create_card(
            {
                "title": "SYNTHETIC CARD",
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
            }
        )["card"]["id"]

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

    def test_embedded_duplicate_is_rejected_without_writing_or_syncing_card(self):
        before = self.files()
        for vehicle in (
            {"id": self.vehicle_id, "vehicle": "MUST NOT REPLACE"},
            {"client_vehicle_id": self.vehicle_id, "vehicle": "MUST NOT REPLACE"},
        ):
            with self.subTest(vehicle=vehicle):
                with self.assertRaises(ServiceError) as failure:
                    self.service.upsert_client_vehicle(
                        {
                            "client_id": self.client_id,
                            "vehicle": vehicle,
                            "sync_linked_cards": True,
                        }
                    )
                self.assertEqual(failure.exception.code, "client_vehicle_id_conflict")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(self.files(), before)

    def test_unique_embedded_id_creates_and_explicit_id_updates_linked_vehicle(self):
        result = self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"id": "unique-id", "vehicle": "NEW"}}
        )
        self.assertTrue(result["meta"]["created"])
        self.assertEqual(result["vehicle"]["id"], "unique-id")
        linked = self.service.link_card_to_client(
            {
                "client_id": self.client_id,
                "card_id": self.card_id,
                "client_vehicle_id": "unique-id",
            }
        )
        self.assertEqual(linked["card"]["client_vehicle_id"], "unique-id")
        linked = self.service.link_card_to_client(
            {
                "client_id": self.client_id,
                "card_id": self.card_id,
                "client_vehicle_id": self.vehicle_id,
            }
        )
        self.assertEqual(linked["card"]["client_vehicle_id"], self.vehicle_id)
        for requested in (self.vehicle_id, "cv-12345678"):
            with self.subTest(requested=requested):
                result = self.service.upsert_client_vehicle(
                    {
                        "client_id": self.client_id,
                        "client_vehicle_id": requested,
                        "vehicle": {"vehicle": "UPDATED " + requested},
                        "sync_linked_cards": True,
                    }
                )
                self.assertFalse(result["meta"]["created"])
                self.assertEqual(result["vehicle"]["id"], self.vehicle_id)
                card = self.service.get_card({"card_id": self.card_id})["card"]
                self.assertEqual(card["client_vehicle_id"], self.vehicle_id)
                self.assertEqual(card["vehicle"], "UPDATED " + requested)

    def test_client_create_and_update_reject_duplicate_vehicle_ids_without_writes(self):
        duplicate = [
            {"id": " shared-id ", "vehicle": "FIRST"},
            {"client_vehicle_id": "shared-id", "vehicle": "SECOND"},
        ]
        before = self.files()
        for operation, payload in (
            (self.service.create_client, {"display_name": "NEW", "vehicles": duplicate}),
            (
                self.service.create_client,
                {"client": {"display_name": "NEW", "vehicles": duplicate}},
            ),
            (self.service.update_client, {"client_id": self.client_id, "vehicles": duplicate}),
            (
                self.service.update_client,
                {"client_id": self.client_id, "patch": {"vehicles": duplicate}},
            ),
        ):
            with self.subTest(operation=operation.__name__, payload=payload):
                with self.assertRaises(ServiceError) as failure:
                    operation(payload)
                self.assertEqual(failure.exception.code, "client_vehicle_id_conflict")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(self.files(), before)

    def test_client_create_and_update_accept_distinct_vehicle_ids(self):
        vehicles = [{"id": "id-one", "vehicle": "FIRST"}, {"id": "id-two", "vehicle": "SECOND"}]
        created = self.service.create_client({"display_name": "NEW", "vehicles": vehicles})
        self.assertTrue(created["meta"]["created"])
        self.assertEqual(
            [vehicle["id"] for vehicle in created["client"]["vehicles"]], ["id-one", "id-two"]
        )
        updated = self.service.update_client(
            {"client_id": created["client"]["id"], "patch": {"vehicles": vehicles}}
        )
        self.assertEqual(
            [vehicle["id"] for vehicle in updated["client"]["vehicles"]], ["id-one", "id-two"]
        )

    def test_new_full_id_cannot_capture_existing_legacy_short_link(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_vehicle_id"] = "CV-12345678"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        bundle = self.store.read_bundle()
        client = next(item for item in bundle["clients"] if item.id == self.client_id)
        self.assertEqual(
            self.service._find_client_vehicle(client, "CV-12345678").id, self.vehicle_id
        )
        before = self.files()
        for new_id in ("CV-12345678", "cv-12345678"):
            for operation, payload in (
                (
                    self.service.upsert_client_vehicle,
                    {"client_id": self.client_id, "vehicle": {"id": new_id, "vehicle": "OTHER"}},
                ),
                (
                    self.service.update_client,
                    {
                        "client_id": self.client_id,
                        "vehicles": [
                            {"id": self.vehicle_id, "vehicle": "ORIGINAL VEHICLE"},
                            {"id": new_id, "vehicle": "OTHER"},
                        ],
                    },
                ),
                (
                    self.service.create_client,
                    {
                        "display_name": "OTHER CLIENT",
                        "vehicles": [
                            {"id": self.vehicle_id, "vehicle": "ORIGINAL VEHICLE"},
                            {"id": new_id, "vehicle": "OTHER"},
                        ],
                    },
                ),
            ):
                with self.subTest(new_id=new_id, operation=operation.__name__):
                    with self.assertRaises(ServiceError) as failure:
                        operation(payload)
                    self.assertEqual(failure.exception.code, "client_vehicle_id_conflict")
                    self.assertEqual(failure.exception.status_code, 409)
                    self.assertEqual(self.files(), before)
        after = self.store.read_bundle()
        client = next(item for item in after["clients"] if item.id == self.client_id)
        card = next(item for item in after["cards"] if item.id == self.card_id)
        self.assertEqual(
            self.service._find_client_vehicle(client, card.client_vehicle_id).id, self.vehicle_id
        )
        self.assertEqual(card.vehicle, "ORIGINAL VEHICLE")

    def test_new_long_id_cannot_capture_existing_full_id_as_its_short_alias(self):
        for existing_id in ("CV-12345678", "cv-12345678"):
            with self.subTest(existing_id=existing_id):
                client = self.service.create_client(
                    {
                        "display_name": "REVERSE " + existing_id,
                        "vehicles": [{"id": existing_id, "vehicle": "ORIGINAL"}],
                    }
                )["client"]
                before = self.files()
                for operation, payload in (
                    (
                        self.service.upsert_client_vehicle,
                        {
                            "client_id": client["id"],
                            "vehicle": {"id": self.vehicle_id, "vehicle": "OTHER"},
                        },
                    ),
                    (
                        self.service.update_client,
                        {
                            "client_id": client["id"],
                            "vehicles": [
                                {"id": existing_id, "vehicle": "ORIGINAL"},
                                {"id": self.vehicle_id, "vehicle": "OTHER"},
                            ],
                        },
                    ),
                ):
                    with self.assertRaises(ServiceError) as failure:
                        operation(payload)
                    self.assertEqual(failure.exception.code, "client_vehicle_id_conflict")
                    self.assertEqual(self.files(), before)

    def test_replacement_cannot_capture_removed_vehicles_legacy_short_link(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_vehicle_id"] = "CV-12345678"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        bundle = self.store.read_bundle()
        client = next(item for item in bundle["clients"] if item.id == self.client_id)
        self.assertEqual(
            self.service._find_client_vehicle(client, "CV-12345678").id, self.vehicle_id
        )
        before = self.files()
        for new_id in ("CV-12345678", "cv-12345678"):
            with self.subTest(new_id=new_id), self.assertRaises(ServiceError) as failure:
                self.service.update_client(
                    {
                        "client_id": self.client_id,
                        "vehicles": [{"id": new_id, "vehicle": "OTHER"}],
                    }
                )
            self.assertEqual(failure.exception.code, "client_vehicle_id_conflict")
            self.assertEqual(self.files(), before)
        bundle = self.store.read_bundle()
        client = next(item for item in bundle["clients"] if item.id == self.client_id)
        card = next(item for item in bundle["cards"] if item.id == self.card_id)
        self.assertEqual(
            self.service._find_client_vehicle(client, card.client_vehicle_id).id, self.vehicle_id
        )
        self.assertEqual(card.vehicle, "ORIGINAL VEHICLE")

    def test_replacing_an_unlinked_vehicle_with_unrelated_id_is_allowed(self):
        self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"id": "unlinked-old", "vehicle": "OLD"}}
        )
        result = self.service.update_client(
            {
                "client_id": self.client_id,
                "vehicles": [
                    {"id": self.vehicle_id, "vehicle": "ORIGINAL VEHICLE"},
                    {"id": "unlinked-new", "vehicle": "NEW"},
                ],
            }
        )
        self.assertEqual(
            {vehicle["id"] for vehicle in result["client"]["vehicles"]},
            {self.vehicle_id, "unlinked-new"},
        )
        card = self.service.get_card({"card_id": self.card_id})["card"]
        self.assertEqual(card["client_vehicle_id"], self.vehicle_id)

    def test_unchanged_legacy_alias_pair_can_be_read_and_updated(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        client = next(item for item in state["clients"] if item["id"] == self.client_id)
        client["vehicles"].append({"id": "cv-12345678", "vehicle": "LEGACY OTHER"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.store.read_bundle()
        before = self.files()
        profile = self.service.get_client({"client_id": self.client_id})
        vehicles = profile["client"]["vehicles"]
        self.assertEqual({vehicle["id"] for vehicle in vehicles}, {self.vehicle_id, "cv-12345678"})
        self.assertEqual(self.files(), before)
        updated = self.service.update_client(
            {"client_id": self.client_id, "comment": "UNCHANGED IDS"}
        )
        self.assertEqual(
            {vehicle["id"] for vehicle in updated["client"]["vehicles"]},
            {self.vehicle_id, "cv-12345678"},
        )
        updated = self.service.update_client({"client_id": self.client_id, "vehicles": vehicles})
        self.assertEqual(
            {vehicle["id"] for vehicle in updated["client"]["vehicles"]},
            {self.vehicle_id, "cv-12345678"},
        )

    def test_short_reference_prevents_delete_and_empty_replacement(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_vehicle_id"] = "CV-12345678"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.store.read_bundle()
        before = self.files()
        for operation, payload in (
            (
                self.service.delete_client_vehicle,
                {
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                    "unlink_cards": True,
                },
            ),
            (self.service.update_client, {"client_id": self.client_id, "vehicles": []}),
        ):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaises(ServiceError) as failure,
            ):
                operation(payload)
            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self.files(), before)

    def test_dangling_short_reference_cannot_acquire_new_vehicle(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_vehicle_id"] = "cv-12345678"
        client = next(item for item in state["clients"] if item["id"] == self.client_id)
        client["vehicles"] = []
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.store.read_bundle()
        before = self.files()
        new_vehicle = {"id": "12345678-efab-0000-0000-000000000002", "vehicle": "OTHER"}
        for operation, payload in (
            (
                self.service.upsert_client_vehicle,
                {"client_id": self.client_id, "vehicle": new_vehicle},
            ),
            (self.service.update_client, {"client_id": self.client_id, "vehicles": [new_vehicle]}),
        ):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaises(ServiceError) as failure,
            ):
                operation(payload)
            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self.files(), before)

    def test_replacement_with_same_short_id_cannot_rebind_link(self):
        other_id = "12345678-efab-0000-0000-000000000002"
        for old_id, new_id in ((self.vehicle_id, other_id), (other_id, self.vehicle_id)):
            with self.subTest(old_id=old_id):
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                card = next(item for item in state["cards"] if item["id"] == self.card_id)
                card["client_vehicle_id"] = "CV-12345678"
                client = next(item for item in state["clients"] if item["id"] == self.client_id)
                client["vehicles"] = [{"id": old_id, "vehicle": "ORIGINAL"}]
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                self.store.read_bundle()
                before = self.files()
                with self.assertRaises(ServiceError) as failure:
                    self.service.update_client(
                        {
                            "client_id": self.client_id,
                            "vehicles": [{"id": new_id, "vehicle": "OTHER"}],
                        }
                    )
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(self.files(), before)

    def test_ambiguous_legacy_reference_allows_other_fields_but_blocks_id_changes(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["client_vehicle_id"] = "CV-12345678"
        client = next(item for item in state["clients"] if item["id"] == self.client_id)
        client["vehicles"].append(
            {"id": "12345678-efab-0000-0000-000000000002", "vehicle": "OTHER"}
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.store.read_bundle()
        self.service.update_client({"client_id": self.client_id, "comment": "UNCHANGED VEHICLES"})
        before = self.files()
        with self.assertRaises(ServiceError) as failure:
            self.service.upsert_client_vehicle(
                {"client_id": self.client_id, "vehicle": {"id": "unrelated", "vehicle": "NEW"}}
            )
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.files(), before)

    def test_exact_linked_vehicle_can_still_be_unlinked_and_deleted(self):
        result = self.service.delete_client_vehicle(
            {
                "client_id": self.client_id,
                "client_vehicle_id": self.vehicle_id,
                "unlink_cards": True,
            }
        )
        self.assertTrue(result["meta"]["deleted"])
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["client_vehicle_id"], ""
        )

    def test_upsert_cannot_dedupe_away_another_linked_vehicle(self):
        self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"id": "vehicle-two", "vehicle": "SECOND"}}
        )
        self.service.link_card_to_client(
            {
                "client_id": self.client_id,
                "card_id": self.card_id,
                "client_vehicle_id": "vehicle-two",
            }
        )
        before = self.files()
        with self.assertRaises(ServiceError) as failure:
            self.service.upsert_client_vehicle(
                {
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                    "vehicle": {"vehicle": "SECOND"},
                }
            )
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.files(), before)

    def test_upsert_cannot_report_created_when_new_vehicle_is_deduplicated(self):
        before = self.files()
        for card_id in (None, self.card_id):
            payload = {
                "client_id": self.client_id,
                "vehicle": {"id": "new-duplicate", "vehicle": "ORIGINAL VEHICLE"},
            }
            if card_id:
                payload["card_id"] = card_id
            with self.subTest(card_id=card_id), self.assertRaises(ServiceError) as failure:
                self.service.upsert_client_vehicle(payload)
            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self.files(), before)

    def test_upsert_cannot_report_success_when_updated_vehicle_is_deduplicated(self):
        self.service.upsert_client_vehicle(
            {"client_id": self.client_id, "vehicle": {"id": "vehicle-two", "vehicle": "SECOND"}}
        )
        before = self.files()
        with self.assertRaises(ServiceError) as failure:
            self.service.upsert_client_vehicle(
                {
                    "client_id": self.client_id,
                    "client_vehicle_id": "vehicle-two",
                    "vehicle": {"vehicle": "ORIGINAL VEHICLE"},
                }
            )
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.files(), before)
