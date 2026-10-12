"""Shared file workspace lifecycle and late read ownership on synthetic files."""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.browser_ui_support import UiBrowserTestCase


class SharedFilesBrowserTests(UiBrowserTestCase):
    def local_files(self, *names: str) -> list[Path]:
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-shared-files-")
        self.addCleanup(directory.cleanup)
        paths = [Path(directory.name) / name for name in names]
        for index, path in enumerate(paths):
            path.write_text(f"Synthetic shared file {index}", encoding="utf-8")
        return paths

    def listed_files(self) -> list:
        return self.context.request.get(
            self.runtime.base_url + "/api/list_shared_files",
            headers={"X-Operator-Session": self.session_token},
        ).json()["data"]["files"]

    def test_partial_upload_retries_only_the_unfinished_file(self) -> None:
        paths = self.local_files("partial-first.txt", "partial-second.txt")
        self.open_shared()
        before = len(self.listed_files())
        attempts = []

        def fail_second(route):
            attempts.append(route.request.post_data_json["file_name"])
            if len(attempts) == 2:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {"ok": False, "error": {"message": "Synthetic partial upload failure"}}
                    ),
                )
            else:
                route.continue_()

        self.page.route("**/api/upload_shared_file", fail_second)
        with self.page.expect_request("**/api/upload_shared_file"):
            self.page.locator("#sharedFilesInput").set_input_files(paths)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.page.unroute("**/api/upload_shared_file")
        with self.page.expect_request("**/api/upload_shared_file"):
            self.page.locator("#sharedFilesInput").set_input_files(paths)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(len(self.listed_files()), before + 2)
        self.assertEqual(self.writes.count("upload_shared_file"), 3)

    def test_lost_upload_reply_stops_blind_repeat(self) -> None:
        path = self.local_files("uncertain-shared.txt")[0]
        self.open_shared()
        before = len(self.listed_files())

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/upload_shared_file", lose_reply)
        with self.page.expect_request("**/api/upload_shared_file"):
            self.page.locator("#sharedFilesInput").set_input_files(path)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.page.locator("#sharedFilesInput").set_input_files(path)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(len(self.listed_files()), before + 1)

    def test_confirmed_upload_with_failed_list_refresh_retries_only_reads(self) -> None:
        path = self.local_files("confirmed-before-refresh.txt")[0]
        self.open_shared()
        before = len(self.listed_files())
        self.page.route(
            "**/api/list_shared_files",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic list failure"}}),
            ),
        )
        with self.page.expect_request("**/api/upload_shared_file"):
            self.page.locator("#sharedFilesInput").set_input_files(path)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.page.unroute("**/api/list_shared_files")
        self.page.locator("#sharedFilesInput").set_input_files(path)
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(len(self.listed_files()), before + 1)

    def test_lost_synthetic_clipboard_reply_cannot_paste_twice(self) -> None:
        path = self.local_files("synthetic-clipboard.txt")[0]
        self.enterContext(
            patch.object(self.runtime.api, "_clipboard_file_provider", lambda: [path])
        )
        self.open_shared()
        before = len(self.listed_files())

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/paste_shared_files_from_clipboard", lose_reply)
        for _ in range(2):
            if not self.page.locator("#sharedFilesContextMenu").is_visible():
                self.page.locator("#sharedFilesDesktop").click(
                    button="right", position={"x": 10, "y": 10}
                )
            self.page.locator('[data-shared-files-menu-action="paste-clipboard"]').click()
            self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(self.writes.count("paste_shared_files_from_clipboard"), 1)
        self.assertEqual(len(self.listed_files()), before + 1)

    def test_lost_paste_reply_does_not_create_a_second_copy_on_repeat(self) -> None:
        self.open_shared()
        self.page.locator("#sharedFilesDesktop [data-shared-file-id]").first.click()
        self.page.locator("#sharedFilesCopyButton").click()
        self.page.wait_for_function(
            "!state.sharedFilesMutationRequest && state.sharedFilesClipboardId"
        )

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/paste_shared_file", lose_reply)
        with self.page.expect_request("**/api/paste_shared_file"):
            self.page.locator("#sharedFilesPasteButton").click()
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.page.locator("#sharedFilesPasteButton").click()
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(self.writes.count("paste_shared_file"), 1)
        self.assertFalse(self.page.locator("#sharedFilesCopyButton").is_disabled())

    def open_shared(self) -> None:
        self.load_board()
        self.page.locator("#sharedFilesButton").click()
        self.page.locator("#sharedFilesModal.is-open").wait_for()

    def test_late_workspace_read_cannot_steal_a_newer_card_dialog(self) -> None:
        card_id = self.create_card("Card after shared-file intent")
        self.open_shared()
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').first.click()
        held = []
        self.page.route(
            "**/api/list_shared_files", lambda route: held.append((route, route.fetch()))
        )
        self.page.locator("#sharedFilesButton").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.open_card(card_id)
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#sharedFilesModal").evaluate("e=>e.classList.contains('is-open')")
        )

    def test_rename_copy_paste_and_delete_roundtrip_keeps_the_original_file(self) -> None:
        data = self.context.request.post(
            self.runtime.base_url + "/api/upload_shared_file",
            headers={"X-Operator-Session": self.session_token},
            data={
                "file_name": "shared-workflow.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"Synthetic shared file").decode("ascii"),
            },
        ).json()
        self.assertTrue(data["ok"])
        file_id = data["data"]["file"]["id"]
        self.open_shared()
        self.page.locator(f'#sharedFilesDesktop [data-shared-file-id="{file_id}"]').click()
        self.page.once("dialog", lambda dialog: dialog.accept("shared-renamed.txt"))
        self.page.locator("#sharedFilesRenameButton").click()
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.page.locator("#sharedFilesCopyButton").click()
        self.page.wait_for_function(
            "!state.sharedFilesMutationRequest && Boolean(state.sharedFilesClipboardId)"
        )
        with self.page.expect_request("**/api/paste_shared_file"):
            self.page.locator("#sharedFilesPasteButton").click()
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        copy_id = self.page.evaluate("state.sharedFilesActiveId")
        self.assertNotEqual(copy_id, file_id)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#sharedFilesDeleteButton").click()
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertEqual(self.page.locator(f'[data-shared-file-id="{copy_id}"]').count(), 0)
        self.assertEqual(self.page.locator(f'[data-shared-file-id="{file_id}"]').count(), 1)
        self.assertEqual(self.writes.count("rename_shared_file"), 1)
        self.assertEqual(self.writes.count("paste_shared_file"), 1)
        self.assertEqual(self.writes.count("delete_shared_file"), 1)
