"""Board changes must not rebuild an unrelated mobile order list."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrderBoardRerenderBrowserTests(UiBrowserTestCase):
    def prepare_orders(self):
        self.card_id = self.create_card("SYNTHETIC BOARD ORIGINAL")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.evaluate(
            """() => {
                state.repairOrdersItems = Array.from({length: 3}, (_, index) => ({
                    card_id: 'synthetic-' + index,
                    number: String(index + 1),
                    status: 'open',
                    client: 'SYNTHETIC CLIENT',
                    vehicle: 'SYNTHETIC CAR',
                    summary: 'SYNTHETIC ORDER ' + index,
                    grand_total: '10',
                }));
                state.repairOrdersMetaState = {total: 3, returned: 3, has_more: false};
                state.repairOrdersLoadError = false;
                setMobileView('repair-orders');
                window.__originalOrderRow = document.querySelectorAll('#mobileRepairOrdersList button')[1];
                window.__originalOrderRow.focus();
            }"""
        )
        self.page.wait_for_selector(
            '#mobileRepairOrdersList [data-open-repair-order-card="synthetic-1"]'
        )

    def assert_row_and_focus_preserved(self):
        self.assertTrue(
            self.page.evaluate(
                """() => window.__originalOrderRow.isConnected &&
                    document.activeElement === window.__originalOrderRow &&
                    document.querySelectorAll('#mobileRepairOrdersList button').length === 3"""
            )
        )

    def test_full_board_render_preserves_orders_and_focus(self):
        self.prepare_orders()
        self.page.evaluate(
            """() => {
                state.snapshot = {...state.snapshot};
                els.statusLine.textContent = 'SYNTHETIC STATUS';
                renderBoard();
            }"""
        )
        self.assert_row_and_focus_preserved()
        self.assertEqual(self.page.locator("#mobileStatusLine").inner_text(), "SYNTHETIC STATUS")

    def test_single_column_render_preserves_orders_and_focus(self):
        self.prepare_orders()
        updated = self.page.evaluate("() => renderBoardColumnById(state.snapshot.columns[0].id)")
        self.assertTrue(updated)
        self.assert_row_and_focus_preserved()

    def test_return_to_board_and_order_success_error_are_current(self):
        self.prepare_orders()
        self.page.evaluate(
            """cardId => {
                const card = state.snapshot.cards.find(item => item.id === cardId);
                card.title = 'SYNTHETIC BOARD UPDATED';
                renderBoard();
                setMobileView('board');
            }""",
            self.card_id,
        )
        self.assertIn(
            "SYNTHETIC BOARD UPDATED", self.page.locator("#mobileBoardColumns").inner_text()
        )
        self.page.evaluate("setMobileView('repair-orders')")
        self.page.route(
            "**/api/list_repair_orders?**",
            lambda route: route.fulfill(
                json={
                    "ok": True,
                    "data": {
                        "repair_orders": [
                            {
                                "card_id": "synthetic-new",
                                "number": "9",
                                "status": "open",
                                "status_label": "Открыт",
                                "payment_status": "unpaid",
                                "payment_status_label": "Не оплачен",
                                "is_paid": False,
                                "paid_total": "0",
                                "paid_total_display": "0",
                                "works_total": "10",
                                "grand_total": "10",
                                "summary": "SYNTHETIC NEW ORDER",
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
            ),
        )
        self.page.evaluate("loadRepairOrders(false)")
        self.page.wait_for_selector(
            '#mobileRepairOrdersList [data-open-repair-order-card="synthetic-new"]'
        )
        self.page.unroute("**/api/list_repair_orders?**")
        self.page.route(
            "**/api/list_repair_orders?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.evaluate("loadRepairOrders(false)")
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileRepairOrdersList").inner_text())
