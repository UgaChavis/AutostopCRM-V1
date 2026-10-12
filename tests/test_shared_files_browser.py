"""Shared file workspace lifecycle and late read ownership on synthetic files."""

from __future__ import annotations

import base64

from tests.browser_ui_support import UiBrowserTestCase


class SharedFilesBrowserTests(UiBrowserTestCase):
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
