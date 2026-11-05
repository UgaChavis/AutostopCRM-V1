"""Repair-order amounts display kopecks without changing saved values."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderCentsDisplayBrowserTests(UiBrowserTestCase):
    def make_order(self):
        card = self.create_card("SYNTHETIC CENTS")
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [
                        {"name": "SYNTHETIC A", "quantity": "1", "price": "0.49"},
                        {"name": "SYNTHETIC B", "quantity": "1", "price": "1.50"},
                        {"name": "SYNTHETIC C", "quantity": "1", "price": "10"},
                    ],
                    "materials": [{"name": "SYNTHETIC MATERIAL", "quantity": "1", "price": "0.49"}],
                },
            }
        )
        return card

    def test_desktop_load_recalculate_and_save(self):
        card = self.make_order()
        self.load_board()
        self.open_card(card)
        self.page.click("#repairOrderButton")
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        rows = self.page.locator("#repairOrderWorksBody [data-repair-order-row]")
        totals = rows.locator("[data-repair-order-row-total]")
        self.assertEqual(totals.all_inner_texts(), ["0,49 ₽", "1,50 ₽", "10 ₽"])
        self.assertEqual(
            self.page.locator('[data-repair-order-total="works"]').inner_text(), "11,99 ₽"
        )
        self.assertEqual(
            self.page.locator('[data-repair-order-total="materials"]').inner_text(), "0,49 ₽"
        )
        rows.first.locator('[data-repair-order-cell="price"]').fill("1.49")
        self.assertEqual(totals.first.inner_text(), "1,49 ₽")
        self.assertEqual(
            self.page.locator('[data-repair-order-total="works"]').inner_text(), "12,99 ₽"
        )
        self.page.click("#repairOrderSaveButton")
        self.page.wait_for_function("!state.repairOrderSaveInFlight")
        saved = self.runtime.service.get_repair_order({"card_id": card})["repair_order"]
        self.assertEqual([row["price"] for row in saved["works"]], ["1.49", "1.50", "10"])
        self.assertEqual(saved["materials"][0]["price"], "0.49")
        extras = self.page.evaluate("""() => {
          const container = document.createElement('tbody');
          container.innerHTML = repairOrderRowHtml('works', {}, 0) + repairOrderRowHtml('works', {total:'-0.49'}, 1);
          return Array.from(container.querySelectorAll('[data-repair-order-row-total]'), node => node.textContent);
        }""")
        self.assertEqual(extras, ["-", "-0,49 ₽"])

    def test_mobile_load_recalculate_save_and_payment_render(self):
        card = self.make_order()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        rows = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]")
        totals = rows.locator("[data-mobile-repair-row-total]")
        self.assertEqual(totals.all_inner_texts(), ["0,49 ₽", "1,50 ₽", "10 ₽"])
        self.assertIn("11,99 ₽", self.page.locator("#mobileRepairOrderTotals").text_content())
        rows.first.locator('[data-mobile-repair-row-field="price"]').fill("1.49")
        self.assertEqual(totals.first.inner_text(), "1,49 ₽")
        self.assertIn("12,99 ₽", self.page.locator("#mobileRepairOrderTotals").text_content())
        self.page.click("#mobileRepairOrderSaveButton")
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        saved = self.runtime.service.get_repair_order({"card_id": card})["repair_order"]
        self.assertEqual([row["price"] for row in saved["works"]], ["1.49", "1.50", "10"])
        self.assertEqual(saved["materials"][0]["price"], "0.49")
        amounts = self.page.evaluate("""() => ['0.49','1.50','10','-0.49'].map(amount => {
          const box=document.createElement('div');
          box.innerHTML=mobileRepairOrderPaymentRowHtml({id:'synthetic', amount});
          return [box.querySelector('strong').textContent, box.firstElementChild.dataset.mobileRepairOrderPaymentAmount];
        })""")
        self.assertEqual(
            amounts, [["0,49 ₽", "0.49"], ["1,50 ₽", "1.5"], ["10 ₽", "10"], ["-0,49 ₽", "-0.49"]]
        )

    def test_client_profile_shows_order_cents_without_changing_debt_rounding(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"][
            "id"
        ]
        card = self.create_card("SYNTHETIC CLIENT ORDER", client_id=client)
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "0.49"}],
                },
            }
        )
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertEqual(
            self.page.locator("#clientOrdersList .client-mini__order-total-value").inner_text(),
            "0,49 ₽",
        )
        self.assertEqual(self.page.locator("#clientDebtValue").inner_text(), "0 ₽")
        self.page.locator('[data-close="clients"]').click()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="clients"]').click()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
            arg=client,
        )
        self.assertIn(
            "0,49 ₽",
            self.page.locator(
                f'#mobileClientDetail [data-mobile-client-order-card="{card}"]'
            ).inner_text(),
        )
        self.assertEqual(self.page.locator(".mobile-client-detail__debt").inner_text(), "0 ₽")
