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

    def test_loading_tab_reuses_first_request_and_return_from_board(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        tab = self.page.locator('[data-mobile-view="repair-orders"]')
        tab.click()
        self.page.wait_for_function("state.repairOrdersSearchLoading")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        tab.click()
        tab.click()
        self.page.locator('[data-mobile-view="board"]').click()
        tab.click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrdersList [data-open-repair-order-card]").count(),
            self.page.evaluate("state.repairOrdersMetaState.returned"),
        )

    def assert_old_list_cannot_restore_cache_after_desktop_save(
        self, *, stale_error, keep_mobile_view=False
    ):
        card = self.create_card("SYNTHETIC STALE LIST")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        held = []
        requests = []

        def route_list(route):
            requests.append(route.request.url)
            if len(requests) == 1:
                response = route.fetch()
                held.append((route, response.json()))
            else:
                route.continue_()

        self.page.route("**/api/list_repair_orders?**", route_list)
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function("state.repairOrdersSearchLoading")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        if not keep_mobile_view:
            self.page.locator('[data-mobile-view="board"]').click()
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.open_card(card)
        self.page.click("#repairOrderButton")
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.page.locator("#repairOrderClient").fill("SYNTHETIC UPDATED CLIENT")
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function(
            "!state.repairOrderMutationRequest && state.repairOrdersMetaState === null"
        )
        self.assertEqual(
            self.stored_card(card)["repair_order"]["client"], "SYNTHETIC UPDATED CLIENT"
        )
        route, response = held.pop()
        if stale_error:
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        else:
            route.fulfill(status=200, json=response)
        self.page.wait_for_timeout(150)
        self.assertIsNone(self.page.evaluate("state.repairOrdersMetaState"))
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.page.locator("#cardModalCloseButtonTop").click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        self.assertIn("SYNTHETIC UPDATED CLIENT", row.inner_text())
        self.assertEqual(len(requests), 2)
        self.page.locator('[data-mobile-view="board"]').click()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertIn("SYNTHETIC UPDATED CLIENT", row.inner_text())
        self.assertEqual(len(requests), 2)

    def test_old_success_does_not_restore_cache_after_desktop_save(self):
        self.assert_old_list_cannot_restore_cache_after_desktop_save(
            stale_error=False, keep_mobile_view=True
        )

    def test_old_error_does_not_restore_cache_after_desktop_save(self):
        self.assert_old_list_cannot_restore_cache_after_desktop_save(stale_error=True)

    def assert_save_after_resize_refreshes_active_mobile_orders(self, *, refresh_error):
        card = self.create_card("SYNTHETIC ACTIVE LIST")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        held_list = []
        held_refresh = []
        held_save = []
        list_requests = []

        def route_list(route):
            list_requests.append(route.request.url)
            if len(list_requests) == 1:
                held_list.append((route, route.fetch().json()))
            elif len(list_requests) == 2 and refresh_error:
                held_refresh.append(route)
            else:
                route.continue_()

        self.page.route("**/api/list_repair_orders?**", route_list)
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function("state.repairOrdersSearchLoading")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held_list), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.open_card(card)
        self.page.click("#repairOrderButton")
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.page.locator("#repairOrderClient").fill("SYNTHETIC UPDATED CLIENT")
        self.page.route("**/api/update_repair_order", lambda route: held_save.append(route))
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function("!!state.repairOrderMutationRequest")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held_save), 1)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        held_save.pop().continue_()
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        if refresh_error:
            self.page.wait_for_timeout(150)
            self.assertEqual(len(held_refresh), 1)
            self.assertTrue(self.page.evaluate("state.repairOrdersSearchLoading"))
            self.assertEqual(
                self.stored_card(card)["repair_order"]["client"], "SYNTHETIC UPDATED CLIENT"
            )
            route, old_response = held_list.pop()
            route.fulfill(status=200, json=old_response)
            self.page.wait_for_timeout(150)
            self.assertTrue(self.page.evaluate("state.repairOrdersSearchLoading"))
            self.assertIsNone(self.page.evaluate("state.repairOrdersMetaState"))
            held_refresh.pop().fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            )
            self.page.wait_for_function("state.repairOrdersLoadError")
            self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileRepairOrdersList").inner_text())
            self.page.evaluate("closeRepairOrderModal(); closeCardModal()")
            self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        self.assertIn("SYNTHETIC UPDATED CLIENT", row.inner_text())
        self.assertEqual(len(list_requests), 3 if refresh_error else 2)
        if not refresh_error:
            route, old_response = held_list.pop()
            route.fulfill(status=200, json=old_response)
            self.page.wait_for_timeout(150)
            self.assertIn("SYNTHETIC UPDATED CLIENT", row.inner_text())
            self.assertEqual(len(list_requests), 2)

    def test_save_after_resize_refreshes_active_mobile_orders_once(self):
        self.assert_save_after_resize_refreshes_active_mobile_orders(refresh_error=False)

    def test_background_refresh_failure_does_not_delay_save_and_can_retry(self):
        self.assert_save_after_resize_refreshes_active_mobile_orders(refresh_error=True)

    def test_filter_change_during_first_load_still_requests_latest_status(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("() => { setRepairOrdersFilter('ready'); }")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        self.assertIn("status=ready", held[1].request.url)
        held.pop().continue_()
        self.page.wait_for_function(
            "state.repairOrdersMetaState?.status === 'ready' && !state.repairOrdersSearchLoading"
        )
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.status"), "ready")
        self.assertFalse(self.page.evaluate("state.repairOrdersSearchLoading"))

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
