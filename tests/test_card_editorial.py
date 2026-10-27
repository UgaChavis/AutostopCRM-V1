from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import ClientVehicle
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class CardEditorialTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.client = self.service.create_client(
            {
                "display_name": "Synthetic editorial client",
                "vehicles": [{"vehicle": "Synthetic car", "engine_model": "Initial"}],
            }
        )["client"]
        self.vehicle_id = self.client["vehicles"][0]["id"]
        self.card = self.service.create_card(
            {
                "title": "Synthetic repair",
                "vehicle": "Synthetic car",
                "description": "Old text",
                "client_id": self.client["id"],
                "client_vehicle_id": self.vehicle_id,
            }
        )["card"]

    def request(self, **fields):
        return {
            "card_id": self.card["id"],
            "content_only": True,
            "mode": "apply",
            "actor_name": "DIRECTOR",
            "source": "mcp",
            "response_mode": "full",
            "expected_updated_at": self.card["updated_at"],
            "idempotency_key": "editorial-1",
            "description": "New concise text",
            **fields,
        }

    def stored_card(self):
        return next(
            card for card in self.store.read_bundle()["cards"] if card.id == self.card["id"]
        )

    def test_closed_archived_editorial_preserves_every_order_field_except_named_rows(self):
        bundle = deepcopy(self.store.read_bundle())
        card = next(card for card in bundle["cards"] if card.id == self.card["id"])
        card.archived = True
        card.repair_order = RepairOrder.from_dict(
            {
                "number": "123",
                "status": "closed",
                "closed_at": "27.10.2026 12:00",
                "works": [
                    {
                        "id": "work-1",
                        "name": "диагностика",
                        "quantity": "2",
                        "price": "1500",
                        "total": "3000",
                        "salary_amount": "300",
                        "salary_accrued_at": "27.10.2026 12:00",
                        "work_price_snapshot": "1500",
                        "executor_id": "synthetic-mechanic",
                    }
                ],
                "materials": [
                    {
                        "id": "material-1",
                        "name": "фильтр",
                        "quantity": "1",
                        "cost_price": "200",
                        "price": "500",
                        "total": "500",
                        "inventory_item_id": "item-1",
                        "inventory_movement_id": "movement-1",
                        "catalog_number": "UNCHANGED",
                    }
                ],
                "payments": [{"id": "payment-1", "amount": "3500", "cashbox_id": "cashbox-1"}],
            }
        )
        self.store.write_bundle(**bundle)
        before = deepcopy(self.store.read_bundle())
        order_before = self.stored_card().repair_order.to_storage_dict()
        payload = self.request(
            repair_order_names={
                "works": [{"row_id": "work-1", "name": "Диагностика"}],
                "materials": [{"row_id": "material-1", "name": "Фильтр (предположительно ABC-1)"}],
            },
            vehicle_profile={
                "engine_model": "Candidate",
                "tentative_fields": ["engine_model"],
                "field_sources": {"engine_model": "Synthetic catalog evidence"},
            },
        )
        with ExitStack() as stack:
            for name in (
                "update_card",
                "_update_repair_order",
                "_apply_ready_column_side_effects",
                "_sync_linked_client_vehicle_from_card",
                "_sync_client_vehicle_linked_cards",
            ):
                stack.enter_context(
                    patch.object(self.service, name, side_effect=AssertionError(name))
                )
            dry_run = self.service.cleanup_card({**payload, "mode": "dry_run"})
            self.assertEqual(dry_run["changed"], 0)
            self.assertEqual(before, self.store.read_bundle())
            self.assertEqual(
                dry_run["items"][0]["card"]["vehicle_profile"]["tentative_fields"], ["engine_model"]
            )
            applied = self.service.cleanup_card(payload)
        after = self.store.read_bundle()
        order_after = self.stored_card().repair_order.to_storage_dict()
        for kind in ("works", "materials"):
            self.assertNotEqual(order_before[kind][0]["name"], order_after[kind][0]["name"])
            order_after[kind][0]["name"] = order_before[kind][0]["name"]
        self.assertEqual(order_before, order_after)
        self.assertTrue(self.stored_card().archived)
        self.assertEqual(self.stored_card().column, card.column)
        for domain in (
            "clients",
            "cashboxes",
            "cash_transactions",
            "inventory_items",
            "inventory_movements",
        ):
            self.assertEqual(before[domain], after[domain], domain)
        self.assertEqual(applied["changed"], 1)
        events = [
            event for event in after["events"] if event.id not in {e.id for e in before["events"]}
        ]
        self.assertTrue(
            any(
                event.action == "repair_order_updated" and event.details.get("editorial_only")
                for event in events
            )
        )

    def test_exact_retry_survives_restart_and_never_reapplies_over_newer_edit(self):
        request = self.request()
        first = self.service.cleanup_card(request)
        second = self.request(
            idempotency_key="editorial-2",
            description="Later text",
            expected_updated_at=first["verification"]["updated_at"],
        )
        self.service.cleanup_card(second)
        before = self.state_file.read_bytes()
        reloaded = CardService(JsonStore(self.state_file, self.logger), self.logger)
        replay = reloaded.cleanup_card(request)
        self.assertTrue(replay["meta"]["idempotent_replay"])
        self.assertEqual(replay["changed"], 0)
        self.assertEqual(replay["items"][0]["card"]["description"], "Later text")
        self.assertEqual(
            replay["verification"]["applied_updated_at"], first["verification"]["updated_at"]
        )
        self.assertEqual(before, self.state_file.read_bytes())
        with self.assertRaises(ServiceError) as conflict:
            reloaded.cleanup_card({**request, "description": "Different intent"})
        self.assertEqual(conflict.exception.code, "editorial_idempotency_conflict")

    def test_concurrent_storage_write_rejects_uncommitted_patch_and_receipt(self):
        other = self.service.create_card({"title": "Concurrent repair"})["card"]
        save = self.service._save_bundle

        def concurrent_save(*args, **kwargs):
            concurrent = CardService(JsonStore(self.state_file, self.logger), self.logger)
            concurrent.update_card({"card_id": other["id"], "description": "Parallel change"})
            return save(*args, **kwargs)

        with patch.object(self.service, "_save_bundle", side_effect=concurrent_save):
            with self.assertRaises(ServiceError) as conflict:
                self.service.cleanup_card(self.request())
        self.assertEqual(conflict.exception.code, "state_write_conflict")
        self.assertEqual(self.stored_card().description, "Old text")
        retry = self.service.cleanup_card(self.request())
        self.assertFalse(retry["meta"]["idempotent_replay"])
        self.assertEqual(retry["changed"], 1)

    def test_forbidden_or_stale_patch_is_atomic_even_after_valid_description(self):
        before = deepcopy(self.store.read_bundle())
        for bad in (
            {"expected_updated_at": "stale"},
            {"expected_updated_at": ""},
            {"idempotency_key": ""},
            {"deadline": {"hours": 2}},
            {"repair_order": {"works": []}},
            {"vehicle_profile": {"customer_name": "Not editorial vehicle data"}},
            {"repair_order_names": {"works": [{"row_id": "missing", "name": "Correction"}]}},
            {
                "repair_order_names": {
                    "materials": [{"row_id": "missing", "name": "Text", "price": "1"}]
                }
            },
        ):
            with self.subTest(bad=bad), self.assertRaises(ServiceError):
                self.service.cleanup_card(self.request(**bad))
            self.assertEqual(before, self.store.read_bundle())

    def test_exact_client_vehicle_patch_has_separate_revision_and_no_fanout(self):
        other = self.service.create_card(
            {
                "title": "Other repair",
                "client_id": self.client["id"],
                "client_vehicle_id": self.vehicle_id,
            }
        )["card"]
        current_client = self.service.get_client({"client_id": self.client["id"]})["client"]
        linked = {
            "client_id": self.client["id"],
            "client_vehicle_id": self.vehicle_id,
            "expected_updated_at": current_client["updated_at"],
            "vehicle": {
                "engine_model": "Candidate",
                "tentative_fields": ["engine_model"],
                "field_sources": {"engine_model": "Synthetic evidence"},
                "source_confidence": 0.7,
                "source_links_or_refs": ["https://example.org/catalog"],
            },
        }
        for bad in ({"client_vehicle_id": "other"}, {"expected_updated_at": "stale"}):
            before = self.state_file.read_bytes()
            with self.assertRaises(ServiceError):
                self.service.cleanup_card(self.request(client_vehicle_patch={**linked, **bad}))
            self.assertEqual(before, self.state_file.read_bytes())
        other_before = self.service.get_card({"card_id": other["id"]})["card"]
        profile_before = deepcopy(self.stored_card().vehicle_profile)
        applied = self.service.cleanup_card(self.request(client_vehicle_patch=linked))
        vehicle = applied["items"][0]["client_vehicle"]["vehicle"]
        self.assertEqual(vehicle["tentative_fields"], ["engine_model"])
        self.assertIn("engine_model", vehicle["autofilled_fields"])
        self.assertEqual(ClientVehicle.from_value(vehicle).to_dict(), vehicle)
        self.assertEqual(profile_before, self.stored_card().vehicle_profile)
        self.assertEqual(other_before, self.service.get_card({"card_id": other["id"]})["card"])
        event = next(
            e
            for e in reversed(self.store.read_bundle()["events"])
            if e.action == "client_vehicle_updated"
        )
        self.assertEqual(event.details["before"]["engine_model"], "Initial")
        self.assertEqual(event.details["after"]["engine_model"], "Candidate")

    def test_vehicle_provenance_survives_legacy_edit_and_explicit_card_sync(self):
        seeded = self.service.upsert_client_vehicle(
            {
                "client_id": self.client["id"],
                "client_vehicle_id": self.vehicle_id,
                "vehicle": {
                    "vehicle": "Synthetic car",
                    "engine_model": "Candidate",
                    "tentative_fields": ["engine_model"],
                    "field_sources": {"engine_model": "Synthetic evidence"},
                },
            }
        )["vehicle"]
        edited = self.service.upsert_client_vehicle(
            {
                "client_id": self.client["id"],
                "client_vehicle_id": self.vehicle_id,
                "vehicle": {"vehicle": "Updated display"},
            }
        )["vehicle"]
        self.assertEqual(edited["tentative_fields"], seeded["tentative_fields"])
        self.assertEqual(self.stored_card().vehicle_profile.tentative_fields, ["engine_model"])
        self.service.upsert_client_vehicle(
            {
                "client_id": self.client["id"],
                "client_vehicle_id": self.vehicle_id,
                "vehicle": {"vehicle": "Updated display", "engine_model": "Confirmed"},
            }
        )
        self.assertEqual(self.stored_card().vehicle_profile.tentative_fields, [])
        self.assertIn("engine_model", self.stored_card().vehicle_profile.manual_fields)
