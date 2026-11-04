"""Repeated navigation to mobile orders keeps the current list and draft."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersActiveTabBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.page.set_viewport_size({"width": 390, "height": 844})

    def test_same_tab_keeps_rows_and_return_renders_latest_list(self):
        self.load_board()
        self.page.evaluate(
            """() => {
                state.repairOrdersItems = [{card_id: 'old', number: '1', status: 'open'}];
                state.repairOrdersMetaState = {total: 1, returned: 1, has_more: false};
                setMobileView('repair-orders');
                window.originalOrderRow = document.querySelector('#mobileRepairOrdersList button');
                window.originalOrderRow.focus();
                document.querySelector('[data-mobile-view="repair-orders"]').click();
            }"""
        )
        self.assertTrue(self.page.evaluate("window.originalOrderRow.isConnected"))
        self.assertTrue(self.page.evaluate("document.activeElement === window.originalOrderRow"))
        self.page.evaluate(
            """() => {
                setMobileView('board');
                state.repairOrdersItems = [{card_id: 'new', number: '2', status: 'open'}];
                setMobileView('repair-orders');
            }"""
        )
        self.assertEqual(
            self.page.locator(
                '#mobileRepairOrdersList [data-open-repair-order-card="new"]'
            ).count(),
            1,
        )

    def test_empty_meta_still_loads_and_retry_recovers(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function("state.repairOrdersSearchLoading")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileRepairOrdersList").inner_text())
        self.page.unroute("**/api/list_repair_orders?**")
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function("!state.repairOrdersLoadError && !!state.repairOrdersMetaState")
        self.assertEqual(
            self.page.locator("#mobileRepairOrdersList [data-open-repair-order-card]").count(),
            self.page.evaluate("state.repairOrdersMetaState.returned"),
        )

    def test_same_tab_preserves_open_detail_draft(self):
        card = self.create_card("SYNTHETIC MOBILE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC DRAFT")
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC DRAFT"
        )
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), card)
