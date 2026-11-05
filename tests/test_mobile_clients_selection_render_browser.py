"""Client selection retains mobile list rows without hiding loading or errors."""

from uuid import uuid4

from tests.browser_ui_support import UiBrowserTestCase


class MobileClientsSelectionRenderBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        suffix = uuid4().hex[:12].translate(str.maketrans("0123456789", "ghijklmnop"))
        self.names = [f"SYNTHETIC {suffix}{name}" for name in ("ALPHA", "BETA")]
        self.ids = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in self.names
        ]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_function(
            "ids => state.clientsLoaded && !state.mobileClientsLoading && !!state.clientsMetaState && ids.every(id => state.clients.some(client => client.id === id))",
            arg=self.ids,
        )
        self.page.evaluate("""() => {
          const list = els.mobileClientsList;
          const html = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
          window.mobileClientListWrites = 0;
          Object.defineProperty(list, 'innerHTML', { configurable: true,
            get() { return html.get.call(this); },
            set(value) { window.mobileClientListWrites++; html.set.call(this, value); },
          });
        }""")

    def row(self, client_id):
        return self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client_id}"]')

    def test_selection_keeps_focus_rows_and_meta_until_response(self):
        client_id = self.ids[0]
        row = self.row(client_id)
        row.focus()
        row.evaluate("row => window.selectedMobileRow = row")
        original_meta = self.page.locator("#mobileClientsMeta").inner_text()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route), times=1)
        row.press("Enter")
        self.assertEqual(len(held), 1)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#mobileClientDetail").inner_text())
        self.assertTrue(row.evaluate("row => row.classList.contains('is-active')"))
        self.assertTrue(self.page.evaluate("document.activeElement === window.selectedMobileRow"))
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), original_meta)
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
            arg=client_id,
        )
        self.assertEqual(self.page.evaluate("window.mobileClientListWrites"), 0)
        self.assertTrue(self.page.evaluate("document.activeElement === window.selectedMobileRow"))
        self.assertIn(self.names[0], self.page.locator("#mobileClientDetail").inner_text())
        self.page.click("#mobileClientsBackButton")
        self.page.click('[data-mobile-open="clients"]')
        self.row(client_id).wait_for()
        self.assertIn(self.names[0], self.page.locator("#mobileClientDetail").inner_text())

    def test_profile_error_and_retry_keep_list_and_meta(self):
        client_id = self.ids[0]
        self.page.route(
            "**/api/get_client?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC ERROR"}}
            ),
            times=1,
        )
        self.row(client_id).click()
        self.page.wait_for_function(
            "!state.mobileClientProfileLoading && !state.clientsActiveProfile"
        )
        self.assertIn("ПРОФИЛЬ НЕ ЗАГРУЖЕН", self.page.locator("#mobileClientDetail").inner_text())
        self.assertEqual(self.page.evaluate("window.mobileClientListWrites"), 0)
        self.assertIn("ПОКАЗАНО", self.page.locator("#mobileClientsMeta").inner_text())
        self.row(client_id).click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
            arg=client_id,
        )
        self.assertEqual(self.page.evaluate("window.mobileClientListWrites"), 0)

    def test_search_failure_during_profile_request_is_not_hidden_and_retry_works(self):
        held_profile = []
        held_search = []
        self.page.route("**/api/get_client?**", lambda route: held_profile.append(route), times=1)
        self.page.route("**/api/search_clients?**", lambda route: held_search.append(route))
        self.row(self.ids[0]).click()
        self.assertEqual(len(held_profile), 1)
        self.page.locator("#mobileClientsSearchInput").fill(self.names[1])
        self.page.wait_for_function("state.mobileClientsLoading")
        held_profile.pop().continue_()
        self.page.wait_for_timeout(250)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#mobileClientsMeta").inner_text())
        self.assertEqual(self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0)
        self.assertEqual(len(held_search), 1)
        held_search.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC SEARCH ERROR"}}
        )
        self.page.wait_for_function("state.clientsLoadError && !state.mobileClientsLoading")
        self.assertIn("ОШИБКА", self.page.locator("#mobileClientsMeta").inner_text())
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileClientsList").inner_text())
        self.assertIn("СКРЫТ", self.page.locator("#mobileClientDetail").inner_text())
        self.page.unroute("**/api/search_clients?**")
        self.page.locator("#mobileClientsSearchInput").fill(self.names[0])
        self.page.wait_for_function(
            "id => state.clientsLoaded && !state.mobileClientsLoading && state.clients.some(c => c.id === id)",
            arg=self.ids[0],
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.row(self.ids[0]).wait_for()

    def test_changed_name_and_unexpected_dom_force_full_render(self):
        client_id = self.ids[0]
        new_name = self.names[0] + " CHANGED"
        self.runtime.service.update_client({"client_id": client_id, "display_name": new_name})
        self.page.evaluate("async () => await loadMobileClients({force: true})")
        self.assertIn(new_name, self.row(client_id).inner_text())
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route), times=1)
        self.row(client_id).click()
        self.assertEqual(len(held), 1)
        self.page.evaluate("els.mobileClientsList.innerHTML = '<div>unexpected</div>'")
        self.page.evaluate("window.mobileClientListWrites = 0")
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
            arg=client_id,
        )
        self.assertEqual(self.page.evaluate("window.mobileClientListWrites"), 1)
        self.assertIn(new_name, self.row(client_id).inner_text())
