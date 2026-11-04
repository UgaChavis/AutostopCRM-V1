"""Selecting a client keeps the loaded list until the profile is ready."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientSelectionRenderBrowserTests(UiBrowserTestCase):
    def open_clients(self):
        ids = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in ("SYNTHETIC ALPHA", "SYNTHETIC BETA", "SYNTHETIC GAMMA")
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function(
            "ids => state.clientsLoaded && ids.every(id => state.clients.some(client => client.id === id))",
            arg=ids,
        )
        self.page.evaluate("""() => {
          const list = document.querySelector('#clientsList');
          const html = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
          window.clientListRenders = 0;
          Object.defineProperty(list, 'innerHTML', {configurable: true,
            get() { return html.get.call(this); },
            set(value) { window.clientListRenders++; html.set.call(this, value); }});
        }""")
        return ids

    def test_selection_renders_once_and_late_response_cannot_change_it(self):
        first, delayed, latest = self.open_clients()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        held = []

        def hold_delayed(route):
            if delayed in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_client?**", hold_delayed)
        self.page.evaluate("""() => {
          window.clientListRenders = 0;
          window.firstClientRow = document.querySelector('#clientsList [data-client-id]');
          window.originalClientOrder = [...document.querySelectorAll('#clientsList [data-client-id]')].map(row => row.dataset.clientId);
        }""")
        self.page.locator(f'#clientsList [data-client-id="{delayed}"]').click()
        self.assertEqual(len(held), 1)
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
        self.assertTrue(
            self.page.evaluate(
                "window.firstClientRow === document.querySelector('#clientsList [data-client-id]')"
            )
        )
        self.assertEqual(self.page.locator("#clientsList .client-row.is-active").count(), 0)
        self.assertTrue(
            self.page.locator(".clients-profile-pane").evaluate(
                "el => el.classList.contains('is-empty')"
            )
        )
        self.page.locator(f'#clientsList [data-client-id="{latest}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=latest
        )
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 1)
        self.assertEqual(
            self.page.locator("#clientsList .client-row.is-active").get_attribute("data-client-id"),
            latest,
        )
        self.assertTrue(
            self.page.evaluate(
                "JSON.stringify(window.originalClientOrder) === JSON.stringify([...document.querySelectorAll('#clientsList [data-client-id]')].map(row => row.dataset.clientId))"
            )
        )
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 1)
        self.assertEqual(self.page.evaluate("state.clientsActiveProfile.client.id"), latest)

    def test_failed_profile_retains_list_and_successful_retry_renders_once(self):
        first, _, _ = self.open_clients()
        self.page.route(
            "**/api/get_client?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "document.querySelector('#clientProfileTitle').textContent.includes('ПРОФИЛЬ НЕ ЗАГРУЖЕН')"
        )
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
        self.assertEqual(self.page.locator("#clientsList .client-row.is-active").count(), 0)
        self.page.unroute("**/api/get_client?**")
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 1)
        self.assertEqual(
            self.page.locator("#clientsList .client-row.is-active").get_attribute("data-client-id"),
            first,
        )

    def test_discard_warning_is_cleared_when_next_profile_fails(self):
        first, second, _ = self.open_clients()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        expected_meta = self.page.locator("#clientsMeta").inner_text()
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC UNSAVED")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator(f'#clientsList [data-client-id="{second}"]').click()
        self.assertIn("СНАЧАЛА СОХРАНИТЕ", self.page.locator("#clientsMeta").inner_text())
        self.page.route(
            "**/api/get_client?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.evaluate("window.clientListRenders = 0")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'#clientsList [data-client-id="{second}"]').click()
        self.page.wait_for_function(
            "document.querySelector('#clientProfileTitle').textContent.includes('ПРОФИЛЬ НЕ ЗАГРУЖЕН')"
        )
        self.assertEqual(self.page.locator("#clientsMeta").inner_text(), expected_meta)
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
