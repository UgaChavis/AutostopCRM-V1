from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.storage.change_feed_projection import ProjectedEntity
from minimal_kanban.storage.change_feed_store import ChangeFeedStore


class ChangeFeedBulkPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.feed = ChangeFeedStore(self.directory / "feed.sqlite3")
        self.feed.initialize_baseline([])

    @staticmethod
    def event(identity: str) -> dict:
        return {
            "source_event_id": identity,
            "occurred_at": "2026-01-01T00:00:00Z",
            "action": "card_updated",
            "entity_type": "card",
            "entity_id": identity,
            "change_type": "update",
            "tombstone": False,
            "correlation_ref": "",
            "idempotency_ref": "",
            "producer": "state_projection",
        }

    def stage(self, identities: tuple[str, ...]) -> None:
        baseline = self.snapshot(self.feed)
        self.baseline_entities = baseline["entity_state"]
        self.baseline_sources = baseline["source_state"]
        with self.feed._transaction(immediate=True) as connection:
            for identity in ("seen-retained", "seen-compacted"):
                self.feed._publish_event(connection, self.event(identity))
            connection.execute("DELETE FROM events WHERE source_event_id = 'seen-compacted'")
            self.feed._set_metadata(connection, "high_water", "7")
            self.feed._set_metadata(connection, "pending_fingerprint", "target")
            self.feed._set_metadata(connection, "committed_fingerprint", "before")
            for ordinal, identity in enumerate(identities):
                self.feed._insert_pending_event(
                    connection,
                    ordinal=ordinal * 3,
                    state_fingerprint="target",
                    event=self.event(identity),
                )
            connection.executemany(
                "INSERT INTO entity_state VALUES ('card', ?, 'old', 'route', 'active')",
                (("update",), ("delete",), ("foreign",)),
            )
            connection.executemany(
                "INSERT INTO pending_entity_changes VALUES (?, 'card', ?, ?, 'new', 'changed', 'active')",
                (
                    ("target", "update", "upsert"),
                    ("target", "delete", "delete"),
                    ("other", "foreign", "upsert"),
                    ("target", "new", "upsert"),
                ),
            )
            connection.executemany(
                "INSERT INTO source_state VALUES ('card', ?, 'old')",
                (("update",), ("delete",)),
            )
            connection.executemany(
                "INSERT INTO pending_source_changes VALUES ('card', ?, ?, 'new')",
                (("update", "upsert"), ("delete", "delete"), ("new", "upsert")),
            )
        self.feed._prepared_sources = ("target", {("card", "update"): "new"})
        self.feed._seen_baseline_cache = (("before", "7"), {"seen-retained", "seen-compacted"})

    @staticmethod
    def snapshot(feed: ChangeFeedStore) -> dict:
        with feed._connection() as connection:
            return {
                table: sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
                for table in (
                    "metadata",
                    "events",
                    "seen_sources",
                    "pending_events",
                    "entity_state",
                    "pending_entity_changes",
                    "source_state",
                    "pending_source_changes",
                )
            }

    def assert_applied_changes(self) -> None:
        state = self.snapshot(self.feed)
        self.assertEqual(
            sorted(
                [
                    *self.baseline_entities,
                    ("card", "foreign", "old", "route", "active"),
                    ("card", "new", "new", "changed", "active"),
                    ("card", "update", "new", "changed", "active"),
                ]
            ),
            state["entity_state"],
        )
        self.assertEqual(
            sorted([*self.baseline_sources, ("card", "new", "new"), ("card", "update", "new")]),
            state["source_state"],
        )
        for table in ("pending_events", "pending_entity_changes", "pending_source_changes"):
            self.assertEqual([], state[table], table)
        self.assertIn(("seen-compacted", None), state["seen_sources"])
        self.assertIn(("committed_fingerprint", "target"), state["metadata"])

    def test_order_deduplication_cache_keys_and_reference_recovery_are_equivalent(self) -> None:
        identities = ("new-z", "seen-compacted", "new-a", "seen-retained")
        self.stage(identities)
        reference_path = self.directory / "reference.sqlite3"
        shutil.copyfile(self.feed.path, reference_path)
        reference = ChangeFeedStore(reference_path)
        reference.reconcile_state("target", [])

        self.assertEqual(2, self.feed.commit_state_write("target"))
        self.assertEqual(0, self.feed.commit_state_write("target"))
        self.assertEqual(self.snapshot(reference), self.snapshot(self.feed))
        rows = self.feed.raw_events_for_test()
        self.assertEqual([1, 8, 9], [row["sequence"] for row in rows])
        self.assertEqual(["seen-retained", "new-z", "new-a"], [row["event_id"] for row in rows])
        self.assert_applied_changes()
        self.assertIs(self.feed._source_baseline_cache, self.feed._prepared_sources)
        self.assertEqual((("target", "9"), set(identities)), self.feed._seen_baseline_cache)

    def test_empty_and_already_seen_batches_still_apply_state_without_allocating_sequences(
        self,
    ) -> None:
        for identities in ((), ("seen-compacted", "seen-retained")):
            with self.subTest(identities=identities):
                self.feed = ChangeFeedStore(self.directory / f"empty-{len(identities)}.sqlite3")
                self.feed.initialize_baseline([])
                self.stage(identities)
                self.assertEqual(0, self.feed.commit_state_write("target"))
                self.assertEqual(0, self.feed.commit_state_write("target"))
                self.assert_applied_changes()
                self.assertEqual(("target", "7"), self.feed._seen_baseline_cache[0])
                self.assertEqual([1], [row["sequence"] for row in self.feed.raw_events_for_test()])

    def test_fingerprint_mismatch_leaves_pending_and_published_state_untouched(self) -> None:
        self.stage(("new-z", "new-a"))
        before = self.snapshot(self.feed)
        caches = (self.feed._source_baseline_cache, self.feed._seen_baseline_cache)
        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            self.feed.commit_state_write("wrong")
        self.assertEqual(before, self.snapshot(self.feed))
        self.assertEqual(caches, (self.feed._source_baseline_cache, self.feed._seen_baseline_cache))

    def test_external_producer_between_prepare_and_commit_preserves_all_sequences(self) -> None:
        self.stage(("new-z", "new-a"))
        external = ChangeFeedStore(self.feed.path)
        external.initialize_external_projection("files", {})
        key = ("shared_file", "file-1")
        external.reconcile_external_projection(
            "files", {key: ProjectedEntity(*key, "first", "route", "active")}
        )
        self.assertEqual(2, self.feed.commit_state_write("target"))
        external.reconcile_external_projection(
            "files", {key: ProjectedEntity(*key, "second", "route", "active")}
        )
        rows = self.feed.raw_events_for_test()
        self.assertEqual([1, 8, 9, 10, 11], [row["sequence"] for row in rows])
        self.assertEqual(["new-z", "new-a"], [row["event_id"] for row in rows[2:4]])
        self.assertEqual(["files", "files"], [rows[1]["producer"], rows[4]["producer"]])
        self.assertEqual(("target", "10"), self.feed._seen_baseline_cache[0])

    def test_sqlite_trigger_failure_after_inserts_rolls_back_and_restarts_safely(self) -> None:
        self.stage(("new-z", "new-a"))
        with self.feed._connection() as connection:
            connection.execute(
                """CREATE TRIGGER fail_after_seen_insert AFTER INSERT ON seen_sources
                   WHEN NEW.source_event_id = 'new-a'
                   BEGIN SELECT RAISE(ABORT, 'synthetic publish failure'); END"""
            )
        before = self.snapshot(self.feed)
        caches = (self.feed._source_baseline_cache, self.feed._seen_baseline_cache)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic publish failure"):
            self.feed.commit_state_write("target")
        self.assertEqual(before, self.snapshot(self.feed))
        self.assertEqual(caches, (self.feed._source_baseline_cache, self.feed._seen_baseline_cache))
        with self.feed._connection() as connection:
            connection.execute("DROP TRIGGER fail_after_seen_insert")
        self.feed = ChangeFeedStore(self.feed.path)
        self.feed.reconcile_state("target", [])
        self.assert_applied_changes()
        self.assertEqual([1, 8, 9], [row["sequence"] for row in self.feed.raw_events_for_test()])
        self.assertEqual(0, self.feed.commit_state_write("target"))

    def test_large_pending_batch_has_bounded_sql_and_unchanged_transaction_mode(self) -> None:
        identities = tuple(f"new-{index:03}" for index in reversed(range(310)))
        self.stage(identities)
        with self.feed._transaction(immediate=True) as connection:
            connection.executemany(
                "INSERT INTO pending_entity_changes VALUES ('target', 'card', ?, 'upsert', 'new', 'route', 'active')",
                ((identity,) for identity in identities),
            )
            connection.executemany(
                "INSERT INTO pending_source_changes VALUES ('card', ?, 'upsert', 'new')",
                ((identity,) for identity in identities),
            )
        statements = []
        connections = []
        original_connect = self.feed._connect

        def traced_connect(*, durable=True):
            connection = original_connect(durable=durable)
            connections.append((durable, connection.execute("PRAGMA synchronous").fetchone()[0]))
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.feed, "_connect", side_effect=traced_connect):
            self.assertEqual(310, self.feed.commit_state_write("target"))
        self.assertEqual([(False, 1)], connections)
        self.assertEqual("BEGIN IMMEDIATE", statements[0])
        self.assertEqual("COMMIT", statements[-1])
        self.assertLessEqual(len(statements), 24, "SQL calls must not grow per pending event")
        rows = self.feed.raw_events_for_test()[1:]
        self.assertEqual(list(identities), [row["event_id"] for row in rows])
        self.assertEqual(list(range(8, 318)), [row["sequence"] for row in rows])


if __name__ == "__main__":
    unittest.main()
