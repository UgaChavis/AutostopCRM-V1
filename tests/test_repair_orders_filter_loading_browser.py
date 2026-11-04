"""An old order list must not appear under a new filter or after an error."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersFilterLoadingBrowserTests(UiBrowserTestCase):
    def open_list(self):
        card = self.create_card("SYNTHETIC OPEN ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.click("#repairOrdersButton")
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').wait_for()
        return card

    def test_filter_error_search_field_and_retry(self):
        self.open_list()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.click("#repairOrdersReadyTab")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.assertIn("ЗАГРУЗКА", self.page.locator("#repairOrdersList").inner_text())
        self.page.click('[data-repair-orders-search-field="number"]')
        self.assertTrue(self.page.evaluate("state.repairOrdersSearchLoading"))
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}}
        )
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.page.click('[data-repair-orders-search-field="client"]')
        self.assertIn("SYNTHETIC FAILURE", self.page.locator("#repairOrdersList").inner_text())
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.page.click("#repairOrdersReadyTab")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.status === 'ready' && !state.repairOrdersSearchLoading"
        )
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
        self.assertNotIn("SYNTHETIC FAILURE", self.page.locator("#repairOrdersList").inner_text())

    def test_search_debounce_and_late_response(self):
        card = self.open_list()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.locator("#repairOrdersSearchInput").fill("SYNTHETIC OPEN")
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.page.wait_for_timeout(1200)
        self.assertEqual(len(held), 1)
        self.page.locator("#repairOrdersSearchInput").fill("SYNTHETIC NO MATCH")
        held.pop().continue_()
        self.page.wait_for_timeout(1200)
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        held.pop().continue_()
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.query === 'SYNTHETIC NO MATCH'"
        )
        self.assertEqual(
            self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').count(),
            0,
        )
        self.assertIn("НИЧЕГО НЕ НАЙДЕНО", self.page.locator("#repairOrdersList").inner_text())

    def test_close_while_loading_then_mobile_retries(self):
        card = self.open_list()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.click("#repairOrdersReadyTab")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.click('[data-close="repair-orders"]')
        self.assertFalse(self.page.evaluate("state.repairOrdersSearchLoading"))
        self.assertIsNone(self.page.evaluate("state.repairOrdersMetaState"))
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-view="repair-orders"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop().continue_()
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.status === 'ready' && !state.repairOrdersSearchLoading"
        )
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(
            self.page.locator(
                f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]'
            ).count(),
            0,
        )
        self.assertFalse(self.page.evaluate("state.repairOrdersSearchLoading"))

    def test_open_order_draft_survives_background_failure(self):
        card = self.open_list()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.page.locator("#repairOrderClient").fill("SYNTHETIC UNSAVED DRAFT")
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.evaluate("() => { window.orderListRefresh = loadRepairOrders(false); }")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}}
        )
        self.page.evaluate("() => window.orderListRefresh")
        self.assertTrue(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )
        self.assertTrue(self.page.evaluate("repairOrderModalHasUnsavedChanges()"))
