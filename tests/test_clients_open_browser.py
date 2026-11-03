"""Client list opening with delayed or failed reads on disposable state."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientsOpenBrowserTests(UiBrowserTestCase):
    def test_opens_during_read_and_renders_the_response(self) -> None:
        self.load_board()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for(timeout=1000)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsMeta").inner_text())
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsList").inner_text())
        held.pop().continue_()
        self.page.wait_for_function("state.clientsLoaded")
        self.assertGreater(self.page.locator("#clientsList [data-client-id]").count(), 0)

    def test_close_during_read_does_not_reopen_or_apply_the_response(self) -> None:
        self.load_board()
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for(timeout=1000)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.locator('#clientsModal [data-close="clients"]').click()
        with self.page.expect_response("**/api/list_clients?**") as response:
            held.pop().continue_()
        response.value.body()
        self.page.wait_for_timeout(100)
        self.assertFalse(
            self.page.locator("#clientsModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))

    def test_failed_read_shows_an_error_in_the_open_window(self) -> None:
        self.load_board()
        self.page.route(
            "**/api/list_clients?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic failure"}}),
            ),
        )
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for(timeout=1000)
        self.page.get_by_text("ОШИБКА ЗАГРУЗКИ КЛИЕНТОВ.").wait_for()
        self.assertIn("Synthetic failure", self.page.locator("#clientsList").inner_text())

    def test_search_during_initial_read_rejects_the_previous_response(self) -> None:
        self.load_board()
        initial, search = [], []
        self.page.route("**/api/list_clients?**", lambda route: initial.append(route))
        self.page.route("**/api/search_clients?**", lambda route: search.append(route))
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for(timeout=1000)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(initial), 1)
        self.page.locator("#clientsSearchInput").fill("unlikely synthetic query")
        with self.page.expect_response("**/api/list_clients?**") as response:
            initial.pop().continue_()
        response.value.body()
        self.page.wait_for_timeout(250)
        self.assertFalse(self.page.evaluate("state.clientsLoaded"))
        self.assertEqual(len(search), 1)
        search.pop().continue_()
        self.page.wait_for_function("state.clientsLoaded")
        self.assertIn("КЛИЕНТЫ НЕ НАЙДЕНЫ", self.page.locator("#clientsList").inner_text())

    def test_closing_cancels_pending_search_timer(self) -> None:
        self.load_board()
        search = []
        self.page.route("**/api/search_clients?**", lambda route: search.append(route))
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")
        self.page.locator("#clientsSearchInput").fill("unlikely synthetic query")
        self.page.locator('#clientsModal [data-close="clients"]').click()
        self.assertIsNone(self.page.evaluate("state.clientsSearchTimer"))
        self.page.wait_for_timeout(250)
        self.assertEqual(search, [])

    def test_reopen_after_clearing_search_fetches_the_unfiltered_list(self) -> None:
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.wait_for_function("state.clientsLoaded")
        self.page.locator("#clientsSearchInput").fill("unlikely synthetic query")
        self.page.get_by_text("КЛИЕНТЫ НЕ НАЙДЕНЫ.").wait_for()
        self.page.evaluate("""() => {
            const input = document.querySelector('#clientsSearchInput');
            input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            document.querySelector('#clientsModal [data-close="clients"]').click();
        }""")
        held = []
        self.page.route("**/api/list_clients?**", lambda route: held.append(route))
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for(timeout=1000)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertIn("ЗАГРУЗКА", self.page.locator("#clientsList").inner_text())
        held.pop().continue_()
        self.page.wait_for_function("state.clientsLoaded")
        self.assertGreater(self.page.locator("#clientsList [data-client-id]").count(), 0)
