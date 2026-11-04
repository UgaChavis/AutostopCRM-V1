"""A desktop-only search must not hide orders in the mobile list."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersDesktopSearchBrowserTests(UiBrowserTestCase):
    def test_resize_back_resets_search_without_click_and_preserves_draft(self):
        card = self.create_card("SYNTHETIC MOBILE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="repair-orders"]')
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderLoading && !!state.mobileRepairOrderCard"
        )
        self.page.fill("#mobileRepairOrderClient", "SYNTHETIC DRAFT")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.click("#repairOrdersButton")
        self.page.fill("#repairOrdersSearchInput", "SYNTHETIC NO MATCH")
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.query === 'SYNTHETIC NO MATCH' && !state.repairOrdersSearchLoading"
        )
        self.page.click('[data-close="repair-orders"]')
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function(
            "state.mobileLite && state.repairOrdersQuery === ''", timeout=5000
        )
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.query === ''"
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC DRAFT"
        )
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), card)

    def open_desktop_list(self):
        card = self.create_card("SYNTHETIC VISIBLE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.load_board()
        self.page.click("#repairOrdersButton")
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').wait_for()
        return card

    def enter_mobile_orders(self):
        self.page.click('[data-close="repair-orders"]')
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-view="repair-orders"]')

    def test_search_is_cleared_and_desktop_return_and_draft_are_preserved(self):
        card = self.open_desktop_list()
        self.page.fill("#repairOrdersSearchInput", "SYNTHETIC NO MATCH")
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.query === 'SYNTHETIC NO MATCH' && !state.repairOrdersSearchLoading"
        )
        self.enter_mobile_orders()
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        row.wait_for()
        self.assertEqual(self.page.evaluate("state.repairOrdersQuery"), "")
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.query"), "")
        seq = self.page.evaluate("state.repairOrdersRequestSeq")
        self.page.click('[data-mobile-view="repair-orders"]')
        self.assertEqual(self.page.evaluate("state.repairOrdersRequestSeq"), seq)
        row.click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderLoading && !!state.mobileRepairOrderCard"
        )
        self.page.fill("#mobileRepairOrderClient", "SYNTHETIC DRAFT")

        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.click("#repairOrdersButton")
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && !!state.repairOrdersMetaState"
        )
        self.assertEqual(self.page.locator("#repairOrdersSearchInput").input_value(), "")
        self.page.fill("#repairOrdersSearchInput", "SYNTHETIC NO MATCH")
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.query === 'SYNTHETIC NO MATCH' && !state.repairOrdersSearchLoading"
        )
        self.enter_mobile_orders()
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.query === ''"
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC DRAFT"
        )
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), card)

    def test_late_desktop_search_cannot_restore_filter(self):
        card = self.open_desktop_list()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.fill("#repairOrdersSearchInput", "SYNTHETIC NO MATCH")
        self.page.wait_for_timeout(1200)
        self.assertEqual(len(held), 1)
        self.assertIn("query=", held[0].request.url)
        self.enter_mobile_orders()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        self.assertNotIn("query=", held[1].request.url)
        held.pop().continue_()
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        row.wait_for()
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertTrue(row.is_visible())
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.query"), "")
        self.assertFalse(self.page.evaluate("state.repairOrdersSearchLoading"))

    def test_pending_desktop_search_timer_does_not_restart_hidden_search(self):
        card = self.open_desktop_list()
        requests = []
        self.page.on(
            "request",
            lambda request: (
                requests.append(request.url) if "/api/list_repair_orders?" in request.url else None
            ),
        )
        self.page.fill("#repairOrdersSearchInput", "SYNTHETIC NO MATCH")
        self.enter_mobile_orders()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]'
        ).wait_for()
        self.page.wait_for_timeout(1200)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("query=", requests[0])
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.query"), "")
