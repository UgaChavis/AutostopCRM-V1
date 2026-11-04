"""Late cashbox reads must not replace an unrelated mobile order list."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersCashboxRerenderBrowserTests(UiBrowserTestCase):
    def prepare_pending_cashboxes(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.evaluate(
            """() => {
                state.repairOrdersItems = Array.from({length: 3}, (_, index) => ({
                    card_id: 'synthetic-' + index, number: String(index + 1),
                    status: 'open', client: 'SYNTHETIC CLIENT', vehicle: 'SYNTHETIC CAR',
                    summary: 'SYNTHETIC ORDER ' + index, grand_total: '10',
                }));
                state.repairOrdersMetaState = {total: 3, returned: 3, has_more: false};
                state.repairOrdersLoadError = false;
            }"""
        )
        held = []
        self.page.route("**/api/list_cashboxes?**", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="cashboxes"]').click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.evaluate(
            """() => {
                window.__originalOrderRow = document.querySelectorAll('#mobileRepairOrdersList button')[1];
                window.__originalOrderRow.focus();
                els.statusLine.textContent = 'SYNTHETIC STATUS';
            }"""
        )
        return held

    def check_late_result(self, cashboxes):
        held = self.prepare_pending_cashboxes()
        held.pop().fulfill(json={"ok": True, "data": {"cashboxes": cashboxes, "meta": {}}})
        self.page.wait_for_function("state.cashboxesLoaded && !state.cashboxesLoadController")
        self.assertTrue(
            self.page.evaluate(
                """() => window.__originalOrderRow.isConnected &&
                    document.activeElement === window.__originalOrderRow &&
                    document.querySelectorAll('#mobileRepairOrdersList button').length === 3"""
            )
        )
        self.assertEqual(self.page.locator("#mobileStatusLine").inner_text(), "SYNTHETIC STATUS")
        self.assertEqual(self.page.evaluate("state.cashboxes.length"), len(cashboxes))
        return held

    def test_late_cashboxes_keep_order_focus_and_render_cashboxes(self):
        cashboxes = [{"id": "synthetic-box", "name": "SYNTHETIC BOX", "statistics": {}}]
        held = self.check_late_result(cashboxes)
        self.page.locator('[data-mobile-view="cashboxes"]').click()
        self.assertIn("SYNTHETIC BOX", self.page.locator("#mobileCashboxList").inner_text())
        self.assertIn("SYNTHETIC BOX", self.page.locator("#mobileCashboxDetail").inner_text())
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().fulfill(json={"ok": True, "data": {"cashboxes": cashboxes, "meta": {}}})
        self.page.wait_for_function("!state.cashboxesLoadController")

    def test_late_empty_cashboxes_keep_order_focus(self):
        self.check_late_result([])
        self.assertIsNone(self.page.evaluate("state.activeCashbox"))
