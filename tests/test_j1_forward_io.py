"""Portable metadata comparisons for protected file reads."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts import j1_forward_io as io


def metadata(**changes):
    return SimpleNamespace(
        **(
            {
                "st_dev": 7,
                "st_ino": 19,
                "st_size": 42,
                "st_mtime_ns": 100,
                "st_ctime_ns": 120,
                "st_birthtime_ns": 80,
            }
            | changes
        )
    )


class ProtectedReadMetadataTests(unittest.TestCase):
    def test_windows_path_and_fd_ctime_have_distinct_stable_meanings(self):
        path = metadata(st_ctime_ns=80)
        descriptor = metadata(st_ctime_ns=120)
        self.assertTrue(io.unchanged_read(path, descriptor, descriptor, path, windows=True))
        self.assertFalse(io.unchanged_read(path, descriptor, descriptor, path, windows=False))

    def test_both_windows_change_time_branches_must_remain_stable(self):
        path, descriptor = metadata(st_ctime_ns=80), metadata(st_ctime_ns=120)
        for position, replacement in (
            (2, metadata(st_ctime_ns=121)),
            (3, metadata(st_ctime_ns=81)),
        ):
            values = [path, descriptor, descriptor, path]
            values[position] = replacement
            with self.subTest(position=position):
                self.assertFalse(io.unchanged_read(*values, windows=True))

    def test_common_windows_identity_and_content_metadata_cannot_change(self):
        path, descriptor = metadata(st_ctime_ns=80), metadata(st_ctime_ns=120)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_birthtime_ns"):
            for position in range(4):
                values = [path, descriptor, descriptor, path]
                values[position] = metadata(
                    st_ctime_ns=80 if position in (0, 3) else 120,
                    **{field: getattr(path, field) + 1},
                )
                with self.subTest(field=field, position=position):
                    self.assertFalse(io.unchanged_read(*values, windows=True))

    def test_linux_all_metadata_branches_remain_equal(self):
        value = metadata()
        self.assertTrue(io.unchanged_read(value, value, value, value, windows=False))
        for position in range(4):
            values = [value] * 4
            values[position] = metadata(st_ctime_ns=121)
            with self.subTest(position=position):
                self.assertFalse(io.unchanged_read(*values, windows=False))


if __name__ == "__main__":
    unittest.main()
