"""Closing or switching an order must not silently discard a desktop draft."""

from tests.browser_ui_support import UiBrowserTestCase


class DesktopRepairOrderDiscardBrowserTests(UiBrowserTestCase):
    def open_order_list(self):
        cards = [self.create_card("SYNTHETIC ORDER " + suffix) for suffix in ("A", "B")]
        for card in cards:
            self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.click("#repairOrdersButton")
        self.open_order(cards[0])
        return cards

    def open_order(self, card):
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")

    def set_draft(self):
        self.page.locator("#repairOrderClient").fill("SYNTHETIC UNSAVED CLIENT")
        self.assertTrue(self.page.evaluate("repairOrderModalHasUnsavedChanges()"))

    def assert_draft(self):
        self.assertTrue(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC UNSAVED CLIENT"
        )

    def test_decline_close_keeps_draft_and_confirm_discards(self):
        first, second = self.open_order_list()
        self.set_draft()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assert_draft()
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.open_order(second)
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.open_order(first)
        self.assertNotEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC UNSAVED CLIENT"
        )
        self.assertEqual(self.writes.count("update_repair_order"), 0)

    def test_clean_order_closes_without_confirmation(self):
        self.open_order_list()
        dialogs = []
        self.page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(dialogs, [])

    def test_parent_close_blocked_during_active_save(self):
        self.open_order_list()
        self.set_draft()
        held = []
        self.page.route("**/api/update_repair_order", lambda route: held.append(route))
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function("!!state.repairOrderMutationRequest")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.assertFalse(self.page.evaluate("closeNamedModal('repair-orders')"))
        self.assertTrue(self.page.locator("#repairOrdersModal").is_visible())
        self.assert_draft()
        held.pop().fulfill(
            status=409,
            json={
                "ok": False,
                "error": {"code": "card_update_conflict", "message": "SYNTHETIC CONFLICT"},
            },
        )
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        self.assert_draft()

    def test_parent_close_and_direct_switch_respect_decline(self):
        first, second = self.open_order_list()
        self.set_draft()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.assertFalse(self.page.evaluate("closeNamedModal('repair-orders')"))
        self.assertTrue(self.page.locator("#repairOrdersModal").is_visible())
        self.assert_draft()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.evaluate("id => openRepairOrderCard(id)", second)
        self.assertEqual(self.page.evaluate("state.activeCard.id"), first)
        self.assert_draft()

    def test_parent_card_close_respects_decline(self):
        card = self.create_card("SYNTHETIC CARD")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.open_card(card)
        self.page.click("#repairOrderButton")
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.set_draft()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.assertFalse(self.page.evaluate("closeCardModal()"))
        self.assertTrue(self.page.locator("#cardModal").is_visible())
        self.assert_draft()

    def test_parent_client_close_respects_decline(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"][
            "id"
        ]
        card = self.create_card("SYNTHETIC ORDER", client_id=client)
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        self.page.click("#clientsButton")
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.locator(f'#clientOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        self.set_draft()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.assertFalse(self.page.evaluate("closeNamedModal('clients')"))
        self.assertTrue(self.page.locator("#clientsModal").is_visible())
        self.assert_draft()

    def test_unconfirmed_write_marker_survives_discard(self):
        card, _ = self.open_order_list()
        self.set_draft()
        self.page.evaluate("id => markRepairOrderWriteVerificationPending(id)", card)
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assert_draft()
        self.assertEqual(self.page.evaluate("state.repairOrderWriteVerificationPending"), card)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(self.page.evaluate("state.repairOrderWriteVerificationPending"), card)

    def test_parent_close_preserves_unsubmitted_payment_input_on_decline(self):
        self.open_order_list()
        self.page.click("#repairOrderPaymentsButton")
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("123")
        self.page.locator("#repairOrderPaymentNote").fill("SYNTHETIC PAYMENT DRAFT")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.assertFalse(self.page.evaluate("closeNamedModal('repair-order')"))
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "123")
        self.assertEqual(
            self.page.locator("#repairOrderPaymentNote").input_value(), "SYNTHETIC PAYMENT DRAFT"
        )
        self.assertEqual(self.writes.count("add_repair_order_payment"), 0)

    def test_payment_window_confirms_before_discarding_unsubmitted_amount(self):
        self.open_order_list()
        self.page.click("#repairOrderPaymentsButton")
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("123")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator('#repairOrderPaymentsModal [data-close="repair-order-payments"]').click()
        self.assertTrue(self.page.locator("#repairOrderPaymentsModal").is_visible())
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "123")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderPaymentsModal [data-close="repair-order-payments"]').click()
        self.assertFalse(self.page.locator("#repairOrderPaymentsModal").is_visible())
        self.assertTrue(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(self.writes.count("add_repair_order_payment"), 0)

    def test_lost_payment_reply_clears_only_after_confirmed_readback(self):
        card, _ = self.open_order_list()
        self.page.click("#repairOrderPaymentsButton")
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("123")
        self.page.locator("#repairOrderPaymentNote").fill("SYNTHETIC PAYMENT")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/update_repair_order", lose_reply)
        self.page.click("#repairOrderPaymentAddButton")
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        order = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(len(order["payments"]), 1)
        transaction_id = order["payments"][0]["cash_transaction_id"]
        self.assertTrue(transaction_id)
        transactions = self.runtime.state_store.read_bundle()["cash_transactions"]
        self.assertEqual(sum(item.id == transaction_id for item in transactions), 1)
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "")
        self.assertEqual(self.page.locator("#repairOrderPaymentNote").input_value(), "")
        self.assertFalse(self.page.evaluate("repairOrderPaymentHasDraft()"))
        self.page.click("#repairOrderPaymentAddButton")
        self.assertEqual(self.writes.count("update_repair_order"), 1)

    def test_unknown_payment_result_keeps_input_and_blocks_repeat(self):
        card, _ = self.open_order_list()
        self.page.click("#repairOrderPaymentsButton")
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("123")
        self.page.locator("#repairOrderPaymentNote").fill("SYNTHETIC PAYMENT")

        def fail(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC FAILURE"}})

        self.page.route("**/api/update_repair_order", fail)
        self.page.route("**/api/get_repair_order", fail)
        self.page.click("#repairOrderPaymentAddButton")
        self.page.wait_for_function(
            "!state.repairOrderMutationRequest && !!state.repairOrderPaymentVerificationPending"
        )
        self.assertEqual(self.page.evaluate("state.repairOrderPaymentVerificationPending"), card)
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "123")
        self.assertEqual(
            self.page.locator("#repairOrderPaymentNote").input_value(), "SYNTHETIC PAYMENT"
        )
        self.assertTrue(self.page.locator("#repairOrderPaymentAddButton").is_disabled())
        self.assertEqual(self.writes.count("update_repair_order"), 1)

    def test_rejected_payment_keeps_unsubmitted_input(self):
        card, _ = self.open_order_list()
        self.page.click("#repairOrderPaymentsButton")
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("123")
        self.page.locator("#repairOrderPaymentNote").fill("SYNTHETIC PAYMENT")
        self.page.route(
            "**/api/update_repair_order",
            lambda route: route.fulfill(
                status=422,
                json={
                    "ok": False,
                    "error": {"code": "validation_error", "message": "SYNTHETIC REJECTION"},
                },
            ),
        )
        self.page.click("#repairOrderPaymentAddButton")
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "123")
        self.assertEqual(
            self.page.locator("#repairOrderPaymentNote").input_value(), "SYNTHETIC PAYMENT"
        )
        self.assertFalse(self.page.evaluate("Boolean(state.repairOrderPaymentVerificationPending)"))
        self.assertEqual(
            self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]["payments"], []
        )
