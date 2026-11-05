"""File mutations must not accept a complete but stale card readback."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFilesStaleReadbackBrowserTests(UiBrowserTestCase):
    def observe_status(self):
        self.page.evaluate(
            """() => {
            const original = setStatus;
            window.syntheticStatuses = [];
            setStatus = (message, ...args) => {
                window.syntheticStatuses.push(message);
                return original(message, ...args);
            };
        }"""
        )

    def test_upload_stale_readback_keeps_receipt_until_fresh_read(self):
        card_id = self.create_card("SYNTHETIC STALE UPLOAD")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("SYNTHETIC UNSAVED UPLOAD")
        old_card = self.stored_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.observe_status()

        def stale(route):
            route.fulfill(json={"ok": True, "data": {"card": old_card}})

        self.page.route("**/api/get_card?card_id=*", stale)
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-stale-receipt-")
        self.addCleanup(directory.cleanup)
        upload = Path(directory.name) / "synthetic.png"
        upload.write_bytes(PNG_1X1_BYTES)
        self.page.locator("#fileInput").set_input_files(upload)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        self.assertTrue(self.page.evaluate("Boolean(state.cardFileUploadCheckpoint?.receipts[0])"))
        self.assertEqual(self.page.evaluate("state.activeCard.attachments.length"), 0)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED UPLOAD")
        self.assertNotIn("ФАЙЛ ЗАГРУЖЕН.", self.page.evaluate("window.syntheticStatuses"))

        self.page.unroute("**/api/get_card?card_id=*", stale)
        self.page.locator("#fileInput").set_input_files(upload)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        self.assertEqual(self.page.evaluate("state.activeCard.attachments.length"), 1)
        self.assertIsNone(self.page.evaluate("state.cardFileUploadCheckpoint"))
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED UPLOAD")

    def test_delete_stale_readback_keeps_draft_until_fresh_read(self):
        card_id = self.create_card("SYNTHETIC STALE DELETE")
        created = self.runtime.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "synthetic.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                "source": "ui",
            }
        )
        attachment_id = created["attachment"]["id"]
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("SYNTHETIC UNSAVED DELETE")
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.locator("#fileList [data-preview-file]").first.wait_for()
        old_card = self.stored_card(card_id)
        self.observe_status()

        def stale(route):
            route.fulfill(json={"ok": True, "data": {"card": old_card}})

        self.page.route("**/api/get_card?card_id=*", stale)
        self.page.locator(f'#fileList [data-remove-file="{attachment_id}"]').click()
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("remove_card_attachment"), 1)
        self.assertTrue(self.stored_card(card_id)["attachments"][0]["removed"])
        self.assertFalse(self.page.evaluate("state.activeCard.attachments[0].removed"))
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DELETE")
        self.assertNotIn("ФАЙЛ УДАЛЕН.", self.page.evaluate("window.syntheticStatuses"))

        self.page.unroute("**/api/get_card?card_id=*", stale)
        self.page.evaluate("async () => await refreshActiveCardFiles()")
        self.assertTrue(self.page.evaluate("state.activeCard.attachments[0].removed"))
        self.assertEqual(self.writes.count("remove_card_attachment"), 1)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DELETE")
