"""Incomplete client list responses must not look like empty results."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientsInvalidResponseBrowserTests(UiBrowserTestCase):
    def test_desktop_rejects_incomplete_list_and_retries(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC LIST CLIENT"})[
            "client"
        ]["id"]
        self.load_board()
        for mode in (
            "empty",
            "missing_meta",
            "missing_id",
            "bad_totals",
            "bad_has_more",
            "missing_name",
            "invalid_stats",
            "invalid_warning",
            "duplicate_id",
        ):

            def partial(route):
                payload = route.fetch().json()
                data = payload["data"]
                if mode == "empty":
                    payload["data"] = {}
                elif mode == "missing_meta":
                    del data["meta"]
                elif mode == "missing_id":
                    del data["clients"][0]["id"]
                elif mode == "missing_name":
                    del data["clients"][0]["name"]
                elif mode == "bad_totals":
                    data["meta"]["returned"] += 1
                elif mode == "invalid_stats":
                    data["clients"][0]["stats"] = {}
                elif mode == "invalid_warning":
                    data["clients"][0]["read_warnings"] = None
                elif mode == "duplicate_id":
                    data["clients"].append(dict(data["clients"][0]))
                    data["meta"]["total"] += 1
                    data["meta"]["returned"] += 1
                else:
                    data["meta"]["has_more"] = not data["meta"]["has_more"]
                route.fulfill(json=payload)

            self.page.route("**/api/list_clients?**", partial)
            self.page.locator("#clientsButton").click()
            self.page.wait_for_function("state.clientsLoadError")
            self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#clientsList").inner_text())
            self.assertFalse(self.page.evaluate("state.clientsLoaded"))
            self.page.locator('[data-close="clients"]').click()
            self.page.unroute("**/api/list_clients?**", partial)
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').wait_for()
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_desktop_search_rejects_missing_statistics(self):
        self.runtime.service.create_client({"display_name": "SYNTHETIC SEARCH CLIENT"})
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")

        def partial(route):
            payload = route.fetch().json()
            del payload["data"]["clients"][0]["stats"]["vehicles_total"]
            route.fulfill(json=payload)

        self.page.route("**/api/search_clients?**", partial)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC SEARCH CLIENT")
        self.page.wait_for_function("state.clientsLoadError")
        self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#clientsList").inner_text())
        self.page.unroute("**/api/search_clients?**", partial)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC SEARCH")
        self.page.wait_for_function("state.clientsLoaded && !state.clientsLoadError")
        self.assertIn("SYNTHETIC SEARCH CLIENT", self.page.locator("#clientsList").inner_text())

    def test_mobile_search_failure_preserves_draft_and_ignores_stale_partial(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC MOBILE CLIENT"})[
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
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="clients"]').click()
        held = []

        def partial(route):
            route.fulfill(json={"ok": True, "data": {}})

        self.page.route("**/api/search_clients?**", partial)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC MOBILE CLIENT")
        self.page.wait_for_function("!state.mobileClientsLoading && state.clientsLoadError")
        self.assertIn("НЕ УДАЛОСЬ ЗАГРУЗИТЬ", self.page.locator("#mobileClientsList").inner_text())
        self.assertIn("ПРОФИЛЬ СКРЫТ", self.page.locator("#mobileClientDetail").inner_text())
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")
        self.page.unroute("**/api/search_clients?**", partial)

        def delay_old(route):
            if "OLD" in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/search_clients?**", delay_old)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC OLD")
        self.page.wait_for_timeout(350)
        self.assertEqual(len(held), 1)
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC MOBILE CLIENT")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id)", arg=client
        )
        held.pop().fulfill(json={"ok": True, "data": {}})
        self.page.wait_for_timeout(150)
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))
        self.assertIn(
            "SYNTHETIC MOBILE CLIENT", self.page.locator("#mobileClientsList").inner_text()
        )
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")

    def test_restricted_role_loads_list_and_search(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC OPERATOR CLIENT"})[
            "client"
        ]["id"]
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-clients-viewer",
                "password": "synthetic-clients-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-clients-viewer", "password": "synthetic-clients-password"},
        ).json()["data"]["session"]["token"]
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(context.close)
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').wait_for()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC OPERATOR CLIENT")
        self.page.wait_for_function(
            "state.clientsLoaded && state.clientsQuery === 'SYNTHETIC OPERATOR CLIENT'"
        )
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{client}"]').count(), 1)
        self.assertFalse(self.page.evaluate("state.clientsLoadError"))

    def test_late_profile_cannot_restore_stale_list_after_search_error(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC DELAYED CLIENT"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))

        def invalid_search(route):
            route.fulfill(json={"ok": True, "data": {}})

        self.page.route("**/api/search_clients?**", invalid_search)
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC ABSENT")
        self.page.wait_for_function("state.clientsLoadError")
        held.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertIn("ОШИБКА ЗАГРУЗКИ", self.page.locator("#clientsMeta").inner_text())
        self.assertEqual(self.page.locator("#clientsList [data-client-id]").count(), 0)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC PRESERVED DRAFT")
        self.page.unroute("**/api/search_clients?**", invalid_search)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC DELAYED CLIENT")
        self.page.wait_for_function("state.clientsLoaded && !state.clientsLoadError")
        self.assertEqual(self.page.locator(f'#clientsList [data-client-id="{client}"]').count(), 1)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC PRESERVED DRAFT"
        )

    def test_late_profile_does_not_restore_rows_while_search_waits(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC LOADING CLIENT"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")
        profiles, searches = [], []
        self.page.route("**/api/get_client?**", lambda route: profiles.append(route))
        self.page.route("**/api/search_clients?**", lambda route: searches.append(route))
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(profiles), 1)
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC QUERY B")
        self.assertEqual(self.page.locator("#clientsList [data-client-id]").count(), 0)
        self.page.wait_for_timeout(350)
        self.assertEqual(len(searches), 1)
        profiles.pop().continue_()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertEqual(self.page.locator("#clientsList [data-client-id]").count(), 0)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsList").inner_text())
        self.page.locator("#clientCommentInput").fill("SYNTHETIC LOADING DRAFT")
        searches.pop().continue_()
        self.page.wait_for_function("state.clientsLoaded && !state.clientsLoadError")
        self.assertIn("НЕ НАЙДЕНЫ", self.page.locator("#clientsList").inner_text())
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC LOADING DRAFT"
        )
