from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError


VARIANTS = (
    ("events",),
    ("clients",),
    ("clients", "stickies", "events", "settings"),
    ("clients", "client_search", "client_search_cards"),
)


class ContextSnapshotMemoryTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="snapshot-memory-appdata-")
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
        self.client_id = self.service.create_client(
            {"display_name": "SYNTHETIC_CLIENT", "vehicles": [{"vehicle": "SYNTHETIC_VEHICLE"}]}
        )["client"]["id"]
        self.card_id = self.service.create_card({"title": "SYNTHETIC_BEFORE"})["card"]["id"]
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["events"].append(
            {
                "id": "synthetic-memory-event",
                "timestamp": state["cards"][0]["created_at"],
                "action": "card_updated",
                "card_id": self.card_id,
                "details": {"nested": {"value": "SYNTHETIC_ORIGINAL"}},
            }
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def snapshots(self):
        return [self.store._read_context_bundle_snapshot(domains=domains) for domains in VARIANTS]

    def test_domain_variants_decode_once_and_share_raw_records(self):
        before = self.state_file.read_bytes()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            snapshots = self.snapshots()
            self.assertEqual(read.call_count, 1)
            for domains, snapshot in zip(VARIANTS, snapshots, strict=True):
                self.assertIs(self.store._read_context_bundle_snapshot(domains=domains), snapshot)
            self.assertEqual(read.call_count, 1)
        for snapshot in snapshots[1:]:
            for name, key in (
                ("_cards", self.card_id),
                ("_clients", self.client_id),
                ("_events", "synthetic-memory-event"),
            ):
                self.assertIs(getattr(snapshot, name)[key][0], getattr(snapshots[0], name)[key][0])
        self.assertEqual(self.store._context_snapshot_state, json.loads(before))
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_normalized_cards_and_clients_are_detached_from_shared_raw_records(self):
        events, clients, full, search = self.snapshots()
        raw = deepcopy(self.store._context_snapshot_state)
        events["cards"][0].title = "SYNTHETIC_MUTATED"
        clients["clients"][0].vehicles[0].vehicle = "SYNTHETIC_MUTATED"
        self.assertEqual(full["cards"][0].title, "SYNTHETIC_BEFORE")
        self.assertEqual(search["clients"][0].vehicles[0].vehicle, "SYNTHETIC_VEHICLE")
        self.assertEqual(self.store._context_snapshot_state, raw)

    def test_normalized_event_details_are_detached_from_other_variants_and_raw_records(self):
        events, _, full, _ = self.snapshots()
        raw = deepcopy(self.store._context_snapshot_state)
        first = next(event for event in events["events"] if event.id == "synthetic-memory-event")
        other = next(event for event in full["events"] if event.id == "synthetic-memory-event")
        first.details["nested"]["value"] = "SYNTHETIC_MUTATED"
        self.assertEqual(other.details["nested"]["value"], "SYNTHETIC_ORIGINAL")
        self.assertEqual(self.store._context_snapshot_state, raw)

    def test_nested_order_history_is_detached_from_other_variants_and_raw_records(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        order = state["cards"][0]["repair_order"]
        for name in ("cycles", "payroll_postings"):
            order[name] = [{"synthetic": {"value": "SYNTHETIC_ORIGINAL"}}]
        order["active_correction"] = {"synthetic": {"value": "SYNTHETIC_ORIGINAL"}}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        events, _, full, _ = self.snapshots()
        raw = deepcopy(self.store._context_snapshot_state)
        first, other = events["cards"][0].repair_order, full["cards"][0].repair_order
        for name in ("cycles", "payroll_postings"):
            with self.subTest(field=name):
                getattr(first, name)[0]["synthetic"]["value"] = "SYNTHETIC_MUTATED"
                self.assertEqual(
                    getattr(other, name)[0]["synthetic"]["value"], "SYNTHETIC_ORIGINAL"
                )
        first.active_correction["synthetic"]["value"] = "SYNTHETIC_MUTATED"
        self.assertEqual(other.active_correction["synthetic"]["value"], "SYNTHETIC_ORIGINAL")
        self.assertEqual(self.store._context_snapshot_state, raw)

    def test_service_write_drops_all_shared_raw_and_domain_caches(self):
        previous = self.snapshots()
        old_raw = self.store._context_snapshot_state
        self.service.update_card({"card_id": self.card_id, "title": "SYNTHETIC_AFTER"})
        self.assertIsNone(self.store._context_snapshot_state)
        self.assertEqual(self.store._context_snapshots, {})
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            current = self.snapshots()
            self.assertEqual(read.call_count, 1)
        self.assertIsNot(self.store._context_snapshot_state, old_raw)
        for before, after in zip(previous, current, strict=True):
            self.assertIsNot(before, after)
            self.assertEqual(after["cards"][0].title, "SYNTHETIC_AFTER")
            self.assertEqual(before["cards"][0].title, "SYNTHETIC_BEFORE")

    def test_atomic_replacement_releases_old_generation_before_decoding(self):
        old = self.snapshots()
        read = self.store._read_state
        replacement = self.state_file.with_suffix(".new")
        replacement.write_bytes(
            self.state_file.read_bytes().replace(b"SYNTHETIC_BEFORE", b"SYNTHETIC_AFTER")
        )
        replacement.replace(self.state_file)

        def assert_released(*args, **kwargs):
            self.assertIsNone(self.store._context_snapshot_state)
            self.assertEqual(self.store._context_snapshots, {})
            return read(*args, **kwargs)

        with patch.object(self.store, "_read_state", side_effect=assert_released) as decode:
            current = self.snapshots()
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(len(self.store._context_snapshots), len(VARIANTS))
        for before, after in zip(old, current, strict=True):
            self.assertIsNot(before._cards[self.card_id][0], after._cards[self.card_id][0])
            self.assertEqual(after["cards"][0].title, "SYNTHETIC_AFTER")

    def test_invalid_replacement_does_not_retain_or_serve_previous_generation(self):
        self.snapshots()
        self.state_file.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(StateFileCorruptedError):
            self.snapshots()
        self.assertIsNone(self.store._context_snapshot_state)
        self.assertEqual(self.store._context_snapshots, {})
