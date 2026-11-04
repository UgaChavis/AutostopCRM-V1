"""Opening an order never borrows another card's identity."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderCardIdentityBrowserTests(UiBrowserTestCase):
    def test_partial_or_wrong_card_response_does_not_open_or_write(self):
        order_card = self.create_card("SYNTHETIC ORDER A")
        other_card = self.create_card("SYNTHETIC CARD B")
        self.runtime.service.get_repair_order({"card_id": order_card, "create_if_missing": True})
        self.runtime.service.update_repair_order(
            {"card_id": order_card, "repair_order": {"client": "SYNTHETIC CLIENT A"}}
        )
        self.load_board()
        self.open_card(other_card)
        writes = []

        def capture_write(route):
            writes.append(route.request.post_data_json)
            route.continue_()

        self.page.route("**/api/update_repair_order", capture_write)
        for response_mode in ("missing", "wrong_id"):

            def partial(route):
                payload = route.fetch().json()
                if response_mode == "missing":
                    del payload["data"]["card"]
                else:
                    payload["data"]["card"]["id"] = other_card
                route.fulfill(json=payload)

            self.page.route("**/api/get_repair_order", partial)
            self.page.evaluate("id => { openRepairOrderCard(id); }", order_card)
            self.page.wait_for_function(
                "document.querySelector('#statusLine').textContent.includes('НЕ УДАЛОСЬ ПОДТВЕРДИТЬ')"
            )
            self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
            self.assertEqual(self.page.evaluate("state.editingId"), other_card)
            self.assertEqual(writes, [])
            self.page.unroute("**/api/get_repair_order", partial)
            self.page.evaluate("setStatus('', false)")

        self.page.evaluate("id => { openRepairOrderCard(id); }", order_card)
        self.page.wait_for_function(
            "id => state.editingId === id && state.repairOrderWriteReady", arg=order_card
        )
        self.assertEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC CLIENT A"
        )
        self.assertEqual(writes, [])

    def test_mobile_rejects_partial_and_wrong_card_then_opens_valid_response(self):
        order_card = self.create_card("SYNTHETIC MOBILE ORDER A")
        other_card = self.create_card("SYNTHETIC MOBILE CARD B")
        self.runtime.service.get_repair_order({"card_id": order_card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.evaluate("id => markRepairOrderPaymentVerificationPending(id)", order_card)
        for response_mode in (
            "missing",
            "wrong_id",
            "empty_order",
            "missing_version",
            "bad_payment",
            "bad_row",
            "missing_correction_flag",
            "missing_correction",
            "wrong_card_id",
            "payment_mismatch",
            "correction_mismatch",
            "correction_flag_mismatch",
        ):

            def partial(route):
                payload = route.fetch().json()
                if response_mode == "missing":
                    del payload["data"]["card"]
                elif response_mode == "wrong_id":
                    payload["data"]["card"]["id"] = other_card
                elif response_mode == "empty_order":
                    payload["data"]["repair_order"] = {}
                elif response_mode == "missing_version":
                    payload["data"]["card"]["updated_at"] = ""
                elif response_mode == "bad_payment":
                    payload["data"]["card"]["repair_order"]["payments"] = [{}]
                elif response_mode == "bad_row":
                    payload["data"]["repair_order"]["works"] = [{}]
                elif response_mode == "missing_correction_flag":
                    del payload["data"]["card"]["repair_order"]["correction_active"]
                elif response_mode == "missing_correction":
                    del payload["data"]["repair_order"]["active_correction"]
                elif response_mode == "wrong_card_id":
                    payload["data"]["card_id"] = other_card
                elif response_mode == "payment_mismatch":
                    payload["data"]["repair_order"]["payments"] = [
                        {
                            key: "synthetic"
                            for key in (
                                "id",
                                "amount",
                                "paid_at",
                                "note",
                                "payment_method",
                                "actor_name",
                                "cashbox_id",
                                "cashbox_name",
                                "cash_transaction_id",
                            )
                        }
                    ]
                elif response_mode == "correction_mismatch":
                    payload["data"]["repair_order"]["active_correction"] = {"id": "synthetic"}
                else:
                    payload["data"]["repair_order"]["correction_active"] = True
                route.fulfill(json=payload)

            self.page.route("**/api/get_repair_order", partial)
            self.page.locator(
                f'#mobileRepairOrdersList [data-open-repair-order-card="{order_card}"]'
            ).click()
            self.page.wait_for_function(
                "document.querySelector('#statusLine').textContent.includes('НЕ УДАЛОСЬ ПОДТВЕРДИТЬ')"
            )
            self.assertFalse(self.page.evaluate("Boolean(state.mobileRepairOrderCardId)"))
            self.assertNotEqual(self.page.evaluate("state.editingId"), other_card)
            self.assertTrue(
                self.page.evaluate("id => repairOrderNeedsVerification(id)", order_card)
            )
            self.page.unroute("**/api/get_repair_order", partial)
            self.page.evaluate("setStatus('', false)")

        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{order_card}"]'
        ).click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=order_card,
        )
        self.assertEqual(self.page.evaluate("state.editingId"), order_card)
        self.assertFalse(self.page.evaluate("id => repairOrderNeedsVerification(id)", order_card))

    def test_direct_desktop_order_rejects_wrong_card_response(self):
        card = self.create_card("SYNTHETIC DIRECT ORDER A")
        other = self.create_card("SYNTHETIC DIRECT CARD B")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.open_card(card)

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["card"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/get_repair_order", wrong)
        self.page.evaluate("openRepairOrderModal()")
        self.page.wait_for_function("!state.repairOrderLoading")
        self.assertFalse(self.page.evaluate("state.repairOrderWriteReady"))
        self.assertEqual(self.page.evaluate("state.editingId"), card)
        self.assertIn("НЕ УДАЛОСЬ ПОДТВЕРДИТЬ", self.page.locator("#statusLine").text_content())

    def test_reopen_accepts_real_payment_with_pending_verification(self):
        card = self.create_card("SYNTHETIC ORDER PAYMENT")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        row = self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]')
        row.click()
        self.page.wait_for_function("!state.mobileRepairOrderLoading")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            len(
                self.runtime.service.get_repair_order({"card_id": card})["repair_order"]["payments"]
            ),
            1,
        )
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.page.evaluate("id => markRepairOrderPaymentVerificationPending(id)", card)
        row.click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        self.assertFalse(self.page.evaluate("id => repairOrderNeedsVerification(id)", card))
        self.assertEqual(
            self.page.evaluate("state.mobileRepairOrderCard.repair_order.payments.length"), 1
        )
