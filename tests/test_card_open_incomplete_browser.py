"""A partial get_card response must not be offered as an editable card."""

# The browser fixture selects this checkout before importing the application.
# ruff: noqa: I001
import json

from tests.browser_ui_support import UiBrowserTestCase

from minimal_kanban.models import Card


class CardOpenIncompleteBrowserTests(UiBrowserTestCase):
    def check_empty_old_response_uses_new_read(self, *, newer_pending):
        card_id = self.create_card("SYNTHETIC EMPTY OLD RESPONSE")
        self.load_board()
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "id => { window.firstCard = 'pending'; fetchFullCard(id).then(card => { window.firstCard = card.id; }, () => { window.firstCard = 'rejected'; }); }",
            card_id,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { state.cardFetchInFlight.delete(id); window.secondCard = 'pending'; fetchFullCard(id).then(card => { window.secondCard = card.id; }); }",
            card_id,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        old, new = held
        if newer_pending:
            old.fulfill(status=200, json={"ok": True, "data": {}})
            self.page.wait_for_timeout(150)
            self.assertEqual(self.page.evaluate("window.firstCard"), "pending")
            new.continue_()
        else:
            new.continue_()
            self.page.wait_for_function("window.secondCard !== 'pending'")
            old.fulfill(status=200, json={"ok": True, "data": {}})
        self.page.wait_for_function(
            "window.firstCard !== 'pending' && window.secondCard !== 'pending'"
        )
        self.assertEqual(self.page.evaluate("window.firstCard"), card_id)
        self.assertEqual(self.page.evaluate("window.secondCard"), card_id)

    def test_empty_old_response_waits_for_newer_pending_read(self):
        self.check_empty_old_response_uses_new_read(newer_pending=True)

    def test_empty_old_response_uses_newer_verified_cache(self):
        self.check_empty_old_response_uses_new_read(newer_pending=False)

    def test_incomplete_cache_with_matching_revision_is_not_reused(self):
        card_id = self.create_card("SYNTHETIC CACHE CARD")
        self.load_board()
        revision = self.stored_card(card_id)["updated_at"]
        self.page.evaluate(
            "([id, revision]) => state.fullCardCache.set(id, {id, updated_at: revision})",
            [card_id, revision],
        )
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "([id, revision]) => { window.checkedCard = 'pending'; fetchFullCard(id, revision).then(card => { window.checkedCard = card; }); }",
            [card_id, revision],
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("window.checkedCard !== 'pending'")
        self.assertTrue(self.page.evaluate("mobileCardHasRequiredFields(window.checkedCard)"))
        self.assertEqual(self.page.evaluate("window.checkedCard.id"), card_id)

    def test_new_incomplete_cache_cannot_be_replaced_by_old_valid_response(self):
        card_id = self.create_card("SYNTHETIC CACHE CARD")
        self.load_board()
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "id => { window.checkedCard = 'pending'; fetchFullCard(id).then(() => { window.checkedCard = 'accepted'; }, () => { window.checkedCard = 'rejected'; }); }",
            card_id,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { state.cardFetchInFlight.delete(id); state.fullCardCache.set(id, {id, updated_at: 'new'}); }",
            card_id,
        )
        held.pop().continue_()
        self.page.wait_for_function("window.checkedCard !== 'pending'")
        self.assertEqual(self.page.evaluate("window.checkedCard"), "rejected")

    def test_new_pending_incomplete_response_rejects_old_valid_read(self):
        card_id = self.create_card("SYNTHETIC PENDING CARD")
        self.load_board()
        held = []
        self.page.route("**/api/get_card?**", lambda route: held.append(route))
        self.page.evaluate(
            "id => { window.firstCard = 'pending'; fetchFullCard(id).then(() => { window.firstCard = 'accepted'; }, () => { window.firstCard = 'rejected'; }); }",
            card_id,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { state.cardFetchInFlight.delete(id); window.secondCard = 'pending'; fetchFullCard(id).then(() => { window.secondCard = 'accepted'; }, () => { window.secondCard = 'rejected'; }); }",
            card_id,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop(0).continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("window.firstCard"), "pending")
        route = held.pop()
        payload = route.fetch().json()
        del payload["data"]["card"]["description"]
        route.fulfill(json=payload)
        self.page.wait_for_function(
            "window.firstCard !== 'pending' && window.secondCard !== 'pending'"
        )
        self.assertEqual(self.page.evaluate("window.firstCard"), "rejected")
        self.assertEqual(self.page.evaluate("window.secondCard"), "rejected")

    def test_incomplete_card_is_rejected_for_both_roles_and_views(self):
        card_id = self.create_card(
            "SYNTHETIC COMPLETE CARD", description="SYNTHETIC SAVED DESCRIPTION"
        )
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-incomplete-card-operator",
                "password": "synthetic-incomplete-card-password",
                "role": "operator",
                "permissions": [],
                "expected_permissions": [],
            },
        )
        self.assertEqual(created.status, 200)
        login = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={
                "username": "synthetic-incomplete-card-operator",
                "password": "synthetic-incomplete-card-password",
            },
        )
        self.assertEqual(login.status, 200)
        operator_token = login.json()["data"]["session"]["token"]
        legacy_card = Card.from_dict(
            {"id": "synthetic-legacy-card", "title": "SYNTHETIC LEGACY", "column": "inbox"}
        ).to_dict()

        for role, token in (("admin", self.session_token), ("operator", operator_token)):
            for device, width in (("desktop", 1440), ("mobile", 390)):
                with self.subTest(role=role, device=device):
                    context = self.browser.new_context(
                        viewport={"width": width, "height": 900},
                        extra_http_headers=self.runtime.auth_headers,
                    )
                    self.addCleanup(context.close)
                    context.add_init_script(
                        "localStorage.setItem('kanban-operator-session', "
                        + json.dumps(token)
                        + ");"
                    )
                    page = context.new_page()
                    page.goto(self.runtime.browser_url)
                    page.wait_for_function(
                        "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                    )
                    page.evaluate("stopSnapshotPolling()")
                    self.assertTrue(
                        page.evaluate("card => mobileCardHasRequiredFields(card)", legacy_card)
                    )
                    full = context.request.get(
                        self.runtime.base_url
                        + "/api/get_card?include_attachment_status=0&card_id="
                        + card_id,
                        headers={"X-Operator-Session": token},
                    )
                    self.assertEqual(full.status, 200)
                    self.assertTrue(
                        page.evaluate(
                            "card => mobileCardHasRequiredFields(card)", full.json()["data"]["card"]
                        )
                    )
                    selector = (
                        f'[data-mobile-card-id="{card_id}"]'
                        if device == "mobile"
                        else f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
                    )
                    writes = []
                    page.on(
                        "request",
                        lambda request: (
                            writes.append(request.url.rsplit("/", 1)[-1])
                            if request.method == "POST"
                            else None
                        ),
                    )
                    for missing in (
                        "description",
                        "vehicle_profile",
                        "attachments",
                        "profile_vin",
                        "card",
                    ):

                        def partial(route):
                            payload = route.fetch().json()
                            card = payload["data"]["card"]
                            if missing == "card":
                                del payload["data"]["card"]
                            elif missing == "profile_vin":
                                del card["vehicle_profile"]["vin"]
                            else:
                                del card[missing]
                            route.fulfill(json=payload)

                        page.route("**/api/get_card?**", partial)
                        page.locator(selector).first.click()
                        page.wait_for_function(
                            "!state.cardOpeningShell && !state.mobileCardOpenRequest && !state.mobileCardLoading"
                        )
                        self.assertFalse(
                            page.evaluate("state.activeCardIsFull"), f"{role}/{device}: {missing}"
                        )
                        self.assertFalse(page.evaluate("state.cardOpenSideEffectTimer !== null"))
                        self.assertNotIn("open_card", writes)
                        self.assertNotIn("update_card", writes)
                        page.unroute("**/api/get_card?**", partial)

                    page.locator(selector).first.click()
                    page.wait_for_function(
                        "id => state.activeCardIsFull && state.editingId === id", arg=card_id
                    )
                    page.evaluate("clearCardOpenSideEffectTimer()")
                    if device == "desktop":
                        self.assertEqual(
                            page.locator("#cardDescription").input_value(),
                            "SYNTHETIC SAVED DESCRIPTION",
                        )
                    else:
                        self.assertEqual(
                            page.evaluate("state.mobileCard.description"),
                            "SYNTHETIC SAVED DESCRIPTION",
                        )
                    context.close()
