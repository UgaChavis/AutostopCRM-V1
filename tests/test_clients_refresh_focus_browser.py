"""Keep keyboard focus while refreshing an unchanged synthetic client list."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientsRefreshFocusBrowserTests(UiBrowserTestCase):
    def open_refresh(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC ORIGINAL"})[
            "client"
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded && !!state.clientsMetaState")
        self.page.locator('[data-close="clients"]').click()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded && !state.clientsMetaState")
        self.assertEqual(len(held), 1)
        return client, held

    def test_unchanged_refresh_keeps_focused_row_through_selection(self):
        client, held = self.open_refresh()
        row = self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]')
        row.focus()
        row.evaluate("row => window.originalFocusRow = row")
        row.press("Enter")
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client["id"]
        )
        self.assertTrue(self.page.evaluate("document.activeElement === window.originalFocusRow"))
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsMeta").inner_text())
        held.pop().continue_()
        self.page.wait_for_function("!!state.clientsMetaState")
        self.assertTrue(self.page.evaluate("document.activeElement === window.originalFocusRow"))
        self.assertTrue(row.evaluate("row => row.classList.contains('is-active')"))

    def test_changed_name_renders_new_data_for_same_id(self):
        client, held = self.open_refresh()
        self.runtime.service.update_client(
            {"client_id": client["id"], "display_name": "SYNTHETIC CHANGED"}
        )
        held.pop().continue_()
        self.page.wait_for_function("!!state.clientsMetaState")
        row = self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]')
        self.assertIn("SYNTHETIC CHANGED", row.inner_text())

    def test_changed_search_hides_old_rows_and_ignores_stale_refresh(self):
        _, held = self.open_refresh()
        search = []
        self.page.route("**/api/search_clients?**", lambda route: search.append(route))
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC NO MATCH")
        self.page.wait_for_function(
            "!state.clientsLoaded && !!document.querySelector('#clientsList .empty')"
        )
        self.assertEqual(self.page.locator("#clientsList .client-row").count(), 0)
        held.pop().continue_()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#clientsList .client-row").count(), 0)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsList").inner_text())
        self.assertEqual(len(search), 1)
        search.pop().continue_()
        self.page.wait_for_function("state.clientsLoaded && !!state.clientsMetaState")
        self.assertIn("НЕ НАЙДЕНЫ", self.page.locator("#clientsList").inner_text())

    def test_failed_refresh_hides_rows_and_retry_works(self):
        client, held = self.open_refresh()
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("state.clientsLoadError")
        self.assertEqual(self.page.locator("#clientsList .client-row").count(), 0)
        self.assertIn("ОШИБКА", self.page.locator("#clientsMeta").inner_text())
        self.page.unroute("**/api/list_clients?**")
        self.page.locator('[data-close="clients"]').click()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').wait_for()
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_old_session_reply_does_not_restore_rows(self):
        _, held = self.open_refresh()
        self.page.evaluate("state.operatorSessionToken = 'SYNTHETIC CHANGED TOKEN'")
        held.pop().continue_()
        self.page.wait_for_function("state.clientsLoadError && !state.clientsLoaded")
        self.assertEqual(self.page.locator("#clientsList .client-row").count(), 0)


class ClientsRefreshMetaBrowserTests(UiBrowserTestCase):
    def test_unchanged_rows_show_updated_total_and_has_more(self):
        ids = [
            self.runtime.service.create_client({"display_name": f"SYNTHETIC META {index:02d}"})[
                "client"
            ]["id"]
            for index in range(34)
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsMetaState?.total === 35")
        self.page.locator('[data-close="clients"]').click()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded && !state.clientsMetaState")
        row = self.page.locator(f'#clientsList [data-client-id="{ids[0]}"]')
        row.focus()
        row.evaluate("row => window.originalFocusRow = row")
        self.runtime.service.create_client({"display_name": "ZZZZ SYNTHETIC EXTRA"})
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "state.clientsMetaState?.total === 36 && state.clientsMetaState?.has_more"
        )
        self.assertIn("35 ИЗ 36", self.page.locator("#clientsMeta").inner_text())
        self.assertTrue(self.page.evaluate("document.activeElement === window.originalFocusRow"))
        self.assertEqual(self.page.locator("#clientsList .client-row").count(), 35)
