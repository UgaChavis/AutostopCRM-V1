"""Client debt must not present an incomplete order list as a complete total."""

from __future__ import annotations

from copy import deepcopy

from tests.browser_ui_support import UiBrowserTestCase


class ClientDebtBrowserTests(UiBrowserTestCase):
    def check_debt(self, *, mobile: bool) -> None:
        if mobile:
            self.page.set_viewport_size({"width": 390, "height": 844})
        client_id = self.runtime.service.create_client({"display_name": "AAA SYNTHETIC DEBT"})[
            "client"
        ]["id"]
        other_id = self.runtime.service.create_client({"display_name": "AAB SYNTHETIC DEBT"})[
            "client"
        ]["id"]
        self.load_board()
        response = self.context.request.get(
            self.runtime.base_url
            + "/api/get_client?client_id="
            + client_id
            + ("&order_limit=8" if mobile else "&order_limit=30"),
            headers={"X-Operator-Session": self.session_token},
        ).json()
        data = response["data"]

        if mobile:
            self.page.click('[data-mobile-view="more"]')
            self.page.click('[data-mobile-open="clients"]')
            row = self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client_id}"]')
            debt = self.page.locator(".mobile-client-detail__debt")
        else:
            self.page.locator("#clientsButton").click()
            row = self.page.locator(f'#clientsList [data-client-id="{client_id}"]')
            debt = self.page.locator("#clientDebtValue")

        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        with self.page.expect_request("**/api/get_client?**"):
            row.click()
        if mobile:
            self.assertIn("ЗАГРУЗКА ПРОФИЛЯ", self.page.locator("#mobileClientDetail").inner_text())
        else:
            self.assertFalse(debt.is_visible())
        held.pop().fulfill(json=response)
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client_id
        )
        self.page.unroute("**/api/get_client?**")
        self.page.route("**/api/get_client?**", lambda route: route.fulfill(json=response))

        cases = (
            ([], {"repair_orders_total": 0, "repair_orders_returned": 0}, "0 ₽", True),
            (["100", "25"], {"repair_orders_total": 2, "repair_orders_returned": 2}, "125 ₽", True),
            (
                ["0.03", "0.29", "0.18"],
                {"repair_orders_total": 3, "repair_orders_returned": 3},
                "1 ₽",
                True,
            ),
            (["-25"], {"repair_orders_total": 1, "repair_orders_returned": 1}, "НЕТ ДАННЫХ", True),
            (["0"], {"repair_orders_total": 31, "repair_orders_returned": 1}, "НЕТ ДАННЫХ", False),
            (
                ["0"] * (8 if mobile else 30),
                {"repair_orders_total": 31, "repair_orders_returned": 8 if mobile else 30},
                "НЕТ ДАННЫХ",
                True,
            ),
            (["0"], {"repair_orders_total": 2, "repair_orders_returned": 2}, "НЕТ ДАННЫХ", False),
            (["0"], {}, "НЕТ ДАННЫХ", False),
            (
                ["0"],
                {
                    "repair_orders_total": 1,
                    "repair_orders_returned": 1,
                    "read_warnings": ["related_card_client_match_unverified"],
                },
                "НЕТ ДАННЫХ",
                True,
            ),
            (
                ["0"],
                {
                    "repair_orders_total": 1,
                    "repair_orders_returned": 1,
                    "read_warnings": ["repair_order_row_identity_unverified"],
                },
                "НЕТ ДАННЫХ",
                True,
            ),
            (
                ["0"],
                {
                    "repair_orders_total": 1,
                    "repair_orders_returned": 1,
                    "read_warnings": ["repair_order_payment_identity_unverified"],
                },
                "НЕТ ДАННЫХ",
                True,
            ),
            (
                ["0"],
                {
                    "repair_orders_total": 1,
                    "repair_orders_returned": 1,
                    "read_warnings": ["deadline_calculated_from_created_at"],
                },
                "0 ₽",
                True,
            ),
            ([None], {"repair_orders_total": 1, "repair_orders_returned": 1}, "НЕТ ДАННЫХ", True),
            (
                ["broken 100"],
                {"repair_orders_total": 1, "repair_orders_returned": 1},
                "НЕТ ДАННЫХ",
                True,
            ),
            (
                ["1.001"],
                {"repair_orders_total": 1, "repair_orders_returned": 1},
                "НЕТ ДАННЫХ",
                True,
            ),
            (
                ["90071992547409.92"],
                {"repair_orders_total": 1, "repair_orders_returned": 1},
                "НЕТ ДАННЫХ",
                True,
            ),
        )
        for amounts, meta, expected, expected_valid in cases:
            with self.subTest(mobile=mobile, amounts=amounts, meta=meta):
                data["repair_orders"] = [
                    {
                        "card_id": f"synthetic-{index}",
                        "number": str(index),
                        "status": "open",
                        "due_total": amount,
                        "grand_total": "0",
                    }
                    for index, amount in enumerate(amounts)
                ]
                data["meta"] = {
                    "vehicles_total": len(data["vehicles"]),
                    "order_limit": 8 if mobile else 30,
                    **deepcopy(meta),
                }
                data["client"]["stats"]["vehicles_total"] = len(data["vehicles"])
                data["client"]["stats"]["repair_orders_total"] = meta.get("repair_orders_total")
                self.assertEqual(
                    self.page.evaluate("profile => clientDebtAmountText(profile)", data), expected
                )
                actual_valid = self.page.evaluate(
                    "args => clientProfileReadValid(args.profile, args.id, args.limit)",
                    {"profile": data, "id": client_id, "limit": 8 if mobile else 30},
                )
                self.assertEqual(actual_valid, expected_valid)
                self.page.evaluate("setStatus('', false)")
                with self.page.expect_response("**/api/get_client?**"):
                    row.click()
                if not expected_valid:
                    self.page.wait_for_function(
                        "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
                    )
                    if mobile:
                        self.assertEqual(debt.count(), 0)
                    else:
                        self.assertFalse(debt.is_visible())
                    continue
                self.page.wait_for_function(
                    "id => state.clientsActiveProfile?.client?.id === id", arg=client_id
                )
                self.assertEqual(debt.inner_text(), expected)

        data["repair_orders"] = [{"card_id": "synthetic", "due_total": "125"}]
        data["meta"] = {
            "repair_orders_total": 1,
            "repair_orders_returned": 1,
            "vehicles_total": len(data["vehicles"]),
            "order_limit": 8 if mobile else 30,
        }
        data["client"]["stats"]["repair_orders_total"] = 1
        with self.page.expect_response("**/api/get_client?**"):
            row.click()
        self.assertEqual(debt.inner_text(), "125 ₽")
        self.page.unroute("**/api/get_client?**")
        held.clear()
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        other_row = self.page.locator(
            f'#mobileClientsList [data-mobile-client-id="{other_id}"]'
            if mobile
            else f'#clientsList [data-client-id="{other_id}"]'
        )
        with self.page.expect_request("**/api/get_client?**"):
            other_row.click()
        if mobile:
            self.assertIn("ЗАГРУЗКА ПРОФИЛЯ", self.page.locator("#mobileClientDetail").inner_text())
            self.assertEqual(debt.count(), 0)
        else:
            self.assertFalse(debt.is_visible())
        held.pop().fulfill(status=500, json={"ok": False, "error": {"message": "synthetic"}})
        self.page.wait_for_function("() => !state.mobileClientProfileLoading")
        if mobile:
            self.assertEqual(debt.count(), 0)
        else:
            self.assertFalse(debt.is_visible())

    def test_desktop_debt_requires_complete_valid_orders(self) -> None:
        self.check_debt(mobile=False)

    def test_mobile_debt_requires_complete_valid_orders(self) -> None:
        self.check_debt(mobile=True)
