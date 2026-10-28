"""Assertions for synthetic snapshots when the existing file lock is required."""

import os
from contextlib import contextmanager
from unittest.mock import call, patch

from minimal_kanban.storage import json_store
from minimal_kanban.storage.file_lock import ProcessFileLock


def assert_locked_read_and_missing_lock(case, read_title):
    # This helper observes the current flag; it does not turn lock mode on.
    case.assertTrue(json_store._SNAPSHOT_REQUIRES_FILE_LOCK)
    lock_path = case.state_file.with_suffix(".lock")
    peer = ProcessFileLock(lock_path, timeout_seconds=0)
    read_state = case.store._read_state

    def read_while_locked(*args, **kwargs):
        with case.assertRaises(TimeoutError):
            with peer.acquire(existing_only=True):
                pass
        return read_state(*args, **kwargs)

    before = case.files()
    case.assertEqual(read_title(), "CACHE_TITLE_ONE")
    case.assertEqual(before, case.files())
    with patch.object(case.store, "_read_state", side_effect=read_while_locked) as read:
        before = case.files()
        case.assertEqual(read_title(), "CACHE_TITLE_ONE")
        case.assertEqual(before, case.files())
        case.assertEqual(read.call_count, 1)

    with peer.acquire(existing_only=True):
        pass
    lock_path.unlink()
    before = case.files()
    with case.assertRaises(FileNotFoundError):
        read_title()
    case.assertFalse(lock_path.exists())
    case.assertEqual(before, case.files())


def assert_fresh_under_file_lock(case, read_title):
    # On POSIX, warm the cache first; lock mode must bypass it on the next read.
    case.assertEqual(read_title(), "CACHE_TITLE_ONE")
    peer = ProcessFileLock(case.state_file.with_suffix(".lock"), timeout_seconds=0)
    acquire = case.store._process_lock.acquire
    read_state = case.store._read_state
    lock_held = []

    @contextmanager
    def observed_acquire(*, existing_only=False):
        case.assertTrue(existing_only)
        with acquire(existing_only=existing_only):
            case.assertFalse(lock_held)
            lock_held.append(True)
            try:
                yield
            finally:
                lock_held.pop()

    def read_while_locked(*args, **kwargs):
        case.assertEqual(lock_held, [True])
        # An independent file handle must be excluded while the read runs.
        with case.assertRaises(TimeoutError):
            with peer.acquire(existing_only=True):
                pass
        return read_state(*args, **kwargs)

    with (
        patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True),
        patch.object(case.store._process_lock, "acquire", side_effect=observed_acquire) as guard,
        patch.object(case.store, "_read_state", side_effect=read_while_locked) as read,
        patch.object(
            case.store,
            "_repair_order_file_signature",
            side_effect=AssertionError("lock mode must not rely on cache signatures"),
        ),
        patch.object(
            case.store, "_write_state", side_effect=AssertionError("unexpected state write")
        ),
    ):
        before_read = case.files()
        case.assertEqual(read_title(), "CACHE_TITLE_ONE")
        case.assertEqual(before_read, case.files())

        before_change = case.state_file.stat()
        data = case.state_file.read_bytes()
        case.assertGreater(data.count(b"CACHE_TITLE_ONE"), 0)
        case.state_file.write_bytes(data.replace(b"CACHE_TITLE_ONE", b"CACHE_TITLE_TWO"))
        os.utime(case.state_file, ns=(before_change.st_atime_ns, before_change.st_mtime_ns))
        after_change = case.state_file.stat()
        case.assertEqual(
            (before_change.st_size, before_change.st_mtime_ns),
            (after_change.st_size, after_change.st_mtime_ns),
        )
        before_read = case.files()
        case.assertEqual(read_title(), "CACHE_TITLE_TWO")
        case.assertEqual(before_read, case.files())
        case.assertEqual(read.call_count, 2)
        case.assertEqual(guard.call_args_list, [call(existing_only=True), call(existing_only=True)])

    case.assertFalse(lock_held)
    # The guard must also be released once each read has returned.
    with peer.acquire(existing_only=True):
        pass
