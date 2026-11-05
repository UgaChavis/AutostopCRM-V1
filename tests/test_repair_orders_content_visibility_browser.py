"""Offscreen desktop order rows keep their geometry and remain actionable."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersContentVisibilityBrowserTests(UiBrowserTestCase):
    def check_mixed_desktop_rows(self, width):
        self.page.set_viewport_size({"width": width, "height": 960})
        cards = [self.create_card("SYNTHETIC ORDER " + name) for name in ("LONG", "LAST")]
        for card in cards:
            self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.click("#repairOrdersButton")
        self.page.wait_for_function(
            "state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.page.evaluate(
            """cards => {
              state.repairOrdersRequestSeq++;
              state.repairOrdersFilter = 'open';
              state.repairOrdersSearchLoading = false;
              state.repairOrdersLoadError = false;
              state.repairOrdersMetaState = {total: 300, returned: 300, has_more: false, inconsistent_total: 0};
              state.repairOrdersItems = Array.from({length: 300}, (_, i) => ({
                card_id: i === 40 ? cards[0] : i === 299 ? cards[1] : 'synthetic-' + i,
                number: i === 40 ? 'LONG-SYNTHETIC-NUMBER-2026' : String(i + 1),
                status: 'open', client: 'SYNTHETIC CLIENT', vehicle: 'SYNTHETIC CAR',
                summary: 'SYNTHETIC WORK', grand_total: '100.00', payment_status: 'unpaid'
              }));
              renderRepairOrdersView(true);
            }""",
            cards,
        )
        geometry = self.page.evaluate(
            """async () => {
              const rows = [...els.repairOrdersList.querySelectorAll('.repair-orders-row')];
              const measure = () => ({height: els.repairOrdersList.scrollHeight,
                rows: rows.map(row => row.getBoundingClientRect().height)});
              const override = document.createElement('style');
              override.textContent = '.repair-orders-row { content-visibility: visible !important }';
              document.head.appendChild(override);
              const before = measure();
              override.remove();
              await new Promise(requestAnimationFrame);
              await new Promise(requestAnimationFrame);
              return {before, after: measure(),
                regular: getComputedStyle(rows[299]).contentVisibility,
                long: getComputedStyle(rows[40]).contentVisibility};
            }"""
        )
        self.assertEqual(len(geometry["after"]["rows"]), 300)
        self.assertEqual(geometry["before"], geometry["after"])
        self.assertEqual(geometry["regular"], "auto")
        self.assertEqual(geometry["long"], "visible")

        long_row = self.page.locator(
            f'#repairOrdersList [data-open-repair-order-card="{cards[0]}"]'
        )
        long_row.scroll_into_view_if_needed()
        long_row.click()
        self.page.wait_for_function("id => state.editingId === id", arg=cards[0])
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.page.locator("#repairOrderModal.is-open").wait_for(state="hidden")

        last = self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{cards[1]}"]')
        previous = self.page.locator("#repairOrdersList .repair-orders-row").nth(298)
        previous.scroll_into_view_if_needed()
        before = previous.evaluate(
            """row => {
              const scroller = row.closest('.repair-orders-body-scroll');
              scroller.scrollTop += row.getBoundingClientRect().bottom - scroller.getBoundingClientRect().bottom + 2;
              row.focus({preventScroll: true});
              return {focused: document.activeElement === row,
                nextTop: row.nextElementSibling.getBoundingClientRect().top,
                bottom: scroller.getBoundingClientRect().bottom};
            }"""
        )
        self.assertTrue(before["focused"])
        self.assertGreater(before["nextTop"], before["bottom"])
        self.page.keyboard.press("Tab")
        self.page.wait_for_function(
            "id => document.activeElement?.dataset.openRepairOrderCard === id",
            arg=cards[1],
        )
        focus = last.evaluate(
            """row => {
              const rect = row.getBoundingClientRect();
              const scroller = row.closest('.repair-orders-body-scroll').getBoundingClientRect();
              const style = getComputedStyle(row);
              return {visible: rect.bottom > scroller.top && rect.top < scroller.bottom,
                focusVisible: row.matches(':focus-visible'), outlineStyle: style.outlineStyle,
                outlineWidth: style.outlineWidth};
            }"""
        )
        self.assertTrue(focus["visible"])
        self.assertTrue(focus["focusVisible"])
        self.assertNotEqual(focus["outlineStyle"], "none")
        self.assertNotEqual(focus["outlineWidth"], "0px")
        self.page.keyboard.press("Space")
        self.page.wait_for_function("id => state.editingId === id", arg=cards[1])
        self.page.locator("#repairOrderModal.is-open").wait_for()

    def test_mixed_rows_desktop(self):
        self.check_mixed_desktop_rows(1440)

    def test_mixed_rows_narrow_desktop(self):
        self.check_mixed_desktop_rows(1024)

    def test_mobile_rows_do_not_receive_desktop_visibility_rule(self):
        card = self.create_card("SYNTHETIC MOBILE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="repair-orders"]')
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        row.wait_for()
        self.assertEqual(
            row.evaluate("element => getComputedStyle(element).contentVisibility"), "visible"
        )
        row.click()
        self.page.wait_for_function("id => state.mobileRepairOrderCard?.id === id", arg=card)
        self.page.locator("#mobileRepairOrderDetail").wait_for(state="visible")
