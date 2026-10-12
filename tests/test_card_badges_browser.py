"""Personal read markers must not hide failed writes or overwrite newer card data."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class CardBadgesBrowserTests(UiBrowserTestCase):
    def test_personal_clones_share_badges_and_other_user_update_survives_reload(self) -> None:
        card_id = self.create_card(
            "Personal badge copies",
            source="mcp",
            tags=[{"label": "НАДО ЧТО ТО СДЕЛАТЬ", "color": "red"}],
        )
        self.load_board()
        self.page.locator("#boardSettingsButton").click()
        self.page.locator("#extraBoardColumnToggleButton").click()
        self.page.locator('[data-virtual-column="extra"]').wait_for()
        self.page.locator('[data-close="settings"]').click()
        cards = f'.card[data-card-id="{card_id}"]'
        self.assertEqual(self.page.locator(cards).count(), 2)
        self.assertEqual(self.page.locator(cards + " .card__unread-badge").count(), 2)
        self.page.evaluate("id => markCardSeen(id)", card_id)
        self.assertEqual(self.page.locator(cards + " .card__unread-badge").count(), 0)
        self.assertFalse(self.stored_card(card_id)["is_unread"])

        user = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "review-ui",
                "password": "synthetic-review-password",
                "permissions": ["employees_read_access"],
            },
        )
        self.assertEqual(user.status, 200)
        other_context = self.browser.new_context(
            viewport={"width": 1440, "height": 960},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(other_context.close)
        session = other_context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "review-ui", "password": "synthetic-review-password"},
        ).json()["data"]["session"]["token"]
        other_context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(session) + ");"
        )
        other = other_context.new_page()
        other.on("pageerror", lambda error: self.page_errors.append(str(error)))
        other.goto(self.runtime.browser_url)
        other.wait_for_function(
            "state.snapshot && state.operatorSessionToken && window.__AUTOSTOP_UI_BOUND__"
        )
        other.evaluate("stopSnapshotPolling()")
        self.assertEqual(other.locator(cards + " .card__unread-badge").count(), 1)
        other.locator(cards).click()
        other.wait_for_function("state.activeCardIsFull")
        other.locator("#cardTitle").fill("Other operator's update")
        other.locator("#saveCardButton").click()
        other.wait_for_function("state.cardSaveInFlight === false")
        self.page.evaluate("refreshSnapshot(true)")
        self.assertEqual(self.page.locator(cards + " .card__updated-badge").count(), 2)
        self.load_board()
        self.page.wait_for_function("id => boardCardElementsById(id).length === 2", arg=card_id)
        self.assertEqual(self.page.locator(cards + " .card__updated-badge").count(), 2)
        with self.page.expect_response("**/api/mark_card_seen"):
            self.page.locator(cards + ':not([data-virtual-card="true"])').hover()
        self.page.wait_for_function("id => !state.unreadSeenInFlight.has(id)", arg=card_id)
        self.assertEqual(self.page.locator(cards + " .card__updated-badge").count(), 0)
        self.assertFalse(self.stored_card(card_id)["has_unseen_update"])

    def test_stalled_seen_and_failed_readback_restore_badge_and_release_owner(self) -> None:
        self.context.add_init_script(
            "const originalTimeout = window.setTimeout; "
            "window.setTimeout = (fn, ms, ...args) => originalTimeout(fn, ms === 45000 ? 150 : ms, ...args);"
        )
        card_id = self.create_card("Unread timeout recovery", source="mcp")
        self.load_board()
        held = []
        self.page.route("**/api/mark_card_seen", lambda route: held.append(route))
        self.page.route(
            "**/api/get_card?*",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"code": "test_failure", "message": "Offline"}}
                ),
            ),
        )
        self.page.evaluate("id => markCardSeen(id)", card_id)
        self.assertEqual(
            self.page.locator(f'.card[data-card-id="{card_id}"] .card__unread-badge').count(), 1
        )
        self.assertFalse(self.page.evaluate("id => state.unreadSeenInFlight.has(id)", card_id))
        held.pop().abort("failed")

    def test_opening_new_card_acknowledges_seen_and_survives_reload(self) -> None:
        card_id = self.create_card("Unread opening must be durable", source="mcp")
        self.load_board()
        self.page.locator(f'.card[data-card-id="{card_id}"]').click()
        self.page.wait_for_function("state.activeCardIsFull === true")
        self.page.wait_for_timeout(1100)
        self.assertFalse(self.stored_card(card_id)["is_unread"])
        self.load_board()
        self.assertEqual(
            self.page.locator(f'.card[data-card-id="{card_id}"] .card__unread-badge').count(),
            0,
        )

    def test_failed_mark_seen_restores_new_badge_from_server(self) -> None:
        card_id = self.create_card("Unread with failed acknowledgement", source="mcp")
        self.load_board()
        selector = f'.card[data-card-id="{card_id}"] .card__unread-badge'
        self.assertEqual(self.page.locator(selector).count(), 1)
        self.page.route(
            "**/api/mark_card_seen",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"code": "test_failure", "message": "Test unavailable"}}
                ),
            ),
        )
        self.page.evaluate("id => markCardSeen(id)", card_id)
        self.assertEqual(self.page.locator(selector).count(), 1)
        self.assertTrue(self.stored_card(card_id)["is_unread"])

    def test_late_seen_reply_cannot_replace_newer_saved_card(self) -> None:
        card_id = self.create_card("Before old seen reply")
        self.load_board()
        held = []

        def hold_seen(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/mark_card_seen", hold_seen)
        self.page.evaluate("id => { void markCardSeen(id, {force:true}); }", card_id)
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("Saved after old seen request")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(150)
        self.assertEqual(
            self.page.evaluate("id => snapshotCardById(id).title", card_id),
            "Saved after old seen request",
        )
        self.assertEqual(self.stored_card(card_id)["title"], "Saved after old seen request")
