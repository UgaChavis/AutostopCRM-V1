"""Refreshing unchanged permissions must not rebuild mobile order rows."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class MobileOrdersPermissionsRerenderBrowserTests(UiBrowserTestCase):
    @staticmethod
    def prepare_orders(page):
        page.evaluate(
            """() => {
                state.repairOrdersItems = Array.from({length: 3}, (_, index) => ({
                    card_id: 'synthetic-' + index, number: String(index + 1),
                    status: 'open', client: 'SYNTHETIC CLIENT', vehicle: 'SYNTHETIC CAR',
                    summary: 'SYNTHETIC ORDER ' + index, grand_total: '10',
                }));
                state.repairOrdersMetaState = {total: 3, returned: 3, has_more: false};
                state.repairOrdersLoadError = false;
                state.repairOrdersSearchLoading = false;
                setMobileView('repair-orders');
                window.__originalOrderRow = document.querySelectorAll('#mobileRepairOrdersList button')[1];
                window.__originalOrderRow.focus();
                els.statusLine.textContent = 'SYNTHETIC STATUS';
            }"""
        )

    def test_operator_refresh_preserves_order_focus_and_updates_status(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.prepare_orders(self.page)
        self.page.evaluate("loadOperatorProfile(false)")
        self.assertTrue(
            self.page.evaluate(
                """() => window.__originalOrderRow.isConnected &&
                    document.activeElement === window.__originalOrderRow &&
                    document.querySelectorAll('#mobileRepairOrdersList button').length === 3"""
            )
        )
        self.assertEqual(self.page.locator("#mobileStatusLine").inner_text(), "SYNTHETIC STATUS")

    def test_real_permission_revocation_and_viewer_reset_clear_private_state(self):
        username = "synthetic-permissions-viewer"
        password = "synthetic-permissions-password"
        create = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": username,
                "password": password,
                "role": "operator",
                "permissions": ["employees_cashboxes_access"],
                "expected_permissions": [],
            },
        )
        self.assertEqual(create.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": username, "password": password},
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
        self.assertTrue(viewer.evaluate("operatorCanAccessEmployeesCashboxes()"))
        self.prepare_orders(viewer)
        viewer.evaluate(
            """() => {
                state.cashboxes = [{id: 'synthetic-box', name: 'SYNTHETIC PRIVATE CASHBOX'}];
                state.cashboxesLoaded = true;
                state.activeCashbox = {cashbox: state.cashboxes[0], transactions: []};
            }"""
        )
        revoke = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": username,
                "permissions": [],
                "expected_permissions": ["employees_cashboxes_access"],
            },
        )
        self.assertEqual(revoke.status, 200)
        viewer.evaluate("loadOperatorProfile(false)")
        self.assertFalse(viewer.evaluate("operatorCanAccessEmployeesCashboxes()"))
        self.assertEqual(viewer.evaluate("state.cashboxes.length"), 0)
        self.assertIsNone(viewer.evaluate("state.activeCashbox"))
        self.assertFalse(viewer.evaluate("window.__originalOrderRow.isConnected"))
        self.assertEqual(viewer.locator("#mobileRepairOrdersList button").count(), 3)
        self.assertEqual(viewer.locator("#mobileStatusLine").inner_text(), "SYNTHETIC STATUS")
        viewer.evaluate("clearOperatorSession()")
        self.assertEqual(viewer.locator("#mobileRepairOrdersList").inner_text(), "")
        self.assertEqual(viewer.evaluate("state.repairOrdersItems.length"), 0)
