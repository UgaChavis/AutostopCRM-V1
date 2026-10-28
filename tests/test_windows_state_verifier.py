from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage import json_store, windows_state_verifier
from minimal_kanban.storage.file_lock import ProcessFileLock


class VerifiedWindowsFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-windows-state-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.payload = b'{"synthetic": "alpha"}'
        self.path.write_bytes(self.payload)

    def test_exact_bytes_and_replacement_are_checked(self):
        with patch.object(
            windows_state_verifier, "_open_read_without_writers", side_effect=lambda p: p.open("rb")
        ):
            with windows_state_verifier.verified_committed_file(
                self.path, self.payload
            ) as verified:
                self.assertIsNotNone(verified)
                self.assertTrue(verified.current())
                if sys.platform != "win32":
                    replacement = self.path.with_suffix(".new")
                    replacement.write_bytes(self.payload)
                    replacement.replace(self.path)
                    self.assertFalse(verified.current())
            with windows_state_verifier.verified_committed_file(
                self.path, b"Z" * len(self.payload)
            ) as missing:
                self.assertIsNone(missing)

    def test_unavailable_verifier_is_a_cache_miss(self):
        with patch.object(
            windows_state_verifier, "_open_read_without_writers", side_effect=OSError
        ):
            with windows_state_verifier.verified_committed_file(
                self.path, self.payload
            ) as verified:
                self.assertIsNone(verified)

    def test_path_creation_time_does_not_override_file_identity(self):
        current = self.path.stat()
        path_stat = SimpleNamespace(
            st_dev=current.st_dev,
            st_ino=current.st_ino,
            st_size=current.st_size,
            st_mtime_ns=current.st_mtime_ns,
            st_ctime_ns=current.st_ctime_ns + 1,
        )
        with (
            patch.object(
                windows_state_verifier,
                "_open_read_without_writers",
                side_effect=lambda path: path.open("rb"),
            ),
            patch.object(type(self.path), "stat", return_value=path_stat),
        ):
            with windows_state_verifier.verified_committed_file(
                self.path, self.payload
            ) as verified:
                self.assertIsNotNone(verified)
                self.assertTrue(verified.current())

    def test_mapping_failure_closes_open_handle(self):
        opened = []

        def open_file(path):
            handle = path.open("rb")
            opened.append(handle)
            return handle

        with patch.object(windows_state_verifier, "_open_read_without_writers", open_file):
            with patch.object(windows_state_verifier.mmap, "mmap", side_effect=MemoryError):
                with windows_state_verifier.verified_committed_file(
                    self.path, self.payload
                ) as value:
                    self.assertIsNone(value)
            self.assertTrue(opened[-1].closed)
            with patch.object(windows_state_verifier.mmap, "mmap", side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    with windows_state_verifier.verified_committed_file(self.path, self.payload):
                        pass
            self.assertTrue(opened[-1].closed)

    @unittest.skipUnless(sys.platform == "win32", "Windows share modes")
    def test_native_handle_excludes_writes_and_rename(self):
        with windows_state_verifier.verified_committed_file(self.path, self.payload) as verified:
            self.assertIsNotNone(verified)
            with self.assertRaises(OSError):
                fd = os.open(self.path, os.O_WRONLY | os.O_BINARY)
                os.close(fd)
            with self.assertRaises(OSError):
                self.path.replace(self.path.with_suffix(".moved"))
            self.assertTrue(verified.current())
        self.assertEqual(self.path.read_bytes(), self.payload)


class WindowsCommittedCardLockTests(CardServiceCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="synthetic-windows-card-")
        self.addCleanup(self.appdata.cleanup)
        env = patch.dict(os.environ, {"APPDATA": self.appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "SYNTHETIC_WINDOWS_CARD"})["card"]["id"]

    def _assert_verified_read_holds_lock(self):
        cached = self.store._committed_card_source
        self.assertIsNotNone(cached)
        peer = ProcessFileLock(self.state_file.with_suffix(".lock"), timeout_seconds=0)
        original = cached[2].for_card

        def while_locked(*args):
            with self.assertRaises(TimeoutError):
                with peer.acquire(existing_only=True):
                    pass
            return original(*args)

        with (
            patch.object(cached[2], "for_card", side_effect=while_locked) as source,
            patch.object(
                self.store, "_read_state", side_effect=AssertionError("unexpected reread")
            ),
        ):
            card = self.service.get_card({"card_id": self.card_id})["card"]
        self.assertEqual(card["title"], "SYNTHETIC_WINDOWS_CARD")
        source.assert_called_once()
        self.state_file.with_suffix(".lock").unlink()
        with self.assertRaises(FileNotFoundError):
            self.service.get_card({"card_id": self.card_id})
        self.assertFalse(self.state_file.with_suffix(".lock").exists())

    def test_simulated_windows_read_holds_existing_lock(self):
        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(json_store.sys, "platform", "win32"),
            patch.object(
                windows_state_verifier,
                "_open_read_without_writers",
                side_effect=lambda path: path.open("rb"),
            ),
        ):
            self._assert_verified_read_holds_lock()

    def test_same_size_and_mtime_edit_uses_fresh_locked_snapshot(self):
        before = self.state_file.stat()
        changed = self.state_file.read_bytes().replace(
            b"SYNTHETIC_WINDOWS_CARD", b"SYNTHETIC_WINDOWS_EDIT"
        )
        self.assertEqual(len(changed), before.st_size)
        self.state_file.write_bytes(changed)
        os.utime(self.state_file, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.state_file.stat().st_mtime_ns, before.st_mtime_ns)
        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(json_store.sys, "platform", "win32"),
            patch.object(
                windows_state_verifier,
                "_open_read_without_writers",
                side_effect=lambda path: path.open("rb"),
            ),
            patch.object(
                self.store,
                "_repair_order_file_signature",
                side_effect=AssertionError("Windows read must not trust timestamps"),
            ),
        ):
            card = self.service.get_card({"card_id": self.card_id})["card"]
        self.assertEqual(card["title"], "SYNTHETIC_WINDOWS_EDIT")
        self.assertIsNone(self.store._committed_card_source)
        self.assertEqual(self.state_file.read_bytes(), changed)

    def test_mapping_memory_error_falls_back_to_locked_read(self):
        opened = []

        def open_file(path):
            handle = path.open("rb")
            opened.append(handle)
            return handle

        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(json_store.sys, "platform", "win32"),
            patch.object(windows_state_verifier, "_open_read_without_writers", open_file),
            patch.object(windows_state_verifier.mmap, "mmap", side_effect=MemoryError),
        ):
            card = self.service.get_card({"card_id": self.card_id})["card"]
        self.assertEqual(card["title"], "SYNTHETIC_WINDOWS_CARD")
        self.assertIsNone(self.store._committed_card_source)
        self.assertTrue(opened and opened[0].closed)

    def test_unavailable_verifier_keeps_detached_responses(self):
        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(json_store.sys, "platform", "win32"),
            patch.object(windows_state_verifier, "_open_read_without_writers", side_effect=OSError),
            patch.object(self.store, "_read_state", wraps=self.store._read_state) as read,
        ):
            first = self.service.get_card({"card_id": self.card_id})
            first["card"]["title"] = "SYNTHETIC_CALLER_EDIT"
            second = self.service.get_card({"card_id": self.card_id})
        self.assertEqual(second["card"]["title"], "SYNTHETIC_WINDOWS_CARD")
        self.assertEqual(read.call_count, 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows share modes")
    def test_native_windows_read_holds_existing_lock(self):
        self._assert_verified_read_holds_lock()
