# ruff: noqa: E402
from __future__ import annotations

import base64
import http.client
import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.services.shared_files_service import SharedFilesService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
        self.port = reserve_port()
        self.server = ApiServer(
            self.card_service,
            self.logger,
            shared_files_service=self.shared_files,
            start_port=self.port,
            fallback_limit=1,
            bearer_token="secret-token",
            clipboard_file_provider=lambda: list(self.clipboard_paths),
        )
        self.server.start()
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
