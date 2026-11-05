"""Client-profile snapshots preserve detached cards and legacy normalization."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase


class ClientProfileCardSnapshotDetachmentTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="profile-card-snapshot-")
        self.addCleanup(appdata.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": appdata.name})
        environment.start()
        self.addCleanup(environment.stop)
        snapshot_mode = patch(
            "minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", False
        )
        snapshot_mode.start()
        self.addCleanup(snapshot_mode.stop)
        super().setUp()
        self.card_id = self.service.create_card({"title": "synthetic card"})["card"]["id"]

    def write_state(self, state):
        replacement = self.state_file.with_suffix(".replacement")
        replacement.write_text(json.dumps(state), encoding="utf-8")
        replacement.replace(self.state_file)

    def card_state(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        return state, next(card for card in state["cards"] if card["id"] == self.card_id)

    def reference_cards(self, state):
        independent = deepcopy(state)
        columns, _ = self.store._normalize_columns(independent)
        cards, _ = self.store._normalize_cards({"cards": deepcopy(independent["cards"])}, columns)
        return cards

    def assert_reference_parity(self, snapshot, state):
        expected = self.reference_cards(state)
        self.assertEqual(
            [card.to_storage_dict() for card in snapshot["cards"]],
            [card.to_storage_dict() for card in expected],
        )

    def test_profile_card_mutations_do_not_touch_source_or_other_snapshot(self):
        state, raw = self.card_state()
        raw["vehicle_profile"].update(
            customer_phones=["123", "456"],
            source_links_or_refs=["synthetic source"],
            manual_fields=["vin"],
            autofilled_fields=["model_display"],
            tentative_fields=["model_display"],
            field_sources={"vin": "synthetic"},
            warnings=["synthetic warning"],
        )
        raw["tags"] = [{"label": "synthetic tag", "color": "red"}]
        raw["attachments"] = [
            {
                "id": "synthetic-attachment",
                "file_name": "sample.txt",
                "stored_name": "sample.txt",
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_by": "test",
            }
        ]
        raw["seen_by_users"] = {"test": "2026-01-01T00:00:00+00:00"}
        raw["ai_autofill_log"] = [{"message": "synthetic log"}]
        raw["repair_order"].update(
            number="synthetic-order",
            works=[{"id": "work-1", "name": "work", "quantity": "1", "price": "2"}],
            materials=[{"id": "material-1", "name": "material", "quantity": "1", "price": "3"}],
            payments=[{"id": "payment-1", "amount": "1"}],
            tags=[{"label": "order tag"}],
            cycles=[{"nested": {"values": ["original"]}}],
            payroll_postings=[{"nested": {"values": ["original"]}}],
            active_correction={"nested": {"values": ["original"]}},
        )
        self.write_state(state)
        before = self.state_file.read_bytes()
        profile = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        other = self.store._read_context_bundle_snapshot(domains=("events",))
        raw_before = deepcopy(self.store._context_snapshot_state)
        other_before = [card.to_storage_dict() for card in other["cards"]]
        card = next(card for card in profile["cards"] if card.id == self.card_id)
        self.assertTrue(card.attachments and card.tags and card.ai_autofill_log)
        self.assertTrue(card.repair_order.works and card.repair_order.materials)
        self.assertTrue(card.repair_order.payments and card.repair_order.tags)

        vehicle = card.vehicle_profile
        vehicle.customer_phones.append("789")
        vehicle.source_links_or_refs.append("changed")
        vehicle.manual_fields.append("engine_code")
        vehicle.autofilled_fields.append("engine_code")
        vehicle.tentative_fields.append("engine_code")
        vehicle.field_sources["vin"] = "changed"
        vehicle.warnings.append("changed")
        card.tags[0].label = "changed"
        card.attachments[0].file_name = "changed.txt"
        card.seen_by_users["test"] = "changed"
        card.ai_autofill_log[0]["message"] = "changed"
        order = card.repair_order
        order.works[0].name = "changed"
        order.materials[0].name = "changed"
        order.payments[0].amount = "999"
        order.tags[0].label = "changed"
        for history in (order.cycles[0], order.payroll_postings[0], order.active_correction):
            history["nested"]["values"].append("changed")

        self.assertEqual(self.store._context_snapshot_state, raw_before)
        self.assertEqual([card.to_storage_dict() for card in other["cards"]], other_before)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_legacy_order_history_is_detached_and_normalized(self):
        state, raw = self.card_state()
        order = raw["repair_order"]
        order.pop("cycles", None)
        order.pop("payments", None)
        order["repair_order_cycles"] = [{"nested": {"values": ["legacy"]}}]
        order["payment_history"] = [{"id": "legacy-payment", "value": "2"}]
        self.write_state(state)
        snapshot = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        self.assert_reference_parity(snapshot, state)
        card = next(card for card in snapshot["cards"] if card.id == self.card_id)
        self.assertEqual(card.repair_order.cycles[0]["nested"]["values"], ["legacy"])
        self.assertEqual(card.repair_order.payments[0].amount, "2")
        before = deepcopy(self.store._context_snapshot_state)
        card.repair_order.cycles[0]["nested"]["values"].append("changed")
        card.repair_order.payments[0].amount = "999"
        self.assertEqual(self.store._context_snapshot_state, before)

    def test_malformed_history_still_fails_before_archived_retention(self):
        state, template = self.card_state()
        newer = []
        for index in range(301):
            card = deepcopy(template)
            card["id"] = f"synthetic-newer-{index:03d}"
            card["archived"] = True
            card["updated_at"] = "2026-02-01T00:00:00+00:00"
            newer.append(card)
        for field, value in (
            ("cycles", None),
            ("cycles", 7),
            ("payroll_postings", None),
            ("payroll_postings", 7),
        ):
            with self.subTest(field=field, value=value):
                raw = deepcopy(template)
                raw["archived"] = True
                raw["updated_at"] = "2026-01-01T00:00:00+00:00"
                raw["repair_order"][field] = value
                state["cards"] = [*newer, raw]
                self.write_state(state)
                before = self.state_file.read_bytes()
                with self.assertRaises(TypeError) as original:
                    self.reference_cards(state)
                with self.assertRaises(type(original.exception)):
                    self.store._read_context_bundle_snapshot(domains=("client_profile",))
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_legacy_malformed_cycles_fail_and_malformed_correction_is_ignored(self):
        state, raw = self.card_state()
        raw["repair_order"].pop("cycles", None)
        raw["repair_order"]["repair_order_cycles"] = None
        self.write_state(state)
        with self.assertRaises(TypeError) as original:
            self.reference_cards(state)
        with self.assertRaises(type(original.exception)):
            self.store._read_context_bundle_snapshot(domains=("client_profile",))

        raw["repair_order"]["repair_order_cycles"] = [7, {"nested": {"values": ["ok"]}}]
        raw["repair_order"]["active_correction"] = [{"nested": ["ignored"]}]
        self.write_state(state)
        before = self.state_file.read_bytes()
        snapshot = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        self.assert_reference_parity(snapshot, state)
        card = next(card for card in snapshot["cards"] if card.id == self.card_id)
        self.assertEqual(card.repair_order.active_correction, {})
        self.assertEqual(card.repair_order.cycles, [{"nested": {"values": ["ok"]}}])
        card.repair_order.cycles[0]["nested"]["values"].append("changed")
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(self.store._context_snapshot_state, state)

    def test_retention_keeps_active_and_order_cards_and_newest_plain_archives(self):
        state, template = self.card_state()
        active = deepcopy(template)
        active["id"] = "synthetic-active"
        active["archived"] = False
        order = deepcopy(template)
        order["id"] = "synthetic-archived-order"
        order["archived"] = True
        order["repair_order"]["number"] = "archived-order"
        order["repair_order"].pop("cycles", None)
        order["repair_order"]["repair_order_cycles"] = [{"nested": {"values": ["legacy"]}}]
        plain_archives = []
        for index in range(301):
            card = deepcopy(template)
            card["id"] = f"synthetic-archive-{index:03d}"
            card["archived"] = True
            card["updated_at"] = f"2026-01-{index // 24 + 1:02d}T{index % 24:02d}:00:00+00:00"
            plain_archives.append(card)
        state["cards"] = [active, order, *plain_archives]
        self.write_state(state)
        before = self.state_file.read_bytes()
        snapshot = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        self.assert_reference_parity(snapshot, state)
        ids = {card.id for card in snapshot["cards"]}
        self.assertEqual(len(ids), 302)
        self.assertIn("synthetic-active", ids)
        self.assertIn("synthetic-archived-order", ids)
        self.assertNotIn("synthetic-archive-000", ids)
        self.assertIn("synthetic-archive-300", ids)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_profile_card_history_does_not_follow_later_raw_mutation(self):
        state, raw = self.card_state()
        raw["repair_order"].update(
            number="synthetic-order",
            cycles=[{"nested": {"values": ["original"]}}],
            payroll_postings=[{"nested": {"values": ["original"]}}],
            active_correction={"nested": {"values": ["original"]}},
        )
        self.write_state(state)
        before = self.state_file.read_bytes()
        snapshot = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        card = next(card for card in snapshot["cards"] if card.id == self.card_id)
        stored_raw = next(
            row for row in self.store._context_snapshot_state["cards"] if row["id"] == self.card_id
        )["repair_order"]
        for field in ("cycles", "payroll_postings"):
            stored_raw[field][0]["nested"]["values"].append("changed")
        stored_raw["active_correction"]["nested"]["values"].append("changed")
        for history in (
            card.repair_order.cycles[0],
            card.repair_order.payroll_postings[0],
            card.repair_order.active_correction,
        ):
            self.assertEqual(history["nested"]["values"], ["original"])
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_profile_snapshot_ties_match_old_retention_and_position_order(self):
        state, template = self.card_state()
        stamp = "2026-01-01T00:00:00+00:00"
        cards = []
        for card_id, archived in (
            ("synthetic-c", False),
            ("synthetic-a", False),
            ("synthetic-b", False),
            ("synthetic-archive-a", True),
            ("synthetic-archive-b", True),
        ):
            card = deepcopy(template)
            card.update(
                id=card_id, archived=archived, created_at=stamp, updated_at=stamp, position=0
            )
            cards.append(card)
        state["cards"] = cards
        self.write_state(state)
        snapshot = self.store._read_context_bundle_snapshot(domains=("client_profile",))
        self.assert_reference_parity(snapshot, state)
        ids = [card.id for card in snapshot["cards"]]
        self.assertEqual(
            ids,
            [
                "synthetic-c",
                "synthetic-a",
                "synthetic-b",
                "synthetic-archive-b",
                "synthetic-archive-a",
            ],
        )
        positions = {card.id: card.position for card in snapshot["cards"]}
        self.assertEqual(positions, {card_id: index for index, card_id in enumerate(sorted(ids))})
