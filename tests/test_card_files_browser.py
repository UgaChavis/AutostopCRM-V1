"""Real attachment and preview interactions on disposable records and files."""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFilesBrowserTests(UiBrowserTestCase):
    def test_lost_upload_reply_keeps_one_attachment_and_stops_blind_repeat(self) -> None:
        card_id = self.create_card("Attachment uncertain write")
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-attachment-uncertain-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "uncertain.txt"
        path.write_text("Synthetic uncertain attachment", encoding="utf-8")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/add_card_attachment", lose_reply)
        with self.page.expect_request("**/api/add_card_attachment"):
            self.page.locator("#fileInput").set_input_files(path)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.page.locator("#fileInput").set_input_files(path)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        self.assertTrue(self.page.evaluate("state.cardFileUploadCheckpoint.uncertain"))

    def test_passive_file_read_cannot_replace_a_confirmed_timer_revision(self) -> None:
        card_id = self.create_card("Attachment status and timer revision")
        self.add_image(card_id, "status-revision.png")
        self.load_board()
        self.open_card(card_id)
        held = []
        self.page.route(
            "**/api/get_card?card_id=*", lambda route: held.append((route, route.fetch()))
        )
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.wait_for_timeout(80)
        self.assertEqual(len(held), 1)
        self.page.locator('#cardModal [data-tab="overview"]').click()
        self.page.locator("#signalStartButton").click()
        self.page.wait_for_function("!state.cardTimerSaving && state.cardTimerState === 'running'")
        confirmed_revision = self.stored_card(card_id)["updated_at"]
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.activeCard.updated_at"), confirmed_revision)
        self.assertEqual(self.page.evaluate("state.activeCard.timer_state"), "running")

    def test_partial_upload_retry_does_not_recreate_confirmed_file(self) -> None:
        card_id = self.create_card("Attachment partial batch")
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-attachment-batch-")
        self.addCleanup(directory.cleanup)
        paths = [Path(directory.name) / name for name in ("batch-first.txt", "batch-second.txt")]
        for index, path in enumerate(paths):
            path.write_text(f"Synthetic attachment {index}", encoding="utf-8")
        attempted = []

        def fail_second(route):
            attempted.append(route.request.post_data_json["file_name"])
            if len(attempted) == 2:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {"ok": False, "error": {"message": "Synthetic upload failure"}}
                    ),
                )
            else:
                route.continue_()

        self.page.route("**/api/add_card_attachment", fail_second)
        with self.page.expect_request("**/api/add_card_attachment"):
            self.page.locator("#fileInput").set_input_files(paths)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.page.unroute("**/api/add_card_attachment")
        with self.page.expect_request("**/api/add_card_attachment"):
            self.page.locator("#fileInput").set_input_files(paths)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        attachments = [
            item for item in self.stored_card(card_id)["attachments"] if not item["removed"]
        ]
        self.assertEqual(
            sorted(item["file_name"] for item in attachments),
            ["batch-first.txt", "batch-second.txt"],
        )
        self.assertEqual(self.writes.count("add_card_attachment"), 3)

    def test_stalled_preview_releases_loading_state_and_keeps_close_available(self) -> None:
        card_id = self.create_card("Attachment preview timeout")
        attachment_id = self.add_image(card_id, "preview-timeout.png")
        self.open_files(card_id)
        self.page.evaluate("""() => {
          const schedule = window.setTimeout.bind(window);
          window.setTimeout = (fn, ms, ...args) => schedule(fn, ms === 90000 ? 60 : ms, ...args);
        }""")
        self.page.route(
            "**/api/attachment?*",
            lambda route: None if route.request.resource_type == "fetch" else route.continue_(),
        )
        self.preview(attachment_id)
        self.page.wait_for_timeout(160)
        self.assertFalse(self.page.evaluate("state.filePreview.loading"))
        self.assertTrue(self.page.evaluate("Boolean(state.filePreview.error)"))
        self.assertTrue(self.page.locator("#filePreviewCloseButton").is_enabled())

    def test_upload_preview_and_remove_roundtrip_preserves_card_draft(self) -> None:
        card_id = self.create_card("Attachment lifecycle")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Unsaved title during attachment lifecycle")
        self.page.locator('#cardModal [data-tab="files"]').click()
        with self.page.expect_request("**/api/add_card_attachment"):
            self.page.locator("#fileInput").set_input_files(
                {"name": "lifecycle.png", "mimeType": "image/png", "buffer": PNG_1X1_BYTES}
            )
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        attachment_id = self.stored_card(card_id)["attachments"][0]["id"]
        self.preview(attachment_id)
        self.page.wait_for_function("Boolean(state.filePreview.objectUrl)")
        self.page.locator("#filePreviewCloseButton").click()
        self.page.locator(f'#fileList [data-remove-file="{attachment_id}"]').click()
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertTrue(self.stored_card(card_id)["attachments"][0]["removed"])
        self.page.locator('#cardModal [data-tab="overview"]').click()
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(),
            "Unsaved title during attachment lifecycle",
        )
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertEqual(self.writes.count("remove_card_attachment"), 1)

    def add_image(self, card_id: str, name: str) -> str:
        data = self.runtime.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": name,
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                "source": "ui",
            }
        )
        return next(item["id"] for item in data["card"]["attachments"] if item["file_name"] == name)

    def open_files(self, card_id: str) -> None:
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.locator("#fileList [data-preview-file]").first.wait_for()

    def preview(self, attachment_id: str) -> None:
        self.page.locator(
            f'#fileList .file-row__actions [data-preview-file="{attachment_id}"]'
        ).click()

    def hold_previews(self) -> list:
        held = []

        def intercept(route):
            if route.request.resource_type == "fetch":
                held.append((route, route.fetch()))
            else:
                route.continue_()

        self.page.route("**/api/attachment?*", intercept)
        return held

    def test_reversed_preview_replies_keep_the_last_selected_image(self) -> None:
        card_id = self.create_card("Preview ownership")
        first_id = self.add_image(card_id, "preview-first.png")
        second_id = self.add_image(card_id, "preview-second.png")
        self.open_files(card_id)
        held = self.hold_previews()
        self.preview(first_id)
        self.page.wait_for_timeout(80)
        self.page.locator("#filePreviewCloseButton").click()
        self.preview(second_id)
        self.page.wait_for_timeout(80)
        self.assertEqual(len(held), 2)
        route, response = held[1]
        route.fulfill(response=response)
        self.page.wait_for_function("state.filePreview.loading === false")
        self.assertEqual(self.page.evaluate("state.filePreview.attachmentId"), second_id)
        route, response = held[0]
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.filePreview.attachmentId"), second_id)
        self.assertEqual(self.page.locator("#filePreviewTitle").inner_text(), "PREVIEW-SECOND.PNG")

    def test_closed_preview_cannot_be_reopened_by_a_late_response(self) -> None:
        card_id = self.create_card("Closed preview ownership")
        attachment_id = self.add_image(card_id, "preview-closed.png")
        self.open_files(card_id)
        held = self.hold_previews()
        self.preview(attachment_id)
        self.page.wait_for_timeout(80)
        self.page.locator("#filePreviewCloseButton").click()
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.filePreview.attachmentId"), "")
        self.assertTrue(
            self.page.locator("#filePreviewPanel").evaluate("e=>e.classList.contains('hidden')")
        )

    def test_late_preview_error_cannot_erase_a_newer_ready_preview(self) -> None:
        card_id = self.create_card("Preview error ownership")
        first_id = self.add_image(card_id, "preview-error-first.png")
        second_id = self.add_image(card_id, "preview-error-second.png")
        self.open_files(card_id)
        held = self.hold_previews()
        self.preview(first_id)
        self.page.wait_for_timeout(80)
        self.page.locator("#filePreviewCloseButton").click()
        self.preview(second_id)
        self.page.wait_for_timeout(80)
        route, response = held[1]
        route.fulfill(response=response)
        self.page.wait_for_function("!state.filePreview.loading")
        held[0][0].abort("failed")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.filePreview.attachmentId"), second_id)
        self.assertEqual(self.page.evaluate("state.filePreview.error"), "")
