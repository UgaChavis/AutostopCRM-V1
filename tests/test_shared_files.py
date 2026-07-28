# ruff: noqa: E402
from __future__ import annotations

import base64
import http.client
import json
import logging
import math
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.services.shared_files_service import (
    SHARED_FILES_MAX_UPLOAD_BYTES,
    SHARED_FILES_STORAGE_LIMIT_BYTES,
    SharedFile,
    SharedFilesService,
)
from minimal_kanban.storage.json_store import JsonStore


def b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


class SharedFilesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.shared_files.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=128,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_upload_shared_file_does_not_leave_partial_file_when_write_fails(self) -> None:
        original_write_bytes = Path.write_bytes

        def partial_temp_write(path: Path, data: bytes) -> int:
            original_write_bytes(path, b"partial")
            raise OSError("disk full")

        with (
            patch.object(Path, "write_bytes", partial_temp_write),
            self.assertRaises(OSError),
        ):
            self.service.upload_shared_file(
                {"file_name": "partial.txt", "content_base64": b64(b"payload")}
            )

        self.assertEqual(self.service.list_shared_files({})["files"], [])
        stored_files = [
            path for path in (self.base_dir / "shared-files").glob("*") if path.is_file()
        ]
        self.assertEqual(stored_files, [])

    def test_upload_shared_file_rejects_oversized_index_without_orphan_file(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "first.txt", "content_base64": b64(b"payload")}
        )
        original_index = self.service.index_file.read_text(encoding="utf-8")
        original_files = {
            path.name for path in (self.base_dir / "shared-files").glob("*") if path.is_file()
        }

        with patch(
            "minimal_kanban.services.shared_files_service.SHARED_FILES_INDEX_MAX_BYTES",
            len(original_index.encode("utf-8")) + 20,
        ):
            with self.assertRaises(ServiceError) as oversized:
                self.service.upload_shared_file(
                    {
                        "file_name": ("second-" + ("x" * 120) + ".txt"),
                        "content_base64": b64(b"x"),
                    }
                )

        self.assertEqual(oversized.exception.code, "storage_limit_exceeded")
        self.assertEqual(self.service.index_file.read_text(encoding="utf-8"), original_index)
        self.assertEqual(
            {path.name for path in (self.base_dir / "shared-files").glob("*") if path.is_file()},
            original_files,
        )
        self.assertEqual(
            self.service.list_shared_files({})["files"][0]["id"], uploaded["file"]["id"]
        )

    def test_storage_regular_file_check_rejects_symlink_before_file_stat(self) -> None:
        with (
            patch.object(Path, "is_symlink", return_value=True),
            patch.object(
                Path, "is_file", side_effect=AssertionError("must not stat symlink target")
            ),
        ):
            self.assertFalse(self.service._storage_is_regular_file(Path("stored.txt")))

    def test_upload_list_download_rename_copy_position_and_delete_roundtrip(self) -> None:
        uploaded = self.service.upload_shared_file(
            {
                "file_name": r"..\docs\Invoice 01.pdf",
                "mime_type": "application/pdf",
                "content_base64": b64(b"%PDF shared invoice"),
                "x": 24,
                "y": 48,
                "actor_name": "tester",
                "source": "ui",
            }
        )
        file_id = uploaded["file"]["id"]
        self.assertEqual(uploaded["file"]["original_name"], "Invoice 01.pdf")
        self.assertEqual(uploaded["file"]["extension"], ".pdf")
        self.assertEqual(uploaded["storage"]["used_bytes"], len(b"%PDF shared invoice"))

        listed = self.service.list_shared_files({})
        self.assertEqual([item["id"] for item in listed["files"]], [file_id])
        self.assertEqual(listed["storage"]["limit_bytes"], 128)

        path, file_meta = self.service.get_shared_file_download(file_id)
        self.assertEqual(path.read_bytes(), b"%PDF shared invoice")
        self.assertEqual(file_meta["id"], file_id)

        renamed = self.service.rename_shared_file(
            {"file_id": file_id, "file_name": "Invoice final.pdf", "actor_name": "tester"}
        )
        self.assertEqual(renamed["file"]["original_name"], "Invoice final.pdf")

        copied = self.service.copy_shared_file({"file_id": file_id})
        pasted = self.service.paste_shared_file(
            {"source_id": copied["clipboard"]["source_id"], "x": 96, "y": 120}
        )
        self.assertNotEqual(pasted["file"]["id"], file_id)
        self.assertEqual(pasted["file"]["source_id"], file_id)
        self.assertEqual(pasted["file"]["x"], 96)
        self.assertEqual(pasted["file"]["y"], 120)
        self.assertTrue(pasted["file"]["original_name"].endswith(".pdf"))

        moved = self.service.update_shared_file_position({"file_id": file_id, "x": 220, "y": 140})
        self.assertEqual(moved["file"]["x"], 220)
        self.assertEqual(moved["file"]["y"], 140)

        deleted = self.service.delete_shared_file({"file_id": file_id, "actor_name": "tester"})
        self.assertTrue(deleted["deleted"])
        remaining_ids = [item["id"] for item in self.service.list_shared_files({})["files"]]
        self.assertEqual(remaining_ids, [pasted["file"]["id"]])
        self.assertFalse(
            (self.base_dir / "shared-files" / uploaded["file"]["stored_name"]).exists()
        )

    def test_delete_shared_file_rejects_stale_revision(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "revision.pdf", "content_base64": b64(b"%PDF revision")}
        )
        file_id = uploaded["file"]["id"]

        with self.assertRaises(ServiceError) as conflict:
            self.service.delete_shared_file(
                {
                    "file_id": file_id,
                    "expected_updated_at": "2000-01-01T00:00:00+00:00",
                }
            )

        self.assertEqual(conflict.exception.code, "shared_file_update_conflict")
        self.assertEqual(
            self.service.get_shared_file_info({"file_id": file_id})["file"]["id"],
            file_id,
        )

    def test_fetch_and_download_treat_symlink_as_missing_file(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "link.txt", "content_base64": b64(b"stored")}
        )
        file_id = uploaded["file"]["id"]
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        target_file = self.base_dir / "outside-target.txt"
        target_file.write_bytes(b"outside")
        stored_path.unlink()
        try:
            stored_path.symlink_to(target_file)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")

        listed = self.service.list_shared_files({})
        self.assertFalse(listed["files"][0]["exists_on_disk"])
        with self.assertRaises(ServiceError) as fetch_error:
            self.service.fetch_shared_file({"file_id": file_id})
        self.assertEqual(fetch_error.exception.code, "not_found")
        with self.assertRaises(ServiceError) as download_error:
            self.service.get_shared_file_download(file_id)
        self.assertEqual(download_error.exception.code, "not_found")

    def test_delete_shared_file_removes_index_when_storage_path_is_directory(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "directory.txt", "content_base64": b64(b"stored")}
        )
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        stored_path.unlink()
        stored_path.mkdir()

        deleted = self.service.delete_shared_file({"file_id": uploaded["file"]["id"]})

        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.service.list_shared_files({})["files"], [])
        self.assertTrue(stored_path.is_dir())

    def test_delete_shared_file_unlinks_symlink_without_touching_target(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "delete-link.txt", "content_base64": b64(b"stored")}
        )
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        target_file = self.base_dir / "delete-target.txt"
        target_file.write_bytes(b"outside")
        stored_path.unlink()
        try:
            stored_path.symlink_to(target_file)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")

        deleted = self.service.delete_shared_file({"file_id": uploaded["file"]["id"]})

        self.assertTrue(deleted["deleted"])
        self.assertFalse(stored_path.exists())
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), b"outside")

    def test_paste_shared_file_does_not_leave_partial_copy_when_copy_fails(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "source.txt", "content_base64": b64(b"payload")}
        )

        def partial_copy(source: Path, target: Path, *, max_bytes: int, label: str) -> int:
            _ = source
            _ = (max_bytes, label)
            Path(target).write_bytes(b"partial")
            raise OSError("disk full")

        with (
            patch("minimal_kanban.services.shared_files_service.copy_file_limited", partial_copy),
            self.assertRaises(OSError),
        ):
            self.service.paste_shared_file({"source_id": uploaded["file"]["id"]})

        listed = self.service.list_shared_files({})
        self.assertEqual([item["id"] for item in listed["files"]], [uploaded["file"]["id"]])
        stored_files = sorted(
            path.name for path in (self.base_dir / "shared-files").glob("*") if path.is_file()
        )
        self.assertEqual(stored_files, [uploaded["file"]["stored_name"]])

    def test_rejects_executables_and_enforces_total_storage_limit(self) -> None:
        with self.assertRaises(ServiceError) as executable_error:
            self.service.upload_shared_file(
                {"file_name": "tool.ps1", "content_base64": b64(b"Write-Host nope")}
            )
        self.assertEqual(executable_error.exception.code, "validation_error")

        uploaded = self.service.upload_shared_file(
            {"file_name": "big.pdf", "content_base64": b64(b"a" * 100)}
        )
        self.assertEqual(uploaded["storage"]["used_bytes"], 100)
        with self.assertRaises(ServiceError) as limit_error:
            self.service.upload_shared_file(
                {"file_name": "too-big.pdf", "content_base64": b64(b"b" * 40)}
            )
        self.assertEqual(limit_error.exception.code, "storage_limit_exceeded")

        with self.assertRaises(ServiceError) as copy_limit_error:
            self.service.paste_shared_file({"source_id": uploaded["file"]["id"]})
        self.assertEqual(copy_limit_error.exception.code, "storage_limit_exceeded")

    def test_upload_rejects_files_larger_than_single_upload_limit_before_store_capacity(
        self,
    ) -> None:
        limited = SharedFilesService(
            storage_dir=self.base_dir / "limited-shared-files",
            index_file=self.base_dir / "limited_shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=128,
            max_upload_bytes=4,
        )

        with self.assertRaises(ServiceError) as oversized:
            limited.upload_shared_file(
                {"file_name": "too-large.txt", "content_base64": b64(b"12345")}
            )

        self.assertEqual(oversized.exception.status_code, 413)
        self.assertEqual(oversized.exception.code, "upload_too_large")
        self.assertEqual(oversized.exception.details["max_size_bytes"], 4)
        self.assertEqual(limited.list_shared_files({})["files"], [])

    def test_upload_from_local_path_rejects_files_larger_than_single_upload_limit(self) -> None:
        limited = SharedFilesService(
            storage_dir=self.base_dir / "limited-local-shared-files",
            index_file=self.base_dir / "limited_local_shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=128,
            max_upload_bytes=4,
        )
        source = self.base_dir / "clipboard-large.txt"
        source.write_bytes(b"12345")

        with self.assertRaises(ServiceError) as oversized:
            limited.upload_shared_file_from_local_path({"path": str(source)})

        self.assertEqual(oversized.exception.status_code, 413)
        self.assertEqual(oversized.exception.code, "upload_too_large")
        self.assertEqual(oversized.exception.details["field"], "path")
        self.assertEqual(limited.list_shared_files({})["files"], [])

    def test_upload_shared_file_from_local_path_uses_existing_storage_rules(self) -> None:
        source = self.base_dir / "Clipboard Invoice.pdf"
        source.write_bytes(b"clipboard invoice body")

        uploaded = self.service.upload_shared_file_from_local_path(
            {"path": str(source), "x": 32, "y": 64, "actor_name": "tester", "source": "ui"}
        )

        file_meta = uploaded["file"]
        self.assertEqual(file_meta["original_name"], "Clipboard Invoice.pdf")
        self.assertEqual(file_meta["extension"], ".pdf")
        self.assertEqual(file_meta["x"], 32)
        self.assertEqual(file_meta["y"], 64)
        stored_path, _ = self.service.get_shared_file_download(file_meta["id"])
        self.assertEqual(stored_path.read_bytes(), b"clipboard invoice body")

    def test_upload_from_local_path_can_use_exact_remaining_capacity(self) -> None:
        self.service.upload_shared_file(
            {"file_name": "existing.bin", "content_base64": b64(b"a" * 100)}
        )
        source = self.base_dir / "remaining.bin"
        source.write_bytes(b"b" * 28)

        uploaded = self.service.upload_shared_file_from_local_path({"path": str(source)})

        self.assertEqual(uploaded["storage"]["used_bytes"], 128)
        stored_path, _ = self.service.get_shared_file_download(uploaded["file"]["id"])
        self.assertEqual(stored_path.read_bytes(), b"b" * 28)

    def test_upload_shared_file_from_local_path_rejects_growth_during_copy(self) -> None:
        source = self.base_dir / "growing.txt"
        source.write_text("small", encoding="utf-8")

        with (
            patch(
                "minimal_kanban.services.shared_files_service.copy_file_limited",
                side_effect=ValueError("shared file upload is too large"),
            ),
            self.assertRaises(ServiceError) as raised,
        ):
            self.service.upload_shared_file_from_local_path({"path": str(source)})

        self.assertEqual(raised.exception.code, "upload_too_large")
        self.assertEqual(self.service.list_shared_files({})["files"], [])
        self.assertEqual(list((self.base_dir / "shared-files").glob("*")), [])

    def test_paste_shared_file_can_use_exact_remaining_capacity(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "copy-exact.bin", "content_base64": b64(b"c" * 64)}
        )

        pasted = self.service.paste_shared_file({"source_id": uploaded["file"]["id"]})

        self.assertEqual(pasted["storage"]["used_bytes"], 128)
        stored_path, _ = self.service.get_shared_file_download(pasted["file"]["id"])
        self.assertEqual(stored_path.read_bytes(), b"c" * 64)

    def test_index_persists_across_service_restart(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "persist.xlsx", "content_base64": b64(b"xlsx bytes")}
        )
        restarted = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=128,
        )
        listed = restarted.list_shared_files({})
        self.assertEqual(listed["files"][0]["id"], uploaded["file"]["id"])

    def test_fetch_shared_file_uses_actual_disk_size_for_base64_limit(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "stale.txt", "content_base64": b64(b"12")}
        )
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        stored_path.write_bytes(b"12345")

        fetched = self.service.fetch_shared_file(
            {"file_id": uploaded["file"]["id"], "include_base64": True, "max_base64_bytes": 4}
        )

        self.assertEqual(fetched["content"]["size_bytes"], 5)
        self.assertFalse(fetched["content"]["base64_included"])
        self.assertNotIn("base64", fetched["content"])

    def test_storage_capacity_counts_actual_disk_size_when_index_size_is_stale(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "stale-capacity.txt", "content_base64": b64(b"12")}
        )
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        stored_path.write_bytes(b"x" * 120)

        listed = self.service.list_shared_files({})
        self.assertEqual(listed["files"][0]["size_bytes"], 120)
        self.assertEqual(listed["storage"]["used_bytes"], 120)

        with self.assertRaises(ServiceError) as limit_error:
            self.service.upload_shared_file(
                {"file_name": "new.txt", "content_base64": b64(b"y" * 9)}
            )

        self.assertEqual(limit_error.exception.code, "storage_limit_exceeded")
        self.assertEqual(self.service.list_shared_files({})["storage"]["used_bytes"], 120)

    def test_paste_shared_file_counts_actual_source_size_when_index_size_is_stale(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "stale-copy.txt", "content_base64": b64(b"12")}
        )
        stored_path = self.service.storage_dir / uploaded["file"]["stored_name"]
        stored_path.write_bytes(b"x" * 80)

        copied = self.service.copy_shared_file({"file_id": uploaded["file"]["id"]})
        self.assertEqual(copied["clipboard"]["size_bytes"], 80)

        with self.assertRaises(ServiceError) as limit_error:
            self.service.paste_shared_file({"source_id": uploaded["file"]["id"]})

        self.assertEqual(limit_error.exception.code, "storage_limit_exceeded")
        self.assertEqual(limit_error.exception.details["used_bytes"], 80)
        self.assertEqual(limit_error.exception.details["incoming_bytes"], 80)

    def test_fetch_shared_file_omits_base64_when_file_grows_during_read(self) -> None:
        uploaded = self.service.upload_shared_file(
            {"file_name": "growing.txt", "content_base64": b64(b"12")}
        )

        with patch(
            "minimal_kanban.services.shared_files_service.read_bytes_limited",
            side_effect=ValueError("shared file content is too large"),
        ):
            fetched = self.service.fetch_shared_file(
                {"file_id": uploaded["file"]["id"], "include_base64": True, "max_base64_bytes": 4}
            )

        self.assertFalse(fetched["content"]["base64_included"])
        self.assertNotIn("base64", fetched["content"])

    def test_constructor_ignores_bool_and_fractional_byte_limits(self) -> None:
        defaulted = SharedFilesService(
            storage_dir=self.base_dir / "defaulted-shared-files",
            index_file=self.base_dir / "defaulted_shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=True,
            max_upload_bytes=1.5,
        )

        listed = defaulted.list_shared_files({})
        self.assertEqual(listed["storage"]["limit_bytes"], SHARED_FILES_STORAGE_LIMIT_BYTES)
        uploaded = defaulted.upload_shared_file(
            {"file_name": "small.txt", "content_base64": b64(b"abc")}
        )
        self.assertEqual(uploaded["storage"]["used_bytes"], 3)

        huge = SharedFilesService(
            storage_dir=self.base_dir / "huge-shared-files",
            index_file=self.base_dir / "huge_shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=1e308,
            max_upload_bytes=1e308,
        )
        self.assertEqual(
            huge.list_shared_files({})["storage"]["limit_bytes"],
            SHARED_FILES_STORAGE_LIMIT_BYTES,
        )
        self.assertEqual(huge._max_upload_bytes, SHARED_FILES_MAX_UPLOAD_BYTES)

        clamped = SharedFilesService(
            storage_dir=self.base_dir / "clamped-shared-files",
            index_file=self.base_dir / "clamped_shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=0,
            max_upload_bytes="0",
        )
        self.assertEqual(clamped.list_shared_files({})["storage"]["limit_bytes"], 1)
        with self.assertRaises(ServiceError) as oversized:
            clamped.upload_shared_file({"file_name": "too-large.txt", "content_base64": b64(b"ab")})
        self.assertEqual(oversized.exception.code, "upload_too_large")
        self.assertEqual(oversized.exception.details["max_size_bytes"], 1)
        self.assertEqual(SHARED_FILES_MAX_UPLOAD_BYTES, 25 * 1024 * 1024)

    def test_index_rejects_nonstandard_json_constants(self) -> None:
        self.service.index_file.write_text(
            """
            {
              "schema_version": 1,
              "files": [
                {
                  "id": "file-1",
                  "original_name": "broken.txt",
                  "stored_name": "broken.txt",
                  "extension": ".txt",
                  "size_bytes": NaN,
                  "created_at": "2026-01-01T00:00:00+00:00"
                }
              ]
            }
            """,
            encoding="utf-8",
        )

        with self.assertRaises(ServiceError) as corrupted:
            self.service.list_shared_files({})

        self.assertEqual(corrupted.exception.code, "storage_error")

    def test_index_rejects_oversized_json_before_reading(self) -> None:
        self.service.index_file.write_text('{"files":[],"padding":"xxxxxxxx"}', encoding="utf-8")

        with patch("minimal_kanban.services.shared_files_service.SHARED_FILES_INDEX_MAX_BYTES", 8):
            with self.assertRaises(ServiceError) as oversized:
                self.service.list_shared_files({})

        self.assertEqual(oversized.exception.code, "storage_error")

    def test_index_rejects_deeply_nested_json_without_recursion_crash(self) -> None:
        self.service.index_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

        with self.assertRaises(ServiceError) as corrupted:
            self.service.list_shared_files({})

        self.assertEqual(corrupted.exception.code, "storage_error")

    def test_write_index_sanitizes_non_finite_numbers(self) -> None:
        item = SharedFile(
            id="file-1",
            original_name="numbers.txt",
            stored_name="numbers.txt",
            extension=".txt",
            size_bytes=math.nan,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            x=math.inf,
            y=-math.inf,
        )

        self.service._write_index([item])

        raw_index = self.service.index_file.read_text(encoding="utf-8")
        self.assertNotIn("NaN", raw_index)
        self.assertNotIn("Infinity", raw_index)
        parsed = json.loads(raw_index)
        parsed_file = parsed["files"][0]
        self.assertEqual(parsed_file["size_bytes"], 0.0)
        self.assertEqual(parsed_file["x"], 0.0)
        self.assertEqual(parsed_file["y"], 0.0)

    def test_capacity_helpers_tolerate_corrupted_non_integral_byte_counts(self) -> None:
        corrupted = SharedFile(
            id="file-1",
            original_name="corrupted.txt",
            stored_name="corrupted.txt",
            extension=".txt",
            size_bytes=math.inf,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

        storage = self.service._storage_payload([corrupted])

        self.assertEqual(storage["used_bytes"], 0)
        self.assertEqual(storage["remaining_bytes"], 128)
        self.service._ensure_single_upload_size(True, field="content_base64")
        self.service._ensure_storage_capacity([], 1.5)

        corrupted.size_bytes = 1e308
        storage = self.service._storage_payload([corrupted])
        self.assertEqual(storage["used_bytes"], SHARED_FILES_STORAGE_LIMIT_BYTES)
        self.assertEqual(storage["remaining_bytes"], 0)

    def test_shared_file_from_dict_clamps_corrupted_size_bytes(self) -> None:
        item = SharedFile.from_dict(
            {
                "id": "file-1",
                "original_name": "corrupted.txt",
                "stored_name": "corrupted.txt",
                "size_bytes": 1e308,
            }
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.size_bytes, SHARED_FILES_STORAGE_LIMIT_BYTES)

    def test_fetch_and_positions_default_bool_fractional_numeric_inputs(self) -> None:
        uploaded = self.service.upload_shared_file(
            {
                "file_name": "coerce.txt",
                "content_base64": b64(b"abcdef"),
                "x": True,
                "y": 4.5,
            }
        )
        file_id = uploaded["file"]["id"]

        self.assertEqual(uploaded["file"]["x"], 0)
        self.assertEqual(uploaded["file"]["y"], 0)

        moved = self.service.update_shared_file_position(
            {"file_id": file_id, "x": False, "y": 12.75}
        )
        self.assertEqual(moved["file"]["x"], 0)
        self.assertEqual(moved["file"]["y"], 0)

        fetched = self.service.fetch_shared_file(
            {"file_id": file_id, "include_base64": True, "max_base64_bytes": True}
        )
        self.assertTrue(fetched["content"]["base64_included"])
        self.assertEqual(base64.b64decode(fetched["content"]["base64"]), b"abcdef")

        bounded_fetch = self.service.fetch_shared_file(
            {"file_id": file_id, "include_base64": True, "max_base64_bytes": 1e308}
        )
        self.assertEqual(bounded_fetch["content"]["max_base64_bytes"], 8 * 1024 * 1024)


class SharedFilesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.shared_files.api.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.base_dir / "state.json", logger=self.logger)
        self.card_service = CardService(self.store, self.logger)
        self.shared_files = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
            storage_limit_bytes=256,
        )
        self.clipboard_paths: list[Path] = []
        self.server = ApiServer(
            self.card_service,
            self.logger,
            shared_files_service=self.shared_files,
            start_port=0,
            fallback_limit=25,
            bearer_token="secret-token",
            clipboard_file_provider=lambda: list(self.clipboard_paths),
        )
        self.server.start()
        self.port = self.server.port
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self, path: str, payload: dict | None = None, *, method: str = "POST"
    ) -> tuple[int, dict]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_shared_files_api_roundtrip_and_download_route(self) -> None:
        status, upload = self.request(
            "/api/upload_shared_file",
            {
                "file_name": "Invoice.txt",
                "mime_type": "text/plain",
                "content_base64": b64(b"invoice body"),
                "x": 10,
                "y": 20,
            },
        )
        self.assertEqual(status, 200)
        file_id = upload["data"]["file"]["id"]

        status, listed = self.request("/api/list_shared_files", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["files"][0]["id"], file_id)

        status, fetched = self.request(
            "/api/fetch_shared_file",
            {"file_id": file_id, "include_base64": True, "max_base64_bytes": 64},
        )
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(fetched["data"]["content"]["base64"]), b"invoice body")

        status, renamed = self.request(
            "/api/rename_shared_file", {"file_id": file_id, "file_name": "Invoice final.txt"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["data"]["file"]["original_name"], "Invoice final.txt")

        status, copied = self.request("/api/copy_shared_file", {"file_id": file_id})
        self.assertEqual(status, 200)
        status, pasted = self.request(
            "/api/paste_shared_file",
            {"source_id": copied["data"]["clipboard"]["source_id"], "x": 100, "y": 120},
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(pasted["data"]["file"]["id"], file_id)

        status, moved = self.request(
            "/api/update_shared_file_position", {"file_id": file_id, "x": 44, "y": 55}
        )
        self.assertEqual(status, 200)
        self.assertEqual(moved["data"]["file"]["x"], 44)

        download = urllib.request.Request(
            f"{self.base_url}/api/shared_file?file_id={file_id}&access_token=secret-token",
            method="GET",
        )
        with urllib.request.urlopen(download, timeout=5) as response:
            self.assertEqual(response.status, http.client.OK)
            self.assertEqual(response.read(), b"invoice body")
            self.assertIn("Invoice%20final.txt", response.headers["Content-Disposition"])

        status, deleted = self.request("/api/delete_shared_file", {"file_id": file_id})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])

    def test_shared_file_inline_download_forces_active_mime_to_attachment(self) -> None:
        status, upload = self.request(
            "/api/upload_shared_file",
            {
                "file_name": "preview.html",
                "mime_type": "text/html",
                "content_base64": b64(b"<script>alert(1)</script>"),
            },
        )
        self.assertEqual(status, 200)
        file_id = upload["data"]["file"]["id"]

        download = urllib.request.Request(
            f"{self.base_url}/api/shared_file?file_id={file_id}&disposition=inline&access_token=secret-token",
            method="GET",
        )
        with urllib.request.urlopen(download, timeout=5) as response:
            self.assertEqual(response.status, http.client.OK)
            self.assertEqual(response.headers.get_content_type(), "application/octet-stream")
            self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.read(), b"<script>alert(1)</script>")

    def test_shared_file_download_route_rejects_oversized_disk_file_before_reading(self) -> None:
        status, upload = self.request(
            "/api/upload_shared_file",
            {
                "file_name": "Large.txt",
                "mime_type": "text/plain",
                "content_base64": b64(b"small"),
            },
        )
        self.assertEqual(status, 200)
        file_id = upload["data"]["file"]["id"]
        stored_path, _ = self.shared_files.get_shared_file_download(file_id)
        stored_path.write_bytes(b"x" * 16)
        download = urllib.request.Request(
            f"{self.base_url}/api/shared_file?file_id={file_id}&access_token=secret-token",
            method="GET",
        )

        with (
            patch("minimal_kanban.api.server.API_FILE_RESPONSE_MAX_BYTES", 8),
            patch.object(Path, "read_bytes", side_effect=AssertionError("must not load file")),
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(download, timeout=5)

        self.assertEqual(raised.exception.code, http.client.REQUEST_ENTITY_TOO_LARGE)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["code"], "validation_error")

    def test_shared_files_api_pastes_files_from_system_clipboard_provider(self) -> None:
        source = self.base_dir / "clipboard invoice.txt"
        source.write_bytes(b"clipboard body")
        self.clipboard_paths = [source]

        status, pasted = self.request("/api/paste_shared_files_from_clipboard", {"x": 40, "y": 72})

        self.assertEqual(status, 200)
        files = pasted["data"]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["original_name"], "clipboard invoice.txt")
        self.assertEqual(files[0]["x"], 40)
        self.assertEqual(files[0]["y"], 72)
        stored_path, _ = self.shared_files.get_shared_file_download(files[0]["id"])
        self.assertEqual(stored_path.read_bytes(), b"clipboard body")
