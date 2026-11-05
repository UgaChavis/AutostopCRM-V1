"""Malformed order or vehicle entries must not partially apply a profile."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileCorruptOrdersBrowserTests(UiBrowserTestCase):
    def check_profile(self, *, mobile):
        client_id = self.runtime.service.create_client({"display_name": "SYNTHETIC PROFILE"})[
            "client"
        ]["id"]
        if mobile:
            self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        if mobile:
            self.page.click('[data-mobile-view="more"]')
            self.page.click('[data-mobile-open="clients"]')
            row = self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client_id}"]')
        else:
            self.page.locator("#clientsButton").click()
            row = self.page.locator(f'#clientsList [data-client-id="{client_id}"]')

        mode = [""]
        requests = []

        def response(route):
            original = route.fetch()
            data = original.json()
            requests.append(mode[0])
            profile = data["data"]
            if mode[0] == "orders":
                profile["repair_orders"] = [None]
                profile["meta"]["repair_orders_total"] = 1
                profile["meta"]["repair_orders_returned"] = 1
                profile["client"]["stats"]["repair_orders_total"] = 1
            elif mode[0] == "related_vehicles":
                profile["vehicles"] = [None]
                profile["meta"]["vehicles_total"] = 1
                profile["client"]["stats"]["vehicles_total"] = 1
            elif mode[0] == "stored_vehicles":
                profile["client"]["vehicles"] = [None]
            route.fulfill(response=original, json=data)

        self.page.route("**/api/get_client?**", response)
        for corrupt in ("orders", "related_vehicles", "stored_vehicles"):
            with self.subTest(corrupt=corrupt):
                mode[0] = corrupt
                self.page.evaluate("setStatus('', false)")
                row.click()
                self.page.wait_for_function(
                    "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
                )
                self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
                if mobile:
                    self.assertIn(
                        "ПРОФИЛЬ НЕ ЗАГРУЖЕН", self.page.locator("#mobileClientDetail").inner_text()
                    )
                else:
                    self.assertEqual(
                        self.page.locator("#clientProfileTitle").inner_text(), "ПРОФИЛЬ НЕ ЗАГРУЖЕН"
                    )
                    self.assertFalse(self.page.evaluate("clientDesktopSelectionMatches()"))
                    self.assertTrue(
                        self.page.locator(".clients-profile-pane").evaluate(
                            "e => e.classList.contains('is-empty')"
                        )
                    )
                self.assertFalse(
                    any(
                        item in self.writes
                        for item in (
                            "update_client",
                            "upsert_client_vehicle",
                            "delete_client_vehicle",
                        )
                    )
                )

                mode[0] = ""
                row.click()
                self.page.wait_for_function(
                    "id => state.clientsActiveProfile?.client?.id === id", arg=client_id
                )
                if mobile:
                    self.assertIn(
                        "SYNTHETIC PROFILE", self.page.locator("#mobileClientDetail").inner_text()
                    )
                else:
                    self.assertTrue(self.page.evaluate("clientDesktopSelectionMatches()"))
                    self.assertEqual(
                        self.page.locator("#clientProfileTitle").inner_text(), "SYNTHETIC PROFILE"
                    )
        self.assertEqual(len(requests), 6)
        self.assertFalse(
            any(
                item in self.writes
                for item in ("update_client", "upsert_client_vehicle", "delete_client_vehicle")
            )
        )

    def test_desktop_rejects_malformed_entries_and_retries(self):
        self.check_profile(mobile=False)

    def test_mobile_rejects_malformed_entries_and_retries(self):
        self.check_profile(mobile=True)
