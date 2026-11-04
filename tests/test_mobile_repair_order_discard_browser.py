"""Mobile order transitions must not silently discard unsaved work."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrderDiscardBrowserTests(UiBrowserTestCase):
    def open_first(self, *, works=None):
        first = self.create_card("SYNTHETIC DISCARD FIRST")
        second = self.create_card("SYNTHETIC DISCARD SECOND")
        for card in (first, second):
            self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        if works:
            self.runtime.service.update_repair_order(
                {"card_id": first, "repair_order": {"works": works}}
            )
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{first}"]'
        ).click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=first,
        )
        return first, second

    def test_close_clean_and_dirty_with_cancel_and_confirm(self):
        first, _ = self.open_first()
        dialogs = []

        def dismiss(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        self.page.on("dialog", dismiss)
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(dialogs, [])
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{first}"]'
        ).click()
        self.page.wait_for_function("!state.mobileRepairOrderLoading")
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC UNSAVED")
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), first)
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )
        self.page.remove_listener("dialog", dismiss)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), "")

    def test_other_card_dismiss_keeps_view_and_confirm_discards(self):
        first, second = self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC UNSAVED")
        self.page.locator('[data-mobile-view="board"]').click()
        self.page.locator(f'[data-mobile-card-id="{second}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=second
        )
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileCardRepairOrderButton").click()
        self.assertEqual(self.page.evaluate("state.mobileView"), "board")
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), first)
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#mobileCardRepairOrderButton").click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=second,
        )
        self.assertNotEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC UNSAVED"
        )

    def test_payment_entry_survives_confirmed_save_and_requires_discard_confirmation(self):
        self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC SAVED FIELD")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderPaymentNote").fill("SYNTHETIC UNSAVED PAYMENT")
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderSaving && state.mobileRepairOrderCard?.repair_order?.client === 'SYNTHETIC SAVED FIELD'"
        )
        self.assertEqual(self.page.locator("#mobileRepairOrderPaymentAmount").input_value(), "123")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(self.page.locator("#mobileRepairOrderPaymentAmount").input_value(), "123")
        self.assertTrue(self.page.evaluate("Boolean(state.mobileRepairOrderCardId)"))

    def test_failed_write_keeps_dirty_baseline_and_confirm_is_required(self):
        self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC REJECTED")
        self.page.route(
            "**/api/update_repair_order",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderSaving && repairOrderNeedsVerification()"
        )
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC REJECTED"
        )
        self.assertTrue(self.page.evaluate("Boolean(state.mobileRepairOrderCardId)"))

    def test_status_work_row_and_added_payment_count_as_drafts(self):
        self.open_first()
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("ready")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(self.page.locator("#mobileRepairOrderStatusSelect").input_value(), "ready")
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("open")
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        name = self.page.locator(
            '#mobileRepairOrderWorks [data-mobile-repair-row-field="name"]'
        ).first
        name.fill("SYNTHETIC WORK")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(name.input_value(), "SYNTHETIC WORK")
        name.fill("")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)

    def test_same_order_reopen_retains_draft_without_confirmation(self):
        first, _ = self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC SAME ORDER")
        dialogs = []
        self.page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        self.page.locator('[data-mobile-view="board"]').click()
        self.page.locator(f'[data-mobile-card-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=first
        )
        self.page.locator("#mobileCardRepairOrderButton").click()
        self.assertEqual(dialogs, [])
        self.assertEqual(self.page.evaluate("state.mobileView"), "repair-orders")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC SAME ORDER"
        )

    def test_replacing_work_with_same_values_is_a_draft(self):
        self.open_first(works=[{"name": "SYNTHETIC WORK", "quantity": "1", "price": "10"}])
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        row = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]").first
        original_id = row.get_attribute("data-mobile-repair-order-row-id")
        self.assertTrue(original_id)
        row.locator('[data-mobile-repair-order-remove="works"]').click()
        row = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]").first
        row.locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC WORK")
        row.locator('[data-mobile-repair-row-field="quantity"]').fill("1")
        row.locator('[data-mobile-repair-row-field="price"]').fill("10")
        self.assertNotEqual(row.get_attribute("data-mobile-repair-order-row-id"), original_id)
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertTrue(self.page.evaluate("Boolean(state.mobileRepairOrderCardId)"))

    def test_confirmed_payment_preserves_unsaved_status_after_lost_reply(self):
        first, _ = self.open_first()
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("ready")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        before = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        writes = []

        def lose_reply(route):
            writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_repair_order", lose_reply)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(self.page.locator("#mobileRepairOrderStatusSelect").input_value(), "ready")
        self.assertEqual(
            self.page.evaluate("state.mobileRepairOrderCard.repair_order.status"), "open"
        )
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before + 1
        )
        self.assertIn("СТАТУС НЕ ИЗМЕНЁН", self.page.locator("#statusLine").inner_text())
        self.page.unroute("**/api/update_repair_order", lose_reply)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileRepairOrderSaving && state.mobileRepairOrderCard?.repair_order?.status === 'ready'"
        )
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before + 1
        )
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            self.runtime.service.get_repair_order({"card_id": first, "create_if_missing": False})[
                "repair_order"
            ]["status"],
            "ready",
        )

    def test_confirmed_payment_does_not_hide_changed_readback_fields(self):
        first, _ = self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC OPERATOR DRAFT")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        before = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        writes = []

        def concurrent_edit_after_write(route):
            writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            self.runtime.service.update_repair_order(
                {
                    "card_id": first,
                    "repair_order": {"client": "SYNTHETIC OTHER OPERATOR"},
                }
            )
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_repair_order", concurrent_edit_after_write)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC OPERATOR DRAFT"
        )
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.assertEqual(
            self.page.evaluate("state.mobileRepairOrderCard.repair_order.client"),
            "SYNTHETIC OTHER OPERATOR",
        )
        self.assertTrue(self.page.evaluate("repairOrderWriteNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.assertIn(
            "ОСТАЛЬНЫЕ ИЗМЕНЕНИЯ НЕ ПОДТВЕРЖДЕНЫ", self.page.locator("#statusLine").inner_text()
        )
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before + 1
        )
        self.assertEqual(
            self.runtime.service.get_repair_order({"card_id": first, "create_if_missing": False})[
                "repair_order"
            ]["client"],
            "SYNTHETIC OTHER OPERATOR",
        )

    def exercise_changed_payment_readback(
        self, *, change_method=False, change_amount=False, lose_response=True
    ):
        self.open_first()
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderPaymentNote").fill("SYNTHETIC MY NOTE")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        writes = []

        def lose_reply(route):
            writes.append(route.request.url)
            response = route.fetch()
            self.assertEqual(response.status, 200)
            if lose_response:
                route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
            else:
                route.fulfill(response=response)

        def changed_readback(route):
            response = route.fetch()
            payload = response.json()
            order = payload["data"]["card"]["repair_order"]
            if change_method:
                order["payment_method"] = "card"
            elif change_amount:
                order["payments"][0]["amount"] = "123.001"
            else:
                order["payments"][0]["note"] = "SYNTHETIC OTHER NOTE"
            route.fulfill(json=payload)

        self.page.route("**/api/update_repair_order", lose_reply)
        self.page.route("**/api/get_repair_order", changed_readback)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertIn(
            "SYNTHETIC MY NOTE", self.page.locator("#mobileRepairOrderPayments").inner_text()
        )
        self.assertTrue(self.page.evaluate("repairOrderNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(writes), 1)

    def test_confirmed_payment_with_changed_readback_note_blocks_repeat(self):
        self.exercise_changed_payment_readback(change_method=False)

    def test_confirmed_payment_with_changed_readback_method_blocks_repeat(self):
        self.exercise_changed_payment_readback(change_method=True)

    def test_fractional_kopeck_in_readback_blocks_repeat(self):
        self.exercise_changed_payment_readback(change_amount=True)

    def test_success_with_changed_readback_note_blocks_repeat(self):
        self.exercise_changed_payment_readback(lose_response=False)

    def test_success_payment_with_matching_readback_saves_normally(self):
        first, _ = self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC NORMAL PAYMENT")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderPaymentNote").fill("SYNTHETIC NORMAL NOTE")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        before = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertFalse(self.page.evaluate("repairOrderNeedsVerification()"))
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before + 1
        )
        order = self.runtime.service.get_repair_order(
            {"card_id": first, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(order["client"], "SYNTHETIC NORMAL PAYMENT")
        self.assertEqual(order["payments"][0]["note"], "SYNTHETIC NORMAL NOTE")

    def exercise_status_readback(self, *, concurrent_change):
        first, _ = self.open_first()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC MY STATUS DRAFT")
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("ready")
        writes = []

        def lost_status_reply(route):
            writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            if concurrent_change:
                self.runtime.service.update_repair_order(
                    {
                        "card_id": first,
                        "repair_order": {"client": "SYNTHETIC OTHER STATUS EDIT"},
                    }
                )
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/set_repair_order_status", lost_status_reply)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(self.page.locator("#mobileRepairOrderStatusSelect").input_value(), "ready")
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC MY STATUS DRAFT"
        )
        self.assertEqual(
            self.page.evaluate("repairOrderWriteNeedsVerification()"), concurrent_change
        )
        self.assertEqual(
            self.page.locator("#mobileRepairOrderSaveButton").is_disabled(), concurrent_change
        )
        if concurrent_change:
            self.assertEqual(
                self.page.evaluate("state.mobileRepairOrderCard.repair_order.client"),
                "SYNTHETIC OTHER STATUS EDIT",
            )
            self.page.evaluate("saveMobileRepairOrder()")
            self.assertEqual(len(writes), 1)
            self.assertEqual(
                self.runtime.service.get_repair_order(
                    {"card_id": first, "create_if_missing": False}
                )["repair_order"]["client"],
                "SYNTHETIC OTHER STATUS EDIT",
            )

    def test_status_readback_does_not_hide_concurrent_fields(self):
        self.exercise_status_readback(concurrent_change=True)

    def test_status_readback_accepts_matching_fields(self):
        self.exercise_status_readback(concurrent_change=False)

    def test_status_readback_with_changed_payment_transaction_blocks_repeat(self):
        self.open_first()
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("ready")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        before = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        after_status = {"value": False}
        status_writes = []

        def lose_status_reply(route):
            status_writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            after_status["value"] = True
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        def changed_transaction(route):
            response = route.fetch()
            payload = response.json()
            if after_status["value"]:
                payload["data"]["card"]["repair_order"]["payments"][0]["cash_transaction_id"] = (
                    "synthetic-other-transaction"
                )
            route.fulfill(json=payload)

        self.page.route("**/api/set_repair_order_status", lose_status_reply)
        self.page.route("**/api/get_repair_order", changed_transaction)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(len(status_writes), 1)
        self.assertTrue(self.page.evaluate("repairOrderWriteNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(status_writes), 1)
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before + 1
        )
