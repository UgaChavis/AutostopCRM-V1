from __future__ import annotations

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
from minimal_kanban.storage.json_store import JsonStore


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
        with patch(
            "minimal_kanban.storage.json_store.cached_crm_source_signatures",
            wraps=cached_crm_source_signatures,
        ) as project:
            self.store.write_cached_bundle(bundle, **bundle)
            project.assert_not_called()
            changed = {**bundle, "settings": {**bundle["settings"], "isolation_marker": "new"}}
            self.store.write_cached_bundle(bundle, **changed)
            project.assert_called_once()
        self.assertEqual(
            JsonStore(self.state_file).read_bundle()["settings"]["isolation_marker"], "new"
        )
        self.assertFalse(self.store.change_feed_store.has_pending_state_write())

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
