from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.storage.limited_io import (  # noqa: E402
    copy_file_limited,
    is_regular_file,
    read_bytes_limited,
    read_text_limited,
)


class LimitedIoTests(unittest.TestCase):
    def test_read_uses_small_bounded_requests_even_with_a_large_safety_ceiling(self) -> None:
        payload = b"x" * (2 * 64 * 1024 + 17)
        stream = io.BytesIO(payload)
        read = Mock(wraps=stream.read)
        stream.read = read
        path = Mock(spec=Path)
        path.stat.return_value = SimpleNamespace(st_size=len(payload))
        path.open.return_value = stream

        self.assertEqual(
            read_bytes_limited(path, max_bytes=100 * 1024 * 1024, label="state"), payload
        )
        self.assertGreaterEqual(read.call_count, 4)
        for call in read.call_args_list:
            self.assertGreater(call.args[0], 0)
            self.assertLessEqual(call.args[0], 64 * 1024)

    def test_read_accepts_the_exact_limit_and_handles_truncation_after_stat(self) -> None:
        for payload in (b"", b"abc", b"x" * (64 * 1024 + 1)):
            with self.subTest(bytes=len(payload)):
                path = Mock(spec=Path)
                path.stat.return_value = SimpleNamespace(st_size=len(payload))
                path.open.return_value = io.BytesIO(payload)
                self.assertEqual(
                    read_bytes_limited(path, max_bytes=len(payload), label="state"), payload
                )
        path = Mock(spec=Path)
        path.stat.return_value = SimpleNamespace(st_size=8)
        path.open.return_value = io.BytesIO(b"abc")
        self.assertEqual(read_bytes_limited(path, max_bytes=8, label="state"), b"abc")

    def test_read_detects_one_extra_byte_after_several_chunks(self) -> None:
        limit = 2 * 64 * 1024
        path = Mock(spec=Path)
        path.stat.return_value = SimpleNamespace(st_size=limit)
        path.open.return_value = io.BytesIO(b"x" * (limit + 1))
        with self.assertRaisesRegex(ValueError, "state is too large"):
            read_bytes_limited(path, max_bytes=limit, label="state")

    def test_read_preserves_utf8_across_chunk_boundaries_and_rejects_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            text = "x" * (64 * 1024 - 1) + "я"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(read_text_limited(path, max_bytes=100 * 1024, label="state"), text)
            with self.assertRaises((IsADirectoryError, PermissionError)):
                read_bytes_limited(root, max_bytes=100 * 1024, label="state")

    def test_regular_file_predicate_rejects_links_directories_and_stat_errors(self) -> None:
        for symlink, regular, expected in (
            (False, True, True),
            (False, False, False),
            (True, True, False),
        ):
            path = Mock(spec=Path)
            path.is_symlink.return_value = symlink
            path.is_file.return_value = regular
            self.assertIs(is_regular_file(path), expected)
            if symlink:
                path.is_file.assert_not_called()
        for method in ("is_symlink", "is_file"):
            path = Mock(spec=Path)
            path.is_symlink.return_value = False
            getattr(path, method).side_effect = OSError("injected")
            self.assertFalse(is_regular_file(path))

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
