from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from copy import copy, deepcopy
from dataclasses import fields, is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import Card, ClientProfile
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.change_feed_projection import project_crm_state
from minimal_kanban.storage.change_feed_store import ChangeFeedStore, compact_change_event
from minimal_kanban.storage.json_store import JsonStore


def mutable_paths(value, path="root"):
    result = {}
    if isinstance(value, (dict, list)):
        result[id(value)] = path
        items = value.items() if isinstance(value, dict) else enumerate(value)
    elif is_dataclass(value):
        items = ((field.name, getattr(value, field.name)) for field in fields(value))
    else:
        return result
    for key, child in items:
        result.update(mutable_paths(child, f"{path}.{key}"))
    return result


class SerializationSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_file = Path(temporary.name) / "state.json"
        logger = logging.getLogger(self.id())
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)
        self.service = CardService(self.store, logger)

    def make_card(self):
        card = Card.from_dict(
            {
                "id": "synthetic-card",
                "title": "Synthetic card",
                "position": 0,
                "tags": ["Synthetic tag"],
                "seen_by_users": {"synthetic-user": "2026-09-08T00:00:00+00:00"},
                "ai_autofill_log": [{"level": "INFO", "message": "before"}],
                "attachments": [
                    {
                        "id": "synthetic-attachment",
                        "file_name": "synthetic.txt",
                        "stored_name": "synthetic.txt",
                        "mime_type": "text/plain",
                        "size_bytes": 1,
                    }
                ],
                "vehicle_profile": {
                    "customer_phones": ["123456789"],
                    "source_links_or_refs": ["synthetic-reference"],
                    "manual_fields": ["engine_code"],
                    "autofilled_fields": ["engine_model"],
                    "tentative_fields": ["gearbox_model"],
                    "field_sources": {"engine_code": "manual"},
                    "warnings": ["Synthetic warning"],
                },
                "repair_order": {
                    "number": "SYNTHETIC-1",
                    "comment": "before",
                    "closed_at": "2026-09-08T00:00:00+00:00",
                    "tags": ["Synthetic tag"],
                    "works": [
                        {"id": "work-1", "name": "Synthetic work", "quantity": "1", "price": "100"}
                    ],
                    "payments": [{"id": "payment-1", "amount": "10"}],
                    # Imported history may contain nested metadata, although
                    # current native payroll/correction writers use scalars.
                    "payroll_postings": [{"id": "posting-1", "details": {"amounts": [1]}}],
                    "active_correction": {"details": {"reasons": ["before"]}},
                },
            }
        )
        # Exercise the actual native cycle snapshot shape, not an invented
        # flat approximation of the historical repair-order payload.
        card.repair_order.cycles = [
            self.service._legacy_repair_order_cycle(
                card_id=card.id,
                order=card.repair_order,
                actor_name="Synthetic actor",
                source="test",
            )
        ]
        return card

    def seed_cards(self):
        bundle = self.store.read_bundle()
        cards = [
            self.make_card(),
            Card.from_dict({"id": "synthetic-neighbour", "title": "Neighbour", "position": 1}),
        ]
        self.store.write_cached_bundle(bundle, **{**bundle, "cards": cards})
        return self.store.read_bundle()

    def commit_cards(self, mode, bundle, cards):
        replacement = {**bundle, "cards": cards}
        if mode == "cached":
            self.store.write_cached_bundle(bundle, **replacement)
        elif mode == "normalized":
            self.store.write_bundle(**replacement)
        else:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            state["cards"] = [card.to_storage_dict() for card in cards]
            self.state_file.write_text(json.dumps(state), encoding="utf-8")
            self.store.reconcile_change_feed()

    def history_changes_after(self, count):
        return [
            (event["entity_type"], event["entity_id"], event["change_type"], event["tombstone"])
            for event in self.store.change_feed_store.raw_events_for_test()[count:]
            if event["entity_type"] in {"repair_order_cycle", "repair_order_payroll_posting"}
        ]

    def history_keys(self, card):
        return (
            ("repair_order_cycle", f"{card.id}:cycle:{card.repair_order.cycles[0]['id']}"),
            ("repair_order_payroll_posting", f"{card.id}:payroll:posting-1"),
        )

    def assert_reconcile_adds_no_events(self):
        before = self.store.change_feed_store.raw_events_for_test()
        restarted = JsonStore(self.state_file)
        restarted.reconcile_change_feed()
        self.assertEqual(restarted.change_feed_store.raw_events_for_test(), before)

    def test_public_and_storage_snapshots_share_no_mutable_model_branches(self):
        card = self.make_card()
        # The serializer must remain a snapshot even if a runtime caller adds
        # nested log metadata before the next normalization boundary.
        card.ai_autofill_log[0]["details"] = {"messages": ["before"]}
        model_paths = mutable_paths(card)
        for model in (card, card.repair_order, card.vehicle_profile):
            for method in ("to_dict", "to_storage_dict"):
                with self.subTest(model=type(model).__name__, method=method):
                    first = getattr(model, method)()
                    second = getattr(model, method)()
                    first_paths = mutable_paths(first)
                    self.assertEqual(
                        {model_paths[key] for key in model_paths.keys() & first_paths.keys()},
                        set(),
                    )
                    self.assertFalse(first_paths.keys() & mutable_paths(second).keys())

    def test_nested_snapshots_are_independent_in_both_mutation_directions(self):
        card = self.make_card()
        card.ai_autofill_log[0]["details"] = {"messages": ["before"]}
        storage = card.to_storage_dict()
        public = card.to_dict()
        expected_public = deepcopy(public)
        storage["ai_autofill_log"][0]["details"]["messages"].append("snapshot only")
        order = storage["repair_order"]
        order["cycles"][0]["snapshot"]["works"][0]["name"] = "Snapshot work"
        order["payroll_postings"][0]["details"]["amounts"].append(2)
        order["active_correction"]["details"]["reasons"].append("snapshot only")
        self.assertEqual(card.ai_autofill_log[0]["details"]["messages"], ["before"])
        self.assertEqual(
            card.repair_order.cycles[0]["snapshot"]["works"][0]["name"], "Synthetic work"
        )
        self.assertEqual(card.repair_order.payroll_postings[0]["details"]["amounts"], [1])
        self.assertEqual(card.repair_order.active_correction["details"]["reasons"], ["before"])
        expected_storage = deepcopy(storage)
        card.ai_autofill_log[0]["details"]["messages"].append("model only")
        card.repair_order.cycles[0]["snapshot"]["works"][0]["name"] = "Model work"
        card.repair_order.payroll_postings[0]["details"]["amounts"].append(3)
        card.repair_order.active_correction["details"]["reasons"].append("model only")
        self.assertEqual(storage, expected_storage)
        self.assertEqual(public, expected_public)

    def test_shallow_routing_copy_with_changed_log_is_normalized_before_write(self):
        bundle = self.seed_cards()
        original = bundle["cards"][0]
        previous_payload = self.store._storage_dict_cache["cards"][id(original)][2]
        changed = copy(original)
        changed.position = 1
        changed.ai_autofill_log[0]["message"] = "x" * 500
        neighbour = copy(bundle["cards"][1])
        neighbour.position = 0
        self.assertEqual(previous_payload["ai_autofill_log"][0]["message"], "before")
        self.assertIsNone(self.store._routing_only_card_payload(changed))
        self.store.write_cached_bundle(bundle, **{**bundle, "cards": [changed, neighbour]})
        for store in (self.store, JsonStore(self.state_file)):
            with self.subTest(restarted=store is not self.store):
                persisted = next(
                    card for card in store.read_bundle()["cards"] if card.id == changed.id
                )
                self.assertEqual(persisted.ai_autofill_log[0]["message"], "x" * 240)

    def test_changed_native_cycle_snapshot_invalidates_projection_cache_after_write(self):
        bundle = self.seed_cards()
        original = bundle["cards"][0]
        cycle_id = original.repair_order.cycles[0]["id"]
        cycle_key = ("repair_order_cycle", f"{original.id}:cycle:{cycle_id}")
        previous_payload, previous_entities = self.store._card_projection_cache[original.id]
        previous_digest = previous_entities[cycle_key].digest
        changed = copy(original)
        changed.position = 1
        changed.repair_order.cycles[0]["snapshot"]["comment"] = "Changed historical snapshot"
        neighbour = copy(bundle["cards"][1])
        neighbour.position = 0
        self.assertEqual(
            previous_payload["repair_order"]["cycles"][0]["snapshot"]["comment"], "before"
        )
        self.assertIsNone(self.store._routing_only_card_payload(changed))
        self.store.write_cached_bundle(bundle, **{**bundle, "cards": [changed, neighbour]})
        current = self.store._state_from_bundle(self.store.read_bundle())
        expected = project_crm_state(current)
        self.assertNotEqual(expected[cycle_key].digest, previous_digest)
        self.assertEqual(
            self.store._card_projection_cache[changed.id][1][cycle_key], expected[cycle_key]
        )
        self.assertEqual(
            project_crm_state(current, card_cache=self.store._card_projection_cache), expected
        )
        self.assertTrue(
            any(
                event["entity_type"] == cycle_key[0]
                and event["entity_id"] == cycle_key[1]
                and event["change_type"] == "update"
                for event in self.store.change_feed_store.raw_events_for_test()
            )
        )
        restarted = JsonStore(self.state_file)
        self.assertEqual(
            project_crm_state(restarted._state_from_bundle(restarted.read_bundle())), expected
        )

    def test_history_noop_and_child_deletion_match_all_write_paths(self):
        for mode in ("cached", "normalized", "external"):
            with self.subTest(mode=mode):
                bundle = self.seed_cards()
                cards = deepcopy(bundle["cards"])
                target = next(card for card in cards if card.id == "synthetic-card")
                keys = self.history_keys(target)
                target.title = "Only parent content changed"
                target.updated_at = "2026-09-09T00:00:01+00:00"
                before = len(self.store.change_feed_store.raw_events_for_test())
                self.commit_cards(mode, bundle, cards)
                self.assertEqual(self.history_changes_after(before), [])

                bundle = self.store.read_bundle()
                cards = deepcopy(bundle["cards"])
                target = next(card for card in cards if card.id == "synthetic-card")
                target.repair_order.cycles = []
                target.repair_order.payroll_postings = []
                target.updated_at = "2026-09-09T00:00:02+00:00"
                before = len(self.store.change_feed_store.raw_events_for_test())
                self.commit_cards(mode, bundle, cards)
                self.assertCountEqual(
                    self.history_changes_after(before),
                    [(*key, "delete", True) for key in keys],
                )
                self.assert_reconcile_adds_no_events()

    def test_cycle_update_and_parent_deletion_match_all_write_paths(self):
        for mode in ("cached", "normalized", "external"):
            with self.subTest(mode=mode):
                bundle = self.seed_cards()
                cards = deepcopy(bundle["cards"])
                target = next(card for card in cards if card.id == "synthetic-card")
                keys = self.history_keys(target)
                target.repair_order.cycles[0]["snapshot"]["comment"] = "Changed cycle"
                target.updated_at = "2026-09-09T00:00:01+00:00"
                before = len(self.store.change_feed_store.raw_events_for_test())
                self.commit_cards(mode, bundle, cards)
                self.assertEqual(self.history_changes_after(before), [(*keys[0], "update", False)])

                bundle = self.store.read_bundle()
                cards = [card for card in bundle["cards"] if card.id != target.id]
                before = len(self.store.change_feed_store.raw_events_for_test())
                self.commit_cards(mode, bundle, cards)
                self.assertCountEqual(
                    self.history_changes_after(before),
                    [(*key, "delete", True) for key in keys],
                )
                self.assert_reconcile_adds_no_events()

    def test_native_nested_history_retains_values_and_types_through_storage_boundaries(self):
        for mode in ("cached", "normalized", "external"):
            with self.subTest(mode=mode):
                bundle = self.seed_cards()
                cards = deepcopy(bundle["cards"])
                target = next(card for card in cards if card.id == "synthetic-card")
                target.title = f"Round trip through {mode}"
                target.updated_at = "2026-09-09T00:00:01+00:00"
                expected = target.repair_order.to_storage_dict()
                self.commit_cards(mode, bundle, cards)
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                persisted = next(card for card in state["cards"] if card["id"] == target.id)
                history = persisted["repair_order"]
                self.assertEqual(history["cycles"], expected["cycles"])
                self.assertIsInstance(history["cycles"][0]["snapshot"]["works"][0], dict)
                self.assertEqual(history["payroll_postings"], expected["payroll_postings"])
                self.assertIs(type(history["payroll_postings"][0]["details"]["amounts"][0]), int)
                restarted = JsonStore(self.state_file)
                reloaded = next(
                    card for card in restarted.read_bundle()["cards"] if card.id == target.id
                )
                self.assertEqual(
                    reloaded.repair_order.to_storage_dict()["cycles"], expected["cycles"]
                )
                self.assert_reconcile_adds_no_events()

    def test_native_history_audit_actions_already_select_their_parent_card(self):
        for action in ("repair_order_cycle_created", "repair_order_payroll_posting_updated"):
            with self.subTest(action=action):
                event = compact_change_event(
                    {"id": "synthetic-audit", "action": action, "card_id": "synthetic-card"}
                )
                covered = {(event["entity_type"], event["entity_id"]): {event["change_type"]}}
                self.assertEqual(
                    ChangeFeedStore._audit_source_keys(covered), {("card", "synthetic-card")}
                )

    def test_source_prefixes_are_literal_case_sensitive_and_preserve_foreign_children(self):
        for mode in ("cached", "normalized"):
            with self.subTest(mode=mode):
                bundle = self.store.read_bundle()
                cards = []
                clients = []
                parent_ids = ("scope_a", "scopeXa", "SCOPE_A")
                for index, parent_id in enumerate(parent_ids):
                    card = self.make_card()
                    card.id = parent_id
                    card.position = index
                    cards.append(card)
                    clients.append(
                        ClientProfile.from_dict(
                            {
                                "id": parent_id,
                                "display_name": "Synthetic client",
                                "vehicles": [
                                    {"id": "shared-child", "vehicle": "Synthetic vehicle"}
                                ],
                            }
                        )
                    )
                self.store.write_cached_bundle(
                    bundle, **{**bundle, "cards": cards, "clients": clients}
                )
                feed = self.store.change_feed_store
                with feed._connection() as connection:
                    before_entities = feed._entity_state(connection)
                    for source_type in ("card", "client"):
                        selected = feed._entity_state_for_sources(
                            connection, {(source_type, parent_ids[0])}
                        )
                        self.assertTrue(selected)
                        self.assertTrue(
                            all(
                                key[1] == "scope_a" or key[1].startswith("scope_a:")
                                for key in selected
                            )
                        )

                bundle = self.store.read_bundle()
                cards = deepcopy(bundle["cards"])
                clients = deepcopy(bundle["clients"])
                target = next(card for card in cards if card.id == parent_ids[0])
                target.repair_order.cycles[0]["snapshot"]["comment"] = "Only target cycle changes"
                target.updated_at = "2026-09-09T00:00:01+00:00"
                client = next(client for client in clients if client.id == parent_ids[0])
                client.vehicles[0].notes = "Only target vehicle changes"
                client.updated_at = target.updated_at
                before = len(feed.raw_events_for_test())
                replacement = {**bundle, "cards": cards, "clients": clients}
                if mode == "cached":
                    self.store.write_cached_bundle(bundle, **replacement)
                else:
                    self.store.write_bundle(**replacement)
                foreign_prefixes = tuple(f"{parent_id}:" for parent_id in parent_ids[1:])
                self.assertFalse(
                    any(
                        event["entity_id"].startswith(foreign_prefixes)
                        for event in feed.raw_events_for_test()[before:]
                    )
                )
                with feed._connection() as connection:
                    after_entities = feed._entity_state(connection)
                foreign_before = {
                    key: value
                    for key, value in before_entities.items()
                    if key[1].startswith(foreign_prefixes)
                }
                self.assertTrue(foreign_before)
                self.assertEqual(
                    {key: after_entities.get(key) for key in foreign_before}, foreign_before
                )
                self.assert_reconcile_adds_no_events()


if __name__ == "__main__":
    unittest.main()
