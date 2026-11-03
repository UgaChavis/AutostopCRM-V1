"""Opening a board card while its fresh data is delayed."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class CardOpenLoadingBrowserTests(UiBrowserTestCase):
    def test_loading_from_employee_report_is_visible_and_returns_focus(self) -> None:
        card_id = self.create_card("Synthetic employee loading")
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        self.page.evaluate(
            """id => {
          document.getElementById('employeesReportShell').dataset.detailsOpen = 'true';
          const row = document.createElement('tr');
          row.dataset.cardId = id;
          const cell = document.createElement('td');
          cell.textContent = 'Synthetic card row';
          row.appendChild(cell);
          document.getElementById('employeesDetailTable').replaceChildren(row);
        }""",
            card_id,
        )
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.get_by_text("Synthetic card row").click()
        self.page.locator('#cardModal[data-card-loading="true"].is-open').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1).parentKey"), "employees")
        close = self.page.locator("#cardModalCloseButtonTop")
        self.assertEqual(
            close.evaluate("""element => {
          const rect = element.getBoundingClientRect();
          return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
            ?.closest('.modal')?.id;
        }"""),
            "cardModal",
        )
        self.assertTrue(self.page.locator(".card-loading-status").is_visible())
        self.assertTrue(close.evaluate("e => document.activeElement === e"))
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=card_id
        )
        self.assertTrue(close.evaluate("e => document.activeElement === e"))
        close.click()
        self.assertTrue(self.page.locator("#employeesModal.is-open").is_visible())
        self.page.wait_for_function(
            "document.activeElement.matches('#employeesModal [data-close=employees]')"
        )

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
        self.assertTrue(self.page.locator(".card-loading-status").is_visible())
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "cardModalCloseButtonTop")
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
        self.page.wait_for_function("document.activeElement.id === 'cardModalCloseButtonTop'")
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

    def test_reopen_after_cancel_reads_fresh_and_rejects_old_cache_reply(self) -> None:
        card_id = self.create_card("Synthetic before")
        self.load_board()
        held = []

        def hold_response(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/get_card?**", hold_response)
        tile = self.page.locator(f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])')
        tile.click()
        self.page.locator('#cardModal[data-card-loading="true"]').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.keyboard.press("Escape")
        self.runtime.service.update_card(
            {"card_id": card_id, "title": "Synthetic after", "actor_name": "other"}
        )
        tile.click()
        self.page.locator('#cardModal[data-card-loading="true"]').wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2, "Reopening after cancellation must issue a new GET")
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_function("state.activeCardIsFull")
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Synthetic after")
        route, response = held.pop()
        with self.page.expect_response("**/api/get_card?**") as old_response:
            route.fulfill(response=response)
        old_response.value.body()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "Synthetic after")
        self.assertEqual(
            self.page.evaluate("id => state.fullCardCache.get(id)?.title", card_id),
            "Synthetic after",
        )

    def test_repeated_open_without_cancel_deduplicates_the_same_read(self) -> None:
        card_id = self.create_card("Synthetic shared request")
        self.load_board()
        held = self.start_open(card_id)
        self.page.evaluate("id => { void openCardById(id); }", card_id)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=card_id
        )

    def test_cancel_does_not_discard_a_newer_independent_verification_read(self) -> None:
        card_id = self.create_card("Synthetic verification read")
        self.load_board()
        held = self.start_open(card_id)
        self.page.evaluate(
            """id => {
          state.repairOrderPaymentVerificationPending = id;
          window.independentCardRead = 'pending';
          fetchFullCard(id).then(card => { window.independentCardRead = card?.title || 'missing'; });
        }""",
            card_id,
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.evaluate("id => state.cardFetchInFlight.has(id)", card_id))
        held.pop().continue_()
        self.page.wait_for_function("window.independentCardRead !== 'pending'")
        self.assertEqual(
            self.page.evaluate("window.independentCardRead"), "Synthetic verification read"
        )
        self.assertEqual(self.page.evaluate("state.repairOrderPaymentVerificationPending"), card_id)
        held.pop().fulfill(
            status=200,
            json={"ok": True, "data": {"card": {"id": card_id, "title": "Stale synthetic reply"}}},
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(
            self.page.evaluate("id => state.fullCardCache.get(id)?.title", card_id),
            "Synthetic verification read",
        )

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

    def test_new_card_supersedes_a_pending_open_without_losing_its_draft(self) -> None:
        card_id = self.create_card("Synthetic pending card")
        self.load_board()
        held = self.start_open(card_id)
        self.page.evaluate("openNewCardInColumn('inbox')")
        self.page.locator("#cardTitle").fill("New synthetic draft")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator("#cardModal.is-open").is_visible())
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "New synthetic draft")
        self.assertIsNone(self.page.evaluate("state.editingId"))

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

    def test_access_denial_does_not_show_card_or_trigger_open_effect(self) -> None:
        card_id = self.create_card("Synthetic denied card")
        self.load_board()
        self.page.route(
            "**/api/get_card?**",
            lambda route: route.fulfill(
                status=403,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic access denied"}}),
            ),
        )
        self.page.locator(
            f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        ).click()
        self.page.locator("#statusLine").get_by_text("Synthetic access denied").wait_for()
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertIsNone(self.page.evaluate("state.activeCard"))
        self.assertNotIn("open_card", self.writes)

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
        self.page.locator("#cardModalCloseButtonTop").click()
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
