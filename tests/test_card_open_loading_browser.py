"""Opening a board card while its fresh data is delayed."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class CardOpenLoadingBrowserTests(UiBrowserTestCase):
    def start_open(self, card_id: str):
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.locator(
            f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        ).click()
        self.page.locator('#cardModal[data-card-loading="true"].is-open').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        return held

    def test_only_loading_and_close_are_available_until_fresh_card_arrives(self) -> None:
        card_id = self.create_card("Synthetic delayed card")
        self.load_board()
        held = self.start_open(card_id)
        self.assertTrue(self.page.get_by_text("ЗАГРУЗКА КАРТОЧКИ...").is_visible())
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "cardOpeningCloseButton")
        self.assertFalse(self.page.locator("#cardVehicle").is_visible())
        self.assertFalse(self.page.locator("#saveCardButton").is_visible())
        self.assertFalse(self.page.locator("#archiveAction").is_visible())
        self.assertFalse(self.page.locator("#cardModal [data-tab=files]").is_visible())
        self.assertFalse(self.page.locator("#signalStartButton").is_visible())
        self.assertIsNone(self.page.evaluate("state.activeCard"))
        held.pop().continue_()
        self.page.wait_for_function(
            "state.activeCardIsFull && state.editingId === " + json.dumps(card_id)
        )
        self.assertTrue(self.page.locator("#cardVehicle").is_visible())
        self.page.wait_for_function("document.activeElement.id === 'cardVehicle'")
        self.assertEqual(
            self.page.evaluate("state.modalStack.filter(e => e.key === 'card').length"), 1
        )

    def test_escape_closes_loading_and_late_response_cannot_reopen_it(self) -> None:
        card_id = self.create_card("Synthetic cancelled card")
        self.load_board()
        held = self.start_open(card_id)
        self.page.keyboard.press("Escape")
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        with self.page.expect_response("**/api/get_card?**") as response:
            held.pop().continue_()
        response.value.body()
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertIsNone(self.page.evaluate("state.activeCard"))

    def test_newer_card_owns_loading_and_old_reply_is_ignored(self) -> None:
        first = self.create_card("Synthetic first card")
        second = self.create_card("Synthetic second card")
        self.load_board()
        held = self.start_open(first)
        self.page.evaluate("id => { void openCardById(id); }", second)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        held.pop(0).continue_()
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('#cardModal[data-card-loading="true"]').is_visible())
        self.assertIsNone(self.page.evaluate("state.activeCard"))
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=second
        )
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Synthetic second card")

    def test_old_error_does_not_close_the_new_loading_card(self) -> None:
        first = self.create_card("Synthetic old error")
        second = self.create_card("Synthetic current card")
        self.load_board()
        held = self.start_open(first)
        self.page.evaluate("id => { void openCardById(id); }", second)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": {"message": "Obsolete synthetic error"}}),
        )
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('#cardModal[data-card-loading="true"]').is_visible())
        self.assertNotIn("Obsolete synthetic error", self.page.locator("#statusLine").inner_text())
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=second
        )

    def test_error_closes_loading_and_reports_failure(self) -> None:
        card_id = self.create_card("Synthetic failed card")
        self.load_board()
        self.page.route(
            "**/api/get_card?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic read failure"}}),
            ),
        )
        self.page.locator(
            f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        ).click()
        self.page.locator("#statusLine").get_by_text("Synthetic read failure").wait_for()
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertIsNone(self.page.evaluate("state.activeCard"))

    def test_parent_remains_open_and_focus_returns_when_loading_is_closed(self) -> None:
        card_id = self.create_card("Synthetic parent card")
        self.load_board()
        self.page.locator("#archiveButton").click()
        self.page.locator("#archiveModal.is-open").wait_for()
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.locator("#archiveSearchInput").focus()
        self.page.evaluate("id => { void openCardById(id); }", card_id)
        self.page.locator('#cardModal[data-card-loading="true"]').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.locator("#cardOpeningCloseButton").click()
        self.assertTrue(self.page.locator("#archiveModal.is-open").is_visible())
        self.page.wait_for_function("document.activeElement.id === 'archiveSearchInput'")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )

    def test_closing_parent_invalidates_its_loading_child(self) -> None:
        card_id = self.create_card("Synthetic parent closed")
        self.load_board()
        self.page.locator("#archiveButton").click()
        self.page.locator("#archiveModal.is-open").wait_for()
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate("id => { void openCardById(id); }", card_id)
        self.page.locator('#cardModal[data-card-loading="true"]').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.evaluate("closeNamedModal('archive')")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertFalse(
            self.page.locator("#archiveModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertIsNone(self.page.evaluate("state.activeCard"))

    def test_loaded_card_keeps_its_parent_and_restores_opener_focus(self) -> None:
        card_id = self.create_card("Synthetic parent loaded")
        self.load_board()
        self.page.locator("#archiveButton").click()
        self.page.locator("#archiveModal.is-open").wait_for()
        self.page.locator("#archiveSearchInput").focus()
        self.page.evaluate("id => { void openCardById(id); }", card_id)
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=card_id
        )
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1).parentKey"), "archive")
        self.page.locator("#cardModalCloseButtonTop").click()
        self.assertTrue(self.page.locator("#archiveModal.is-open").is_visible())
        self.page.wait_for_function("document.activeElement.id === 'archiveSearchInput'")

    def test_logout_during_loading_rejects_late_reply(self) -> None:
        card_id = self.create_card("Synthetic logged out")
        self.load_board()
        held = self.start_open(card_id)
        self.page.evaluate("clearOperatorSession()")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertIsNone(self.page.evaluate("state.activeCard"))
