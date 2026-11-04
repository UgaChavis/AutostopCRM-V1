"""Mobile users can open every order returned by the server's bounded list."""

from copy import deepcopy
from uuid import uuid4

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrdersLimitBrowserTests(UiBrowserTestCase):
    def test_returned_orders_limit_notice_and_filters(self):
        template_id = self.create_card("SYNTHETIC ORDER TEMPLATE")
        self.runtime.service.get_repair_order({"card_id": template_id, "create_if_missing": True})
        with self.runtime.service._lock:
            bundle = self.runtime.state_store.read_bundle()
            template = next(card for card in bundle["cards"] if card.id == template_id)
            cards = []
            for index in range(302):
                card = deepcopy(template)
                card.id = str(uuid4())
                card.title = f"SYNTHETIC ORDER {index}"
                card.repair_order.number = str(10000 + index)
                card.repair_order.status = "ready" if index == 301 else "open"
                cards.append(card)
            bundle["cards"] = cards
            self.runtime.state_store.write_bundle(**bundle)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="repair-orders"]')
        self.page.wait_for_function("state.repairOrdersMetaState?.total === 301")
        listing = self.page.locator("#mobileRepairOrdersList")
        rows = listing.locator("[data-open-repair-order-card]")
        self.assertEqual(rows.count(), 300)
        self.assertIn("ЗАГРУЖЕНО 300 ИЗ 301", listing.inner_text())
        for index in (40, 299):
            row = rows.nth(index)
            card_id = row.get_attribute("data-open-repair-order-card")
            row.click()
            self.page.wait_for_function("id => state.mobileRepairOrderCard?.id === id", arg=card_id)
            self.assertTrue(self.page.locator("#mobileRepairOrderDetail").is_visible())
            self.page.click("#mobileRepairOrderBackButton")

        for status, expected_count in (("ready", 1), ("closed", 0)):
            self.page.set_viewport_size({"width": 1440, "height": 960})
            self.page.click("#repairOrdersButton")
            self.page.click(f'[data-repair-orders-status="{status}"]')
            self.page.wait_for_function(
                "args => state.repairOrdersMetaState?.status === args.status && state.repairOrdersMetaState.total === args.count",
                arg={"status": status, "count": expected_count},
            )
            self.page.click('[data-close="repair-orders"]')
            self.page.set_viewport_size({"width": 390, "height": 844})
            self.assertEqual(rows.count(), expected_count)
            self.assertNotIn("ЗАГРУЖЕНО", listing.inner_text())
        self.assertIn("АРХИВ ЗАКАЗ-НАРЯДОВ ПУСТ", listing.inner_text())
