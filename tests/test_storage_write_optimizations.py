from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from copy import copy, deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import AuditEvent, Card, CardTag, parse_datetime
from minimal_kanban.storage.change_feed_projection import (
    cached_crm_source_signatures,
    project_crm_source_signatures,
    project_crm_state,
)
from minimal_kanban.storage.json_store import JsonStore, StateWriteConflictError, _serialized_state


class StorageWriteOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state_file = Path(temp.name) / "state.json"
        logger = logging.getLogger(self.id())
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)

    def seed_cards(self):
        bundle = self.store.read_bundle()
        cards = [
            Card.from_dict({"id": str(index), "title": f"Card {index}", "position": index})
            for index in range(2)
        ]
        self.store.write_cached_bundle(bundle, **{**bundle, "cards": cards})
        return self.store.read_bundle()

    def test_routing_only_shallow_copies_reuse_normalized_content_and_survive_restart(self):
        bundle = self.seed_cards()
        originals = list(bundle["cards"])
        cards = [copy(card) for card in originals]
        cards[0].position, cards[1].position = 1, 0
        with patch.object(
            Card, "from_dict", side_effect=AssertionError("unnecessary normalization")
        ):
            self.store.write_cached_bundle(bundle, **{**bundle, "cards": cards})
        self.assertEqual([card.position for card in originals], [0, 1])
        reloaded = JsonStore(self.state_file).read_bundle()["cards"]
        self.assertEqual({card.id: card.position for card in reloaded}, {"0": 1, "1": 0})
        self.assertEqual(
            {card.id: card.updated_at for card in reloaded},
            {card.id: card.updated_at for card in originals},
        )

    def test_routing_shortcut_rejects_content_replacement_and_invalid_position(self):
        original = self.seed_cards()["cards"][0]
        for name, value in (
            ("title", "Changed"),
            ("position", True),
            ("position", -1),
            ("position", 1_000_001),
        ):
            with self.subTest(field=name, value=value):
                card = copy(original)
                setattr(card, name, value)
                self.assertIsNone(self.store._routing_only_card_payload(card))
        self.assertIsNone(self.store._routing_only_card_payload(deepcopy(original)))
        original.is_unread = not original.is_unread
        self.assertIsNone(self.store._routing_only_card_payload(copy(original)))

    def test_card_projection_cache_matches_full_projection_and_invalidates_replacements(self):
        bundle = self.seed_cards()
        state = self.store._state_from_bundle(bundle)
        cache = {}
        self.assertEqual(project_crm_state(state, card_cache=cache), project_crm_state(state))
        moved = {
            **state,
            "cards": [{**card, "position": 1 - card["position"]} for card in state["cards"]],
        }
        expected = project_crm_state(moved)
        with patch(
            "minimal_kanban.storage.change_feed_projection._project_card",
            side_effect=AssertionError("unnecessary reprojection"),
        ):
            self.assertEqual(project_crm_state(moved, card_cache=cache), expected)
        replaced = deepcopy(moved)
        replaced["cards"][0]["title"] = "External replacement with unchanged revision"
        self.assertEqual(project_crm_state(replaced, card_cache=cache), project_crm_state(replaced))
        replaced = {**replaced, "cards": []}
        self.assertEqual(project_crm_state(replaced, card_cache=cache), project_crm_state(replaced))
        self.assertEqual(cache, {})

    def test_shallow_copy_nested_mutation_is_not_mistaken_for_routing_only(self):
        bundle = self.seed_cards()
        card = copy(bundle["cards"][0])
        card.position = 1
        card.tags.append(CardTag(label="Changed through shared branch"))
        self.assertIsNone(self.store._routing_only_card_payload(card))
        self.store.write_cached_bundle(bundle, **{**bundle, "cards": [card, bundle["cards"][1]]})
        reloaded = JsonStore(self.state_file).read_bundle()["cards"]
        self.assertEqual(next(item for item in reloaded if item.id == card.id).tags, card.tags)

    def test_failed_cached_write_discards_projection_cache(self):
        bundle = self.seed_cards()
        self.assertTrue(self.store._card_projection_cache)
        card = deepcopy(bundle["cards"][0])
        card.title = "Rejected"
        before = self.state_file.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.write_cached_bundle(
                    bundle, **{**bundle, "cards": [card, bundle["cards"][1]]}
                )
        self.assertFalse(self.store._card_projection_cache)
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(JsonStore(self.state_file).read_bundle()["cards"][0].title, "Card 0")

    def test_noop_write_skips_source_projection_but_changed_state_calls_it(self) -> None:
        bundle = self.store.read_bundle()
        self.store.write_cached_bundle(bundle, **bundle)
        signature = self.store._state_signature()
        with patch(
            "minimal_kanban.storage.json_store.cached_crm_source_signatures",
            wraps=cached_crm_source_signatures,
        ) as project:
            with (
                patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected write")),
                patch.object(Path, "replace", side_effect=AssertionError("unexpected replace")),
                patch.object(
                    self.store.change_feed_store,
                    "commit_state_write",
                    side_effect=AssertionError("unexpected publish"),
                ),
            ):
                self.store.write_cached_bundle(bundle, **bundle)
            self.assertEqual(self.store._state_signature(), signature)
            project.assert_not_called()
            changed = {**bundle, "settings": {**bundle["settings"], "isolation_marker": "new"}}
            self.store.write_cached_bundle(bundle, **changed)
            project.assert_called_once()
        self.assertEqual(
            JsonStore(self.state_file).read_bundle()["settings"]["isolation_marker"], "new"
        )
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())

    def test_changed_unprojected_state_still_replaces_file_with_zero_feed_events(self) -> None:
        state = self.store._read_state()
        before = self.store.change_feed_store.raw_events_for_test()
        state["unprojected_marker"] = "must persist despite zero new events"
        with patch.object(Path, "replace", autospec=True, side_effect=Path.replace) as replace:
            self.store._write_state(state)
        replace.assert_called_once()
        self.assertEqual(self.store.change_feed_store.raw_events_for_test(), before)
        self.assertEqual(
            self.store._read_state()["unprojected_marker"], state["unprojected_marker"]
        )
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())

    def test_same_semantics_with_different_bytes_still_replaces_file(self) -> None:
        state = self.store._read_state()
        formatted = json.dumps(state, indent=2).encode("utf-8")
        self.state_file.write_bytes(formatted)
        self.store.reconcile_change_feed()
        with patch.object(Path, "replace", autospec=True, side_effect=Path.replace) as replace:
            self.store._write_state(state)
        replace.assert_called_once()
        self.assertNotEqual(self.state_file.read_bytes(), formatted)
        self.assertEqual(json.loads(self.state_file.read_bytes()), state)

    def test_missing_or_externally_changed_file_never_uses_unchanged_shortcut(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                bundle = self.store.read_bundle()
                self.store.write_cached_bundle(bundle, **bundle)
                previous = self.state_file.read_bytes()
                if missing:
                    self.state_file.unlink()
                else:
                    self.state_file.write_bytes(previous + b"\n")
                with (
                    patch.object(Path, "replace", side_effect=AssertionError("unexpected write")),
                    self.assertRaises(StateWriteConflictError),
                ):
                    self.store.write_cached_bundle(bundle, **bundle)
                with patch.object(
                    Path, "replace", autospec=True, side_effect=Path.replace
                ) as replace:
                    self.store._write_state(
                        json.loads(previous), already_safe=True, fast_serializer=True
                    )
                replace.assert_called_once()
                self.assertEqual(self.state_file.read_bytes(), previous)

    def test_pending_recovery_completes_write_even_when_requested_bytes_are_committed(self) -> None:
        bundle = self.store.read_bundle()
        self.store.write_cached_bundle(bundle, **bundle)
        self.store.change_feed_store.prepare_state_write("interrupted-other-state", [])
        prepare = self.store.change_feed_store.prepare_state_write

        def mark_before_pending_failure(*args, **kwargs):
            if kwargs.get("on_unchanged") is not None:
                kwargs["on_unchanged"]()
            return prepare(*args, **kwargs)

        with (
            patch.object(
                self.store.change_feed_store,
                "prepare_state_write",
                side_effect=mark_before_pending_failure,
            ),
            patch.object(Path, "replace", autospec=True, side_effect=Path.replace) as replace,
            patch.object(
                self.store,
                "_reconcile_change_feed_locked",
                wraps=self.store._reconcile_change_feed_locked,
            ) as reconcile,
        ):
            self.store.write_cached_bundle(bundle, **bundle)
        reconcile.assert_called_once()
        replace.assert_called_once()
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())

    def test_matching_pending_fingerprint_still_publishes_and_replaces(self) -> None:
        bundle = self.store.read_bundle()
        changed = {**bundle, "settings": {**bundle["settings"], "pending_marker": "committed"}}
        with patch.object(
            self.store.change_feed_store, "commit_state_write", side_effect=OSError("interrupted")
        ):
            self.store.write_cached_bundle(bundle, **changed)
        self.assertTrue(self.store.change_feed_store.has_pending_state_write())
        with patch.object(Path, "replace", autospec=True, side_effect=Path.replace) as replace:
            self.store.write_cached_bundle(bundle, **bundle)
        replace.assert_called_once()
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())
        self.assertEqual(
            JsonStore(self.state_file).read_bundle()["settings"]["pending_marker"], "committed"
        )

    def test_prepare_failure_after_unchanged_callback_does_not_report_success(self) -> None:
        bundle = self.store.read_bundle()
        self.store.write_cached_bundle(bundle, **bundle)
        previous = self.state_file.read_bytes()

        def fail_after_callback(*args, **kwargs):
            kwargs["on_unchanged"]()
            raise OSError("injected transaction completion failure")

        with (
            patch.object(
                self.store.change_feed_store, "prepare_state_write", side_effect=fail_after_callback
            ),
            patch.object(Path, "replace", side_effect=AssertionError("unexpected write")),
            self.assertRaises(OSError),
        ):
            self.store.write_cached_bundle(bundle, **bundle)
        self.assertEqual(self.state_file.read_bytes(), previous)
        self.assertIsNone(self.store._read_cache_bundle)

    def test_payload_cache_reuses_tuple_only_while_identity_and_version_match(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Original"})
        self.store._storage_payloads("cards", [card], lambda item: item.to_storage_dict())
        previous = self.store._storage_dict_cache["cards"][id(card)]
        self.store._storage_payloads("cards", [card], lambda item: item.to_storage_dict())
        self.assertIs(self.store._storage_dict_cache["cards"][id(card)], previous)
        card.position += 1
        payloads = self.store._storage_payloads(
            "cards", [card], lambda item: item.to_storage_dict()
        )
        self.assertIsNot(self.store._storage_dict_cache["cards"][id(card)], previous)
        self.assertEqual(payloads[0]["position"], card.position)
        replacement = deepcopy(card)
        replacement.title = "Replacement with unchanged revision"
        payloads = self.store._storage_payloads(
            "cards", [replacement], lambda item: item.to_storage_dict()
        )
        self.assertEqual(payloads[0]["title"], replacement.title)
        self.assertNotIn(id(card), self.store._storage_dict_cache["cards"])

    def test_signature_cache_keeps_domains_separate_and_releases_deleted_payloads(self) -> None:
        shared = {"id": "shared", "updated_at": "unchanged", "position": 0}
        state = {"cards": [shared], "clients": [shared]}
        cache = {}
        for candidate in (state, state, deepcopy(state)):
            self.assertEqual(
                cached_crm_source_signatures(candidate, cache),
                project_crm_source_signatures(candidate),
            )
        state["cards"] = [{**shared, "position": 1}]
        self.assertEqual(
            cached_crm_source_signatures(state, cache), project_crm_source_signatures(state)
        )
        state["cards"] = []
        self.assertEqual(
            cached_crm_source_signatures(state, cache), project_crm_source_signatures(state)
        )
        self.assertFalse(cache["cards"])

    def test_retention_parses_once_per_call_and_sorts_by_instant_then_id(self) -> None:
        stamps = [
            ("b", "2026-09-01T10:00:00+03:00"),
            ("later", "2026-09-01T08:00:00+00:00"),
            ("a", "2026-09-01T10:00:00+03:00"),
            ("expired", "2000-01-01T00:00:00+00:00"),
            ("invalid", "invalid"),
        ]
        events = [AuditEvent(key, stamp, "test", "api", "test", "test") for key, stamp in stamps]
        with (
            patch(
                "minimal_kanban.storage.json_store.utc_now",
                return_value=datetime(2026, 9, 2, tzinfo=UTC),
            ),
            patch(
                "minimal_kanban.storage.json_store.parse_datetime", wraps=parse_datetime
            ) as parse,
        ):
            retained, changed = self.store._apply_event_retention(events)
            self.assertEqual(parse.call_count, len({stamp for _, stamp in stamps}))
        self.assertTrue(changed)
        self.assertEqual([event.id for event in retained], ["a", "b", "later"])

    def test_full_state_depth_limit_preserves_valid_data_and_rejects_excess_before_write(self):
        nested = 17
        for _ in range(512):
            nested = {"child": nested}
        for fast in (False, True):
            with self.subTest(fast=fast):
                safe, payload, _ = _serialized_state(nested, fast_serializer=fast)
                decoded = json.loads(payload)
                for _ in range(512):
                    safe = safe["child"]
                    decoded = decoded["child"]
                self.assertIs(type(safe), int)
                self.assertEqual((safe, decoded), (17, 17))
        previous = self.state_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "too deeply nested"):
            self.store._write_state({"excess": nested})
        self.assertEqual(self.state_file.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
