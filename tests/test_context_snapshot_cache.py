from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError
from tests.snapshot_lock_helpers import (
    assert_fresh_under_file_lock,
    assert_locked_read_and_missing_lock,
)


class ContextSnapshotCacheTests(CardServiceCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="context-cache-appdata-")
        self.addCleanup(self.appdata.cleanup)
        env = patch.dict(os.environ, {"APPDATA": self.appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "CACHE_TITLE_ONE"})["card"]["id"]

    def files(self):
        roots = (Path(self.temp_dir.name), Path(self.appdata.name))
        return {
            (i, str(path.relative_to(root))): path.read_bytes()
            for i, root in enumerate(roots)
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_repeated_reads_cache_and_return_detached_response(self):
        before = self.files()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            first = self.service.get_card({"card_id": self.card_id})
            first["card"]["title"] = "MUTATED_BY_CALLER"
            first["card"]["repair_order"]["works"].append({"name": "MUTATED"})
            second = self.service.get_card({"card_id": self.card_id})
            self.assertEqual(second["card"]["title"], "CACHE_TITLE_ONE")
            self.assertEqual(second["card"]["repair_order"]["works"], [])
            self.assertEqual(read.call_count, 2 if sys.platform == "win32" else 1)
        self.assertEqual(before, self.files())

    def test_event_details_are_detached_from_cached_models(self):
        state = json.loads(self.state_file.read_text())
        state["events"].insert(
            0,
            {
                "id": "synthetic-event-cache",
                "timestamp": "2026-10-28T10:00:00+00:00",
                "card_id": self.card_id,
                "action": "card_updated",
                "details": {"nested": {"value": "ORIGINAL"}},
            },
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        files = self.files()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            first = self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            )
            event = next(item for item in first["events"] if item["id"] == "synthetic-event-cache")
            event["details"]["nested"]["value"] = "MUTATED"
            second = self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            )
            event = next(item for item in second["events"] if item["id"] == "synthetic-event-cache")
            self.assertEqual(event["details"]["nested"]["value"], "ORIGINAL")
            self.assertEqual(read.call_count, 2 if sys.platform == "win32" else 1)
        self.assertEqual(files, self.files())

    def test_atomic_replace_and_service_write_invalidate_snapshot(self):
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"], "CACHE_TITLE_ONE"
        )
        data = self.state_file.read_bytes().replace(b"CACHE_TITLE_ONE", b"CACHE_TITLE_TWO")
        replacement = self.state_file.with_suffix(".new")
        replacement.write_bytes(data)
        replacement.replace(self.state_file)
        before = self.files()
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"], "CACHE_TITLE_TWO"
        )
        self.assertEqual(before, self.files())
        self.service.update_card({"card_id": self.card_id, "title": "CACHE_TITLE_UPDATED"})
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"], "CACHE_TITLE_UPDATED"
        )

    def test_inplace_same_size_restored_mtime_invalidates(self):
        self.service.get_card({"card_id": self.card_id})
        before = self.state_file.stat()
        data = self.state_file.read_bytes().replace(b"CACHE_TITLE_ONE", b"CACHE_TITLE_TWO")
        self.state_file.write_bytes(data)
        os.utime(self.state_file, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = self.state_file.stat()
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
        if sys.platform != "win32":
            self.assertNotEqual(before.st_ctime_ns, after.st_ctime_ns)
        files = self.files()
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"], "CACHE_TITLE_TWO"
        )
        self.assertEqual(files, self.files())

    def test_forced_lock_mode_reads_fresh_without_writes(self):
        assert_fresh_under_file_lock(
            self, lambda: self.service.get_card({"card_id": self.card_id})["card"]["title"]
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows one-byte lock backing file")
    def test_windows_default_lock_is_held_and_missing_lock_fails(self):
        assert_locked_read_and_missing_lock(
            self, lambda: self.service.get_card({"card_id": self.card_id})["card"]["title"]
        )

    def test_forced_lock_is_held_and_missing_lock_fails(self):
        with patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True):
            assert_locked_read_and_missing_lock(
                self, lambda: self.service.get_card({"card_id": self.card_id})["card"]["title"]
            )

    def test_missing_or_corrupt_state_never_returns_cached_data(self):
        self.service.get_card({"card_id": self.card_id})
        self.state_file.unlink()
        files = self.files()
        with self.assertRaises(FileNotFoundError):
            self.service.get_card({"card_id": self.card_id})
        self.assertEqual(files, self.files())
        self.state_file.write_text("{invalid", encoding="utf-8")
        files = self.files()
        with self.assertRaises(StateFileCorruptedError):
            self.service.get_card({"card_id": self.card_id})
        self.assertEqual(files, self.files())

    def test_replacement_during_build_does_not_cache_old_data(self):
        updated = self.state_file.read_bytes().replace(b"CACHE_TITLE_ONE", b"CACHE_TITLE_TWO")
        normalize = self.store._normalize_cards
        replaced = []

        def replace(*args, **kwargs):
            value = normalize(*args, **kwargs)
            if not replaced:
                path = self.state_file.with_suffix(".new")
                path.write_bytes(updated)
                path.replace(self.state_file)
                replaced.append(True)
            return value

        with patch.object(self.store, "_normalize_cards", side_effect=replace):
            self.service.get_card({"card_id": self.card_id})
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"], "CACHE_TITLE_TWO"
        )

    def test_windows_missing_lock_fails_without_creating_one(self):
        self.service.get_card({"card_id": self.card_id})
        lock_path = self.state_file.with_suffix(".lock")
        lock_path.unlink()
        files = self.files()
        with patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True):
            with self.assertRaises(FileNotFoundError):
                self.service.get_card({"card_id": self.card_id})
        self.assertFalse(lock_path.exists())
        self.assertEqual(files, self.files())
