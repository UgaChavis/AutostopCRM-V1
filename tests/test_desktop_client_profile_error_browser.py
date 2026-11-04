"""Client profile errors remain visible until a successful retry."""

from tests.browser_ui_support import UiBrowserTestCase


class DesktopClientProfileErrorBrowserTests(UiBrowserTestCase):
    def prepare_clients(self):
        clients = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in ("SYNTHETIC FIRST", "SYNTHETIC SECOND")
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        return clients

    def test_error_remains_visible_and_retry_loads_profile(self):
        first, _second = self.prepare_clients()

        def fail(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/get_client?**", fail)
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "document.querySelector('#clientProfileTitle').textContent.includes('ПРОФИЛЬ НЕ ЗАГРУЖЕН')"
        )
        self.assertFalse(self.page.locator("#clientDebtValue").is_visible())
        self.assertFalse(self.page.locator("#clientDisplayNameInput").is_visible())
        self.page.evaluate("setStatus('СЕРВЕР АКТИВЕН', false)")
        self.assertIn("ПРОФИЛЬ НЕ ЗАГРУЖЕН", self.page.locator("#clientProfileTitle").inner_text())
        self.assertIn("ПОВТОРНО", self.page.locator("#clientProfilePhone").inner_text())
        self.page.unroute("**/api/get_client?**", fail)
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC FIRST"
        )

    def test_late_failure_does_not_replace_new_profile_or_new_viewer(self):
        first, second = self.prepare_clients()
        held = []

        def delay_first(route):
            if first in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_client?**", delay_first)
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.locator(f'#clientsList [data-client-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=second
        )
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_timeout(100)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC SECOND"
        )
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("resetViewerScopedState()")
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
        self.assertEqual(self.page.locator("#clientProfileTitle").inner_text(), "КЛИЕНТ НЕ ВЫБРАН")
