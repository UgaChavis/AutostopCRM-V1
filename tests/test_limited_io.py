from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.storage.limited_io import (  # noqa: E402
    copy_file_limited,
    read_bytes_limited,
    read_text_limited,
)


class LimitedIoTests(unittest.TestCase):
    def test_read_text_limited_reads_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"ok": true}', encoding="utf-8")

            self.assertEqual(
                read_text_limited(path, max_bytes=64, label="state"),
                '{"ok": true}',
            )

    def test_read_text_limited_rejects_payload_that_grows_after_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text("x" * 16, encoding="utf-8")

            with (
                patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
                self.assertRaisesRegex(ValueError, "state is too large"),
            ):
                read_text_limited(path, max_bytes=8, label="state")

    def test_read_text_limited_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_bytes(b"\xff")

            with self.assertRaises(UnicodeDecodeError):
                read_text_limited(path, max_bytes=8, label="state")

    def test_read_bytes_limited_rejects_invalid_max_bytes_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.bin"
            path.write_bytes(b"x")

            for max_bytes in (True, 1.5, float("inf"), "8"):
                with (
                    self.subTest(max_bytes=max_bytes),
                    self.assertRaisesRegex(ValueError, "payload max bytes must be an integer"),
                ):
                    read_bytes_limited(path, max_bytes=max_bytes, label="payload")  # type: ignore[arg-type]

            with self.assertRaisesRegex(ValueError, "payload max bytes is too large"):
                read_bytes_limited(path, max_bytes=1e308, label="payload")  # type: ignore[arg-type]

    def test_read_bytes_limited_rejects_payload_that_grows_after_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.bin"
            path.write_bytes(b"x" * 16)

            with (
                patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
                self.assertRaisesRegex(ValueError, "payload is too large"),
            ):
                read_bytes_limited(path, max_bytes=8, label="payload")

    def test_copy_file_limited_rejects_payload_that_grows_after_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.bin"
            target = Path(temp_dir) / "target.bin"
            source.write_bytes(b"x" * 16)

            with (
                patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
                self.assertRaisesRegex(ValueError, "payload is too large"),
            ):
                copy_file_limited(source, target, max_bytes=8, label="payload")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp_dir).glob(".target.bin.*.tmp")), [])

    def test_copy_file_limited_rejects_invalid_max_bytes_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.bin"
            target = Path(temp_dir) / "target.bin"
            source.write_bytes(b"x")

            for max_bytes in (True, 1.5, float("inf"), "8"):
                with (
                    self.subTest(max_bytes=max_bytes),
                    self.assertRaisesRegex(ValueError, "payload max bytes must be an integer"),
                ):
                    copy_file_limited(source, target, max_bytes=max_bytes, label="payload")  # type: ignore[arg-type]

    def test_copy_file_limited_preserves_existing_target_after_late_limit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.bin"
            target = Path(temp_dir) / "target.bin"
            source.write_bytes(b"0123456789")
            target.write_bytes(b"existing")

            with (
                patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
                patch("minimal_kanban.storage.limited_io._COPY_CHUNK_BYTES", 4),
                self.assertRaisesRegex(ValueError, "payload is too large"),
            ):
                copy_file_limited(source, target, max_bytes=6, label="payload")

            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(list(Path(temp_dir).glob(".target.bin.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
