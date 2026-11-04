"""Repeated client selections share only the same active UI request."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileSelectionDedupeBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.runtime.service.create_client({"display_name": "SYNTHETIC SELECT FIRST"})[
            "client"
        ]["id"]
        self.second = self.runtime.service.create_client(
            {"display_name": "SYNTHETIC SELECT SECOND"}
        )["client"]["id"]

    def open_clients(self, mobile=False):
        self.page.set_viewport_size({"width": 390 if mobile else 1440, "height": 900})
        self.load_board()
        if mobile:
            self.page.click('[data-mobile-view="more"]')
            self.page.click('[data-mobile-open="clients"]')
        else:
            self.page.click("#clientsButton")
        self.page.wait_for_function(
            "ids => state.clientsLoaded && ids.every(id => state.clients.some(client => client.id === id))",
            arg=[self.first, self.second],
        )
        if mobile:
            self.page.wait_for_function("!state.mobileClientsLoading")

    def select(self, client, mobile=False):
        container = "#mobileClientsList" if mobile else "#clientsList"
        attr = "data-mobile-client-id" if mobile else "data-client-id"
        self.page.locator(f'{container} [{attr}="{client}"]').click()

    def change_token(self):
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.assertNotEqual(token, self.session_token)
        generation = self.page.evaluate("state.viewerStateGeneration")
        self.page.evaluate("token => setOperatorSessionToken(token, {persist:false})", token)
        self.assertEqual(self.page.evaluate("state.viewerStateGeneration"), generation)

    def test_desktop_repeated_clicks_join_then_failure_allows_retry(self):
        self.open_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        for _ in range(3):
            self.select(self.first)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("!state.clientsProfileSelectionRequest")
        self.assertIn("НЕ ЗАГРУЖЕН", self.page.locator("#clientProfileTitle").inner_text())
        self.select(self.first)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.first
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC SELECT FIRST"
        )
        self.select(self.first)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("!state.clientsProfileSelectionRequest")

    def test_desktop_new_selection_is_not_joined_to_old_request(self):
        self.open_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        self.select(self.first)
        self.select(self.second)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "STALE"}})
        self.select(self.second)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.second
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC SELECT SECOND"
        )

    def test_desktop_a_b_a_keeps_new_owner_when_old_responses_arrive(self):
        self.open_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        self.select(self.first)
        self.select(self.second)
        self.select(self.first)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 3)
        self.assertIn(self.first, held[0].request.url)
        self.assertIn(self.second, held[1].request.url)
        self.assertIn(self.first, held[2].request.url)
        self.page.evaluate("window.expectedProfileOwner = state.clientsProfileSelectionRequest")
        held.pop(0).continue_()
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "STALE SECOND"}})
        self.page.wait_for_timeout(100)
        self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
        self.assertTrue(
            self.page.evaluate(
                "state.clientsProfileSelectionRequest === window.expectedProfileOwner"
            )
        )
        self.select(self.first)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.clientsProfileSelectionRequest",
            arg=self.first,
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC SELECT FIRST"
        )
        self.assertNotIn("STALE SECOND", self.page.locator("#statusLine").inner_text())

    def test_mobile_same_client_joins_but_new_client_replaces_owner(self):
        self.open_clients(mobile=True)
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        for _ in range(3):
            self.select(self.first, mobile=True)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.select(self.second, mobile=True)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "STALE"}})
        self.assertTrue(self.page.evaluate("state.mobileClientProfileLoading"))
        self.select(self.second, mobile=True)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.second
        )
        self.assertFalse(self.page.evaluate("state.mobileClientProfileLoading"))
        self.assertIn(
            "SYNTHETIC SELECT SECOND", self.page.locator("#mobileClientDetail").inner_text()
        )

    def test_desktop_to_mobile_uses_separate_limits(self):
        self.open_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        self.select(self.first)
        self.page.wait_for_timeout(100)
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 900})
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_function("state.clientsLoaded && !state.mobileClientsLoading")
        self.select(self.first, mobile=True)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        self.assertIn("order_limit=30", held[0].request.url)
        self.assertIn("order_limit=8", held[1].request.url)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.first
        )
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.clientsActiveProfile.meta.order_limit"), 8)

    def exercise_session_change(self, *, mobile):
        self.open_clients(mobile=mobile)
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        self.select(self.first, mobile=mobile)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        old_response = held[0].fetch()
        self.assertEqual(old_response.status, 200)
        self.change_token()
        held.pop().fulfill(response=old_response)
        self.page.wait_for_function("!state.clientsProfileSelectionRequest")
        self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
        if mobile:
            self.assertFalse(self.page.evaluate("state.mobileClientProfileLoading"))
            self.assertIn("НЕ ЗАГРУЖЕН", self.page.locator("#mobileClientDetail").inner_text())
        else:
            self.assertIn("НЕ ЗАГРУЖЕН", self.page.locator("#clientProfileTitle").inner_text())
        self.select(self.first, mobile=mobile)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.first
        )

    def test_desktop_token_change_cannot_apply_old_profile(self):
        self.exercise_session_change(mobile=False)

    def test_mobile_token_change_cannot_apply_old_profile(self):
        self.exercise_session_change(mobile=True)
