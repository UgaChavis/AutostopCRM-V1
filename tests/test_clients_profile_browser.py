"""Client profile writes and editor controls on disposable browser/storage state."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientsProfileBrowserTests(UiBrowserTestCase):
    def test_logout_invalidates_pending_profile_and_restores_owned_controls(self) -> None:
        self.open_new_profile("Client profile before logout")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("Boolean(state.clientMutationRequest)")
        self.page.wait_for_timeout(60)
        self.assertEqual(len(held), 1)
        self.page.evaluate("clearOperatorSession()")
        self.assertFalse(self.page.locator("#clientDisplayNameInput").is_disabled())
        self.assertIsNone(self.page.evaluate("state.clientProfileSaveCheckpoint"))
        held.pop().continue_()
        self.page.wait_for_timeout(180)
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.assertFalse(
            self.page.locator("#clientsModal").evaluate("e=>e.classList.contains('is-open')")
        )

    def save_profile(self) -> None:
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint !== null && !state.clientMutationRequest"
        )

    def test_lost_update_reply_verifies_normalized_data_without_repeating_write(self) -> None:
        self.create_client_and_open("Client profile update once")
        self.page.locator("#clientDisplayNameInput").fill("  Client profile updated once  ")
        self.page.locator("#clientPhoneInput").fill("8 999 123 45 67")
        self.page.locator("#clientPhoneAddButton").click()
        self.page.locator("#clientPhoneInput2").fill("+7 999 123 45 67")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/update_client", lose_reply)
        self.save_profile()
        self.assertEqual(self.writes.count("update_client"), 1)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(),
            "Client profile updated once",
        )
        self.assertTrue(self.page.locator("#clientSaveButton").is_disabled())
        self.assertEqual(self.page.locator("[data-client-phone-input]").count(), 1)

    def test_profile_refresh_keeps_controls_locked_until_confirmed_data_is_rendered(self) -> None:
        self.create_client_and_open("Client profile refreshing")
        self.page.locator("#clientDisplayNameInput").fill("Client profile refreshed")
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        with self.page.expect_request("**/api/update_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("state.clientsActiveId && state.clientsMetaState === null")
        self.page.wait_for_timeout(60)
        self.assertTrue(self.page.locator("#clientDisplayNameInput").is_disabled())
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertFalse(self.page.locator("#clientDisplayNameInput").is_disabled())
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "Client profile refreshed"
        )

    def test_validation_error_restores_controls_and_keeps_profile_draft(self) -> None:
        self.open_new_profile("Client profile validation draft")
        self.page.route(
            "**/api/create_client",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic validation"}}),
            ),
        )
        self.save_profile()
        self.assertFalse(self.page.locator("#clientDisplayNameInput").is_disabled())
        self.assertFalse(self.page.locator("#clientSaveButton").is_disabled())
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(),
            "Client profile validation draft",
        )
        self.page.unroute("**/api/create_client")
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest && state.clientsActiveId")
        self.assertEqual(self.writes.count("create_client"), 2)

    def open_new_profile(self, name: str) -> None:
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill(name)

    def test_pending_profile_save_freezes_draft_and_preserves_one_write(self) -> None:
        self.open_new_profile("Client profile controls")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("Boolean(state.clientMutationRequest)")
        self.page.wait_for_timeout(60)
        self.assertTrue(self.page.locator("#clientDisplayNameInput").is_disabled())
        self.page.evaluate("els.clientSaveButton.click()")
        self.assertTrue(self.page.locator("#clientSaveButton").is_disabled())
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "!state.clientMutationRequest && Boolean(state.clientsActiveId)"
        )
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_lost_profile_create_reply_blocks_recreation_and_preserves_draft(self) -> None:
        self.open_new_profile("Client profile uncertain once")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_client", lose_reply)
        self.save_profile()
        self.save_profile()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(),
            "Client profile uncertain once",
        )
