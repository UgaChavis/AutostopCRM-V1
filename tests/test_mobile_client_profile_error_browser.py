"""Mobile client profile errors stay visible until a successful retry."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileClientProfileErrorBrowserTests(UiBrowserTestCase):
    def prepare_clients(self):
        first, second = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in ("SYNTHETIC FIRST", "SYNTHETIC SECOND")
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DESKTOP DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="clients"]').click()
        self.page.wait_for_function("state.clientsLoaded && !state.mobileClientsLoading")
        return first, second

    def test_error_remains_visible_retry_and_desktop_draft(self):
        first, second = self.prepare_clients()

        def fail(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/get_client?**", fail)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.page.wait_for_function("!state.mobileClientProfileLoading")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), second)
        self.page.evaluate("setStatus('СЕРВЕР АКТИВЕН', false)")
        self.assertIn("ПРОФИЛЬ НЕ ЗАГРУЖЕН", self.page.locator("#mobileClientDetail").inner_text())
        self.assertIn("ПОВТОРНО", self.page.locator("#mobileClientDetail").inner_text())
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DESKTOP DRAFT"
        )
        self.page.unroute("**/api/get_client?**", fail)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=second
        )
        self.assertIn("SYNTHETIC SECOND", self.page.locator("#mobileClientDetail").inner_text())
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator("#clientsButton").click()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DESKTOP DRAFT"
        )

    def test_late_profile_error_does_not_override_new_selection_or_viewer(self):
        first, second = self.prepare_clients()
        held = []

        def delay_first(route):
            if first in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_client?**", delay_first)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=second
        )
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_timeout(100)
        self.assertIn("SYNTHETIC SECOND", self.page.locator("#mobileClientDetail").inner_text())
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("resetViewerScopedState()")
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
