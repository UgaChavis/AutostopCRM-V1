"""Incomplete order-list responses are errors, not empty results."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersInvalidResponseBrowserTests(UiBrowserTestCase):
    def test_desktop_incomplete_response_and_retry(self):
        card = self.create_card("SYNTHETIC ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        cases = [
            {},
            {"repair_orders": []},
            {
                "repair_orders": None,
                "meta": {
                    "limit": 300,
                    "total": 0,
                    "returned": 0,
                    "has_more": False,
                    "status": "open",
                },
            },
            {
                "repair_orders": [],
                "meta": {
                    "limit": 300,
                    "total": 1,
                    "returned": 0,
                    "has_more": False,
                    "status": "open",
                },
            },
            {
                "repair_orders": [],
                "meta": {
                    "limit": 300,
                    "total": 0,
                    "returned": 0,
                    "has_more": True,
                    "status": "open",
                },
            },
            {
                "repair_orders": [{}],
                "meta": {
                    "limit": 300,
                    "total": 1,
                    "returned": 1,
                    "has_more": False,
                    "status": "open",
                },
            },
            {
                "repair_orders": [],
                "meta": {
                    "limit": 300,
                    "total": 0,
                    "returned": 0,
                    "has_more": False,
                    "status": "closed",
                },
            },
            {
                "repair_orders": [],
                "meta": {
                    "limit": 1,
                    "total": 0,
                    "returned": 0,
                    "has_more": False,
                    "status": "open",
                },
            },
        ]
        valid_row = {
            "card_id": card,
            "status": "open",
            "status_label": "Открыт",
            "payment_status": "unpaid",
            "payment_status_label": "Не оплачен",
            "is_paid": False,
            "paid_total": "0",
            "paid_total_display": "0",
            "works_total": "10",
            "grand_total": "10",
        }
        valid_meta = {"limit": 300, "total": 1, "returned": 1, "has_more": False, "status": "open"}
        cases.append({"repair_orders": [{"card_id": card}], "meta": valid_meta})
        for missing in ("grand_total", "paid_total", "payment_status", "is_paid", "status"):
            row = dict(valid_row)
            del row[missing]
            cases.append({"repair_orders": [row], "meta": valid_meta})
        cases.append(
            {"repair_orders": [{**valid_row, "payment_status": "paid"}], "meta": valid_meta}
        )
        for partial in cases:

            def respond(route):
                route.fulfill(json={"ok": True, "data": partial})

            self.page.route("**/api/list_repair_orders?**", respond)
            self.page.locator("#repairOrdersButton").click()
            self.page.wait_for_function("state.repairOrdersLoadError")
            self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#repairOrdersList").inner_text())
            self.assertNotIn("ПОКА НЕТ", self.page.locator("#repairOrdersList").inner_text())
            self.page.locator('[data-close="repair-orders"]').click()
            self.page.unroute("**/api/list_repair_orders?**", respond)
        self.page.locator("#repairOrdersButton").click()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').wait_for()
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))

    def test_mobile_error_retry_and_stale_partial_response(self):
        card = self.create_card("SYNTHETIC MOBILE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        held = []
        self.page.route("**/api/list_repair_orders?**", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.wait_for_timeout(150)
        held.pop().fulfill(json={"ok": True, "data": {}})
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertIn(
            "НЕ УДАЛОСЬ ЗАГРУЗИТЬ", self.page.locator("#mobileRepairOrdersList").inner_text()
        )
        self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_timeout(150)
        self.page.locator("#mobileRepairOrdersRefreshButton").click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 2)
        held.pop().continue_()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]'
        ).wait_for()
        held.pop().fulfill(json={"ok": True, "data": {}})
        self.page.wait_for_timeout(150)
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
        self.assertEqual(
            self.page.locator(
                f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]'
            ).count(),
            1,
        )

    def test_restricted_role_displays_full_legacy_order(self):
        card = self.create_card("SYNTHETIC LEGACY ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "date": "4.1.26 9:03",
                    "opened_at": "4.1.26 9:03",
                    "client": "SYNTHETIC CLIENT",
                    "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "123"}],
                },
            }
        )
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-list-viewer",
                "password": "synthetic-list-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-list-viewer", "password": "synthetic-list-password"},
        ).json()["data"]["session"]["token"]
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(context.close)
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        row = self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]')
        row.wait_for()
        self.assertIn("123", row.inner_text())
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
