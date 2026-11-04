"""Reopening clients keeps cached rows responsive while refreshing them."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientsReopenRefreshBrowserTests(UiBrowserTestCase):
    def start_cross_view_refresh(self):
        first, _ = self.prepare()
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-view="more"]')
        self.page.wait_for_function("state.mobileMoreLoaded && !state.mobileMoreLoading")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC CROSS VIEW DRAFT")
        self.page.locator('[data-close="clients"]').click()
        created = self.runtime.service.create_client({"display_name": "SYNTHETIC OTHER OPERATOR"})[
            "client"
        ]["id"]
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-open="clients"]')
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ОБНОВЛЕНИЕ...")
        self.assertEqual(
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').count(), 1
        )
        return first, created, held

    def test_cross_view_refresh_success_updates_open_mobile(self):
        _, created, held = self.start_cross_view_refresh()
        held.pop().continue_()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{created}"]').wait_for()
        self.assertNotEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ОБНОВЛЕНИЕ...")
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC CROSS VIEW DRAFT"
        )

    def test_cross_view_refresh_error_and_retry(self):
        first, _, held = self.start_cross_view_refresh()
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}}
        )
        self.page.wait_for_function("state.clientsLoadError")
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ОШИБКА ЗАГРУЗКИ")
        self.assertEqual(self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0)
        self.page.unroute("**/api/list_clients?**")
        self.page.click("#mobileClientsBackButton")
        self.page.click('[data-mobile-open="clients"]')
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').wait_for()
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC CROSS VIEW DRAFT"
        )

    def test_cross_view_close_does_not_strand_refresh(self):
        _, created, held = self.start_cross_view_refresh()
        self.page.click("#mobileClientsBackButton")
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsMetaState && state.clients.some(c => c.id === id)", arg=created
        )
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.unroute("**/api/list_clients?**")
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{created}"]').wait_for()
        self.page.wait_for_function("!!state.clientsMetaState")
        self.assertNotIn("ЗАГРУЗКА", self.page.locator("#clientsMeta").inner_text())

    def prepare(self):
        first = self.runtime.service.create_client({"display_name": "SYNTHETIC REOPEN A"})[
            "client"
        ]["id"]
        second = self.runtime.service.create_client({"display_name": "SYNTHETIC REOPEN B"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")
        return first, second

    def test_reopen_shows_cached_rows_and_refreshes_without_losing_selected_draft(self):
        first, second = self.prepare()
        self.page.locator('[data-close="clients"]').click()
        created = self.runtime.service.create_client({"display_name": "SYNTHETIC OTHER OPERATOR"})[
            "client"
        ]["id"]
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{first}"]').count(), 1)
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{created}"]').count(), 0)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsMeta").inner_text())
        self.page.locator(f'#clientsList [data-client-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=second
        )
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsMeta").inner_text())
        self.page.locator("#clientCommentInput").fill("SYNTHETIC REOPEN DRAFT")
        held.pop().continue_()
        self.page.locator(f'#clientsList [data-client-id="{created}"]').wait_for()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), second)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC REOPEN DRAFT"
        )

    def test_fast_reopen_discards_stale_response(self):
        _, second = self.prepare()
        old = self.context.request.get(
            self.runtime.base_url + "/api/list_clients?limit=35&include_stats=false",
            headers={"X-Operator-Session": self.session_token},
        ).json()
        self.page.locator('[data-close="clients"]').click()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.page.locator('[data-close="clients"]').click()
        created = self.runtime.service.create_client({"display_name": "SYNTHETIC LATEST REOPEN"})[
            "client"
        ]["id"]
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop().continue_()
        self.page.locator(f'#clientsList [data-client-id="{created}"]').wait_for()
        held.pop().fulfill(json=old)
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{created}"]').count(), 1)
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{second}"]').count(), 1)

    def test_background_error_hides_cached_list_and_retry_keeps_draft(self):
        first, _ = self.prepare()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC ERROR DRAFT")
        self.page.locator('[data-close="clients"]').click()

        def fail(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}})

        self.page.route("**/api/list_clients?**", fail)
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoadError")
        self.assertEqual(self.page.locator("#clientsList [data-client-id]").count(), 0)
        self.assertIn("SYNTHETIC FAILURE", self.page.locator("#clientsList").inner_text())
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC ERROR DRAFT"
        )
        self.page.locator('[data-close="clients"]').click()
        self.page.unroute("**/api/list_clients?**", fail)
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').wait_for()
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC ERROR DRAFT"
        )
