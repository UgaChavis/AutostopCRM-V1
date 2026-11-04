"""The list must not replace an explicit zero total with a work subtotal."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderListZeroTotalBrowserTests(UiBrowserTestCase):
    def test_zero_total_and_missing_total_on_desktop_and_mobile(self):
        card = self.create_card("SYNTHETIC ZERO TOTAL")
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [{"name": "SYNTHETIC WORK", "total": "100"}],
                    "materials": [{"name": "SYNTHETIC ADJUSTMENT", "total": "-100"}],
                },
            }
        )
        result = self.runtime.service.list_repair_orders({"status": "all"})
        order = next(row for row in result["repair_orders"] if row["card_id"] == card)
        self.assertEqual((order["works_total"], order["grand_total"]), ("100", "0"))
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        desktop = self.page.locator(
            f'#repairOrdersList [data-open-repair-order-card="{card}"] .repair-orders-row__total'
        )
        desktop.wait_for()
        desktop_total = desktop.inner_text()
        self.page.locator('[data-close="repair-orders"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        mobile = self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"] .mobile-repair-order-row__sum'
        )
        mobile.wait_for()
        mobile_total = mobile.inner_text()
        values = self.page.evaluate(
            """() => [
              repairOrderListTotalText('0', '100'),
              repairOrderListTotalText(0, '100'),
              repairOrderListTotalText('0.00', '100'),
              repairOrderListTotalText('0,00', '100'),
              repairOrderListTotalText('-0', '100'),
              repairOrderListTotalText('1 234,56', '100'),
              repairOrderListTotalText('not-a-number', '100'),
              repairOrderListTotalText('', '100'),
              repairOrderListTotalText(null, '100')
            ]"""
        )
        self.assertEqual((desktop_total, mobile_total), ("0", "0"))
        self.assertEqual(
            values, ["0", "0", "0.00", "0,00", "-0", "1 234,56", "not-a-number", "100", "100"]
        )
