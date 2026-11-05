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
        with self.page.expect_response("**/api/create_client") as response:
            held.pop().continue_()
        response.value.body()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_timeout(180)
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.assertFalse(
            self.page.locator("#clientsModal").evaluate("e=>e.classList.contains('is-open')")
        )

    def save_profile(self) -> None:
        self.page.evaluate("async () => { await saveClientProfile(); }")

    def test_pending_lazy_profile_save_is_not_already_complete(self) -> None:
        self.open_new_profile("Synthetic delayed profile module")
        self.page.evaluate(
            """() => {
              boardModuleRecords.delete('card_save');
              state.clientProfileSaveCheckpoint = {effectKey: 'previous', uncertain: true};
            }"""
        )
        path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        held = []
        self.page.route("**" + path, lambda route: held.append(route))
        with self.page.expect_request("**" + path):
            self.page.locator("#clientSaveButton").click()
        self.assertTrue(
            self.page.evaluate(
                "state.clientProfileSaveCheckpoint !== null && !state.clientMutationRequest"
            )
        )
        self.assertEqual(self.writes.count("create_client"), 0)
        self.assertEqual(len(held), 1)
        with self.page.expect_response("**/api/create_client"):
            held.pop().continue_()
        self.page.wait_for_function("!state.clientMutationRequest && state.clientsActiveId")
        self.assertEqual(self.writes.count("create_client"), 1)

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
                status=400,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "validation_error", "message": "Synthetic validation"},
                    }
                ),
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
        with self.page.expect_response("**/api/create_client"):
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

    def test_create_resize_during_list_refresh_keeps_confirmed_id(self) -> None:
        self.open_new_profile("Synthetic create resize")
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        with self.page.expect_request("**/api/create_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("() => state.clientsActiveId && state.clientMutationRequest")
        self.page.wait_for_function("() => state.clientsMetaState === null")
        client_id = self.page.evaluate("state.clientsActiveId")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("document.body.classList.contains('is-mobile-lite')")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!document.body.classList.contains('is-mobile-lite')")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), client_id)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => !state.clientMutationRequest && state.clientsActiveProfile?.client?.id === id",
            arg=client_id,
        )
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertTrue(self.page.locator("#clientSaveButton").is_disabled())

    def test_pending_create_survives_close_and_reopen(self) -> None:
        self.open_new_profile("Synthetic create reopen")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        with self.page.expect_request("**/api/create_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.locator('[data-close="clients"]').click()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientNewButton").click()
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "Synthetic create reopen"
        )
        with self.page.expect_response("**/api/create_client"):
            held.pop().continue_()
        self.page.wait_for_function(
            "() => !state.clientMutationRequest && state.clientsActiveProfile?.client?.id"
        )
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_unknown_create_stays_blocked_after_close_and_reopen(self) -> None:
        self.open_new_profile("Synthetic unknown reopen")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        with self.page.expect_request("**/api/create_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.locator('[data-close="clients"]').click()
        self.page.locator("#clientsButton").click()
        route = held.pop()
        self.assertEqual(route.fetch().status, 200)
        route.abort("failed")
        self.page.wait_for_function(
            "() => !state.clientMutationRequest && state.clientProfileSaveCheckpoint?.uncertain"
        )
        self.page.locator('[data-close="clients"]').click()
        self.page.locator("#clientsButton").click()
        self.save_profile()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "Synthetic unknown reopen"
        )

    def test_unknown_create_is_bound_to_exact_body_across_new_forms(self) -> None:
        self.open_new_profile("Synthetic uncertain exact body")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_client", lose_reply)
        self.save_profile()
        self.assertEqual(self.page.evaluate("state.clientProfileUncertainEffects.size"), 1)
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("Synthetic uncertain exact body")
        self.save_profile()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.page.unroute("**/api/create_client")
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("Synthetic different body")
        self.save_profile()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.page.evaluate("state.clientProfileUncertainEffects.size"), 1)
        self.page.evaluate("clearOperatorSession()")
        self.assertEqual(self.page.evaluate("state.clientProfileUncertainEffects.size"), 0)

    def test_unknown_update_survives_other_client_selection(self) -> None:
        first, second = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in ("AAA SYNTHETIC UPDATE A", "AAB SYNTHETIC UPDATE B")
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC UPDATE")
        self.page.locator(f'#clientsList [data-client-id="{second}"]').wait_for()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC FIRST UPDATE")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/update_client", lose_reply)
        self.page.route(
            "**/api/get_client?**",
            lambda route: route.fulfill(
                status=500, json={"ok": False, "error": {"message": "synthetic"}}
            ),
        )
        self.save_profile()
        self.assertEqual(self.page.evaluate("state.clientProfileUncertainEffects.size"), 1)
        self.page.unroute("**/api/get_client?**")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'#clientsList [data-client-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=second
        )
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "")
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SECOND UPDATE")
        self.save_profile()
        self.assertEqual(self.writes.count("update_client"), 1)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SECOND UPDATE"
        )
        self.assertEqual(self.page.evaluate("state.clientProfileUncertainEffects.size"), 0)
        self.page.unroute("**/api/update_client")
        self.save_profile()
        self.assertEqual(self.writes.count("update_client"), 2)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": second})["client"]["comment"], ""
        )
