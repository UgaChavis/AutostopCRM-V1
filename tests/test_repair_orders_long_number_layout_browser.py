"""Long order numbers must occupy their complete row in the desktop list."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersLongNumberLayoutBrowserTests(UiBrowserTestCase):
    def check_long_number(self, width):
        self.page.set_viewport_size({"width": width, "height": 960})
        card = self.create_card("SYNTHETIC LONG NUMBER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.click("#repairOrdersButton")
        self.page.wait_for_function(
            "state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.page.add_style_tag(
            content=".repair-orders-row { content-visibility: visible !important }"
        )
        self.page.evaluate(
            """card => {
              state.repairOrdersRequestSeq++;
              state.repairOrdersFilter = 'open';
              state.repairOrdersSearchLoading = false;
              state.repairOrdersLoadError = false;
              state.repairOrdersMetaState = {total: 300, returned: 300, has_more: false, inconsistent_total: 0};
              state.repairOrdersItems = Array.from({length: 300}, (_, i) => ({
                card_id: i === 40 ? card : 'synthetic-' + i,
                number: i === 40 ? 'LONG-SYNTHETIC-NUMBER-2026' : String(i + 1),
                status: 'open', client: 'SYNTHETIC CLIENT', vehicle: 'SYNTHETIC CAR',
                summary: 'SYNTHETIC WORK', grand_total: '100.00', payment_status: 'unpaid'
              }));
              renderRepairOrdersView(true);
            }""",
            card,
        )
        row = self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]')
        row.scroll_into_view_if_needed()
        layout = row.evaluate(
            """row => {
              const current = row.getBoundingClientRect();
              const next = row.nextElementSibling.getBoundingClientRect();
              const number = row.querySelector('.repair-orders-row__number');
              const normal = row.nextElementSibling;
              return {gap: next.top - current.bottom, rowHeight: current.height,
                numberHeight: number.clientHeight, numberScrollHeight: number.scrollHeight,
                normalHeight: normal.getBoundingClientRect().height,
                visibility: getComputedStyle(normal).contentVisibility,
                number: number.textContent};
            }"""
        )
        self.assertEqual(layout["visibility"], "visible")
        self.assertEqual(layout["number"], "LONG-SYNTHETIC-NUMBER-2026")
        self.assertEqual(layout["numberHeight"], layout["numberScrollHeight"])
        self.assertGreater(layout["rowHeight"], layout["normalHeight"])
        self.assertEqual(layout["normalHeight"], 58)
        self.assertAlmostEqual(layout["gap"], 6, places=1)
        row.click()
        self.page.wait_for_function("id => state.editingId === id", arg=card)
        self.page.locator("#repairOrderModal.is-open").wait_for()

    def test_long_number_at_1440(self):
        self.check_long_number(1440)

    def test_long_number_at_1024(self):
        self.check_long_number(1024)
