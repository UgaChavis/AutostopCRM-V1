"""Client list replies belong to the operator session that requested them."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientsSessionChangeBrowserTests(UiBrowserTestCase):
    def next_token(self):
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.assertNotEqual(token, self.session_token)
        generation = self.page.evaluate("state.viewerStateGeneration")
        sequence = self.page.evaluate("state.clientsRequestSeq")
        self.page.evaluate("token => setOperatorSessionToken(token, {persist: false})", token)
        self.assertEqual(self.page.evaluate("state.viewerStateGeneration"), generation)
        self.assertEqual(self.page.evaluate("state.clientsRequestSeq"), sequence)

    def open_shared(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.click('[data-mobile-view="more"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        return held

    def test_shared_old_success_is_hidden_and_new_search_can_retry(self):
        self.runtime.service.create_client({"display_name": "SYNTHETIC OLD CLIENT"})
        held = self.open_shared()
        old_response = held[0].fetch()
        self.assertEqual(old_response.status, 200)
        new_client = self.runtime.service.create_client({"display_name": "SYNTHETIC NEW CLIENT"})[
            "client"
        ]["id"]
        self.next_token()
        held.pop().fulfill(response=old_response)
        self.page.wait_for_function("state.clientsLoadError && !state.mobileClientsLoading")
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))
        self.assertEqual(self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0)
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileClientsList").inner_text())
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC NEW CLIENT")
        self.page.wait_for_function(
            "id => state.clientsLoaded && !state.mobileClientsLoading && state.clients.some(c => c.id === id)",
            arg=new_client,
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_shared_late_old_error_cannot_override_new_success(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC NEW SESSION"})[
            "client"
        ]["id"]
        held = self.open_shared()
        self.next_token()
        self.page.evaluate("() => { void loadMobileClients({force: true}); }")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsLoaded && !state.mobileClientsLoading && state.clients.some(c => c.id === id)",
            arg=client,
        )
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "OLD SESSION"}})
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.evaluate("state.clientsLoaded"))
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertTrue(
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').is_visible()
        )

    def test_desktop_old_error_shows_retry_and_reopen_loads_new_session(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC DESKTOP SESSION"})[
            "client"
        ]["id"]
        self.load_board()
        held = []

        def hold(route):
            held.append(route)

        self.page.route("**/api/list_clients?**", hold)
        self.page.click("#clientsButton")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.next_token()
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "OLD SESSION"}})
        self.page.wait_for_function("state.clientsLoadError && !state.clientsLoaded")
        self.assertIn("ОШИБКА", self.page.locator("#clientsMeta").inner_text())
        self.assertNotIn("OLD SESSION", self.page.locator("#clientsList").inner_text())
        self.page.unroute("**/api/list_clients?**", hold)
        self.page.locator('[data-close="clients"]').click()
        self.page.click("#clientsButton")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
