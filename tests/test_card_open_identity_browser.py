"""A card response must belong to the board card the operator selected."""

from copy import deepcopy

from tests.browser_ui_support import UiBrowserTestCase


class CardOpenIdentityBrowserTests(UiBrowserTestCase):
    def exercise_wrong_response(self, *, mobile):
        selected = self.create_card("SYNTHETIC SELECTED CARD")
        other = self.create_card("SYNTHETIC OTHER CARD")
        self.page.set_viewport_size({"width": 390 if mobile else 1440, "height": 900})
        self.load_board()
        response = self.context.request.get(
            self.runtime.base_url + "/api/get_card?include_attachment_status=0&card_id=" + other,
            headers={"X-Operator-Session": self.session_token},
        )
        self.assertEqual(response.status, 200)
        payload = response.json()
        selector = (
            f'[data-mobile-card-id="{selected}"]'
            if mobile
            else f'.card[data-card-id="{selected}"]:not([data-virtual-card="true"])'
        )
        for bad_id in (other, "", " " + selected, None):
            bad_payload = deepcopy(payload)
            bad_payload["data"]["card"]["id"] = bad_id

            def bad_response(route):
                route.fulfill(status=200, json=bad_payload)

            self.page.route("**/api/get_card?**", bad_response)
            self.page.locator(selector).first.click()
            self.page.wait_for_function(
                "!state.cardOpeningShell && !state.mobileCardOpenRequest && !state.mobileCardLoading"
            )
            self.assertFalse(self.page.evaluate("state.activeCardIsFull"))
            self.assertNotEqual(self.page.evaluate("state.editingId"), other)
            self.assertIsNone(self.page.evaluate("state.cardOpenSideEffectTimer"))
            self.assertNotIn("open_card", self.writes)
            self.assertFalse(self.page.evaluate("id => state.fullCardCache.has(id)", other))
            self.page.unroute("**/api/get_card?**", bad_response)

        self.page.locator(selector).first.click()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=selected
        )
        self.page.evaluate("clearCardOpenSideEffectTimer()")
        self.assertEqual(self.page.evaluate("state.activeCard.id"), selected)

    def test_desktop_rejects_wrong_or_invalid_id_and_retries(self):
        self.exercise_wrong_response(mobile=False)

    def test_mobile_rejects_wrong_or_invalid_id_and_retries(self):
        self.exercise_wrong_response(mobile=True)

    def test_superseded_wrong_response_keeps_newer_verified_read(self):
        selected = self.create_card("SYNTHETIC SELECTED CARD")
        other = self.create_card("SYNTHETIC OTHER CARD")
        self.load_board()
        response = self.context.request.get(
            self.runtime.base_url + "/api/get_card?include_attachment_status=0&card_id=" + other,
            headers={"X-Operator-Session": self.session_token},
        )
        self.assertEqual(response.status, 200)
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "id => { window.firstRead = 'pending'; fetchFullCard(id).then(card => { window.firstRead = card.id; }, () => { window.firstRead = 'error'; }); }",
            selected,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { state.cardFetchInFlight.delete(id); window.secondRead = 'pending'; fetchFullCard(id).then(card => { window.secondRead = card.id; }); }",
            selected,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop().continue_()
        self.page.wait_for_function("window.secondRead !== 'pending'")
        held.pop().fulfill(status=200, json=response.json())
        self.page.wait_for_function("window.firstRead !== 'pending'")
        self.assertEqual(self.page.evaluate("window.firstRead"), selected)
        self.assertEqual(self.page.evaluate("window.secondRead"), selected)

    def test_superseded_wrong_response_waits_for_newer_pending_read(self):
        selected = self.create_card("SYNTHETIC SELECTED CARD")
        other = self.create_card("SYNTHETIC OTHER CARD")
        self.load_board()
        response = self.context.request.get(
            self.runtime.base_url + "/api/get_card?include_attachment_status=0&card_id=" + other,
            headers={"X-Operator-Session": self.session_token},
        )
        self.assertEqual(response.status, 200)
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "id => { window.firstRead = 'pending'; fetchFullCard(id).then(card => { window.firstRead = card.id; }, () => { window.firstRead = 'error'; }); }",
            selected,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { state.cardFetchInFlight.delete(id); window.secondRead = 'pending'; fetchFullCard(id).then(card => { window.secondRead = card.id; }); }",
            selected,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(status=200, json=response.json())
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("window.firstRead"), "pending")
        held.pop().continue_()
        self.page.wait_for_function(
            "window.firstRead !== 'pending' && window.secondRead !== 'pending'"
        )
        self.assertEqual(self.page.evaluate("window.firstRead"), selected)
        self.assertEqual(self.page.evaluate("window.secondRead"), selected)
