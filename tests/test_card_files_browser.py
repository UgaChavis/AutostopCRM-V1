"""Real attachment and preview interactions on disposable records and files."""

from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFilesBrowserTests(UiBrowserTestCase):
    def test_partial_file_read_preserves_card_and_draft_for_both_roles(self) -> None:
        card_id = self.create_card(
            "Synthetic file read",
            vehicle_profile={"make_display": "Land Rover", "model_display": "Range Rover"},
        )
        self.add_image(card_id, "synthetic-file-read.png")
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-file-operator",
                "password": "synthetic-file-password",
                "role": "operator",
                "permissions": [],
                "expected_permissions": [],
            },
        )
        self.assertEqual(created.status, 200)
        login = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-file-operator", "password": "synthetic-file-password"},
        )
        self.assertEqual(login.status, 200)
        operator_token = login.json()["data"]["session"]["token"]
        for token in (self.session_token, operator_token):
            context = self.browser.new_context(
                viewport={"width": 1440, "height": 960},
                extra_http_headers=self.runtime.auth_headers,
            )
            self.addCleanup(context.close)
            context.add_init_script(
                "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
            )
            page = context.new_page()
            page.goto(self.runtime.browser_url)
            page.wait_for_function(
                "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
            )
            page.evaluate("stopSnapshotPolling()")
            page.locator(f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])').click()
            page.wait_for_function("state.activeCardIsFull")
            page.evaluate("clearCardOpenSideEffectTimer(); cancelDeferredCardSeen(state.editingId)")
            page.locator("#cardTitle").fill("Synthetic unsaved title")
            for damage in ("partial", "foreign"):

                def corrupt(route):
                    payload = route.fetch().json()
                    if damage == "partial":
                        del payload["data"]["card"]["vehicle_profile"]
                    else:
                        payload["data"]["card"]["id"] = "synthetic-foreign"
                    route.fulfill(json=payload)

                page.route("**/api/get_card?card_id=*", corrupt)
                page.locator('#cardModal [data-tab="files"]').click()
                page.wait_for_function("!state.cardFilesRenderedFor")
                self.assertIn("КАРТОЧКА НЕ ПОДТВЕРЖДЕНА", page.locator("#statusLine").inner_text())
                self.assertEqual(page.evaluate("state.activeCard.id"), card_id)
                self.assertEqual(
                    page.evaluate("state.activeCard.vehicle_profile.make_display"), "Land Rover"
                )
                self.assertEqual(
                    page.evaluate(
                        "id => state.fullCardCache.get(id).vehicle_profile.model_display", card_id
                    ),
                    "Range Rover",
                )
                self.assertEqual(
                    page.locator("#cardTitle").input_value(), "Synthetic unsaved title"
                )
                page.unroute("**/api/get_card?card_id=*", corrupt)
                page.locator('#cardModal [data-tab="overview"]').click()
            page.locator('#cardModal [data-tab="files"]').click()
            page.locator("#fileList [data-preview-file]").first.wait_for()
            context.close()

    def test_confirmed_upload_with_partial_readback_keeps_receipt_and_draft(self) -> None:
        card_id = self.create_card("Synthetic file receipt")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Synthetic unsaved upload")
        self.page.locator('#cardModal [data-tab="files"]').click()

        def partial(route):
            payload = route.fetch().json()
            del payload["data"]["card"]["vehicle_profile"]
            route.fulfill(json=payload)

        self.page.route("**/api/get_card?card_id=*", partial)
        directory = tempfile.TemporaryDirectory(prefix="crm-ui-attachment-receipt-")
        self.addCleanup(directory.cleanup)
        upload = Path(directory.name) / "receipt.png"
        upload.write_bytes(PNG_1X1_BYTES)
        self.page.locator("#fileInput").set_input_files(upload)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertTrue(self.page.evaluate("Boolean(state.cardFileUploadCheckpoint?.receipts[0])"))
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Synthetic unsaved upload")
        self.assertEqual(self.page.evaluate("state.activeCard.id"), card_id)
        self.page.locator("#fileInput").set_input_files(upload)
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertEqual(self.writes.count("add_card_attachment"), 1)
        self.assertEqual(len(self.stored_card(card_id)["attachments"]), 1)

    def test_confirmed_delete_with_partial_readback_does_not_report_success(self) -> None:
        card_id = self.create_card("Synthetic file deletion")
        attachment_id = self.add_image(card_id, "synthetic-delete.png")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Synthetic unsaved delete")
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.locator("#fileList [data-preview-file]").first.wait_for()

        def partial(route):
            payload = route.fetch().json()
            del payload["data"]["card"]["vehicle_profile"]
            route.fulfill(json=payload)

        self.page.route("**/api/get_card?card_id=*", partial)
        self.page.locator(f'#fileList [data-remove-file="{attachment_id}"]').click()
        self.page.wait_for_function("!state.cardFilesMutationRequest")
        self.assertTrue(self.stored_card(card_id)["attachments"][0]["removed"])
        self.assertEqual(self.writes.count("remove_card_attachment"), 1)
        self.assertIn("НЕ ОПРЕДЕЛЕН", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.page.evaluate("state.activeCard.id"), card_id)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Synthetic unsaved delete")

    def test_ambiguous_delete_rejects_malformed_attachment_readback(self) -> None:
        card_id = self.create_card("Synthetic ambiguous deletion")
        attachment_id = self.add_image(card_id, "synthetic-still-present.png")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Synthetic unsaved deletion")
        self.page.locator('#cardModal [data-tab="files"]').click()
        self.page.locator("#fileList [data-preview-file]").first.wait_for()

        def reject_delete(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "Synthetic"}})

        self.page.route("**/api/remove_card_attachment", reject_delete)
        for malformed in ({}, {"id": attachment_id, "removed": "false"}):

            def malformed_read(route):
                payload = route.fetch().json()
                payload["data"]["card"]["attachments"] = [malformed]
                route.fulfill(json=payload)

            self.page.route("**/api/get_card?card_id=*", malformed_read)
            self.page.locator(f'#fileList [data-remove-file="{attachment_id}"]').click()
            self.page.wait_for_function("!state.cardFilesMutationRequest")
            self.assertIn("НЕ ОПРЕДЕЛЕН", self.page.locator("#statusLine").inner_text())
            self.assertFalse(self.stored_card(card_id)["attachments"][0]["removed"])
            self.assertEqual(
                self.page.evaluate("state.activeCard.attachments[0].id"), attachment_id
            )
            self.assertEqual(
                self.page.locator("#cardTitle").input_value(), "Synthetic unsaved deletion"
            )
            self.page.unroute("**/api/get_card?card_id=*", malformed_read)

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
        self.addCleanup(lambda: self.page.unroute_all(behavior="ignoreErrors"))
        self.addCleanup(lambda: [route.abort("failed") for route, _ in held])
        self.page.evaluate("""() => {
          const refresh = refreshActiveCardFiles;
          window.__filesRefreshFinished = false;
          refreshActiveCardFiles = (...args) => refresh(...args).finally(() => {
            window.__filesRefreshFinished = true;
          });
        }""")
        self.page.route(
            "**/api/get_card?card_id=*", lambda route: held.append((route, route.fetch()))
        )
        self.page.locator('#cardModal [data-tab="files"]').click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.page.locator('#cardModal [data-tab="overview"]').click()
        self.page.locator("#signalStartButton").click()
        self.page.wait_for_function("!state.cardTimerSaving && state.cardTimerState === 'running'")
        confirmed_revision = self.stored_card(card_id)["updated_at"]
        route, response = held[0]
        stale_card = response.json()["data"]["card"]
        self.assertNotEqual(stale_card["updated_at"], confirmed_revision)
        route.fulfill(response=response)
        held.pop()
        self.page.wait_for_function("window.__filesRefreshFinished === true")
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
        held = []

        def release_mock_requests():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release_mock_requests)
        self.page.route(
            "**/api/attachment?*",
            lambda route: (
                held.append(route) if route.request.resource_type == "fetch" else route.continue_()
            ),
        )
        self.preview(attachment_id)
        self.page.wait_for_timeout(160)
        self.assertFalse(self.page.evaluate("state.filePreview.loading"))
        self.assertTrue(self.page.evaluate("Boolean(state.filePreview.error)"))
        self.assertTrue(self.page.locator("#filePreviewCloseButton").is_enabled())
        release_mock_requests()

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
