"""Mobile repair-order drafts survive unrelated renders and rejected writes."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrderDraftBrowserTests(UiBrowserTestCase):
    def prepare_order(self):
        card = self.create_card("SYNTHETIC MOBILE ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        return card

    def test_navigation_and_generic_renders_keep_draft_and_other_order_is_clean(self):
        card = self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC UNSAVED")
        self.page.evaluate("renderMobileShell()")
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )
        other = self.create_card("SYNTHETIC OTHER ORDER")
        self.runtime.service.get_repair_order({"card_id": other, "create_if_missing": True})
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.evaluate("id => openMobileRepairOrderDetail(id)", other)
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=other,
        )
        self.assertNotEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )
        self.assertNotEqual(card, other)
        held = []

        def delay_first(route):
            if route.request.post_data_json.get("card_id") == card:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_repair_order", delay_first)
        self.page.evaluate("id => { openMobileRepairOrderDetail(id); }", card)
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("id => { openMobileRepairOrderDetail(id); }", other)
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=other,
        )
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), other)

    def test_validation_rejection_preserves_draft_and_allows_save(self):
        card = self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC CORRECTED")

        def reject(route):
            route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/update_repair_order", reject)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC CORRECTED"
        )
        self.assertFalse(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.assertFalse(self.page.evaluate("repairOrderNeedsVerification()"))
        self.page.unroute("**/api/update_repair_order", reject)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderSaving && state.mobileRepairOrderCard?.repair_order?.client === 'SYNTHETIC CORRECTED'"
        )
        result = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )
        self.assertEqual(result["repair_order"]["client"], "SYNTHETIC CORRECTED")

    def test_unknown_write_preserves_draft_and_blocks_repeat_until_reload(self):
        card = self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC APPLIED")
        writes = []

        def applied_then_lost(route):
            writes.append(route.request.url)
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_repair_order", applied_then_lost)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC APPLIED"
        )
        self.assertTrue(self.page.evaluate("repairOrderNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(writes), 1)
        self.page.unroute("**/api/update_repair_order", applied_then_lost)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.evaluate("closeMobileRepairOrderDetail()")
        self.page.evaluate("id => openMobileRepairOrderDetail(id)", card)
        self.page.wait_for_function(
            "!state.mobileRepairOrderLoading && !repairOrderNeedsVerification()"
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC APPLIED"
        )

    def test_other_order_cannot_replace_inflight_save(self):
        card = self.prepare_order()
        other = self.create_card("SYNTHETIC OTHER")
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC PENDING")
        held = []
        self.page.route("**/api/update_repair_order", lambda route: held.append(route))
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("state.mobileRepairOrderSaving")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate("id => { openMobileRepairOrderDetail(id); }", other)
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), card)
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC PENDING"
        )
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertTrue(self.page.evaluate("repairOrderNeedsVerification()"))
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC PENDING"
        )

    def test_rejected_payment_keeps_unsaved_fields_and_payment(self):
        self.prepare_order()
        before = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC PAYMENT DRAFT")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.page.route(
            "**/api/update_repair_order",
            lambda route: route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            ),
        )
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC PAYMENT DRAFT"
        )
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertEqual(len(self.runtime.state_store.read_bundle()["cash_transactions"]), before)

    def test_unknown_payment_without_readback_confirmation_blocks_repeat(self):
        self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC PAYMENT PENDING")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        writes = []

        def fail(route):
            writes.append(route.request.url)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_repair_order", fail)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC PAYMENT PENDING"
        )
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertTrue(self.page.evaluate("repairOrderPaymentNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(writes), 1)

    def test_confirmed_payment_readback_displays_saved_fields(self):
        card = self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC PAYMENT CONFIRMED")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()

        def lost_response(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_repair_order", lost_response)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        saved = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), saved["client"]
        )
        self.assertEqual(len(saved["payments"]), 1)
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertFalse(self.page.evaluate("repairOrderNeedsVerification()"))

    def exercise_success_without_payment(self, *, versions_match):
        card = self.prepare_order()
        original = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )
        if not versions_match:
            original["card"]["updated_at"] = "2001-01-01T00:00:00+00:00"
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC NOT RECORDED")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        writes = []

        def respond_without_payment(route):
            writes.append(route.request.url)
            route.fulfill(json={"ok": True, "data": original})

        self.page.route("**/api/update_repair_order", respond_without_payment)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC NOT RECORDED"
        )
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertEqual(
            self.page.evaluate("repairOrderPaymentNeedsVerification()"), not versions_match
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrderSaveButton").is_disabled(), not versions_match
        )
        self.page.evaluate("saveMobileRepairOrder()")
        if versions_match:
            self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(len(writes), 2 if versions_match else 1)

    def test_success_without_payment_matching_readback_allows_retry(self):
        self.exercise_success_without_payment(versions_match=True)

    def test_success_without_payment_mismatched_readback_blocks_retry(self):
        self.exercise_success_without_payment(versions_match=False)
