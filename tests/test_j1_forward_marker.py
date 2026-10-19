"""Portable syscall-boundary tests for the Linux-only legacy marker adapter."""

from __future__ import annotations

import copy
import os
import stat
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_io as io
from scripts import j1_forward_marker as marker

PAYLOAD = {
    "schema": marker.PAYLOAD_SCHEMA,
    "attempt_id": "236d5cef-6c87-4bf1-a977-a36e70bdf90b",
    "plan_sha256": "a" * 64,
}


class Node:
    def __init__(self, ino, mode, uid=0, gid=0, content=b""):
        self.dev = 7
        self.ino = ino
        self.mode = mode
        self.uid = uid
        self.gid = gid
        self.content = content
        self.nlink = 1
        self.mtime = self.ctime = 100
        self.entries = {}

    def info(self):
        return SimpleNamespace(
            st_dev=self.dev,
            st_ino=self.ino,
            st_mode=self.mode,
            st_uid=self.uid,
            st_gid=self.gid,
            st_nlink=self.nlink,
            st_size=len(self.content),
            st_mtime_ns=self.mtime,
            st_ctime_ns=self.ctime,
        )


class Filesystem:
    """Model directory descriptors separately from replaceable path names."""

    def __init__(self):
        self.root = Node(1, stat.S_IFDIR | 0o755)
        self.data = Node(2, stat.S_IFDIR | 0o700, 10001, 10001)
        self.root.entries["data"] = self.data
        self.descriptors = {}
        self.next_fd = 10
        self.next_ino = 3
        self.events = []
        self.read_hook = None
        self.open_hook = None
        self.sync_hook = None
        self.write_hook = None
        self.unlink_hook = None
        self.write_limit = None
        self.protected_hook = None

    @property
    def entry(self):
        return self.data.entries.get(marker.NAME)

    def install(self, content=None, **attributes):
        node = Node(
            self.next_ino,
            stat.S_IFREG | 0o600,
            content=io.canonical(PAYLOAD) if content is None else content,
        )
        self.next_ino += 1
        for key, value in attributes.items():
            setattr(node, key, value)
        self.data.entries[marker.NAME] = node
        return node

    def protected(self, path):
        assert path == marker.CRM, "the app-owned data leaf must not use generic protected IO"
        if self.protected_hook:
            self.protected_hook()
        io.require(
            stat.S_ISDIR(self.root.mode) and self.root.uid == 0 and not self.root.mode & 0o022,
            "root_protected_directory_required",
        )
        self.events.append(("protected", path))

    def _node(self, path, dir_fd=None):
        if dir_fd is not None:
            parent = self.descriptors[dir_fd][0]
            return parent.entries[path]
        if path == marker.CRM:
            return self.root
        if path == marker.PARENT:
            return self.root.entries["data"]
        raise AssertionError((path, dir_fd))

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        assert follow_symlinks is False
        try:
            return self._node(path, dir_fd).info()
        except KeyError:
            raise FileNotFoundError(path) from None

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        if self.open_hook:
            self.open_hook(path, flags, dir_fd)
        self.events.append(("open", path, flags, mode, dir_fd))
        assert flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
        if flags & os.O_CREAT:
            assert path == marker.NAME and dir_fd is not None
            assert flags & os.O_EXCL and flags & os.O_WRONLY
            parent = self.descriptors[dir_fd][0]
            if path in parent.entries:
                raise FileExistsError(path)
            node = Node(self.next_ino, stat.S_IFREG | mode)
            self.next_ino += 1
            parent.entries[path] = node
        else:
            try:
                node = self._node(path, dir_fd)
            except KeyError:
                raise FileNotFoundError(path) from None
        if stat.S_ISLNK(node.mode):
            raise OSError("ELOOP")
        if flags & os.O_DIRECTORY and not stat.S_ISDIR(node.mode):
            raise NotADirectoryError(path)
        if path == marker.NAME and not flags & os.O_CREAT:
            assert flags & os.O_NONBLOCK, "replacement FIFO must not block"
        descriptor = self.next_fd
        self.next_fd += 1
        self.descriptors[descriptor] = [node, 0]
        return descriptor

    def fstat(self, descriptor):
        return self.descriptors[descriptor][0].info()

    def read(self, descriptor, limit):
        if self.read_hook:
            hook, self.read_hook = self.read_hook, None
            hook()
        node, offset = self.descriptors[descriptor]
        value = node.content[offset : offset + limit]
        self.descriptors[descriptor][1] += len(value)
        self.events.append(("read", node.ino))
        return value

    def write(self, descriptor, raw):
        if self.write_hook:
            self.write_hook()
        node, offset = self.descriptors[descriptor]
        count = len(raw) if self.write_limit is None else min(self.write_limit, len(raw))
        node.content = node.content[:offset] + raw[:count] + node.content[offset + count :]
        self.descriptors[descriptor][1] += count
        node.mtime += 1
        node.ctime += 1
        self.events.append(("write", node.ino, count))
        return count

    def fsync(self, descriptor):
        node = self.descriptors[descriptor][0]
        self.events.append(("fsync", node.ino))
        if self.sync_hook:
            self.sync_hook(node)

    def close(self, descriptor):
        self.events.append(("close", self.descriptors[descriptor][0].ino))
        del self.descriptors[descriptor]

    def unlink(self, path, *, dir_fd):
        assert path == marker.NAME
        del self.descriptors[dir_fd][0].entries[path]
        self.events.append(("unlink", path))
        if self.unlink_hook:
            self.unlink_hook()


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fs = Filesystem()
        self.stack.enter_context(patch.object(marker, "_native"))
        for index, flag in enumerate(("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")):
            if not hasattr(os, flag):
                self.stack.enter_context(patch.object(os, flag, 1 << (22 + index), create=True))
        self.stack.enter_context(patch.object(io, "protected_directory", self.fs.protected))
        for name in ("stat", "open", "fstat", "read", "write", "fsync", "close", "unlink"):
            self.stack.enter_context(patch.object(marker, "_" + name, getattr(self.fs, name)))
        self.pin = marker.prepare()
        self.fs.events.clear()
        self.authorize = Mock()

    def tearDown(self):
        self.assertEqual(self.fs.descriptors, {}, "all descriptors close on refusal")

    def create(self):
        return marker.ensure(self.pin, PAYLOAD, authorize=self.authorize)

    def remove(self, identity):
        marker.remove_owned(self.pin, PAYLOAD, expected_identity=identity, authorize=self.authorize)

    def assertUnmodified(self, original):
        self.assertIs(self.fs.entry, original)
        self.assertFalse(any(event[0] in ("write", "unlink") for event in self.fs.events))

    def test_prepare_pins_app_owned_leaf_without_writes(self):
        self.assertEqual(
            marker.prepare(),
            {
                "schema": marker.PARENT_SCHEMA,
                "path": marker.PARENT,
                "dev": 7,
                "ino": 2,
                "uid": 10001,
                "gid": 10001,
                "mode": 0o700,
            },
        )
        self.assertFalse(any(event[0] in ("write", "unlink", "fsync") for event in self.fs.events))

    def test_prepare_refuses_every_existing_entry_including_dangling_symlink(self):
        for mode in (stat.S_IFREG, stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR):
            with self.subTest(mode=mode):
                node = self.fs.install(mode=mode | 0o600)
                with self.assertRaisesRegex(io.Refusal, "preexisting_maintenance_marker"):
                    marker.prepare()
                self.assertUnmodified(node)

    def test_prepare_rechecks_absence_after_parent_refresh(self):
        calls = 0

        def create_during_refresh():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.fs.install()

        self.fs.protected_hook = create_during_refresh
        with self.assertRaisesRegex(io.Refusal, "preexisting_maintenance_marker"):
            marker.prepare()

    def test_leaf_owner_group_mode_type_are_exact(self):
        for key, value in (
            ("uid", 0),
            ("gid", 0),
            ("mode", stat.S_IFDIR | 0o750),
            ("mode", stat.S_IFDIR | 0o1700),
            ("mode", stat.S_IFLNK | 0o700),
        ):
            with self.subTest(key=key, value=value):
                old = getattr(self.fs.data, key)
                setattr(self.fs.data, key, value)
                with self.assertRaisesRegex(io.Refusal, "marker_data_directory_invalid"):
                    marker.prepare()
                setattr(self.fs.data, key, old)

    def test_strict_ancestor_protection_is_retained(self):
        self.fs.root.mode = stat.S_IFDIR | 0o777
        with self.assertRaisesRegex(io.Refusal, "root_protected_directory_required"):
            self.create()
        self.assertIsNone(self.fs.entry)

    def test_changed_parent_baseline_refuses_before_creation(self):
        self.fs.data.ino += 1
        with self.assertRaisesRegex(io.Refusal, "marker_parent_changed"):
            self.create()
        self.assertIsNone(self.fs.entry)

    def test_create_is_exclusive_root_private_canonical_and_durable(self):
        identity = self.create()
        self.assertEqual(self.fs.entry.content, io.canonical(PAYLOAD))
        self.assertEqual(
            (self.fs.entry.uid, self.fs.entry.gid, self.fs.entry.mode), (0, 0, stat.S_IFREG | 0o600)
        )
        self.assertEqual(identity["sha256"], io.sha(io.canonical(PAYLOAD)))
        self.assertEqual(identity["parent"], self.pin)
        self.assertEqual(identity["file"]["ino"], self.fs.entry.ino)
        self.assertEqual(self.authorize.call_count, 2)
        self.assertEqual(
            [event[1] for event in self.fs.events if event[0] == "fsync"],
            [self.fs.entry.ino, self.fs.data.ino] * 2,
        )
        self.assertEqual(
            marker.require_owned(self.pin, PAYLOAD, expected_identity=identity), identity
        )

    def test_ensure_matching_marker_is_idempotent_and_authorized(self):
        identity = self.create()
        node = self.fs.entry
        self.fs.events.clear()
        self.authorize.reset_mock()
        self.assertEqual(self.create(), identity)
        self.authorize.assert_called_once_with()
        self.assertUnmodified(node)
        self.assertEqual(
            [event[1] for event in self.fs.events if event[0] == "fsync"],
            [node.ino, self.fs.data.ino],
        )

    def test_idempotent_ensure_refuses_replacement_during_durability_readback(self):
        self.create()

        def replace(node):
            if node is self.fs.data:
                self.fs.install()

        self.fs.sync_hook = replace
        with self.assertRaisesRegex(io.Refusal, "marker_file_changed"):
            self.create()

    def test_first_authority_rejection_has_no_filesystem_open_or_effect(self):
        self.authorize.side_effect = io.Refusal("pending_changed")
        with self.assertRaisesRegex(io.Refusal, "pending_changed"):
            self.create()
        self.assertEqual(self.fs.events, [])
        self.assertIsNone(self.fs.entry)

    def test_second_authority_rejection_retains_created_gate(self):
        self.authorize.side_effect = [None, io.Refusal("pending_changed")]
        with self.assertRaisesRegex(io.Refusal, "pending_changed"):
            self.create()
        self.assertEqual(self.fs.entry.content, b"")
        self.assertFalse(any(event[0] == "unlink" for event in self.fs.events))
        self.assertEqual(
            [event[1] for event in self.fs.events if event[0] == "fsync"],
            [self.fs.entry.ino, self.fs.data.ino],
        )

    def test_empty_gate_is_durable_before_second_authority_or_any_payload_write(self):
        calls = 0

        def authorize():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertEqual(self.fs.entry.content, b"")
                self.assertFalse(any(event[0] == "write" for event in self.fs.events))
                self.assertEqual(
                    [event[1] for event in self.fs.events if event[0] == "fsync"],
                    [self.fs.entry.ino, self.fs.data.ino],
                )

        marker.ensure(self.pin, PAYLOAD, authorize=authorize)
        self.assertEqual(calls, 2)

    def test_existing_foreign_content_is_never_truncated(self):
        node = self.fs.install(b"legacy or foreign marker\n")
        with self.assertRaisesRegex(io.Refusal, "owned_maintenance_marker_changed"):
            self.create()
        self.assertUnmodified(node)
        self.assertEqual(node.content, b"legacy or foreign marker\n")

    def test_existing_symlink_fifo_directory_permissions_and_hardlink_refuse(self):
        for attributes in (
            {"mode": stat.S_IFLNK | 0o600},
            {"mode": stat.S_IFIFO | 0o600},
            {"mode": stat.S_IFDIR | 0o600},
            {"mode": stat.S_IFREG | 0o640},
            {"uid": 10001},
            {"gid": 10001},
            {"nlink": 2},
        ):
            with self.subTest(attributes=attributes):
                self.fs.events.clear()
                node = self.fs.install(**attributes)
                with self.assertRaisesRegex(io.Refusal, "marker_owned_file_required"):
                    self.create()
                self.assertUnmodified(node)
                self.assertFalse(
                    any(event[:2] == ("open", marker.NAME) for event in self.fs.events)
                )

    def test_same_bytes_with_noncanonical_json_refuse(self):
        self.fs.install(io.canonical(PAYLOAD).rstrip(b"\n"))
        with self.assertRaisesRegex(io.Refusal, "owned_maintenance_marker_changed"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_open_race_cannot_overwrite_foreign_entry(self):
        node = None

        def replace_before_create(path, flags, directory):
            nonlocal node
            if flags & os.O_CREAT:
                node = self.fs.install(b"foreign")

        self.fs.open_hook = replace_before_create
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertUnmodified(node)

    def test_symlink_swap_between_stat_and_read_open_is_not_followed(self):
        self.fs.install()

        def swap(path, flags, directory):
            if path == marker.NAME:
                self.fs.install(mode=stat.S_IFLNK | 0o600)

        self.fs.open_hook = swap
        with self.assertRaisesRegex(OSError, "ELOOP"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_fifo_swap_between_stat_and_read_open_does_not_block(self):
        self.fs.install()

        def swap(path, flags, directory):
            if path == marker.NAME:
                self.fs.install(mode=stat.S_IFIFO | 0o600)

        self.fs.open_hook = swap
        with self.assertRaisesRegex(io.Refusal, "marker_owned_file_required"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_same_content_new_inode_during_read_refuses(self):
        self.fs.install()
        self.fs.read_hook = self.fs.install
        with self.assertRaisesRegex(io.Refusal, "marker_file_changed"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_same_inode_metadata_or_link_change_during_read_refuses(self):
        for key in ("mtime", "ctime", "nlink", "uid", "gid", "mode"):
            with self.subTest(key=key):
                node = self.fs.install()
                self.fs.read_hook = lambda: setattr(node, key, getattr(node, key) + 1)
                with self.assertRaisesRegex(io.Refusal, "marker_file_changed"):
                    marker.require_owned(self.pin, PAYLOAD)

    def test_parent_path_replacement_during_read_refuses(self):
        self.fs.install()

        def replace():
            self.fs.root.entries["data"] = Node(99, stat.S_IFDIR | 0o700, 10001, 10001)

        self.fs.read_hook = replace
        with self.assertRaisesRegex(io.Refusal, "marker_parent_changed"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_root_path_replacement_during_read_refuses(self):
        self.fs.install()

        def replace():
            root = Node(99, stat.S_IFDIR | 0o755)
            root.entries["data"] = self.fs.data
            self.fs.root = root

        self.fs.read_hook = replace
        with self.assertRaisesRegex(io.Refusal, "marker_root_directory_changed"):
            marker.require_owned(self.pin, PAYLOAD)

    def test_partial_writes_complete_without_truncating(self):
        self.fs.write_limit = 7
        self.create()
        self.assertEqual(self.fs.entry.content, io.canonical(PAYLOAD))
        self.assertGreater(sum(event[0] == "write" for event in self.fs.events), 1)

    def test_zero_write_retains_marker_and_refuses(self):
        self.fs.write_limit = 0
        with self.assertRaisesRegex(io.Refusal, "marker_write_incomplete"):
            self.create()
        self.assertIsNotNone(self.fs.entry)
        self.assertFalse(any(event[0] == "unlink" for event in self.fs.events))

    def test_write_failure_keeps_partial_gate(self):
        self.fs.write_limit = 7
        count = 0

        def fail_second():
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk write failed")

        self.fs.write_hook = fail_second
        with self.assertRaisesRegex(OSError, "disk write failed"):
            self.create()
        self.assertEqual(self.fs.entry.content, io.canonical(PAYLOAD)[:7])
        self.assertFalse(any(event[0] == "unlink" for event in self.fs.events))

    def test_file_or_directory_fsync_failure_retains_gate(self):
        for fail_number in (1, 2, 3, 4):
            with self.subTest(fail_number=fail_number):
                self.fs.data.entries.clear()
                count = 0

                def fail(node):
                    nonlocal count
                    count += 1
                    if count == fail_number:
                        raise OSError("fsync failed")

                self.fs.sync_hook = fail
                with self.assertRaisesRegex(OSError, "fsync failed"):
                    self.create()
                self.assertEqual(
                    self.fs.entry.content, b"" if fail_number <= 2 else io.canonical(PAYLOAD)
                )
                self.assertFalse(any(event[0] == "unlink" for event in self.fs.events))

    def test_created_inode_replacement_at_fsync_refuses_same_bytes(self):
        def replace(node):
            if node is self.fs.data:
                self.fs.install()

        self.fs.sync_hook = replace
        with self.assertRaisesRegex(io.Refusal, "marker_file_changed"):
            self.create()

    def test_remove_requires_exact_persisted_identity_and_fresh_authority(self):
        identity = self.create()
        self.fs.events.clear()
        self.authorize.reset_mock()
        self.remove(identity)
        self.assertIsNone(self.fs.entry)
        self.assertEqual(self.authorize.call_count, 2)
        self.assertEqual(
            [event for event in self.fs.events if event[0] == "fsync"],
            [("fsync", self.fs.data.ino)],
        )

    def test_remove_rejects_new_inode_with_same_owned_content(self):
        identity = self.create()
        node = self.fs.install()
        self.fs.events.clear()
        with self.assertRaisesRegex(io.Refusal, "marker_owned_identity_changed"):
            self.remove(identity)
        self.assertUnmodified(node)

    def test_remove_first_authority_failure_preserves_marker_without_open(self):
        identity = self.create()
        node = self.fs.entry
        self.fs.events.clear()
        self.authorize.side_effect = io.Refusal("head_changed")
        with self.assertRaisesRegex(io.Refusal, "head_changed"):
            self.remove(identity)
        self.assertEqual(self.fs.events, [])
        self.assertIs(self.fs.entry, node)

    def test_remove_second_authority_failure_preserves_marker(self):
        identity = self.create()
        node = self.fs.entry
        self.fs.events.clear()
        self.authorize.side_effect = [None, io.Refusal("head_changed")]
        with self.assertRaisesRegex(io.Refusal, "head_changed"):
            self.remove(identity)
        self.assertUnmodified(node)

    def test_remove_rechecks_identity_after_last_authority_callback(self):
        identity = self.create()
        calls = 0

        def replace_at_second_authorization():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.fs.install()

        self.authorize.side_effect = replace_at_second_authorization
        self.fs.events.clear()
        with self.assertRaisesRegex(io.Refusal, "marker_file_changed"):
            self.remove(identity)
        self.assertFalse(any(event[0] == "unlink" for event in self.fs.events))

    def test_remove_rechecks_parent_after_last_authority_callback(self):
        identity = self.create()
        calls = 0

        def change_at_second_authorization():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.fs.data.mode = stat.S_IFDIR | 0o750

        self.authorize.side_effect = change_at_second_authorization
        with self.assertRaisesRegex(io.Refusal, "marker_data_directory_invalid"):
            self.remove(identity)
        self.assertIsNotNone(self.fs.entry)

    def test_remove_refuses_reappearance_without_deleting_replacement(self):
        identity = self.create()
        self.fs.unlink_hook = self.fs.install
        with self.assertRaisesRegex(io.Refusal, "marker_reappeared"):
            self.remove(identity)
        self.assertIsNotNone(self.fs.entry)
        self.assertEqual(sum(event[0] == "unlink" for event in self.fs.events), 1)

    def test_missing_marker_refuses_read_and_removal(self):
        with self.assertRaisesRegex(io.Refusal, "owned_maintenance_marker_missing"):
            marker.require_owned(self.pin, PAYLOAD)
        with self.assertRaisesRegex(io.Refusal, "owned_maintenance_marker_missing"):
            self.remove({})

    def test_payload_schema_types_aliases_and_foreign_attempt_refuse(self):
        for mutation in (
            {"attempt_id": True},
            {"attempt_id": "not-a-uuid"},
            {"attempt_id": PAYLOAD["attempt_id"].upper()},
            {"plan_sha256": "A" * 64},
            {"schema": "old"},
            {"extra": 1},
        ):
            with self.subTest(mutation=mutation):
                value = dict(PAYLOAD, **mutation)
                with self.assertRaises(io.Refusal):
                    marker.ensure(self.pin, value, authorize=self.authorize)
                self.assertIsNone(self.fs.entry)
        self.authorize.assert_not_called()

    def test_pin_schema_types_and_path_aliases_refuse(self):
        for mutation in (
            {"dev": True},
            {"ino": 0},
            {"uid": 0},
            {"gid": 0},
            {"path": marker.PARENT + "/."},
            {"schema": "old"},
            {"extra": 1},
        ):
            with self.subTest(mutation=mutation):
                pin = dict(self.pin, **mutation)
                with self.assertRaisesRegex(io.Refusal, "marker_parent_pin_invalid"):
                    marker.ensure(pin, PAYLOAD, authorize=self.authorize)
                self.assertIsNone(self.fs.entry)

    def test_missing_pin_cannot_recapture_parent_via_public_operations(self):
        for pin in (None, [], False):
            with self.subTest(pin=pin):
                with self.assertRaisesRegex(io.Refusal, "marker_parent_pin_invalid"):
                    marker.ensure(pin, PAYLOAD, authorize=self.authorize)
                with self.assertRaisesRegex(io.Refusal, "marker_parent_pin_invalid"):
                    marker.require_owned(pin, PAYLOAD)
                with self.assertRaisesRegex(io.Refusal, "marker_parent_pin_invalid"):
                    marker.remove_owned(
                        pin, PAYLOAD, expected_identity={}, authorize=self.authorize
                    )
        self.authorize.assert_not_called()
        self.assertEqual(self.fs.events, [])

    def test_expected_identity_is_exact_not_loose_numeric_comparison(self):
        identity = self.create()
        changed = copy.deepcopy(identity)
        changed["file"]["nlink"] = True
        with self.assertRaisesRegex(io.Refusal, "marker_owned_identity_changed"):
            marker.require_owned(self.pin, PAYLOAD, expected_identity=changed)
        with self.assertRaisesRegex(io.Refusal, "marker_owned_identity_changed"):
            self.remove(changed)
        self.assertIsNotNone(self.fs.entry)

    def test_authority_callback_is_mandatory_for_mutations(self):
        with self.assertRaisesRegex(io.Refusal, "marker_authority_required"):
            marker.ensure(self.pin, PAYLOAD, authorize=None)
        with self.assertRaisesRegex(io.Refusal, "marker_authority_required"):
            marker.remove_owned(self.pin, PAYLOAD, expected_identity={}, authorize=None)
        self.assertIsNone(self.fs.entry)


class NativeGateTests(unittest.TestCase):
    def test_non_linux_and_nonroot_are_rejected(self):
        for platform, uid, gid in (("win32", 0, 0), ("linux", 10001, 0), ("linux", 0, 10001)):
            with self.subTest(platform=platform, uid=uid, gid=gid):
                with (
                    patch.object(marker.sys, "platform", platform),
                    patch.object(marker.os, "geteuid", return_value=uid, create=True),
                    patch.object(marker.os, "getegid", return_value=gid, create=True),
                ):
                    with self.assertRaisesRegex(io.Refusal, "marker_native_root_required"):
                        marker._native()


if __name__ == "__main__":
    unittest.main()
