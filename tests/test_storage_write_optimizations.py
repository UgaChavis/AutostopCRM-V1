from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import AuditEvent, Card, parse_datetime
from minimal_kanban.storage.change_feed_projection import (
    cached_crm_source_signatures,
    project_crm_source_signatures,
)
from minimal_kanban.storage.json_store import JsonStore, StateWriteConflictError


class StorageWriteOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state_file = Path(temp.name) / "state.json"
        logger = logging.getLogger(self.id())
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)

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


if __name__ == "__main__":
    unittest.main()
