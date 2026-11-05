"""A client profile must not invent or round a repair-order total."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientOrderTotalBrowserTests(UiBrowserTestCase):
    def test_order_totals_for_both_roles_and_views(self):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC TOTAL CLIENT"})["client"]["id"]
        card = self.create_card("SYNTHETIC TOTAL ORDER", client_id=client)
        service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [{"name": "SYNTHETIC", "quantity": "1", "price": "100.49"}]
                },
            }
        )
        self.assertEqual(
            service.get_client({"client_id": client})["repair_orders"][0]["grand_total"], "100.49"
        )
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-total-operator",
                "password": "synthetic-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-total-operator", "password": "synthetic-password"},
        ).json()["data"]["session"]["token"]
        restricted = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(restricted.close)
        restricted.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        viewer = restricted.new_page()
        viewer.on("pageerror", lambda error: self.page_errors.append(str(error)))
        viewer_writes = []
        viewer.on(
            "request",
            lambda req: (
                viewer_writes.append(req.url.rsplit("/", 1)[-1]) if req.method == "POST" else None
            ),
        )
        self.load_board()
        viewer.goto(self.runtime.browser_url)
        viewer.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer.evaluate("stopSnapshotPolling()")

        cases = [
            ("server", "100,49 ₽"),
            ("9" * 12000 + ".01", "\u00a0".join(["999"] * 4000) + ",01 ₽"),
            ("0", "0 ₽"),
            ("0.01", "0,01 ₽"),
            ("100.5", "100,50 ₽"),
            ("-1234.49", "-1\u00a0234,49 ₽"),
            ("90071992547409.91", "90\u00a0071\u00a0992\u00a0547\u00a0409,91 ₽"),
            ("missing", "НЕТ ДАННЫХ"),
            (None, "НЕТ ДАННЫХ"),
            (False, "НЕТ ДАННЫХ"),
            (123, "НЕТ ДАННЫХ"),
            ("", "НЕТ ДАННЫХ"),
            ("abc100", "НЕТ ДАННЫХ"),
            ("1.001", "НЕТ ДАННЫХ"),
            ("<img src=x onerror=alert(1)>", "НЕТ ДАННЫХ"),
        ]
        for page in (self.page, viewer):
            for mobile in (False, True):
                with self.subTest(restricted=page is viewer, mobile=mobile):
                    page.set_viewport_size(
                        {"width": 390, "height": 844} if mobile else {"width": 1440, "height": 960}
                    )
                    if mobile:
                        page.click('[data-mobile-view="more"]')
                        page.click('[data-mobile-open="clients"]')
                        row = page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]')
                        total = page.locator(
                            f'#mobileClientDetail [data-mobile-client-order-card="{card}"] span'
                        ).last
                        debt = page.locator(".mobile-client-detail__debt")
                    else:
                        page.locator("#clientsButton").click()
                        row = page.locator(f'#clientsList [data-client-id="{client}"]')
                        total = page.locator("#clientOrdersList .client-mini__order-total-value")
                        debt = page.locator("#clientDebtValue")
                    mode = ["server"]

                    def mutate(route):
                        response = route.fetch()
                        result = response.json()
                        order = result["data"]["repair_orders"][0]
                        if mode[0] == "missing":
                            order.pop("grand_total")
                        elif mode[0] != "server":
                            order["grand_total"] = mode[0]
                        route.fulfill(response=response, json=result)

                    page.route("**/api/get_client?**", mutate)
                    for value, expected in cases:
                        mode[0] = value
                        with page.expect_response("**/api/get_client?**"):
                            row.click()
                        page.wait_for_function("!state.clientsProfileSelectionRequest")
                        self.assertEqual(total.inner_text(), expected, repr(value))
                        self.assertEqual(debt.inner_text(), "100 ₽")
                    page.unroute("**/api/get_client?**", mutate)
                    if not mobile:
                        page.locator('[data-close="clients"]').click()
        self.assertEqual(
            service.get_client({"client_id": client})["repair_orders"][0]["grand_total"], "100.49"
        )
        self.assertFalse(
            any(
                name in self.writes + viewer_writes
                for name in ("update_client", "upsert_client_vehicle", "update_repair_order")
            )
        )
