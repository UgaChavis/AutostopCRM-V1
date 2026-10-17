"""Known-ready controls for the cache publication marker; no production access."""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_kernel_bootstrap as kernel
from tests import test_j1_kernel_bootstrap as fixtures


class CachePendingTests(fixtures.KernelFixture):
    def setUp(self):
        super().setUp()
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        rw = self.private / "rw"
        rw.mkdir(mode=0o700)
        (rw / "research.sqlite3").write_bytes(b"Stat-only fixture; deliberately not SQLite.\n")
        (rw / "research.sqlite3").chmod(0o600)
        code = self.root / "cache-controller.py"
        code.write_bytes(b"# Synthetic identity, never imported.\n")

        def identity(path):
            info = path.stat()
            return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]

        policy = {
            "schema": "autostop.j1.cache-view-policy.v1",
            "controller_sha256": kernel.digest(code.read_bytes()),
            "cache_identity": identity(rw),
            "database_identity": identity(rw / "research.sqlite3"),
        }
        policy_ref = self.put("cache-policy", policy)
        rows = [
            "100 1 1:2 / /var/cache/autostop-j1 ro - ext4 fixture rw",
            "101 1 1:2 / /run/autostop-j1-cache-view-private/rw rw - ext4 fixture rw",
        ]
        mounts = [
            {
                "id": index + 100,
                "parent": 1,
                "root": "/",
                "target": target,
                "options": [options],
                "optional": [],
                "filesystem": "ext4",
                "device": "1:2",
            }
            for index, (target, options) in enumerate(
                [("/var/cache/autostop-j1", "ro"), ("/run/autostop-j1-cache-view-private/rw", "rw")]
            )
        ]
        value = {
            "schema": "autostop.j1.cache-view-state.v1",
            "phase": "ready",
            "boot_id": self.ready["boot_id"],
            "policy_sha256": policy_ref["sha256"],
            "mounts": mounts,
            "cache_identity": policy["cache_identity"],
            "database_identity": policy["database_identity"],
        }
        state_ref = self.put("cache-state", value)
        self.pending = self.root / "preparing-pending.json"
        constants = patch.multiple(
            kernel,
            CACHE_POLICY=Path(policy_ref["path"]),
            CACHE_STATE=Path(state_ref["path"]),
            CACHE_CODE=code,
            CACHE_PENDING=self.pending,
        )
        constants.start()
        self.addCleanup(constants.stop)
        private = self.private

        class PrivateView:
            # Only host/root ownership is substituted, as in the shared fixture.
            def stat(self):
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o700)

            def __truediv__(self, name):
                return private / name

        path = patch.object(
            kernel,
            "Path",
            side_effect=lambda value: (
                PrivateView()
                if str(value) == "/proc/1/root/run/autostop-j1-cache-view-private"
                else Path(value)
            ),
        )
        path.start()
        self.addCleanup(path.stop)
        # Windows has no statvfs; native Linux mount semantics remain external.
        vfs = patch.object(
            kernel.os, "statvfs", return_value=SimpleNamespace(f_flag=0), create=True
        )
        vfs.start()
        self.addCleanup(vfs.stop)
        flag = patch.object(kernel.os, "ST_RDONLY", getattr(os, "ST_RDONLY", 1), create=True)
        flag.start()
        self.addCleanup(flag.stop)
        self.module = SimpleNamespace(
            proc_bytes=Mock(return_value=("\n".join(rows) + "\n").encode())
        )
        self.expected = Path(state_ref["path"]).read_bytes()

    def observe(self):
        return kernel.cache_view(self.module, self.ready["boot_id"])

    def test_enoent_absence_accepts_otherwise_known_ready(self):
        self.assertEqual(self.expected, self.observe())

    def test_present_marker_denies_otherwise_known_ready(self):
        self.pending.write_bytes(b"pending")
        with self.assertRaisesRegex(kernel.KernelError, "cache_view_pending"):
            self.observe()

    def lookup_failure(self, error):
        original_lstat, original_stat = os.lstat, os.stat

        def failing(original):
            def read(path, *args, **kwargs):
                if (
                    isinstance(path, (str, bytes, os.PathLike))
                    and Path(os.fspath(path)) == self.pending
                ):
                    raise error
                return original(path, *args, **kwargs)

            return read

        @contextmanager
        def failure():
            # pathlib.lstat uses stat(follow_symlinks=False); lexists uses lstat.
            with (
                patch.object(kernel.os, "lstat", side_effect=failing(original_lstat)),
                patch.object(kernel.os, "stat", side_effect=failing(original_stat)),
            ):
                yield

        return failure()

    def test_eio_lookup_is_unknown_and_never_accepts_visible_ready(self):
        with self.lookup_failure(OSError(errno.EIO, "injected marker lookup failure")):
            with self.assertRaises(OSError):
                self.observe()

    def test_permission_failure_is_unknown_and_never_accepts_visible_ready(self):
        with self.lookup_failure(
            PermissionError(errno.EACCES, "injected marker permission failure")
        ):
            with self.assertRaises(PermissionError):
                self.observe()
