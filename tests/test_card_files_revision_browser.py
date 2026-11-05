"""Refreshing attachments must not rebase an unrelated card edit."""

import base64
import tempfile
from pathlib import Path

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFilesRevisionBrowserTests(UiBrowserTestCase):
    def test_other_operator_edit_is_not_overwritten_after_files_read(self):
        card_id = self.create_card("SYNTHETIC ORIGINAL", description="SYNTHETIC DESCRIPTION BEFORE")
        self.runtime.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "synthetic-file.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                "source": "ui",
            }
        )
        self.load_board()
        self.open_card(card_id)
        version = self.page.evaluate("state.activeCard.updated_at")
        self.page.locator("#cardTitle").fill("SYNTHETIC LOCAL DRAFT")
        updated = self.runtime.service.update_card(
            {
                "card_id": card_id,
                "description": "SYNTHETIC OTHER OPERATOR",
                "expected_updated_at": version,
                "actor_name": "synthetic-B",
                "source": "ui",
            }
        )["card"]
        self.assertNotEqual(updated["updated_at"], version)
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.locator("#fileList [data-preview-file]").first.wait_for()
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC LOCAL DRAFT")
        with self.page.expect_request("**/api/update_card") as sent:
            with self.page.expect_response("**/api/update_card") as response:
                self.page.click("#saveCardButton")
        self.finish_save()
        self.assertEqual(sent.value.post_data_json["expected_updated_at"], version)
        self.assertEqual(response.value.status, 409)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC LOCAL DRAFT")
        saved = self.stored_card(card_id)
        self.assertEqual(saved["description"], "SYNTHETIC OTHER OPERATOR")
        self.assertEqual(saved["title"], "SYNTHETIC ORIGINAL")

    def test_own_upload_advances_revision_when_card_fields_are_unchanged(self):
        card_id = self.create_card("SYNTHETIC ORIGINAL")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("SYNTHETIC LOCAL DRAFT")
        version = self.page.evaluate("state.activeCard.updated_at")
        self.page.locator('#cardModal [data-tab="files"]').click()
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-card-files-cas-")
        self.addCleanup(directory.cleanup)
        upload = Path(directory.name) / "synthetic-file.png"
        upload.write_bytes(PNG_1X1_BYTES)
        with self.page.expect_response("**/api/add_card_attachment") as upload_response:
            self.page.locator("#fileInput").set_input_files(upload)
        self.assertEqual(upload_response.value.status, 200)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        revision_after_upload = self.page.evaluate("state.activeCard.updated_at")
        self.assertNotEqual(revision_after_upload, version)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC LOCAL DRAFT")
        with self.page.expect_request("**/api/update_card") as sent:
            with self.page.expect_response("**/api/update_card") as response:
                self.page.click("#saveCardButton")
        self.finish_save()
        self.assertEqual(sent.value.post_data_json["expected_updated_at"], revision_after_upload)
        self.assertEqual(response.value.status, 200)
        saved = self.stored_card(card_id)
        self.assertEqual(saved["title"], "SYNTHETIC LOCAL DRAFT")
        self.assertEqual(len(saved["attachments"]), 1)

    def test_own_upload_after_creation_allows_next_edit(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW CARD")
        with self.page.expect_response("**/api/create_card") as created:
            self.page.click("#saveCardButton")
        self.assertEqual(created.value.status, 200)
        card_id = created.value.json()["data"]["card"]["id"]
        self.finish_save()
        self.page.locator(f'.card[data-card-id="{card_id}"]').wait_for()
        self.open_card(card_id)
        version = self.page.evaluate("state.activeCard.updated_at")
        self.page.locator('#cardModal [data-tab="files"]').click()
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-created-files-cas-")
        self.addCleanup(directory.cleanup)
        upload = Path(directory.name) / "synthetic-file.png"
        upload.write_bytes(PNG_1X1_BYTES)
        with self.page.expect_response("**/api/add_card_attachment") as upload_response:
            self.page.locator("#fileInput").set_input_files(upload)
        self.assertEqual(upload_response.value.status, 200)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        new_version = self.page.evaluate("state.activeCard.updated_at")
        self.assertNotEqual(new_version, version)
        self.page.locator('#cardModal [data-tab="overview"]').click()
        self.page.locator("#cardTitle").fill("SYNTHETIC EDIT AFTER UPLOAD")
        with self.page.expect_request("**/api/update_card") as sent:
            with self.page.expect_response("**/api/update_card") as response:
                self.page.click("#saveCardButton")
        self.finish_save()
        self.assertEqual(sent.value.post_data_json["expected_updated_at"], new_version)
        self.assertEqual(response.value.status, 200)
        self.assertEqual(self.stored_card(card_id)["title"], "SYNTHETIC EDIT AFTER UPLOAD")
