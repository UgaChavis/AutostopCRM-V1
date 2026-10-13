"""Dashboard drafts and staged saves on a disposable real CRM browser."""

from __future__ import annotations

import base64
import json
import time

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class DashboardMessageBrowserTests(UiBrowserTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.runtime.service.update_board_settings(
            {
                "actor_name": "synthetic-test-reset",
                "expected_revision": self.stored_message()["revision"],
                "display_dashboard_message": {"body_html": "", "image_file_ids": []},
            }
        )

    def open_editor(self) -> None:
        self.load_board()
        self.page.locator("#boardSettingsButton").click()
        self.page.locator("#editDisplayDashboardMessageButton").click()
        self.page.locator("#displayDashboardMessageModal.is-open").wait_for()
        self.page.wait_for_function("Boolean(state.displayDashboardMessage)")
        self.page.locator("#displayDashboardMessageEditor").fill("Synthetic dashboard draft")

    def add_images(self, count: int = 1) -> None:
        self.page.locator("#displayDashboardImageInput").set_input_files(
            [
                {"name": f"dashboard-{i}.png", "mimeType": "image/png", "buffer": PNG_1X1_BYTES}
                for i in range(count)
            ]
        )

    def save(self) -> None:
        self.page.locator("#displayDashboardMessageSaveButton").click()
        self.page.wait_for_function("!state.displayDashboardMessageSaving")

    def stored_message(self) -> dict:
        return self.runtime.service.get_display_dashboard()["message_board"]

    def assert_open_draft(self) -> None:
        self.assertTrue(self.page.locator("#displayDashboardMessageModal.is-open").is_visible())
        self.assertEqual(
            self.page.locator("#displayDashboardMessageEditor").inner_text(),
            "Synthetic dashboard draft",
        )
        self.assertTrue(self.page.locator("#displayDashboardMessageEditor").is_editable())
        self.assertTrue(self.page.locator("#displayDashboardMessageSaveButton").is_enabled())

    def assert_closed(self) -> None:
        self.assertFalse(
            self.page.locator("#displayDashboardMessageModal").evaluate(
                "e=>e.classList.contains('is-open')"
            )
        )

    @staticmethod
    def reject(route) -> None:
        route.fulfill(
            status=422,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": False,
                    "error": {"code": "synthetic_failure", "message": "Synthetic failure"},
                }
            ),
        )

    def test_pending_save_locks_fields_close_and_double_click(self) -> None:
        self.open_editor()
        self.add_images()
        held = []
        self.page.route("**/api/update_board_settings", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#displayDashboardMessageSaveButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        editor = self.page.locator("#displayDashboardMessageEditor")
        self.assertFalse(editor.is_editable())
        self.assertEqual(editor.get_attribute("contenteditable"), "false")
        self.assertTrue(self.page.locator("#displayDashboardImageInput").is_disabled())
        self.assertTrue(self.page.locator("#displayDashboardFontSize").is_disabled())
        self.assertTrue(self.page.locator("[data-dashboard-image-remove]").is_disabled())
        self.assertTrue(
            self.page.locator("#displayDashboardMessageModal [data-close]").first.is_disabled()
        )
        self.assertEqual(self.page.evaluate("closeNamedModal('settings')"), False)
        self.assertEqual(self.page.evaluate("closeNamedModal('display-dashboard-message')"), False)
        self.page.evaluate("saveDisplayDashboardMessage()")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.writes.count("update_board_settings"), 1)
        self.assertTrue(self.page.locator("#displayDashboardMessageModal.is-open").is_visible())
        held.pop().continue_()
        self.page.wait_for_function("!state.displayDashboardMessageSaving")
        self.assert_closed()
        self.assertEqual(self.stored_message()["body_html"], "Synthetic dashboard draft")

    def test_rejected_message_does_not_upload_confirmed_images_again(self) -> None:
        self.open_editor()
        self.add_images(2)
        self.page.route("**/api/update_board_settings", self.reject)
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("upload_shared_file"), 2)
        self.page.unroute("**/api/update_board_settings")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("upload_shared_file"), 2)
        self.assertEqual(len(self.stored_message()["image_file_ids"]), 2)

    def test_partial_image_upload_retries_only_rejected_image(self) -> None:
        self.open_editor()
        self.add_images(2)
        uploaded_ids = []

        def upload(route):
            if uploaded_ids:
                self.reject(route)
            else:
                response = route.fetch()
                uploaded_ids.append(response.json()["data"]["file"]["id"])
                route.fulfill(response=response)

        self.page.route("**/api/upload_shared_file", upload)
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("update_board_settings"), 0)
        self.page.unroute("**/api/upload_shared_file")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("upload_shared_file"), 3)
        self.assertEqual(self.stored_message()["image_file_ids"][0], uploaded_ids[0])

    def test_removing_confirmed_pending_photo_keeps_other_image_once(self) -> None:
        self.open_editor()
        self.add_images(2)
        self.page.route("**/api/update_board_settings", self.reject)
        self.save()
        self.assert_open_draft()
        ids = self.page.evaluate("state.displayDashboardPendingImages.map(i=>i.uploadedId)")
        self.page.locator("[data-dashboard-image-remove]").first.click()
        self.page.unroute("**/api/update_board_settings")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("upload_shared_file"), 2)
        self.assertEqual(self.stored_message()["image_file_ids"], [ids[1]])

    def test_lost_successful_message_reply_is_reconciled_without_second_write(self) -> None:
        self.open_editor()
        self.add_images()

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/api/update_board_settings", lose_reply)
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 1)
        self.assertEqual(self.stored_message()["body_html"], "Synthetic dashboard draft")

    def test_failed_readback_holds_write_until_read_then_original_revision_retry(self) -> None:
        self.open_editor()
        expected = self.page.evaluate("state.displayDashboardMessage.revision")
        self.page.route("**/api/update_board_settings", lambda route: route.abort("failed"))
        self.page.route("**/api/get_display_dashboard", self.reject)
        self.save()
        self.assert_open_draft()
        self.save()
        self.assertEqual(self.writes.count("update_board_settings"), 1)
        self.page.unroute("**/api/get_display_dashboard")
        self.page.unroute("**/api/update_board_settings")
        bodies = []

        def capture(route):
            bodies.append(route.request.post_data_json)
            route.continue_()

        self.page.route("**/api/update_board_settings", capture)
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("update_board_settings"), 2)
        self.assertEqual(bodies[0]["expected_revision"], expected)

    def test_lost_uploaded_file_reply_prevents_duplicate_upload(self) -> None:
        self.open_editor()
        self.add_images()
        uploaded = []

        def lose_reply(route):
            response = route.fetch()
            uploaded.append(response.json()["data"]["file"]["id"])
            route.abort("failed")

        self.page.route("**/api/upload_shared_file", lose_reply)
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(uploaded), 1)
        self.page.unroute("**/api/upload_shared_file")
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 0)

    def test_edited_draft_survives_later_confirmation_of_previous_save(self) -> None:
        self.open_editor()
        self.add_images()

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/api/update_board_settings", lose_reply)
        self.page.route("**/api/get_display_dashboard", self.reject)
        self.save()
        self.assert_open_draft()
        self.page.locator("#displayDashboardMessageEditor").fill("Newer synthetic draft")
        self.page.unroute("**/api/update_board_settings")
        self.page.unroute("**/api/get_display_dashboard")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 2)
        self.assertEqual(self.stored_message()["body_html"], "Newer synthetic draft")

    def test_message_write_deadline_restores_controls_and_preserves_draft(self) -> None:
        self.open_editor()
        self.page.evaluate(
            """() => {
              const original = globalThis.setTimeout;
              globalThis.setTimeout = (callback, delay, ...args) => original(callback, delay === 90000 ? 80 : delay, ...args);
            }"""
        )
        held = []
        self.page.route("**/api/update_board_settings", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(held), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 1)
        held.pop().abort("failed")
        self.assertNotEqual(self.stored_message()["body_html"], "Synthetic dashboard draft")

    def test_late_existing_photo_preview_keeps_remove_button_locked(self) -> None:
        uploaded = self.context.request.post(
            self.runtime.base_url + "/api/upload_shared_file",
            headers={"X-Operator-Session": self.session_token},
            data={
                "file_name": "existing-dashboard.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                "actor_name": "admin",
                "source": "ui",
                "x": 0,
                "y": 0,
            },
        ).json()["data"]["file"]
        self.runtime.service.update_board_settings(
            {
                "actor_name": "admin",
                "expected_revision": self.stored_message()["revision"],
                "display_dashboard_message": {
                    "body_html": "Existing",
                    "image_file_ids": [uploaded["id"]],
                },
            }
        )
        photos, messages = [], []
        self.page.route("**/api/shared_file?*", lambda route: photos.append(route))
        self.page.route("**/api/update_board_settings", lambda route: messages.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in photos + messages])
        self.open_editor()
        self.page.locator("#displayDashboardMessageSaveButton").click()
        deadline = time.monotonic() + 5
        while (not photos or not messages) and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(photos), 1)
        self.assertEqual(len(messages), 1)
        photos.pop().continue_()
        self.page.locator("[data-dashboard-image-remove]").wait_for()
        self.assertTrue(self.page.locator("[data-dashboard-image-remove]").is_disabled())
        messages.pop().continue_()
        self.page.wait_for_function("!state.displayDashboardMessageSaving")
        self.assert_closed()

    def test_image_upload_deadline_prevents_blind_repeat(self) -> None:
        self.open_editor()
        self.add_images()
        self.page.evaluate(
            """() => {
              const original = globalThis.setTimeout;
              globalThis.setTimeout = (callback, delay, ...args) => original(callback, delay === 90000 ? 80 : delay, ...args);
            }"""
        )
        held = []
        self.page.route("**/api/upload_shared_file", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(held), 1)
        held.pop().abort("failed")
        self.page.unroute("**/api/upload_shared_file")
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 0)

    def test_native_conflict_preserves_draft_and_other_users_message(self) -> None:
        self.open_editor()
        self.add_images()
        current = self.stored_message()
        self.runtime.service.update_board_settings(
            {
                "actor_name": "synthetic-other-user",
                "expected_revision": current["revision"],
                "display_dashboard_message": {
                    "body_html": "Synthetic other message",
                    "image_file_ids": [],
                },
            }
        )
        self.save()
        self.assert_open_draft()
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.stored_message()["body_html"], "Synthetic other message")

    def test_logout_clears_hold_and_late_reply_cannot_write_or_close_replacement(self) -> None:
        self.open_editor()
        self.add_images(2)
        held = []
        self.page.route("**/api/upload_shared_file", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#displayDashboardMessageSaveButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.page.evaluate("clearOperatorSession({openLogin:false})")
        self.assertTrue(self.page.locator("#displayDashboardMessageEditor").is_editable())
        self.assertTrue(self.page.locator("#displayDashboardMessageSaveButton").is_enabled())
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.page.evaluate("token=>{state.operatorSessionToken=token;state.actor='admin';}", token)
        self.page.locator("#boardSettingsButton").click()
        self.page.locator("#editDisplayDashboardMessageButton").click()
        self.page.locator("#displayDashboardMessageModal.is-open").wait_for()
        self.page.locator("#displayDashboardMessageEditor").fill("Replacement draft")
        held.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "data": {"file": {"id": "synthetic-old-file"}}}),
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(self.writes.count("upload_shared_file"), 1)
        self.assertEqual(self.writes.count("update_board_settings"), 0)
        self.assertTrue(self.page.locator("#displayDashboardMessageModal.is-open").is_visible())
        self.assertEqual(
            self.page.locator("#displayDashboardMessageEditor").inner_text(), "Replacement draft"
        )
