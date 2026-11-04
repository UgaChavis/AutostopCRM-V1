"""Client profiles must not show truncated response sections as empty."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileCountsBrowserTests(UiBrowserTestCase):
    def test_desktop_rejects_inconsistent_sections_and_recovers(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC PROFILE"})[
            "client"
        ]["id"]
        self.runtime.service.upsert_client_vehicle(
            {"client_id": client, "vehicle": {"vehicle": "SYNTHETIC CAR"}}
        )
        card = self.create_card("SYNTHETIC PROFILE ORDER", client_id=client)
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.locator("#clientsButton").click()
        for mode in (
            "vehicles",
            "orders",
            "missing_meta",
            "wrong_limit",
            "bad_count",
            "bad_warning",
            "conflicting_vehicle_stats",
            "conflicting_order_stats",
        ):

            def partial(route):
                payload = route.fetch().json()
                data = payload["data"]
                if mode == "vehicles":
                    data["vehicles"] = []
                elif mode == "orders":
                    data["repair_orders"] = []
                elif mode == "missing_meta":
                    del data["meta"]
                elif mode == "wrong_limit":
                    data["meta"]["order_limit"] = 8
                elif mode == "bad_count":
                    data["meta"]["repair_orders_returned"] = 0
                elif mode == "conflicting_vehicle_stats":
                    data["vehicles"] = []
                    data["meta"]["vehicles_total"] = 0
                elif mode == "conflicting_order_stats":
                    data["repair_orders"] = []
                    data["meta"]["repair_orders_total"] = 0
                    data["meta"]["repair_orders_returned"] = 0
                else:
                    data["meta"]["read_warnings"] = [None]
                route.fulfill(json=payload)

            self.page.route("**/api/get_client?**", partial)
            self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
            self.page.wait_for_function(
                "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
            )
            self.assertFalse(self.page.locator("#clientDisplayNameInput").is_visible())
            self.assertTrue(self.page.evaluate("beginClientMutation({allowNew:true}) === null"))
            self.page.unroute("**/api/get_client?**", partial)
            self.page.evaluate("setStatus('', false)")
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertIn("SYNTHETIC CAR", self.page.locator("#clientVehiclesList").inner_text())
        self.assertNotIn("ПОКА НЕТ", self.page.locator("#clientOrdersList").inner_text())

    def test_mobile_accepts_limited_orders_and_honors_warnings(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC MOBILE PROFILE"})[
            "client"
        ]["id"]
        for index in range(9):
            card = self.create_card(f"SYNTHETIC ORDER {index}", client_id=client)
            self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        legacy_card = self.create_card(
            "SYNTHETIC LEGACY ORDER",
            vehicle_profile={"customer_name": "SYNTHETIC MOBILE PROFILE"},
        )
        self.runtime.service.get_repair_order({"card_id": legacy_card, "create_if_missing": True})
        actual = self.runtime.service.get_client({"client_id": client, "order_limit": 8})
        self.assertEqual(actual["meta"]["repair_orders_total"], 10)
        self.assertEqual(actual["meta"]["repair_orders_returned"], 8)
        self.assertIn("related_card_client_match_unverified", actual["meta"]["read_warnings"])
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="clients"]').click()

        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertEqual(
            self.page.evaluate("state.clientsActiveProfile.meta.repair_orders_returned"), 8
        )
        self.assertEqual(
            self.page.locator(".mobile-client-detail__debt").inner_text(), "НЕТ ДАННЫХ"
        )
        self.assertEqual(
            self.page.locator("#mobileClientDetail [data-mobile-client-order-card]").count(), 5
        )
