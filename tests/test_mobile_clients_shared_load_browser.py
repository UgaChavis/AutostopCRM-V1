"""The clients panel can join the current More-panel list request."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileClientsSharedLoadBrowserTests(UiBrowserTestCase):
    def open_with_pending_list(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC SHARED CLIENT"})[
            "client"
        ]["id"]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        held = []

        def hold(route):
            held.append(route)

        self.page.route("**/api/list_clients?**", hold)
        self.page.click('[data-mobile-view="more"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        self.assertIn("ЗАГРУЗКА", self.page.locator("#mobileClientsList").inner_text())
        return client, held, hold

    def test_shared_success_renders_list(self):
        client, held, _ = self.open_with_pending_list()
        held.pop().continue_()
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertTrue(
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').is_visible()
        )

    def test_shared_failure_is_visible_and_reopen_retries(self):
        client, held, hold = self.open_with_pending_list()
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("!state.mobileClientsLoading && state.clientsLoadError")
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileClientsList").inner_text())
        self.page.unroute("**/api/list_clients?**", hold)
        self.page.click("#mobileClientsBackButton")
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_close_reopen_does_not_reuse_invalidated_request(self):
        client, held, _ = self.open_with_pending_list()
        self.page.click("#mobileClientsBackButton")
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "STALE"}})
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        held.pop().continue_()
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_new_query_does_not_join_and_ignores_old_list(self):
        client, held, _ = self.open_with_pending_list()
        searches = []
        self.page.route("**/api/search_clients?**", lambda route: searches.append(route))
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC SHARED CLIENT")
        self.page.wait_for_timeout(350)
        self.assertEqual(len(searches), 1)
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        searches.pop().continue_()
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertEqual(
            self.page.locator("#mobileClientsSearchInput").input_value(), "SYNTHETIC SHARED CLIENT"
        )

    def test_forced_refresh_starts_a_new_request(self):
        client, held, _ = self.open_with_pending_list()
        self.page.evaluate("() => { void loadMobileClients({force: true}); }")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop(0).continue_()
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        held.pop().continue_()
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )

    def test_viewer_reset_ignores_shared_result(self):
        _, held, _ = self.open_with_pending_list()
        self.page.evaluate("resetViewerScopedState()")
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("state.clients.length"), 0)
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))
        self.assertFalse(self.page.evaluate("state.mobileClientsLoading"))

    def test_changed_session_does_not_join_previous_viewers_request(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC TOKEN CLIENT"})[
            "client"
        ]["id"]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.click('[data-mobile-view="more"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.assertNotEqual(token, self.session_token)
        generation = self.page.evaluate("state.viewerStateGeneration")
        self.page.evaluate("token => setOperatorSessionToken(token, {persist: false})", token)
        self.assertEqual(self.page.evaluate("state.viewerStateGeneration"), generation)
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "STALE"}})
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        held.pop().continue_()
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(client => client.id === id)",
            arg=client,
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
