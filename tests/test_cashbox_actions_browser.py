"""Cash UI recovery on synthetic ledgers, with native business services unchanged."""

from __future__ import annotations

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class CashboxActionsBrowserTests(UiBrowserTestCase):
    def test_module_timeout_restores_controls_and_late_load_cannot_send_money(self) -> None:
        self.context.add_init_script(
            "const schedule = window.setTimeout; window.setTimeout = (fn, ms, ...args) => schedule(fn, ms === 15000 ? 150 : ms, ...args);"
        )
        self.open_cashbox()
        self.page.locator("#cashboxAmountInput").fill("17.35")
        path = self.page.evaluate("BOARD_MODULE_MANIFEST.cashbox_ops")
        held = []
        self.page.route("**" + path, lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.await_held_request(held)
        self.page.wait_for_function("!state.cashboxMoneyRequest")
        self.assertFalse(self.page.locator("#cashboxAmountInput").is_disabled())
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.writes.count("create_cash_transaction"), 0)
        self.page.unroute("**" + path)
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)

    def test_write_deadline_restores_the_owned_draft_without_a_blind_repeat(self) -> None:
        self.context.add_init_script(
            "const schedule = window.setTimeout; window.setTimeout = (fn, ms, ...args) => schedule(fn, ms === 90000 ? 150 : ms, ...args);"
        )
        cashbox_id = self.open_cashbox()
        self.fill_transaction()
        held = []

        def release_mock_requests():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release_mock_requests)
        self.page.route("**/api/create_cash_transaction", lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.await_held_request(held)
        self.finish_transaction()
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        self.assertFalse(self.page.locator("#cashboxNoteInput").is_disabled())
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count)
        release_mock_requests()

    def open_cancellation(self) -> str:
        cashbox_id = self.runtime.cashbox_id
        transaction = self.runtime.service.create_cash_transaction(
            {
                "cashbox_id": cashbox_id,
                "amount": "17.35",
                "direction": "income",
                "note": "Synthetic cancellation target",
            }
        )["transaction"]
        self.open_cashbox()
        self.page.locator(f'[data-cashbox-transaction-cancel="{transaction["id"]}"]').click()
        self.page.locator("#cashboxCancelPopover:not([hidden])").wait_for()
        self.page.locator("#cashboxCancelReasonInput").fill("Synthetic cancellation reason")
        return transaction["id"]

    def test_lost_cancellation_reply_closes_only_after_reading_the_known_target(self) -> None:
        transaction_id = self.open_cancellation()

        def lose_reply(route):
            self.assertTrue(route.request.post_data_json["expected_cashbox_updated_at"])
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/cancel_cash_transaction", lose_reply)
        self.page.locator("#cashboxCancelConfirmButton").click()
        self.page.wait_for_function("!state.cashboxMoneyRequest")
        self.assertFalse(self.page.locator("#cashboxCancelPopover").is_visible())
        self.assertEqual(self.writes.count("cancel_cash_transaction"), 1)
        stored = self.cashbox(self.runtime.cashbox_id)["transactions"]
        original = next(item for item in stored if item["id"] == transaction_id)
        self.assertEqual(original["transaction_kind"], "cashbox_cancelled")
        self.assertEqual(
            sum(item.get("related_transaction_id") == transaction_id for item in stored), 1
        )

    def test_pending_cancellation_locks_reason_and_cannot_close_its_popover(self) -> None:
        self.open_cancellation()
        held = []
        self.page.route("**/api/cancel_cash_transaction", lambda route: held.append(route))
        self.page.locator("#cashboxCancelConfirmButton").click()
        self.await_held_request(held)
        self.assertTrue(self.page.locator("#cashboxCancelReasonInput").is_disabled())
        self.assertFalse(self.page.evaluate("closeCashboxCancelPopover()"))
        self.assertTrue(self.page.locator("#cashboxCancelPopover").is_visible())
        held.pop().continue_()
        self.page.wait_for_function("!state.cashboxMoneyRequest")
        self.assertFalse(self.page.locator("#cashboxCancelPopover").is_visible())

    def test_rejected_cancellation_keeps_reason_and_restores_feedback_and_controls(self) -> None:
        self.open_cancellation()
        self.page.route(
            "**/api/cancel_cash_transaction",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic cancellation failure"}}
                ),
            ),
        )
        self.page.locator("#cashboxCancelConfirmButton").click()
        self.page.wait_for_function("!state.cashboxMoneyRequest")
        self.assertTrue(self.page.locator("#cashboxCancelPopover").is_visible())
        self.assertEqual(
            self.page.locator("#cashboxCancelReasonInput").input_value(),
            "Synthetic cancellation reason",
        )
        self.assertFalse(self.page.locator("#cashboxCancelReasonInput").is_disabled())
        self.assertIn(
            "Synthetic cancellation failure",
            self.page.locator("#cashboxCancelFeedback").inner_text(),
        )
        self.page.unroute("**/api/cancel_cash_transaction")
        self.page.locator("#cashboxCancelConfirmButton").click()
        self.page.wait_for_function("!state.cashboxMoneyRequest")
        self.assertFalse(self.page.locator("#cashboxCancelPopover").is_visible())

    def await_held_request(self, held):
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def test_uncommitted_income_retry_keeps_the_original_native_revision_guard(self) -> None:
        cashbox_id = self.open_cashbox()
        self.fill_transaction()
        lost_bodies = []

        def fail_before_server(route):
            lost_bodies.append(route.request.post_data_json)
            route.abort("failed")

        self.page.route("**/api/create_cash_transaction", fail_before_server)
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count)
        self.page.unroute("**/api/create_cash_transaction")
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.writes.count("create_cash_transaction"), 2)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)
        late = self.context.request.post(
            self.runtime.base_url + "/api/create_cash_transaction",
            headers={"X-Operator-Session": self.session_token},
            data=lost_bodies[0],
        )
        self.assertEqual(late.status, 409)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)

    def test_cold_money_module_locks_fields_until_its_confirmed_action(self) -> None:
        self.open_cashbox()
        self.page.locator("#cashboxAmountInput").fill("17.35")
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.cashbox_ops")
        held = []
        self.page.route("**" + module_path, lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.page.wait_for_timeout(60)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#cashboxAmountInput").is_disabled())
        self.assertTrue(self.page.locator("#cashboxIncomeButton").is_disabled())
        held.pop().continue_()
        self.finish_transaction()
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "")

    def test_pending_income_keeps_its_parent_modal_open(self) -> None:
        self.open_cashbox()
        self.fill_transaction()
        held = []
        self.page.route("**/api/create_cash_transaction", lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.await_held_request(held)
        self.assertFalse(self.page.evaluate("closeNamedModal('cashboxes')"))
        self.assertTrue(self.page.locator("#cashboxesModal.is-open").is_visible())
        held.pop().continue_()
        self.finish_transaction()

    def test_native_version_conflict_retains_draft_and_releases_controls(self) -> None:
        cashbox_id = self.open_cashbox()
        self.fill_transaction()
        external = self.runtime.service.create_cash_transaction(
            {
                "cashbox_id": cashbox_id,
                "amount": "3.21",
                "direction": "income",
                "note": "Synthetic second operator",
            }
        )
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        self.assertFalse(self.page.locator("#cashboxNoteInput").is_disabled())
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)
        self.assertTrue(external["transaction"]["id"])

    def test_native_error_preserves_draft_and_allows_a_confirmed_retry(self) -> None:
        cashbox_id = self.open_cashbox()
        self.fill_transaction()
        self.page.route(
            "**/api/create_cash_transaction",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "validation_error",
                            "message": "Synthetic validation failure",
                        },
                    }
                ),
            ),
        )
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count)
        self.page.unroute("**/api/create_cash_transaction")
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "")

    def test_mobile_lost_income_reply_does_not_repeat_the_write(self) -> None:
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="cashboxes"]').click()
        cashbox_id = self.runtime.cashbox_id
        before = len(self.cashbox(cashbox_id)["transactions"])
        self.page.locator(f'[data-mobile-cashbox-id="{cashbox_id}"]').click()
        self.page.wait_for_function(
            "!state.cashboxesLoadController && state.activeCashbox?.cashbox?.updated_at"
        )
        self.page.locator("#mobileCashboxIncomeButton").click()
        self.page.locator("#mobileCashboxAmountInput").fill("17.35")
        self.page.locator("#mobileCashboxNoteInput").fill("Synthetic mobile income")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_cash_transaction", lose_reply)
        self.page.locator("#mobileCashboxSubmitButton").click()
        self.page.wait_for_function("!els.mobileCashboxSubmitButton.disabled")
        self.page.locator("#mobileCashboxSubmitButton").click()
        self.page.wait_for_function("!els.mobileCashboxSubmitButton.disabled")
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), before + 1)
        self.assertEqual(self.page.locator("#mobileCashboxAmountInput").input_value(), "17.35")

    def open_cashbox(self) -> str:
        cashbox_id = self.runtime.cashbox_id
        before = self.cashbox(cashbox_id)
        self.transaction_count = len(before["transactions"])
        self.balance_minor = before["cashbox"]["statistics"]["balance_minor"]
        self.load_board()
        self.page.locator("#cashboxesButton").click()
        self.page.locator("#cashboxesModal.is-open").wait_for()
        self.page.locator(f'#cashboxesList [data-cashbox-id="{cashbox_id}"]').click()
        self.page.wait_for_function(
            "id => state.activeCashbox?.cashbox?.id === id && !state.cashboxesLoadController",
            arg=cashbox_id,
        )
        return cashbox_id

    def fill_transaction(self) -> None:
        self.page.locator("#cashboxAmountInput").fill("17.35")
        self.page.locator("#cashboxNoteInput").fill("Synthetic UI transaction")

    def finish_transaction(self) -> None:
        self.page.wait_for_function("!els.cashboxIncomeButton.disabled")

    def cashbox(self, cashbox_id: str) -> dict:
        return self.runtime.service.get_cashbox({"cashbox_id": cashbox_id})

    def test_lost_committed_income_reply_cannot_create_a_second_receipt(self) -> None:
        cashbox_id = self.open_cashbox()
        self.fill_transaction()

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_cash_transaction", lose_reply)
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        stored = self.cashbox(cashbox_id)
        self.assertEqual(len(stored["transactions"]), self.transaction_count + 1)
        self.assertEqual(
            stored["cashbox"]["statistics"]["balance_minor"], self.balance_minor + 1735
        )

    def test_pending_income_freezes_inputs_and_survives_background_render(self) -> None:
        self.open_cashbox()
        self.fill_transaction()
        held = []
        self.page.route("**/api/create_cash_transaction", lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.await_held_request(held)
        self.assertTrue(self.page.locator("#cashboxAmountInput").is_disabled())
        self.assertTrue(self.page.locator("#cashboxNoteInput").is_disabled())
        self.page.evaluate("renderCashboxDetail(); createCashboxTransaction('income')")
        self.assertTrue(self.page.locator("#cashboxIncomeButton").is_disabled())
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        held.pop().continue_()
        self.finish_transaction()
        self.assertFalse(self.page.locator("#cashboxAmountInput").is_disabled())
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "")

    def test_confirmed_income_with_failed_refresh_retries_only_the_read(self) -> None:
        cashbox_id = self.open_cashbox()
        self.fill_transaction()
        self.page.route(
            "**/api/list_cashboxes?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "unavailable", "message": "Synthetic refresh failure"},
                    }
                ),
            ),
        )
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "17.35")
        self.page.unroute("**/api/list_cashboxes?**")
        self.page.locator("#cashboxIncomeButton").click()
        self.finish_transaction()
        self.assertEqual(self.writes.count("create_cash_transaction"), 1)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)
        self.assertEqual(self.page.locator("#cashboxAmountInput").input_value(), "")

    def open_transfer(self) -> str:
        cashbox_id = self.open_cashbox()
        self.page.locator("#cashboxTransferButton").click()
        self.page.locator("#cashboxTransferModal.is-open").wait_for()
        self.page.locator("#cashboxTransferAmountInput").fill("17.35")
        self.page.locator("#cashboxTransferNoteInput").fill("Synthetic UI transfer")
        return cashbox_id

    def test_lost_committed_transfer_reply_cannot_create_a_second_pair(self) -> None:
        cashbox_id = self.open_transfer()

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_cashbox_transfer", lose_reply)
        self.page.locator("#cashboxTransferConfirmButton").click()
        self.page.wait_for_function("!els.cashboxTransferConfirmButton.disabled")
        self.page.locator("#cashboxTransferConfirmButton").click()
        self.page.wait_for_function("!els.cashboxTransferConfirmButton.disabled")
        self.assertEqual(self.writes.count("create_cashbox_transfer"), 1)
        self.assertEqual(len(self.cashbox(cashbox_id)["transactions"]), self.transaction_count + 1)

    def test_pending_transfer_cannot_be_reenabled_by_preview_render(self) -> None:
        self.open_transfer()
        held = []
        self.page.route("**/api/create_cashbox_transfer", lambda route: held.append(route))
        self.page.locator("#cashboxTransferConfirmButton").click()
        self.page.wait_for_timeout(60)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#cashboxTransferAmountInput").is_disabled())
        self.page.evaluate("handleCashboxTransferAmountInput(); submitCashboxTransfer()")
        self.assertTrue(self.page.locator("#cashboxTransferConfirmButton").is_disabled())
        self.assertEqual(self.writes.count("create_cashbox_transfer"), 1)
        held.pop().continue_()
        self.page.locator("#cashboxTransferModal.is-open").wait_for(state="hidden")

    def test_logout_invalidates_pending_income_without_late_modal_reopening(self) -> None:
        self.open_cashbox()
        self.fill_transaction()
        held = []
        self.page.route("**/api/create_cash_transaction", lambda route: held.append(route))
        self.page.locator("#cashboxIncomeButton").click()
        self.await_held_request(held)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.evaluate("clearOperatorSession()")
        route.fulfill(response=response)
        self.page.wait_for_timeout(150)
        self.assertIsNone(self.page.evaluate("state.activeCashbox"))
        self.assertFalse(self.page.locator("#cashboxAmountInput").is_disabled())
        self.assertFalse(
            self.page.locator("#cashboxesModal").evaluate("e=>e.classList.contains('is-open')")
        )
