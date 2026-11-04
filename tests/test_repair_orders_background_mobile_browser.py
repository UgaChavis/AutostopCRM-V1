"""A background list read must not rewrite another mobile editor."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersBackgroundMobileBrowserTests(UiBrowserTestCase):
    def hold_orders(self):
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        return held

    def complete_orders(self, held):
        held.pop().fulfill(
            json={
                "ok": True,
                "data": {
                    "repair_orders": [
                        {
                            "card_id": "synthetic-order",
                            "number": "444",
                            "status": "open",
                            "status_label": "Открыт",
                            "payment_status": "paid",
                            "payment_status_label": "Оплачен",
                            "is_paid": True,
                            "paid_total": "0",
                            "paid_total_display": "0",
                            "works_total": "0",
                            "grand_total": "0",
                        }
                    ],
                    "meta": {
                        "status": "open",
                        "sort_by": "number",
                        "sort_dir": "desc",
                        "limit": 300,
                        "total": 1,
                        "returned": 1,
                        "has_more": False,
                    },
                },
            }
        )
        self.page.wait_for_function("state.repairOrdersMetaState !== null")

    def test_background_list_does_not_overwrite_mobile_card_draft(self):
        card_id = self.create_card("SYNTHETIC ORIGINAL")
        self.load_board()
        self.page.set_viewport_size({"width": 390, "height": 844})
        held = self.hold_orders()
        self.page.locator('[data-mobile-view="board"]').click()
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC UNSAVED")
        self.complete_orders(held)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC UNSAVED"
        )
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertIn("444", self.page.locator("#mobileRepairOrdersList").inner_text())

    def test_background_list_does_not_change_mobile_transfer_target(self):
        self.runtime.service.create_cashbox({"name": "SYNTHETIC BOX TWO"})
        third = self.runtime.service.create_cashbox({"name": "SYNTHETIC BOX THREE"})["cashbox"][
            "id"
        ]
        self.load_board()
        cash_writes = []
        self.page.on(
            "request",
            lambda request: (
                cash_writes.append(request.url)
                if request.method != "GET" and "/api/create_cashbox_transfer" in request.url
                else None
            ),
        )
        self.page.set_viewport_size({"width": 390, "height": 844})
        held = self.hold_orders()
        self.page.locator('[data-mobile-view="cashboxes"]').click()
        self.page.wait_for_function(
            "state.cashboxesLoaded && state.cashboxes.length >= 3 && !state.cashboxesLoadController"
        )
        self.page.locator("#mobileCashboxTransferButton").click()
        self.page.locator("#mobileCashboxTargetSelect").select_option(third)
        self.page.locator("#mobileCashboxAmountInput").fill("123")
        self.complete_orders(held)
        self.assertEqual(self.page.locator("#mobileCashboxTargetSelect").input_value(), third)
        self.assertEqual(self.page.locator("#mobileCashboxAmountInput").input_value(), "123")
        self.assertEqual(cash_writes, [])
