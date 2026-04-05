from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.desktop_connector_files import (
    AUTH_NOTE_FILENAME,
    CONNECTION_CARD_FILENAME,
    CONNECTOR_JSON_FILENAME,
    URL_FILENAME,
    WAITING_MESSAGE,
    build_connector_file_contents,
    build_pending_connector_file_contents,
    write_connector_files,
    write_pending_connector_files,
)


class DesktopConnectorFilesTests(unittest.TestCase):
    def test_build_connector_file_contents_uses_current_url_and_local_api(self) -> None:
        contents = build_connector_file_contents(
            "https://kanban.example/mcp",
            "http://127.0.0.1:41731",
        )

        self.assertIn(CONNECTION_CARD_FILENAME, contents)
        self.assertIn(CONNECTOR_JSON_FILENAME, contents)
        self.assertIn(AUTH_NOTE_FILENAME, contents)
        self.assertIn(URL_FILENAME, contents)
        self.assertIn("effective_mcp_url = https://kanban.example/mcp", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("effective_local_api_url = http://127.0.0.1:41731", contents[CONNECTION_CARD_FILENAME])

        payload = json.loads(contents[CONNECTOR_JSON_FILENAME])
        self.assertEqual(payload["name"], "AutoStop CRM / This Board Only (kanban.example)")
        self.assertEqual(payload["connector_url"], "https://kanban.example/mcp")
        self.assertEqual(payload["auth_mode"], "none")
        self.assertEqual(contents[URL_FILENAME], "https://kanban.example/mcp")

    def test_write_connector_files_uses_utf8_without_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            written = write_connector_files(
                "https://kanban.example/mcp",
                "http://127.0.0.1:41731",
                desktop_path=Path(temp_dir),
            )

            self.assertEqual(set(written.keys()), {CONNECTION_CARD_FILENAME, CONNECTOR_JSON_FILENAME, AUTH_NOTE_FILENAME, URL_FILENAME})
            url_path = written[URL_FILENAME]
            self.assertEqual(url_path.read_text(encoding="utf-8"), "https://kanban.example/mcp")
            self.assertFalse(url_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_pending_connector_files_keep_public_url_blank_until_ready(self) -> None:
        contents = build_pending_connector_file_contents()
        self.assertIn("effective_mcp_url = ", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("effective_local_api_url = http://127.0.0.1:41731", contents[CONNECTION_CARD_FILENAME])
        self.assertIn('"connector_url": ""', contents[CONNECTOR_JSON_FILENAME])
        self.assertIn("No authentication", contents[AUTH_NOTE_FILENAME])
        self.assertEqual(contents[URL_FILENAME], WAITING_MESSAGE)

        with tempfile.TemporaryDirectory() as temp_dir:
            written = write_pending_connector_files(desktop_path=Path(temp_dir))
            self.assertEqual(written[URL_FILENAME].read_text(encoding="utf-8"), WAITING_MESSAGE)

    def test_connector_files_support_bearer_auth_mode(self) -> None:
        contents = build_connector_file_contents(
            "https://kanban.example/mcp",
            "http://127.0.0.1:41731",
            auth_mode="bearer",
        )

        self.assertIn("connector_auth_mode = bearer", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("Choose Bearer token.", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("Bearer token", contents[AUTH_NOTE_FILENAME])

        payload = json.loads(contents[CONNECTOR_JSON_FILENAME])
        self.assertEqual(payload["auth_mode"], "bearer")
        self.assertIn("Authentication mode: Bearer token.", payload["notes"])

    def test_pending_connector_files_keep_auth_mode_and_local_api_context(self) -> None:
        contents = build_pending_connector_file_contents(
            auth_mode="bearer",
            local_api_url="http://127.0.0.1:49999",
        )

        self.assertIn("connector_auth_mode = bearer", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("effective_local_api_url = http://127.0.0.1:49999", contents[CONNECTION_CARD_FILENAME])
        self.assertIn("Bearer token", contents[AUTH_NOTE_FILENAME])
        self.assertIn('"auth_mode": "bearer"', contents[CONNECTOR_JSON_FILENAME])


if __name__ == "__main__":
    unittest.main()
