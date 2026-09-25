from __future__ import annotations

import base64
import logging
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.shared_files_service import SharedFilesService  # noqa: E402


class SharedFileWriteIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = self.reopen()

    def reopen(self):
        return SharedFilesService(
            storage_dir=self.root / "files",
            index_file=self.root / "index.json",
            logger=logging.getLogger(__name__),
        )

    def upload(self):
        return self.service.upload_shared_file(
            {
                "file_name": "synthetic.txt",
                "content_base64": base64.b64encode(b"Keep bytes").decode(),
            }
        )["file"]

    def test_failed_delete_index_write_preserves_download(self):
        item = self.upload()
        before = self.service.index_file.read_bytes()
        with (
            patch.object(self.service, "_write_index", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.service.delete_shared_file({"file_id": item["id"]})
        self.assertEqual(self.service.index_file.read_bytes(), before)
        path, _ = self.reopen().get_shared_file_download(item["id"])
        self.assertEqual(path.read_bytes(), b"Keep bytes")

    def test_delete_cleanup_failure_keeps_committed_removal(self):
        item = self.upload()
        path = self.service.storage_dir / item["stored_name"]
        unlink = Path.unlink

        def busy(target, *args, **kwargs):
            if target == path:
                raise PermissionError("file busy")
            return unlink(target, *args, **kwargs)

        with patch.object(Path, "unlink", new=busy), self.assertLogs(__name__, level="WARNING"):
            result = self.service.delete_shared_file({"file_id": item["id"]})
        self.assertTrue(result["deleted"])
        self.assertEqual(self.reopen().list_shared_files()["files"], [])
        self.assertTrue(path.is_file())
        with self.assertRaises(ServiceError):
            self.reopen().get_shared_file_download(item["id"])

    def test_late_index_error_does_not_erase_committed_upload_or_copy(self):
        original = self.upload()
        source = self.root / "source.txt"
        source.write_bytes(b"Local bytes")
        actions = (
            (self.upload, b"Keep bytes"),
            (
                lambda: self.service.upload_shared_file_from_local_path({"path": str(source)}),
                b"Local bytes",
            ),
            (lambda: self.service.paste_shared_file({"source_id": original["id"]}), b"Keep bytes"),
        )
        write_index = self.service._write_index
        for action, expected in actions:
            with self.subTest(action=action):
                before = {item["id"] for item in self.service.list_shared_files()["files"]}

                def late_failure(files, **kwargs):
                    write_index(files, **kwargs)
                    raise OSError("after replace")

                with (
                    patch.object(self.service, "_write_index", side_effect=late_failure),
                    self.assertRaisesRegex(OSError, "after replace"),
                ):
                    action()
                reopened = self.reopen()
                created = [
                    item
                    for item in reopened.list_shared_files()["files"]
                    if item["id"] not in before
                ]
                self.assertEqual(len(created), 1)
                path, _ = reopened.get_shared_file_download(created[0]["id"])
                self.assertEqual(path.read_bytes(), expected)

    def assert_other_thread_can_list(self):
        result = []

        def list_in_worker():
            acquired = self.service._lock.acquire(timeout=0.5)
            result.append(acquired)
            if acquired:
                try:
                    result.append(self.service.list_shared_files()["files"])
                finally:
                    self.service._lock.release()

        worker = threading.Thread(target=list_in_worker, daemon=True)
        worker.start()
        worker.join(timeout=2)
        if result == [False]:
            # Release the old implementation's leaked lock in the owning thread.
            self.service._lock.release()
        self.assertFalse(worker.is_alive(), "shared-file read is blocked")
        self.assertEqual(result, [True, []])

    def test_rejected_index_write_reclaims_new_upload(self):
        before = self.service.index_file.read_bytes()
        with (
            patch.object(self.service, "_write_index", side_effect=OSError("before replace")),
            self.assertRaisesRegex(OSError, "before replace"),
        ):
            self.upload()
        self.assertEqual(list(self.service.storage_dir.iterdir()), [])
        self.assertEqual(self.service.index_file.read_bytes(), before)

    def test_uncertain_index_read_retains_bytes_and_original_error(self):
        read_index = self.service._read_index
        with (
            patch.object(
                self.service, "_read_index", side_effect=[read_index(), OSError("read failed")]
            ),
            patch.object(self.service, "_write_index", side_effect=OSError("write failed")),
            self.assertLogs(__name__, level="WARNING"),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            self.upload()
        self.assertEqual(len(list(self.service.storage_dir.iterdir())), 1)

    def test_invalid_index_does_not_keep_thread_lock_after_repair(self):
        before = self.service.index_file.read_bytes()
        self.service.index_file.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(ServiceError):
            self.service.list_shared_files()
        self.service.index_file.write_bytes(before)
        self.assert_other_thread_can_list()

    def test_process_lock_timeout_releases_thread_lock(self):
        @contextmanager
        def timeout():
            raise TimeoutError("lock busy")
            yield

        with (
            patch.object(self.service._process_lock, "acquire", side_effect=timeout),
            self.assertRaises(TimeoutError),
        ):
            self.service.list_shared_files()
        self.assert_other_thread_can_list()
