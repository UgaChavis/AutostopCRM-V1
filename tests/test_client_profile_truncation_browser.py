"""A limited client profile must say when its visible sections are truncated."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileTruncationBrowserTests(UiBrowserTestCase):
    def test_limits_and_legacy_warning_for_both_roles(self):
        service = self.runtime.service
        empty = service.create_client({"display_name": "SYNTHETIC EMPTY PROFILE"})["client"]["id"]
        boundary = service.create_client({"display_name": "SYNTHETIC BOUNDARY PROFILE"})["client"][
            "id"
        ]
        large = service.create_client({"display_name": "SYNTHETIC LARGE PROFILE"})["client"]["id"]
        for client, count in ((boundary, 5), (large, 30)):
            for index in range(count):
                card = self.create_card(f"SYNTHETIC ORDER {client} {index}", client_id=client)
                service.get_repair_order({"card_id": card, "create_if_missing": True})
        for client, count in ((boundary, 4), (large, 5)):
            for index in range(count):
                service.upsert_client_vehicle(
                    {"client_id": client, "vehicle": {"vehicle": f"SYNTHETIC CAR {index}"}}
                )

        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-limit-viewer",
                "password": "synthetic-limit-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-limit-viewer", "password": "synthetic-limit-password"},
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
        self.load_board()
        viewer.goto(self.runtime.browser_url)
        viewer.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer.evaluate("stopSnapshotPolling()")

        def desktop_select(page, client):
            page.locator(f'#clientsList [data-client-id="{client}"]').click()
            page.wait_for_function(
                "id => state.clientsActiveProfile?.client?.id === id", arg=client
            )

        def mobile_select(page, client):
            page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').click()
            page.wait_for_function(
                "id => !state.mobileClientProfileLoading && state.clientsActiveProfile?.client?.id === id",
                arg=client,
            )

        for page in (self.page, viewer):
            page.locator("#clientsButton").click()
            desktop_select(page, empty)
            self.assertNotIn("ПОКАЗАНО", page.locator("#clientOrdersList").inner_text())
            desktop_select(page, large)
            self.assertEqual(
                page.locator("#clientOrdersList [data-open-repair-order-card]").count(), 30
            )
            self.assertNotIn("ПОКАЗАНО", page.locator("#clientOrdersList").inner_text())
            page.locator('[data-close="clients"]').click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.click('[data-mobile-view="more"]')
            page.click('[data-mobile-open="clients"]')
            mobile_select(page, empty)
            self.assertNotIn("ПОКАЗАНО", page.locator("#mobileClientDetail").inner_text())
            mobile_select(page, boundary)
            self.assertNotIn("ПОКАЗАНО", page.locator("#mobileClientDetail").inner_text())
            mobile_select(page, large)
            text = page.locator("#mobileClientDetail").inner_text()
            self.assertIn("ПОКАЗАНО 4 ИЗ 5 В ПРОФИЛЕ", text)
            self.assertIn("ПОКАЗАНО 5 ИЗ 30 В ПРОФИЛЕ", text)
            self.assertNotIn("СВЯЗИ НЕ ПОДТВЕРЖДЕНЫ", text)

        legacy = self.create_card(
            "SYNTHETIC LEGACY", vehicle_profile={"customer_name": "SYNTHETIC LARGE PROFILE"}
        )
        service.get_repair_order({"card_id": legacy, "create_if_missing": True})
        for page in (self.page, viewer):
            mobile_select(page, large)
            page.wait_for_function("state.clientsActiveProfile.meta.repair_orders_total === 31")
            self.assertIn(
                "ПОКАЗАНО 5 ИЗ 31 В ПРОФИЛЕ · СВЯЗИ НЕ ПОДТВЕРЖДЕНЫ",
                page.locator("#mobileClientDetail").inner_text(),
            )
            self.assertNotIn(
                "ЧАСТЬ СВЯЗЕЙ НЕ ПОДТВЕРЖДЕНА",
                page.locator("#mobileClientDetail").inner_text(),
            )
            page.click("#mobileClientsBackButton")
            page.set_viewport_size({"width": 1440, "height": 960})
            page.locator("#clientsButton").click()
            desktop_select(page, large)
            page.wait_for_function("state.clientsActiveProfile.meta.repair_orders_total === 31")
            self.assertIn(
                "ПОКАЗАНО 30 ИЗ 31 В ПРОФИЛЕ · СВЯЗИ НЕ ПОДТВЕРЖДЕНЫ",
                page.locator("#clientOrdersList").inner_text(),
            )
            self.assertNotIn(
                "ЧАСТЬ СВЯЗЕЙ НЕ ПОДТВЕРЖДЕНА",
                page.locator(".clients-profile-pane").inner_text(),
            )
