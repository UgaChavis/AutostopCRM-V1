"""Unknown attachment uploads remain blocked across card workspaces."""

import base64
import tempfile
from pathlib import Path

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFileUploadGuardBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-file-upload-guard-")
        self.addCleanup(directory.cleanup)
        self.upload = Path(directory.name) / "synthetic.png"
        self.upload.write_bytes(PNG_1X1_BYTES)

    def open_mobile(self, card_id):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )
        self.page.locator('[data-mobile-card-tab="files"]').click()

    def mobile_upload(self):
        self.page.locator("#mobileCardFileInput").set_input_files(self.upload)
        self.page.wait_for_function("!state.mobileCardFilesBusy")

    def desktop_upload(self):
        self.page.locator("#fileInput").set_input_files(self.upload)
        self.page.wait_for_function("!state.cardFilesMutationRequest")

    def test_mobile_unknown_blocks_desktop_and_reload(self):
        card_id = self.create_card("SYNTHETIC MOBILE UNKNOWN")
        self.load_board()
        self.open_mobile(card_id)
        attempts = []

        def lose_first(route):
            attempts.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/add_card_attachment", lose_first)
        self.mobile_upload()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        marker = self.page.evaluate(
            "id => {const k='autostop:card-file-upload:'+JSON.stringify(id); return [k,sessionStorage.getItem(k)]}",
            card_id,
        )
        self.assertEqual(marker[1], "1")
        self.assertNotIn(self.upload.name, str(marker))
        self.assertNotIn(self.session_token, str(marker))
        self.mobile_upload()
        self.assertEqual(len(attempts), 1)

        self.page.evaluate("closeMobileCardDetail()")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.desktop_upload()
        self.assertEqual(len(attempts), 1)
        self.page.reload()
        self.page.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.evaluate("stopSnapshotPolling()")
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.desktop_upload()
        self.assertEqual(len(attempts), 1)

    def test_desktop_unknown_blocks_mobile(self):
        card_id = self.create_card("SYNTHETIC DESKTOP UNKNOWN")
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        attempts = []

        def lose_first(route):
            attempts.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.fulfill(status=408, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/add_card_attachment", lose_first)
        self.desktop_upload()
        self.assertEqual(len(attempts), 1)
        self.page.evaluate("closeCardModal()")
        self.open_mobile(card_id)
        self.mobile_upload()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)

    def test_definite_rejection_releases_marker_and_allows_retry(self):
        card_id = self.create_card("SYNTHETIC REJECTED UPLOAD")
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        attempts = []

        def reject_once(route):
            attempts.append(1)
            if len(attempts) == 1:
                route.fulfill(
                    status=422,
                    json={
                        "ok": False,
                        "error": {"code": "validation_error", "message": "SYNTHETIC"},
                    },
                )
            else:
                route.continue_()

        self.page.route("**/api/add_card_attachment", reject_once)
        self.desktop_upload()
        self.assertIsNone(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            )
        )
        self.desktop_upload()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        self.assertIsNone(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            )
        )

    def test_cold_module_load_failure_stops_before_post(self):
        card_id = self.create_card("SYNTHETIC COLD MODULE")
        self.load_board()
        self.open_mobile(card_id)
        self.page.evaluate("clearCardOpenSideEffectTimer()")
        self.assertFalse(
            self.page.evaluate("Boolean(boardModuleRecords.get('card_save')?.exports)")
        )
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        self.page.route("**" + module_path, lambda route: route.abort("failed"))
        self.mobile_upload()
        self.assertNotIn("add_card_attachment", self.writes)
        self.assertIsNone(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            )
        )

    def test_viewer_change_during_cold_module_load_stops_before_post(self):
        card_id = self.create_card("SYNTHETIC COLD CONTEXT")
        self.load_board()
        self.open_mobile(card_id)
        self.page.evaluate("clearCardOpenSideEffectTimer()")
        self.assertFalse(
            self.page.evaluate("Boolean(boardModuleRecords.get('card_save')?.exports)")
        )
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        held = []
        self.page.route("**" + module_path, lambda route: held.append(route))
        self.page.locator("#mobileCardFileInput").set_input_files(self.upload)
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("resetViewerScopedState()")
        held.pop().continue_()
        self.page.wait_for_timeout(200)
        self.assertNotIn("add_card_attachment", self.writes)
        self.assertIsNone(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            )
        )

    def test_mobile_429_does_not_allow_repeat(self):
        card_id = self.create_card("SYNTHETIC RATE LIMIT")
        self.load_board()
        self.open_mobile(card_id)
        attempts = []

        def reject(route):
            attempts.append(1)
            route.fulfill(
                status=429,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/add_card_attachment", reject)
        self.mobile_upload()
        self.mobile_upload()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            ),
            "1",
        )

    def test_mobile_partial_batch_with_unknown_second_file_stops_repeat(self):
        card_id = self.create_card("SYNTHETIC MOBILE PARTIAL BATCH")
        self.load_board()
        self.open_mobile(card_id)
        second = self.upload.with_name("second.png")
        second.write_bytes(PNG_1X1_BYTES)
        attempts = []

        def lose_second(route):
            attempts.append(route.request.post_data_json["file_name"])
            if len(attempts) == 2:
                self.assertEqual(route.fetch().status, 200)
                route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
            else:
                route.continue_()

        self.page.route("**/api/add_card_attachment", lose_second)
        self.page.locator("#mobileCardFileInput").set_input_files([self.upload, second])
        self.page.wait_for_function("!state.mobileCardFilesBusy")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 2)
        self.page.locator("#mobileCardFileInput").set_input_files(second)
        self.page.wait_for_function("!state.mobileCardFilesBusy")
        self.assertEqual(len(attempts), 2)

    def test_mobile_malformed_success_keeps_marker(self):
        card_id = self.create_card("SYNTHETIC MALFORMED UPLOAD")
        self.load_board()
        self.open_mobile(card_id)
        attempts = []

        def corrupt(route):
            attempts.append(1)
            payload = route.fetch().json()
            payload["data"]["card"]["id"] = "synthetic-foreign"
            route.fulfill(json=payload)

        self.page.route("**/api/add_card_attachment", corrupt)
        self.mobile_upload()
        self.mobile_upload()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            ),
            "1",
        )

    def exercise_reused_existing_receipt(self, *, mobile, tombstone):
        card_id = self.create_card("SYNTHETIC REUSED RECEIPT")
        original_id = self.runtime.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "original.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                "source": "ui",
            }
        )["attachment"]["id"]
        if tombstone:
            self.runtime.service.remove_card_attachment(
                {"card_id": card_id, "attachment_id": original_id, "source": "ui"}
            )
        self.load_board()
        if mobile:
            self.open_mobile(card_id)
        else:
            self.open_card(card_id)
            self.page.locator('#cardModal [data-tab="files"]').click()
        attempts = []

        def reuse_id(route):
            attempts.append(1)
            payload = route.fetch().json()
            payload["data"]["attachment"]["id"] = original_id
            for item in payload["data"]["card"]["attachments"]:
                if item["id"] == original_id:
                    item["removed"] = False
            route.fulfill(json=payload)

        self.page.route("**/api/add_card_attachment", reuse_id)
        upload = self.mobile_upload if mobile else self.desktop_upload
        upload()
        upload()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 2)
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            ),
            "1",
        )

    def test_desktop_reused_active_receipt_stays_blocked(self):
        self.exercise_reused_existing_receipt(mobile=False, tombstone=False)

    def test_mobile_reused_tombstone_receipt_stays_blocked(self):
        self.exercise_reused_existing_receipt(mobile=True, tombstone=True)

    def test_batch_cannot_reuse_previous_receipt(self):
        card_id = self.create_card("SYNTHETIC REUSED BATCH RECEIPT")
        self.load_board()
        self.open_card(card_id)
        self.page.locator('#cardModal [data-tab="files"]').click()
        second = self.upload.with_name("second.png")
        second.write_bytes(PNG_1X1_BYTES)
        receipts = []

        def duplicate(route):
            payload = route.fetch().json()
            receipts.append(payload["data"]["attachment"]["id"])
            if len(receipts) == 2:
                payload["data"]["attachment"]["id"] = receipts[0]
            route.fulfill(json=payload)

        self.page.route("**/api/add_card_attachment", duplicate)
        self.page.locator("#fileInput").set_input_files([self.upload, second])
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertTrue(self.page.evaluate("state.cardFileUploadCheckpoint.uncertain"))
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 2)
        self.page.locator("#fileInput").set_input_files([self.upload, second])
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(len(receipts), 2)
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-file-upload:'+JSON.stringify(id))",
                card_id,
            ),
            "1",
        )
