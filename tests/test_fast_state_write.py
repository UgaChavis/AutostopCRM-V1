from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import AuditEvent, Card, utc_now  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage import json_store as json_store_module  # noqa: E402
from minimal_kanban.storage.json_store import (  # noqa: E402
    JsonStore,
    StateWriteConflictError,
    _serialized_state,
)


class FastStateWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.fast_state_write.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _state_path(self, name: str) -> Path:
        return self.base_dir / name / "state.json"

    @staticmethod
    def _card(*, card_id: str = "card-1") -> Card:
        return Card.from_dict(
            {
                "id": card_id,
                "title": "Initial card",
                "description": "Initial description",
                "column": "inbox",
                "position": 0,
                "archived": False,
                "created_at": "2026-07-10T10:00:00+00:00",
                "updated_at": "2026-07-10T10:00:00+00:00",
                "notification_updated_at": "2026-07-10T10:00:00+00:00",
                "deadline_timestamp": "2026-07-12T10:00:00+00:00",
                "deadline_total_seconds": 172800,
            },
            valid_columns={"inbox", "in_progress", "control", "done"},
            default_column="inbox",
        )

    @staticmethod
    def _event(event_id: str, *, message: str = "test event") -> AuditEvent:
        return AuditEvent.from_dict(
            {
                "id": event_id,
                "timestamp": utc_now().isoformat(),
                "actor_name": "TEST",
                "source": "system",
                "action": "test_event",
                "message": message,
                "details": {"event_id": event_id},
                "card_id": "card-1",
            }
        )

    def _seed_store(self, state_file: Path, *, include_event: bool = False) -> JsonStore:
        store = JsonStore(state_file=state_file, logger=self.logger)
        bundle = store.read_bundle()
        bundle["cards"].append(self._card())
        if include_event:
            bundle["events"].append(self._event("event-seed"))
        self._normal_write(store, bundle)
        return store

    @staticmethod
    def _normal_write(store: JsonStore, bundle: dict) -> dict:
        return store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            inventory_items=bundle["inventory_items"],
            inventory_movements=bundle["inventory_movements"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    @staticmethod
    def _fast_write(store: JsonStore, bundle: dict) -> dict:
        return store.write_cached_bundle(
            bundle,
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            inventory_items=bundle["inventory_items"],
            inventory_movements=bundle["inventory_movements"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    def test_fast_write_rejects_bundle_without_store_provenance(self) -> None:
        state_file = self._state_path("foreign-bundle")
        store = self._seed_store(state_file)
        cached_bundle = store.read_bundle()
        foreign_bundle = dict(cached_bundle)
        before = state_file.read_bytes()

        with self.assertRaises(StateWriteConflictError):
            self._fast_write(store, foreign_bundle)

        self.assertEqual(state_file.read_bytes(), before)
        self.assertIsNone(store._read_cache_bundle)
        self.assertIsNone(store._read_cache_signature)

    def test_fast_write_rejects_signature_drift_without_losing_external_write(self) -> None:
        state_file = self._state_path("signature-drift")
        first_store = self._seed_store(state_file)
        stale_bundle = first_store.read_bundle()

        external_store = JsonStore(state_file=state_file, logger=self.logger)
        external_bundle = external_store.read_bundle()
        external_bundle["events"].append(self._event("event-external"))
        self._normal_write(external_store, external_bundle)
        externally_written = state_file.read_bytes()

        stale_bundle["events"].append(self._event("event-stale"))
        with self.assertRaises(StateWriteConflictError):
            self._fast_write(first_store, stale_bundle)

        self.assertEqual(state_file.read_bytes(), externally_written)
        reloaded = JsonStore(state_file=state_file, logger=self.logger).read_bundle()
        event_ids = {event.id for event in reloaded["events"]}
        self.assertIn("event-external", event_ids)
        self.assertNotIn("event-stale", event_ids)

    def test_fast_write_allows_repeated_write_of_same_cached_bundle(self) -> None:
        state_file = self._state_path("repeat")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()

        bundle["events"].append(self._event("event-first"))
        first_result = self._fast_write(store, bundle)
        self.assertIs(first_result, bundle)

        bundle["events"].append(self._event("event-second"))
        second_result = self._fast_write(store, bundle)
        self.assertIs(second_result, bundle)

        reloaded = JsonStore(state_file=state_file, logger=self.logger).read_bundle()
        event_ids = {event.id for event in reloaded["events"]}
        self.assertIn("event-first", event_ids)
        self.assertIn("event-second", event_ids)

    def test_fast_write_reuses_unchanged_storage_payloads(self) -> None:
        state_file = self._state_path("payload-cache")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()
        self._fast_write(store, bundle)

        with patch.object(
            Card,
            "to_storage_dict",
            side_effect=AssertionError("unchanged card payload should be reused"),
        ):
            self._fast_write(store, bundle)

    def test_fast_write_persists_in_place_card_seen_change(self) -> None:
        state_file = self._state_path("payload-cache-seen")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()
        self._fast_write(store, bundle)

        self.assertTrue(bundle["cards"][0].mark_seen("OPERATOR-1"))
        self._fast_write(store, bundle)

        reloaded = JsonStore(state_file=state_file, logger=self.logger).read_bundle()
        self.assertIn("OPERATOR-1", reloaded["cards"][0].seen_by_users)

    def test_fast_write_is_byte_equivalent_to_normal_write_after_card_change(self) -> None:
        normal_path = self._state_path("normal")
        fast_path = self._state_path("fast")
        normal_store = self._seed_store(normal_path)
        fast_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(normal_path, fast_path)
        fast_store = JsonStore(state_file=fast_path, logger=self.logger)
        normal_bundle = normal_store.read_bundle()
        fast_bundle = fast_store.read_bundle()

        event_timestamp = utc_now().isoformat()
        event_payload = {
            "id": "event-update",
            "timestamp": event_timestamp,
            "actor_name": "TEST",
            "source": "system",
            "action": "title_changed",
            "message": "title changed",
            "details": {"before": "Initial card", "after": "Updated card"},
            "card_id": "card-1",
        }
        for bundle in (normal_bundle, fast_bundle):
            bundle["cards"][0].title = "Updated card"
            bundle["cards"][0].updated_at = "2026-07-10T11:00:00+00:00"
            bundle["events"].append(AuditEvent.from_dict(event_payload))

        self._normal_write(normal_store, normal_bundle)
        self._fast_write(fast_store, fast_bundle)

        self.assertEqual(fast_path.read_bytes(), normal_path.read_bytes())

    def test_fast_write_uses_orjson_for_supported_state(self) -> None:
        state_file = self._state_path("orjson-fast-path")
        store = self._seed_store(state_file, include_event=True)
        bundle = store.read_bundle()
        bundle["events"].append(self._event("event-fast-path"))

        with (
            patch.object(
                json_store_module.orjson,
                "dumps",
                wraps=json_store_module.orjson.dumps,
            ) as fast_dumps,
            patch.object(
                json_store_module,
                "_supports_fast_state_serialization",
                side_effect=AssertionError("trusted cached writes must skip the safety scan"),
            ) as safety_scan,
        ):
            self._fast_write(store, bundle)

        self.assertEqual(fast_dumps.call_count, 1)
        safety_scan.assert_not_called()

    def test_fast_serializer_preserves_json_semantics_for_unicode_and_floats(self) -> None:
        state = {
            "message": "Русский текст 🚗",
            "values": [-0.0, 1.25, 1e20, 1e-7],
            "nested": {"enabled": True, "empty": None},
        }

        _safe, fast_payload, _fingerprint = _serialized_state(
            state,
            already_safe=True,
            fast_serializer=True,
        )
        _safe, legacy_payload, _fingerprint = _serialized_state(
            state,
            already_safe=True,
            fast_serializer=False,
        )

        self.assertEqual(json.loads(fast_payload), json.loads(legacy_payload))
        self.assertIn("Русский текст 🚗".encode(), fast_payload)

    def test_fast_serializer_falls_back_or_fails_closed_for_unsupported_values(self) -> None:
        fallback_states = (
            {"value": 1 << 80},
            {1: "non-string-key"},
        )
        for state in fallback_states:
            with self.subTest(state=state):
                with patch.object(
                    json_store_module.orjson,
                    "dumps",
                    wraps=json_store_module.orjson.dumps,
                ) as fast_dumps:
                    _safe, payload, _fingerprint = _serialized_state(
                        state,
                        already_safe=True,
                        fast_serializer=True,
                    )
                fast_dumps.assert_called_once()
                _safe, legacy_payload, _fingerprint = _serialized_state(
                    state,
                    already_safe=True,
                    fast_serializer=False,
                )
                self.assertEqual(payload, legacy_payload)

        unsupported_values = (
            datetime(2026, 7, 22, tzinfo=UTC),
            object(),
            float("nan"),
            float("inf"),
        )
        for value in unsupported_values:
            with self.subTest(value=type(value).__name__):
                with patch.object(
                    json_store_module.orjson,
                    "dumps",
                    side_effect=AssertionError("orjson must not receive unsupported values"),
                ) as fast_dumps:
                    with self.assertRaises((TypeError, ValueError)):
                        _serialized_state(
                            {"value": value},
                            already_safe=True,
                            fast_serializer=True,
                        )
                fast_dumps.assert_not_called()

    def test_fast_write_outbox_recovers_using_exact_state_file_fingerprint(self) -> None:
        state_file = self._state_path("orjson-outbox-recovery")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()
        event = self._event("event-recovered-after-restart")
        event.details["scientific"] = 1e20
        bundle["events"].append(event)

        with patch.object(
            store.change_feed_store,
            "commit_state_write",
            side_effect=RuntimeError("simulated interruption after state replace"),
        ):
            self._fast_write(store, bundle)

        state_bytes = state_file.read_bytes()
        expected_fingerprint = hashlib.sha256(state_bytes).hexdigest()
        self.assertIn(b"1e20", state_bytes)
        self.assertTrue(store.change_feed_store.has_pending_state_write())
        restarted = JsonStore(state_file=state_file, logger=self.logger)
        with patch.object(
            restarted.change_feed_store,
            "reconcile_state",
            wraps=restarted.change_feed_store.reconcile_state,
        ) as reconcile_state:
            restarted.read_bundle()

        self.assertEqual(reconcile_state.call_args.args[0], expected_fingerprint)
        self.assertFalse(restarted.change_feed_store.has_pending_state_write())
        published_ids = {
            row["event_id"] for row in restarted.change_feed_store.raw_events_for_test()
        }
        self.assertIn("event-recovered-after-restart", published_ids)

    def test_strict_serialization_failure_preserves_file_and_invalidates_cache(self) -> None:
        state_file = self._state_path("serialization-failure")
        store = self._seed_store(state_file, include_event=True)
        bundle = store.read_bundle()
        before = state_file.read_bytes()
        bundle["events"][0].details["unsafe"] = object()

        with self.assertRaises(TypeError):
            self._fast_write(store, bundle)

        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(list(state_file.parent.glob(".state.json.*.tmp")), [])
        self.assertIsNone(store._read_cache_bundle)
        self.assertIsNone(store._read_cache_signature)
        refreshed = store.read_bundle()
        self.assertNotIn("unsafe", refreshed["events"][0].details)

    def test_fast_write_rejects_duplicate_card_id(self) -> None:
        state_file = self._state_path("duplicate-card")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()
        duplicate = Card.from_dict(
            bundle["cards"][0].to_storage_dict(),
            valid_columns={column.id for column in bundle["columns"]},
            default_column=bundle["columns"][0].id,
        )
        bundle["cards"].append(duplicate)
        before = state_file.read_bytes()

        with self.assertRaisesRegex(ValueError, "duplicate key"):
            self._fast_write(store, bundle)

        self.assertEqual(state_file.read_bytes(), before)
        self.assertIsNone(store._read_cache_bundle)

    def test_fast_write_rejects_card_with_dangling_column_reference(self) -> None:
        state_file = self._state_path("dangling-column")
        store = self._seed_store(state_file)
        bundle = store.read_bundle()
        bundle["cards"][0].column = "missing-column"
        before = state_file.read_bytes()

        with self.assertRaisesRegex(ValueError, "unknown column"):
            self._fast_write(store, bundle)

        self.assertEqual(state_file.read_bytes(), before)
        self.assertIsNone(store._read_cache_bundle)

    def test_card_service_exposes_storage_conflict_as_http_409_contract(self) -> None:
        state_file = self._state_path("service-conflict")
        store = JsonStore(state_file=state_file, logger=self.logger)
        service = CardService(
            store,
            self.logger,
            attachments_dir=state_file.parent / "attachments",
            repair_orders_dir=state_file.parent / "repair-orders",
        )

        with (
            patch.object(
                store,
                "write_cached_bundle",
                side_effect=StateWriteConflictError("stale"),
            ),
            self.assertRaises(ServiceError) as raised,
        ):
            service.create_card({"title": "conflict", "deadline": {"hours": 2}})

        self.assertEqual(raised.exception.code, "state_write_conflict")
        self.assertEqual(raised.exception.status_code, 409)

    def test_card_service_kill_switch_uses_legacy_normalized_writer(self) -> None:
        state_file = self._state_path("legacy-kill-switch")
        store = JsonStore(state_file=state_file, logger=self.logger)
        service = CardService(
            store,
            self.logger,
            attachments_dir=state_file.parent / "attachments",
            repair_orders_dir=state_file.parent / "repair-orders",
        )

        with (
            patch(
                "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                return_value=False,
            ),
            patch.object(store, "write_bundle", wraps=store.write_bundle) as legacy_write,
            patch.object(
                store,
                "write_cached_bundle",
                wraps=store.write_cached_bundle,
            ) as fast_write,
        ):
            created = service.create_card({"title": "legacy writer", "deadline": {"hours": 2}})

        self.assertTrue(created["card"]["id"])
        self.assertEqual(legacy_write.call_count, 1)
        self.assertEqual(fast_write.call_count, 0)


if __name__ == "__main__":
    unittest.main()
