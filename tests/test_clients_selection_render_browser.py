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

    def test_selection_preserves_rows_and_late_response_cannot_change_it(self):
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
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
        self.assertTrue(
            self.page.evaluate(
                "window.firstClientRow === document.querySelector('#clientsList [data-client-id]')"
            )
        )
        self.assertEqual(self.page.evaluate("document.activeElement?.dataset.clientId"), latest)
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
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
        self.assertEqual(self.page.evaluate("state.clientsActiveProfile.client.id"), latest)

    def test_failed_profile_retains_list_and_successful_retry_preserves_rows(self):
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
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
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

    def test_35_and_50_rows_keep_identity_and_focus_with_one_profile_request(self):
        first, _, _ = self.open_clients()
        for count in (35, 50):
            self.page.evaluate(
                """({id, count}) => {
                  const original = state.clients.find(client => client.id === id);
                  state.clients = [original, ...Array.from({length: count - 1}, (_, i) => ({
                    ...original, id: 'synthetic-list-' + i, name: 'SYNTHETIC ' + i
                  }))];
                  state.clientsQuery = count === 50 ? 'SYNTHETIC' : '';
                  state.clientsMetaState = {total: count, returned: count, has_more: false};
                  state.clientsLoaded = true;
                  state.clientsLoadError = false;
                  renderClientsList();
                  window.clientListRenders = 0;
                  window.savedRows = [...els.clientsList.children];
                }""",
                {"id": first, "count": count},
            )
            requests = []

            def track(request):
                if "/api/get_client?" in request.url:
                    requests.append(request.url)

            self.page.on("request", track)
            row = self.page.locator(f'#clientsList [data-client-id="{first}"]')
            row.click()
            self.page.wait_for_function(
                "id => state.clientsActiveProfile?.client?.id === id && !state.clientsProfileSelectionRequest",
                arg=first,
            )
            self.page.remove_listener("request", track)
            self.assertEqual(len(requests), 1)
            self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
            self.assertTrue(
                self.page.evaluate(
                    "window.savedRows.every((row, i) => row === els.clientsList.children[i])"
                )
            )
            self.assertEqual(self.page.evaluate("document.activeElement?.dataset.clientId"), first)
            self.assertEqual(self.page.locator("#clientsList .client-row.is-active").count(), 1)
            self.assertTrue(row.evaluate("element => element.classList.contains('is-active')"))

    def test_missing_metadata_or_unexpected_list_falls_back_to_render(self):
        first, _, _ = self.open_clients()
        for variant in ("missing_meta", "unexpected_dom"):
            held = []
            self.page.route("**/api/get_client?**", lambda route: held.append(route), times=1)
            self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
            self.assertEqual(len(held), 1)
            self.page.evaluate(
                """variant => {
                  if (variant === 'missing_meta') state.clientsMetaState = null;
                  else els.clientsList.innerHTML = '<div class="empty">ЗАГРУЗКА</div>';
                  window.clientListRenders = 0;
                }""",
                variant,
            )
            held.pop().continue_()
            self.page.wait_for_function(
                "id => state.clientsActiveProfile?.client?.id === id && !state.clientsProfileSelectionRequest",
                arg=first,
            )
            self.assertEqual(self.page.evaluate("window.clientListRenders"), 1)
            self.assertEqual(
                self.page.locator("#clientsList .client-row.is-active").get_attribute(
                    "data-client-id"
                ),
                first,
            )
            self.page.evaluate(
                "state.clientsMetaState = {total: state.clients.length, returned: state.clients.length, has_more: false}"
            )

    def test_search_success_before_profile_keeps_new_search_rows(self):
        first, second, _ = self.open_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route), times=1)
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.assertEqual(len(held), 1)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC BETA")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.length === 1 && state.clients[0].id === id",
            arg=second,
        )
        self.page.evaluate("""() => {
          window.savedSearchRow = els.clientsList.firstElementChild;
          window.clientListRenders = 0;
        }""")
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.clientsProfileSelectionRequest",
            arg=first,
        )
        self.assertEqual(self.page.evaluate("window.clientListRenders"), 0)
        self.assertTrue(
            self.page.evaluate("window.savedSearchRow === els.clientsList.firstElementChild")
        )
        self.assertEqual(
            self.page.locator("#clientsList [data-client-id]").get_attribute("data-client-id"),
            second,
        )
        self.assertEqual(self.page.locator("#clientsList .client-row.is-active").count(), 0)
