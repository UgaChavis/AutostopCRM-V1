"""Mobile client reads must not display old or empty results after failure."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileClientsLoadErrorBrowserTests(UiBrowserTestCase):
    @staticmethod
    def fail_request(route):
        route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}})

    def open_mobile(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')

    def wait_error(self):
        self.page.wait_for_function("!state.mobileClientsLoading && state.clientsLoadError")
        self.assertIn(
            "НЕ УДАЛОСЬ ЗАГРУЗИТЬ КЛИЕНТОВ", self.page.locator("#mobileClientsList").inner_text()
        )
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ОШИБКА ЗАГРУЗКИ")
        self.assertEqual(self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0)
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))

    def test_initial_failure_force_failure_and_reopen_retry(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC INITIAL"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.route("**/api/list_clients?**", self.fail_request)
        self.open_mobile()
        self.wait_error()
        self.page.unroute("**/api/list_clients?**", self.fail_request)
        self.page.click("#mobileClientsBackButton")
        with self.page.expect_request("**/api/list_clients?**"):
            self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id)", arg=client
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

        self.page.route("**/api/list_clients?**", self.fail_request)
        self.page.evaluate("() => loadMobileClients({force: true})")
        self.wait_error()
        self.page.unroute("**/api/list_clients?**", self.fail_request)
        self.page.click("#mobileClientsBackButton")
        with self.page.expect_request("**/api/list_clients?**"):
            self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id)", arg=client
        )
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_failed_search_hides_profile_without_discarding_desktop_draft(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC DRAFT"})["client"][
            "id"
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNSAVED DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.route("**/api/search_clients?**", self.fail_request)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC NO MATCH")
        self.wait_error()
        self.assertIn("ПРОФИЛЬ СКРЫТ", self.page.locator("#mobileClientDetail").inner_text())
        self.assertNotIn("SYNTHETIC DRAFT", self.page.locator("#mobileClientDetail").inner_text())
        self.assertEqual(self.page.evaluate("state.clientsActiveProfile?.client?.id"), client)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )
        self.page.unroute("**/api/search_clients?**", self.fail_request)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC DRAFT")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id)", arg=client
        )
        self.assertIn("SYNTHETIC DRAFT", self.page.locator("#mobileClientDetail").inner_text())
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )

    def test_late_failure_cannot_replace_newer_search_or_new_viewer(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC LATEST"})["client"][
            "id"
        ]
        self.load_board()
        self.open_mobile()
        self.page.wait_for_function("state.clientsLoaded")
        held = []

        def search(route):
            if "OLD" in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/search_clients?**", search)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC OLD")
        self.page.wait_for_timeout(300)
        self.assertEqual(len(held), 1)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC LATEST")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id)", arg=client
        )
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC OLD FAILURE"}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertIn("SYNTHETIC LATEST", self.page.locator("#mobileClientsList").inner_text())

        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC OLD")
        self.page.wait_for_timeout(300)
        self.assertEqual(len(held), 1)
        self.page.evaluate("() => resetViewerScopedState()")
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC OLD FAILURE"}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))
        self.assertEqual(self.page.evaluate("state.clients.length"), 0)

    def test_background_more_result_refreshes_open_clients_and_not_closed_panel(self):
        old = self.runtime.service.create_client({"display_name": "SYNTHETIC OLD LIST"})["client"][
            "id"
        ]
        self.load_board()
        self.page.evaluate("() => loadClients()")
        self.page.wait_for_function("state.clientsLoaded")
        new = self.runtime.service.create_client({"display_name": "SYNTHETIC NEW LIST"})["client"][
            "id"
        ]
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.open_mobile()
        self.page.wait_for_timeout(200)
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{old}"]').count(), 1
        )
        held.pop().continue_()
        self.page.wait_for_function("id => state.clients.some(c => c.id === id)", arg=new)
        self.page.wait_for_function(
            "id => !!document.querySelector('#mobileClientsList [data-mobile-client-id=\"' + id + '\"]')",
            arg=new,
        )

        self.page.click("#mobileClientsBackButton")
        self.page.evaluate("() => { window.closedClientsRequest = loadClients(); }")
        self.page.wait_for_timeout(200)
        self.assertEqual(len(held), 1)
        self.page.evaluate("""() => {
          window.mobileClientRenders = 0;
          const original = renderMobileClientsPanel;
          renderMobileClientsPanel = (...args) => { window.mobileClientRenders++; return original(...args); };
        }""")
        held.pop().continue_()
        self.page.evaluate("() => window.closedClientsRequest")
        self.assertEqual(self.page.evaluate("window.mobileClientRenders"), 0)

    def test_background_more_failure_refreshes_open_clients(self):
        self.runtime.service.create_client({"display_name": "SYNTHETIC CACHED LIST"})
        self.load_board()
        self.page.evaluate("() => loadClients()")
        self.page.wait_for_function("state.clientsLoaded")
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.open_mobile()
        self.page.wait_for_timeout(200)
        self.assertEqual(len(held), 1)
        self.assertIn("SYNTHETIC CACHED LIST", self.page.locator("#mobileClientsList").inner_text())
        self.fail_request(held.pop())
        self.wait_error()

    def exercise_superseded_mobile_loading(self, *, latest_fails):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC RACE CLIENT"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile()
        self.page.wait_for_function("!state.mobileMoreLoading && state.clientsLoaded")
        held = []
        self.page.route("**/api/search_clients?**", lambda route: held.append(route))
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC RACE")
        self.page.wait_for_timeout(350)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.evaluate("state.mobileClientsLoading"))
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        if latest_fails:
            self.fail_request(held.pop())
            self.wait_error()
            held.pop().continue_()
        else:
            held.pop().continue_()
            self.page.wait_for_function(
                "!state.mobileClientsLoading && state.clientsLoaded", timeout=3000
            )
            self.assertIn(
                "SYNTHETIC RACE CLIENT", self.page.locator("#mobileClientsList").inner_text()
            )
            self.fail_request(held.pop())
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.clientsLoadError"), latest_fails)
        self.assertFalse(self.page.evaluate("state.mobileClientsLoading"))
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")

    def test_latest_desktop_success_finishes_mobile_loading(self):
        self.exercise_superseded_mobile_loading(latest_fails=False)

    def test_latest_desktop_failure_finishes_mobile_loading(self):
        self.exercise_superseded_mobile_loading(latest_fails=True)

    def exercise_cross_view_queries(self, *, latest_fails, typing_during_debounce=False):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC QUERY ALPHA"})[
            "client"
        ]["id"]
        self.runtime.service.create_client({"display_name": "SYNTHETIC QUERY BRAVO"})
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC CROSS VIEW DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile()
        self.page.wait_for_function("!state.mobileMoreLoading && state.clientsLoaded")
        held = []

        def hold(route):
            held.append(route)

        self.page.route("**/api/search_clients?**", hold)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC QUERY ALPHA")
        self.page.wait_for_timeout(350)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC QUERY BRAVO")
        self.page.wait_for_timeout(350)
        self.assertEqual(len(held), 3)
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        if typing_during_debounce:
            self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC QUERY ALPH")
            self.fail_request(held.pop())
            self.page.wait_for_timeout(300)
            self.assertEqual(len(held), 3)
            held.pop().continue_()
            self.page.wait_for_function(
                "id => !state.mobileClientsLoading && state.clients.some(c => c.id === id)",
                arg=client,
            )
            for route in held:
                route.continue_()
            self.page.wait_for_timeout(150)
            self.assertFalse(self.page.evaluate("state.clientsLoadError"))
            self.assertEqual(
                self.page.locator("#mobileClientsSearchInput").input_value(),
                "SYNTHETIC QUERY ALPH",
            )
            self.assertIn(
                "SYNTHETIC QUERY ALPHA", self.page.locator("#mobileClientsList").inner_text()
            )
            self.assertEqual(
                self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC CROSS VIEW DRAFT"
            )
            return
        if latest_fails:
            self.fail_request(held.pop())
            self.page.wait_for_function("!state.mobileClientsLoading && state.clientsLoadError")
        else:
            held.pop().continue_()
            self.page.wait_for_function("!state.mobileClientsLoading && state.clientsLoaded")
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ПОИСК НЕ ОБНОВЛЁН")
        self.assertIn("ИЗМЕНИТЕ ЗАПРОС", self.page.locator("#mobileClientsList").inner_text())
        self.assertEqual(self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0)
        self.assertIn("ПРОФИЛЬ СКРЫТ", self.page.locator("#mobileClientDetail").inner_text())
        self.assertEqual(
            self.page.locator("#mobileClientsSearchInput").input_value(), "SYNTHETIC QUERY ALPHA"
        )
        self.assertEqual(
            self.page.locator("#clientsSearchInput").input_value(), "SYNTHETIC QUERY BRAVO"
        )
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC CROSS VIEW DRAFT"
        )
        for route in held:
            route.continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("state.clientsLoadError"), latest_fails)
        self.page.unroute("**/api/search_clients?**", hold)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC QUERY ALPH")
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(c => c.id === id)", arg=client
        )
        self.assertIn("SYNTHETIC QUERY ALPHA", self.page.locator("#mobileClientsList").inner_text())
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC CROSS VIEW DRAFT"
        )

    def test_mobile_query_preserved_after_different_desktop_success(self):
        self.exercise_cross_view_queries(latest_fails=False)

    def test_mobile_query_preserved_after_different_desktop_failure(self):
        self.exercise_cross_view_queries(latest_fails=True)

    def test_mobile_input_during_debounce_rejects_old_desktop_result(self):
        self.exercise_cross_view_queries(latest_fails=True, typing_during_debounce=True)

    def exercise_pending_mobile_debounce(self, *, close_early):
        self.runtime.service.create_client({"display_name": "SYNTHETIC DEBOUNCE ALPHA"})
        self.runtime.service.create_client({"display_name": "SYNTHETIC DEBOUNCE BRAVO"})
        self.load_board()
        self.open_mobile()
        self.page.wait_for_function("!state.mobileMoreLoading && state.clientsLoaded")
        self.page.evaluate("""() => {
          const original = window.setTimeout;
          window.setTimeout = (callback, delay, ...args) =>
            original(callback, delay === 180 ? 1200 : delay === 160 ? 600 : delay, ...args);
        }""")
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC DEBOUNCE ALPHA")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC DEBOUNCE BRAVO")
        if close_early:
            self.page.locator('[data-close="clients"]').click()
        self.page.wait_for_function(
            "!state.mobileClientsLoading && state.clientsLoaded && state.clientsQuery === 'SYNTHETIC DEBOUNCE BRAVO'",
            timeout=3000,
        )
        self.page.wait_for_timeout(1400)
        self.assertEqual(
            self.page.locator("#clientsSearchInput").input_value(), "SYNTHETIC DEBOUNCE BRAVO"
        )
        self.assertEqual(
            self.page.locator("#mobileClientsSearchInput").input_value(), "SYNTHETIC DEBOUNCE ALPHA"
        )
        self.assertEqual(self.page.evaluate("state.clientsQuery"), "SYNTHETIC DEBOUNCE BRAVO")
        if not close_early:
            self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertEqual(self.page.locator("#mobileClientsMeta").inner_text(), "ПОИСК НЕ ОБНОВЛЁН")

    def test_desktop_input_supersedes_pending_mobile_debounce(self):
        self.exercise_pending_mobile_debounce(close_early=False)

    def test_desktop_debounce_finishes_after_closing_desktop(self):
        self.exercise_pending_mobile_debounce(close_early=True)

    def test_mobile_input_supersedes_pending_desktop_debounce(self):
        self.runtime.service.create_client({"display_name": "SYNTHETIC REVERSE ALPHA"})
        self.runtime.service.create_client({"display_name": "SYNTHETIC REVERSE BRAVO"})
        self.load_board()
        self.open_mobile()
        self.page.wait_for_function("!state.mobileMoreLoading && state.clientsLoaded")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.page.evaluate("""() => {
          const original = window.setTimeout;
          window.setTimeout = (callback, delay, ...args) =>
            original(callback, delay === 160 ? 1200 : delay, ...args);
        }""")
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC REVERSE BRAVO")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC REVERSE ALPHA")
        self.page.wait_for_function(
            "!state.mobileClientsLoading && state.clientsQuery === 'SYNTHETIC REVERSE ALPHA'"
        )
        seq = self.page.evaluate("state.clientsRequestSeq")
        self.page.wait_for_timeout(1400)
        self.assertEqual(self.page.evaluate("state.clientsRequestSeq"), seq)
        self.assertEqual(
            self.page.locator("#mobileClientsSearchInput").input_value(), "SYNTHETIC REVERSE ALPHA"
        )
        self.assertIn(
            "SYNTHETIC REVERSE ALPHA", self.page.locator("#mobileClientsList").inner_text()
        )
