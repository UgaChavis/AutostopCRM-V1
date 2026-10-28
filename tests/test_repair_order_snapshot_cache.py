from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError
from tests.snapshot_lock_helpers import assert_fresh_under_file_lock


class RepairOrderSnapshotCacheTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="m2-cache-appdata-")
        self.addCleanup(self.app_data.cleanup)
        env = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "CACHE_TITLE_ONE"})["card"]["id"]
        self.service.update_card(
            {"card_id": self.card_id, "repair_order": {"client": "SYNTHETIC_CLIENT"}}
        )

    def files(self):
        roots = {"state": Path(self.temp_dir.name), "appdata": Path(self.app_data.name)}
        return {
            (label, str(path.relative_to(root))): path.read_bytes()
            for label, root in roots.items()
            for path in root.rglob("*")
            if path.is_file()
        }

    def title(self, cards):
        return next(card.title for card in cards if card.id == self.card_id)

    def changed_bytes(self):
        data = self.state_file.read_bytes()
        self.assertGreaterEqual(data.count(b"CACHE_TITLE_ONE"), 1)
        return data.replace(b"CACHE_TITLE_ONE", b"CACHE_TITLE_TWO")

    def test_snapshot_cache_repeat_and_object_isolation(self):
        before = self.files()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            first = self.store.read_repair_order_cards_snapshot()
            next(card for card in first if card.id == self.card_id).title = "MUTATED_BY_CALLER"
            second = self.store.read_repair_order_cards_snapshot()
            self.assertEqual(self.title(second), "CACHE_TITLE_ONE")
            self.assertEqual(read.call_count, 2 if sys.platform == "win32" else 1)
        self.assertEqual(before, self.files())

    def test_snapshot_cache_external_atomic_replace_invalidates(self):
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_ONE"
        )
        path = self.state_file.with_suffix(".new")
        path.write_bytes(self.changed_bytes())
        path.replace(self.state_file)
        before = self.files()
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_TWO"
        )
        self.assertEqual(before, self.files())

    def test_snapshot_cache_inplace_same_size_restored_mtime_invalidates(self):
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_ONE"
        )
        old = self.state_file.stat()
        changed = self.changed_bytes()
        self.state_file.write_bytes(changed)
        os.utime(self.state_file, ns=(old.st_atime_ns, old.st_mtime_ns))
        after = self.state_file.stat()
        self.assertEqual(old.st_size, after.st_size)
        self.assertEqual(old.st_mtime_ns, after.st_mtime_ns)
        if sys.platform != "win32":
            self.assertNotEqual(old.st_ctime_ns, after.st_ctime_ns)
        before = self.files()
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_TWO"
        )
        self.assertEqual(before, self.files())

    def test_forced_lock_mode_reads_fresh_without_writes(self):
        assert_fresh_under_file_lock(
            self, lambda: self.title(self.store.read_repair_order_cards_snapshot())
        )

    def test_snapshot_cache_missing_and_corrupt_never_returns_cached_data(self):
        self.store.read_repair_order_cards_snapshot()
        self.state_file.unlink()
        before = self.files()
        with self.assertRaises(FileNotFoundError):
            self.store.read_repair_order_cards_snapshot()
        self.assertEqual(before, self.files())
        self.state_file.write_bytes(b"{invalid")
        before = self.files()
        with self.assertRaises(StateFileCorruptedError):
            self.store.read_repair_order_cards_snapshot()
        self.assertEqual(before, self.files())

    def test_snapshot_cache_replacement_during_normalize_not_cached(self):
        new_state = self.changed_bytes()
        normalize = self.store._normalize_cards
        replaced = []

        def replace_during_normalize(*args, **kwargs):
            value = normalize(*args, **kwargs)
            if not replaced:
                path = self.state_file.with_suffix(".new")
                path.write_bytes(new_state)
                path.replace(self.state_file)
                replaced.append(True)
            return value

        with patch.object(self.store, "_normalize_cards", side_effect=replace_during_normalize):
            try:
                self.store.read_repair_order_cards_snapshot()
            except (OSError, RuntimeError):
                pass
        before = self.files()
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_TWO"
        )
        self.assertEqual(before, self.files())

    def test_snapshot_cache_windows_missing_lock_fails_even_when_warm(self):
        self.store.read_repair_order_cards_snapshot()
        lock_path = self.state_file.with_suffix(".lock")
        lock_path.unlink()
        before = self.files()
        with patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True):
            with self.assertRaises(FileNotFoundError):
                self.store.read_repair_order_cards_snapshot()
        self.assertFalse(lock_path.exists())
        self.assertEqual(before, self.files())

    def test_snapshot_cache_service_write_invalidates_cached_snapshot(self):
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_ONE"
        )
        self.service.update_card({"card_id": self.card_id, "title": "CACHE_TITLE_UPDATED"})
        before = self.files()
        self.assertEqual(
            self.title(self.store.read_repair_order_cards_snapshot()), "CACHE_TITLE_UPDATED"
        )
        self.assertEqual(before, self.files())
