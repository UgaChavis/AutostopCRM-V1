"""Unconfirmed client matches stay visible without presenting their totals as certain."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientSearchWarningsBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        service = self.runtime.service
        name = "SYNTHETIC LEGACY " + self._testMethodName
        self.client_id = service.create_client({"display_name": name})["client"]["id"]
        self.certain_id = service.create_client({"display_name": "SYNTHETIC CERTAIN"})["client"][
            "id"
        ]
        self.card_id = service.create_card({"title": "SYNTHETIC CARD", "vehicle": "SYNTHETIC CAR"})[
            "card"
        ]["id"]
        service.update_card(
            {
                "card_id": self.card_id,
                "repair_order": {
                    "client": name,
                    "opened_at": "04.11.2026 19:30",
                    "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "100"}],
                },
            }
        )
        self.results = service.search_clients({"query": "SYNTHETIC"})["clients"]
        self.legacy = next(client for client in self.results if client["id"] == self.client_id)
        self.assertIn("related_card_client_match_unverified", self.legacy["read_warnings"])

    def test_desktop_search_and_card_suggestions_show_uncertainty(self):
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC")
        self.page.wait_for_function(
            "id => state.clientsLoaded && state.clients.some(c => c.id === id && c.read_warnings)",
            arg=self.client_id,
        )
        legacy = self.page.locator(f'#clientsList [data-client-id="{self.client_id}"]')
        certain = self.page.locator(f'#clientsList [data-client-id="{self.certain_id}"]')
        self.assertIn("ЗН: 1", legacy.inner_text())
        self.assertIn("04.11.2026 19:30", legacy.inner_text())
        self.assertIn("04.11.2026 19:30", legacy.get_attribute("aria-label"))
        self.assertIn("СТАТИСТИКА НЕ ПОДТВЕРЖДЕНА", legacy.inner_text())
        self.assertIn("статистика не подтверждена", legacy.get_attribute("aria-label"))
        self.assertNotIn("НЕ ПОДТВЕРЖДЕНА", certain.inner_text())
        legacy.click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.client_id
        )
        orders = self.page.locator("#clientOrdersList")
        self.assertIn("04.11.2026 19:30", orders.inner_text())
        self.page.evaluate("""() => {
          state.clientsActiveProfile.repair_orders[0].opened_at = '04.11.2026 <img src=x onerror=alert(1)>';
          renderClientProfile(state.clientsActiveProfile);
        }""")
        self.assertIn("<img src=x onerror=alert(1)>", orders.inner_text())
        self.assertEqual(orders.locator("img").count(), 0)
        self.page.locator('#clientsModal [data-close="clients"]').click()
        self.open_card(self.card_id)
        self.page.evaluate(
            "client => renderClientSuggestions([client], {query: 'SYNTHETIC', showEmpty: true})",
            self.legacy,
        )
        suggestion = self.page.locator("#clientMatchList")
        self.assertIn("SYNTHETIC CAR", suggestion.inner_text())
        self.assertIn("СВЯЗИ АВТО НЕ ПОДТВЕРЖДЕНЫ", suggestion.inner_text())
        self.assertGreater(suggestion.locator("[data-select-client-vehicle]").count(), 0)
        self.assertGreater(suggestion.locator("[data-select-client-new-vehicle]").count(), 0)

    def test_mobile_search_shows_uncertainty(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        self.page.locator("#mobileClientsSearchInput").fill("SYNTHETIC")
        self.page.wait_for_function(
            "id => !state.mobileClientsLoading && state.clients.some(c => c.id === id && c.read_warnings)",
            arg=self.client_id,
        )
        legacy = self.page.locator(f'#mobileClientsList [data-mobile-client-id="{self.client_id}"]')
        certain = self.page.locator(
            f'#mobileClientsList [data-mobile-client-id="{self.certain_id}"]'
        )
        self.assertIn("ЗН: 1", legacy.inner_text())
        self.assertIn("04.11.2026 19:30", legacy.inner_text())
        self.assertIn("СТАТИСТИКА НЕ ПОДТВЕРЖДЕНА", legacy.inner_text())
        self.assertNotIn("НЕ ПОДТВЕРЖДЕНА", certain.inner_text())
        legacy.click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=self.client_id
        )
        self.assertEqual(
            self.page.locator(".mobile-client-detail__debt").inner_text(), "НЕТ ДАННЫХ"
        )
        detail = self.page.locator("#mobileClientDetail")
        self.assertIn("04.11.2026 19:30", detail.inner_text())
        self.page.evaluate("""() => {
          state.clientsActiveProfile.repair_orders[0].opened_at = '04.11.2026 <img src=x onerror=alert(1)>';
          renderMobileClientDetail();
        }""")
        self.assertIn("<img src=x onerror=alert(1)>", detail.inner_text())
        self.assertEqual(detail.locator("img").count(), 0)
