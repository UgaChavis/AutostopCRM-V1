"""Late cashbox reads must not replace an unrelated mobile order list."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersCashboxRerenderBrowserTests(UiBrowserTestCase):
    CASHBOX_LIST_URL = "**/api/list_cashboxes?limit=200"

    def open_cashboxes(self):
        with self.page.expect_request(self.CASHBOX_LIST_URL):
            self.page.locator('[data-mobile-view="cashboxes"]').click()

    def prepare_pending_cashboxes(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.route(
            "**/api/list_cashboxes?limit=1",
            lambda route: route.fulfill(
                json={"ok": True, "data": {"notification": {"initialized": True}}}
            ),
        )
        self.load_board()
        self.page.evaluate(
            """() => {
                stopSnapshotPolling();
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
        self.page.route(self.CASHBOX_LIST_URL, lambda route: held.append(route))
        self.open_cashboxes()
        self.assertEqual(len(held), 1)
        self.page.evaluate("window.__abandonedCashboxesLoad = state.cashboxesLoadController")
        with self.page.expect_event(
            "requestfailed",
            predicate=lambda request: request.url.endswith("/api/list_cashboxes?limit=200"),
        ) as abandoned_request:
            self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertEqual(abandoned_request.value.failure, "net::ERR_ABORTED")
        self.page.evaluate(
            """() => {
                window.__originalOrderRow = document.querySelectorAll('#mobileRepairOrdersList button')[1];
                window.__originalOrderRow.focus();
                setStatus('SYNTHETIC STATUS');
            }"""
        )
        return held

    def check_late_result(self, cashboxes):
        held = self.prepare_pending_cashboxes()
        held.pop().fulfill(json={"ok": True, "data": {"cashboxes": cashboxes, "meta": {}}})
        self.assertTrue(self.page.evaluate("window.__abandonedCashboxesLoad.signal.aborted"))
        self.assertIsNone(self.page.evaluate("state.cashboxesLoadController"))
        self.assertFalse(self.page.evaluate("state.cashboxesLoaded"))
        self.assertEqual(self.page.evaluate("state.cashboxes"), [])
        self.assertNotIn("mark_cashbox_notifications_seen", self.writes)
        self.assertTrue(
            self.page.evaluate(
                """() => window.__originalOrderRow.isConnected &&
                    document.activeElement === window.__originalOrderRow &&
                    document.querySelectorAll('#mobileRepairOrdersList button').length === 3"""
            )
        )
        self.assertEqual(self.page.locator("#mobileStatusLine").inner_text(), "SYNTHETIC STATUS")
        return held

    def reopen_with_fresh_cashboxes(self, held, cashboxes):
        self.open_cashboxes()
        self.assertEqual(len(held), 1)
        held.pop().fulfill(json={"ok": True, "data": {"cashboxes": cashboxes, "meta": {}}})
        self.page.wait_for_function("state.cashboxesLoaded && !state.cashboxesLoadController")

    def test_late_cashboxes_keep_order_focus_and_render_cashboxes(self):
        held = self.check_late_result([{"id": "abandoned", "name": "ABANDONED BOX"}])
        cashbox = {"id": "fresh", "name": "FRESH BOX", "statistics": {}}
        self.page.route(
            "**/api/get_cashbox?**",
            lambda route: route.fulfill(
                json={
                    "ok": True,
                    "data": {
                        "cashbox": cashbox,
                        "transactions": [{"id": "fresh-row", "note": "FRESH MOVEMENT"}],
                    },
                }
            ),
        )
        self.reopen_with_fresh_cashboxes(held, [cashbox])
        self.assertEqual(self.page.evaluate("state.cashboxes.map(box => box.id)"), ["fresh"])
        self.assertIn("FRESH BOX", self.page.locator("#mobileCashboxList").inner_text())
        self.assertIn("FRESH BOX", self.page.locator("#mobileCashboxDetail").inner_text())
        self.assertIn("FRESH MOVEMENT", self.page.locator("#mobileCashboxDetail").inner_text())

    def test_late_empty_cashboxes_keep_order_focus(self):
        held = self.check_late_result([])
        self.reopen_with_fresh_cashboxes(held, [])
        self.assertIsNone(self.page.evaluate("state.activeCashbox"))
        self.assertIn("КАСС ПОКА НЕТ.", self.page.locator("#mobileCashboxList").inner_text())
