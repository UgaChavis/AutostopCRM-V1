from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import os
import tempfile
import threading
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage import json_store


class ReadSnapshotConcurrencyTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="snapshot-concurrency-appdata-")
        self.addCleanup(appdata.cleanup)
        env = patch.dict(os.environ, {"APPDATA": appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.card_id = self.service.create_card({"title": "BEFORE"})["card"]["id"]
        self.service.update_card({"card_id": self.card_id, "repair_order": {"client": "SYNTHETIC"}})

    def assert_read_ignores_service_lock(self, read):
        finished = threading.Event()
        results, errors = [], []

        def run():
            try:
                results.append(read())
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with self.service._lock:
            worker = threading.Thread(target=run)
            worker.start()
            independent = finished.wait(2)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(independent, "Independent snapshot waited for the writer service lock")
        return results[0]

    def test_single_card_read_does_not_queue_behind_an_unrelated_service_operation(self):
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.get_card({"card_id": self.card_id})
        )
        self.assertEqual(result["card"]["title"], "BEFORE")

    def test_repair_order_list_does_not_queue_behind_an_unrelated_service_operation(self):
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.list_repair_orders({"compact": True})
        )
        self.assertEqual(result["repair_orders"][0]["card_id"], self.card_id)

    def assert_parse_allows_commit(self, snapshot, *, requires_file_lock=False):
        parsing, release, committed = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        normalize = self.store._normalize_cards

        def paused_normalize(*args, **kwargs):
            if threading.current_thread().name == "independent-snapshot":
                parsing.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic snapshot was not released")
            return normalize(*args, **kwargs)

        def read():
            try:
                results.append(snapshot())
            except Exception as exc:
                errors.append(exc)

        def write():
            try:
                self.service.update_card({"card_id": self.card_id, "title": "AFTER"})
                committed.set()
            except Exception as exc:
                errors.append(exc)

        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", requires_file_lock),
            patch.object(self.store, "_normalize_cards", side_effect=paused_normalize),
        ):
            reader = threading.Thread(target=read, name="independent-snapshot")
            writer = threading.Thread(target=write)
            reader.start()
            try:
                self.assertTrue(parsing.wait(2))
                writer.start()
                independent = committed.wait(2)
            finally:
                release.set()
                reader.join(5)
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(independent, "Read-only parsing blocked a concurrent commit")
        return results[0]

    def test_cold_repair_order_snapshot_does_not_block_commit_or_cache_stale_version(self):
        old = self.assert_parse_allows_commit(self.store.read_repair_order_cards_snapshot)
        self.assertEqual(old[0].title, "BEFORE")
        current = self.store.read_repair_order_cards_snapshot()
        self.assertEqual(current[0].title, "AFTER")

    def test_locked_repair_order_snapshot_releases_writer_before_normalization(self):
        old = self.assert_parse_allows_commit(
            self.store.read_repair_order_cards_snapshot, requires_file_lock=True
        )
        self.assertEqual(old[0].title, "BEFORE")
        with patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True):
            current = self.store.read_repair_order_cards_snapshot()
        self.assertEqual(current[0].title, "AFTER")

    def test_projector_does_not_block_writer_or_next_snapshot(self):
        self.service.list_repair_orders({"status": "all"})
        projecting, release, finished = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        serialize = self.service._serialize_repair_order_list_item

        def paused_serialize(*args, **kwargs):
            if threading.current_thread().name == "paused-order-projector":
                projecting.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic projector was not released")
            return serialize(*args, **kwargs)

        def read():
            try:
                results.append(self.service.list_repair_orders({"status": "all"}))
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with patch.object(
            self.service, "_serialize_repair_order_list_item", side_effect=paused_serialize
        ):
            worker = threading.Thread(target=read, name="paused-order-projector")
            worker.start()
            try:
                self.assertTrue(projecting.wait(2))
                self.service.update_card({"card_id": self.card_id, "title": "AFTER"})
                current = self.service.list_repair_orders({"status": "all"})
                self.assertIn("AFTER", current["repair_orders"][0]["heading"])
            finally:
                release.set()
                worker.join(5)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertIn("BEFORE", results[0]["repair_orders"][0]["heading"])

    def test_cold_context_snapshot_does_not_block_commit_or_cache_stale_version(self):
        def snapshot():
            return self.store._read_context_bundle_snapshot(domains=("events",))

        old = self.assert_parse_allows_commit(snapshot)
        self.assertEqual(old["cards"][0].title, "BEFORE")
        self.assertEqual(snapshot()["cards"][0].title, "AFTER")

    def test_context_load_does_not_hold_service_lock_while_parsing(self):
        def read():
            return self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            )

        old = self.assert_parse_allows_commit(read)
        self.assertEqual(old["card"]["title"], "BEFORE")
        self.assertEqual(read()["card"]["title"], "AFTER")
