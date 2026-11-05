"""A background list failure must not overwrite a completed order save."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersBackgroundStatusBrowserTests(UiBrowserTestCase):
    def exercise_background_failure(self, *, leave_view, replace_session=False):
        card = self.create_card("SYNTHETIC BACKGROUND STATUS")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.open_card(card)
        self.page.click("#repairOrderButton")
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.page.locator("#repairOrderClient").fill("SYNTHETIC SAVED CLIENT")
        held_update, held_list = [], []
        self.page.route("**/api/update_repair_order", lambda route: held_update.append(route))
        self.page.route("**/api/list_repair_orders?**", lambda route: held_list.append(route))
        with self.page.expect_request("**/api/update_repair_order"):
            self.page.locator("#repairOrderSaveButton").click()
        self.assertEqual(len(held_update), 1)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        with self.page.expect_request("**/api/list_repair_orders?**"):
            held_update.pop().continue_()
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        self.assertEqual(self.page.locator("#statusLine").inner_text(), "Заказ-наряд сохранён.")
        self.assertEqual(self.stored_card(card)["repair_order"]["client"], "SYNTHETIC SAVED CLIENT")
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.page.locator("#cardModalCloseButtonTop").click()
        if replace_session:
            token = self.context.request.post(
                self.runtime.base_url + "/api/login_operator",
                data={"username": "admin", "password": "admin"},
            ).json()["data"]["session"]["token"]
            self.page.evaluate("token => setOperatorSessionToken(token, {persist:false})", token)
            with self.page.expect_request("**/api/list_repair_orders?**"):
                self.page.locator("#mobileRepairOrdersRefreshButton").click()
            self.assertEqual(len(held_list), 2)
            held_list.pop().continue_()
            self.page.wait_for_function(
                "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
            )
            held_list.pop().fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC STALE ERROR"}}
            )
            self.page.wait_for_timeout(150)
            self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
            self.assertEqual(self.page.locator("#statusLine").inner_text(), "Заказ-наряд сохранён.")
            self.assertEqual(self.writes.count("update_repair_order"), 1)
            return
        if leave_view:
            self.page.locator('[data-mobile-view="board"]').click()
        held_list.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "SYNTHETIC LIST ERROR"}}
        )
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertEqual(self.page.locator("#statusLine").inner_text(), "Заказ-наряд сохранён.")
        self.assertEqual(self.writes.count("update_repair_order"), 1)
        if leave_view:
            self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileRepairOrdersList").inner_text())
        self.page.unroute("**/api/list_repair_orders?**")
        with self.page.expect_response("**/api/list_repair_orders?**"):
            self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && !state.repairOrdersLoadError"
        )
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        self.assertIn("SYNTHETIC SAVED CLIENT", row.inner_text())

        # An interactive retry keeps its existing global error reporting.
        self.page.route(
            "**/api/list_repair_orders?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC INTERACTIVE ERROR"}}
            ),
        )
        with self.page.expect_response("**/api/list_repair_orders?**"):
            self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertEqual(
            self.page.locator("#statusLine").inner_text(), "SYNTHETIC INTERACTIVE ERROR"
        )

    def test_background_error_on_active_tab(self):
        self.exercise_background_failure(leave_view=False)

    def test_background_error_after_leaving_tab(self):
        self.exercise_background_failure(leave_view=True)

    def test_old_session_failure_cannot_replace_new_list_or_status(self):
        self.exercise_background_failure(leave_view=False, replace_session=True)
