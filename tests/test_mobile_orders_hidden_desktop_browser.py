"""Mobile order reads do not build an unseen desktop table."""

import json

from tests.browser_ui_support import UiBrowserTestCase


def order_response(count, prefix="synthetic"):
    return {
        "ok": True,
        "data": {
            "repair_orders": [
                {
                    "card_id": f"{prefix}-{index}",
                    "number": str(index + 1),
                    "status": "open",
                    "status_label": "Открыт",
                    "payment_status": "unpaid",
                    "payment_status_label": "Не оплачен",
                    "is_paid": False,
                    "paid_total": "0",
                    "paid_total_display": "0",
                    "works_total": "10",
                    "grand_total": "10",
                    "summary": "SYNTHETIC ORDER",
                }
                for index in range(count)
            ],
            "meta": {
                "status": "open",
                "sort_by": "number",
                "sort_dir": "desc",
                "limit": 300,
                "total": count,
                "returned": count,
                "has_more": False,
            },
        },
    }


class MobileOrdersHiddenDesktopBrowserTests(UiBrowserTestCase):
    def open_mobile_orders(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()

    def test_full_mobile_then_desktop_loading_and_resize(self):
        self.page.route(
            "**/api/list_repair_orders?**", lambda route: route.fulfill(json=order_response(300))
        )
        self.open_mobile_orders()
        self.page.wait_for_function("state.repairOrdersMetaState?.returned === 300")
        self.assertEqual(self.page.locator("#mobileRepairOrdersList button").count(), 300)
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.page.unroute("**/api/list_repair_orders?**")
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.page.locator("#repairOrdersButton").click()
        self.assertIn("ЗАГРУЗКА", self.page.locator("#repairOrdersList").inner_text())
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().fulfill(json=order_response(2, prefix="new"))
        self.page.wait_for_selector('#repairOrdersList [data-open-repair-order-card="new-1"]')
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        self.page.evaluate("renderRepairOrdersView()")
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 2
        )
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!state.mobileLite")
        self.assertTrue(
            self.page.locator(
                '#repairOrdersList [data-open-repair-order-card="new-1"]'
            ).is_visible()
        )

    def test_open_modal_after_success_builds_desktop_rows(self):
        self.page.route(
            "**/api/list_repair_orders?**", lambda route: route.fulfill(json=order_response(2))
        )
        self.open_mobile_orders()
        self.page.wait_for_function("state.repairOrdersMetaState?.returned === 2")
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
        self.page.evaluate("loadRepairOrders(true)")
        self.page.locator("#repairOrdersModal.is-open").wait_for()
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 2
        )

    def test_mobile_empty_and_error_do_not_show_stale_rows(self):
        self.page.route(
            "**/api/list_repair_orders?**", lambda route: route.fulfill(json=order_response(1))
        )
        self.open_mobile_orders()
        self.page.wait_for_function("state.repairOrdersMetaState?.returned === 1")
        self.page.unroute("**/api/list_repair_orders?**")
        self.page.route(
            "**/api/list_repair_orders?**", lambda route: route.fulfill(json=order_response(0))
        )
        self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_function("state.repairOrdersMetaState?.returned === 0")
        self.assertIn("ПОКА НЕТ", self.page.locator("#mobileRepairOrdersList").inner_text())
        self.page.unroute("**/api/list_repair_orders?**")
        self.page.route(
            "**/api/list_repair_orders?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertIn("НЕ УДАЛОСЬ", self.page.locator("#mobileRepairOrdersList").inner_text())
        self.assertEqual(self.page.locator("#mobileRepairOrdersList button").count(), 0)

    def test_background_update_preserves_open_order_draft_and_focus(self):
        card = self.create_card("SYNTHETIC DRAFT ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.open_mobile_orders()
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC UNSAVED")
        self.page.locator("#mobileRepairOrderClient").focus()
        self.page.route(
            "**/api/list_repair_orders?**", lambda route: route.fulfill(json=order_response(20))
        )
        self.page.evaluate("loadRepairOrders(false)")
        self.page.wait_for_function("state.repairOrdersMetaState?.returned === 20")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )
        self.assertTrue(
            self.page.locator("#mobileRepairOrderClient").evaluate(
                "node => document.activeElement === node"
            )
        )

    def test_restricted_operator_loads_actual_orders(self):
        card = self.create_card("SYNTHETIC RESTRICTED ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-orders-viewer",
                "password": "synthetic-orders-password",
                "role": "operator",
            },
        )
        self.assertEqual(created.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-orders-viewer", "password": "synthetic-orders-password"},
        ).json()["data"]["session"]["token"]
        restricted = self.browser.new_context(
            viewport={"width": 390, "height": 844}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(restricted.close)
        restricted.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        viewer = restricted.new_page()
        viewer.on("pageerror", lambda error: self.page_errors.append(str(error)))
        viewer.goto(self.runtime.browser_url)
        viewer.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer.evaluate("stopSnapshotPolling()")
        viewer.locator('[data-mobile-view="repair-orders"]').click()
        viewer.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').wait_for()
        self.assertEqual(
            viewer.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
        )
