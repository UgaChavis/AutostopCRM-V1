"""Save must finish once, preserve drafts on failure and never recreate a saved ID."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class CardSaveBrowserTests(UiBrowserTestCase):
    def test_reconciled_noop_save_still_acknowledges_read_before_reload(self) -> None:
        card_id = self.create_card("Unchanged card with lost request", source="mcp")
        self.load_board()
        self.page.locator(f'.card[data-card-id="{card_id}"]').click()
        self.page.wait_for_function("state.activeCardIsFull === true")
        self.page.route("**/api/update_card", lambda route: route.abort("failed"))
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.page.wait_for_timeout(750)
        self.assertFalse(self.stored_card(card_id)["is_unread"])
        self.load_board()
        self.assertEqual(
            self.page.locator(f'.card[data-card-id="{card_id}"] .card__unread-badge').count(), 0
        )

    def test_snapshot_sampled_before_save_cannot_restore_previous_card_content(self) -> None:
        card_id = self.create_card("Before held snapshot")
        self.load_board()
        self.open_card(card_id)
        held = []

        def hold_snapshot(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/get_board_snapshot?*", hold_snapshot)
        self.page.evaluate("() => { void refreshSnapshot(true); }")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.locator("#cardTitle").fill("Saved after held snapshot")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(150)
        self.assertEqual(
            self.page.evaluate("id => snapshotCardById(id).title", card_id),
            "Saved after held snapshot",
        )

    def test_lost_update_reply_is_read_back_before_success_without_second_write(self) -> None:
        card_id = self.create_card("Lost update before")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Lost update confirmed")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/update_card", lose_reply)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.writes.count("update_card"), 1)
        self.assertEqual(self.stored_card(card_id)["title"], "Lost update confirmed")

    def test_lost_create_reply_keeps_draft_and_never_blindly_recreates_card(self) -> None:
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Uncertain create kept once")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_card", lose_reply)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(), "Uncertain create kept once"
        )
        self.assertEqual(
            sum(
                card["title"] == "Uncertain create kept once"
                for card in self.runtime.service.get_cards()["cards"]
            ),
            1,
        )

    def test_lost_link_reply_is_read_back_and_does_not_repeat_create_or_link(self) -> None:
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Lost link confirmed")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name:'Smoke Клиент'})",
            self.runtime.client_id,
        )

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/link_card_to_client", lose_reply)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        card = next(
            card
            for card in self.runtime.service.get_cards()["cards"]
            if card["title"] == "Lost link confirmed"
        )
        self.assertEqual(self.stored_card(card["id"])["client_id"], self.runtime.client_id)

    def test_stalled_module_load_restores_controls_and_cannot_write_on_late_load(self) -> None:
        self.context.add_init_script(
            "const originalTimeout = window.setTimeout; "
            "window.setTimeout = (fn, ms, ...args) => originalTimeout(fn, ms === 15000 ? 150 : ms, ...args);"
        )
        card_id = self.create_card("Cold save module")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Cold save module draft")
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        held = []
        self.page.route("**" + module_path, lambda route: held.append(route))
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertTrue(self.page.locator("#cardTitle").is_editable())
        self.assertFalse(self.page.locator("#saveCardButton").is_disabled())
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.stored_card(card_id)["title"], "Cold save module")
        self.page.unroute("**" + module_path)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.stored_card(card_id)["title"], "Cold save module draft")

    def test_one_click_waits_for_own_reply_and_freezes_conflicting_edits(self) -> None:
        card_id = self.create_card("Save lock before", description="Original full description")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Save lock after")
        held = []
        self.page.route("**/api/update_card", lambda route: held.append(route))
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("state.cardSaveInFlight")
        self.page.wait_for_timeout(60)
        self.assert_card_open()
        self.assertTrue(self.page.locator("#cardTitle").is_disabled())
        self.assertEqual(
            self.page.locator("#cardDescriptionEditor").get_attribute("contenteditable"),
            "false",
        )
        # Repeated dispatch and close attempts exercise the actual event handlers.
        self.page.evaluate("els.saveCardButton.click(); closeCardModal();")
        self.assertEqual(len(held), 1)
        self.assertEqual(self.writes.count("update_card"), 1)
        held.pop().continue_()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.stored_card(card_id)["title"], "Save lock after")
        self.assertFalse(self.page.locator("#cardTitle").is_disabled())

    def test_partial_create_keeps_id_draft_and_retries_only_client_link(self) -> None:
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Partial create one ID")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name:'Smoke Клиент'})",
            self.runtime.client_id,
        )

        def fail_link(route):
            route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "validation_error", "message": "Test link failed"},
                    }
                ),
            )

        self.page.route("**/api/link_card_to_client", fail_link)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        saved_id = self.page.evaluate("state.editingId")
        self.assertTrue(saved_id, "Confirmed create ID was forgotten when client link failed")
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Partial create one ID")
        self.assertFalse(self.page.locator("#saveCardButton").is_disabled())
        self.assertFalse(self.stored_card(saved_id).get("client_id"))
        self.page.unroute("**/api/link_card_to_client", fail_link)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.writes.count("link_card_to_client"), 2)
        self.assertEqual(self.stored_card(saved_id)["client_id"], self.runtime.client_id)

    def test_native_version_conflict_preserves_draft_and_restores_controls(self) -> None:
        card_id = self.create_card("Conflict before")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Retained conflicting draft")
        self.runtime.service.update_card(
            {"card_id": card_id, "title": "Other user's title", "actor_name": "other"}
        )
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(), "Retained conflicting draft"
        )
        self.assertTrue(self.page.locator("#cardTitle").is_editable())
        self.assertFalse(self.page.locator("#saveCardButton").is_disabled())
        self.assertEqual(self.stored_card(card_id)["title"], "Other user's title")

    def test_same_card_repair_workspace_refresh_does_not_cancel_confirmed_save(self) -> None:
        card_id = self.create_card("Workspace save before")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Workspace save after")
        held = []
        self.page.route("**/api/update_card", lambda route: held.append(route))
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("state.cardSaveInFlight")
        self.page.wait_for_timeout(60)
        self.page.evaluate("refreshRepairOrderEntry(state.activeCard)")
        held.pop().continue_()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.stored_card(card_id)["title"], "Workspace save after")

    def test_reply_for_closed_workspace_cannot_touch_next_card(self) -> None:
        first = self.create_card("Previous save workspace")
        second = self.create_card("Next save workspace")
        self.load_board()
        self.open_card(first)
        self.page.locator("#cardTitle").fill("Previous request saved")
        held = []
        self.page.route("**/api/update_card", lambda route: held.append(route))
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("state.cardSaveInFlight")
        self.page.wait_for_timeout(60)
        # Model a session/UI reset, which legitimately invalidates the old workspace.
        self.page.evaluate("resetCardModalState(); popModal('card')")
        self.open_card(second)
        self.page.locator("#cardTitle").fill("Next card draft")
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assert_card_open()
        self.assertEqual(self.page.evaluate("state.editingId"), second)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Next card draft")
        self.assertEqual(self.stored_card(second)["title"], "Next save workspace")
