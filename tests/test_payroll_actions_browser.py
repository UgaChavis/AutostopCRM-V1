"""Payroll button recovery on synthetic employee and cashbox state."""

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class PayrollActionsBrowserTests(UiBrowserTestCase):
    def hold_request(self, held):
        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        return lambda route: held.append(route)

    def await_held_request(self, held):
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def test_advance_write_deadline_restores_fields_and_leaves_the_draft(self):
        self.context.add_init_script(
            "const schedule = window.setTimeout; window.setTimeout = (fn, ms, ...args) => schedule(fn, ms === 90000 ? 150 : ms, ...args);"
        )
        self.open_advance()
        held = []
        self.page.route("**/api/create_employee_salary_transaction", self.hold_request(held))
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.await_held_request(held)
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertEqual(
            self.page.locator("#employeeSalaryAdvanceAmountInput").input_value(), "17.35"
        )
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceAmountInput").is_disabled())
        held.pop().abort("failed")

    def test_native_cashbox_conflict_keeps_advance_draft_and_retry_uses_the_new_revision(self):
        self.open_advance()
        self.runtime.service.create_cash_transaction(
            {
                "cashbox_id": self.runtime.cashbox_id,
                "direction": "income",
                "amount": "1.00",
                "note": "Synthetic peer cashbox change",
            }
        )
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertEqual(
            self.page.locator("#employeeSalaryAdvanceAmountInput").input_value(), "17.35"
        )
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 2)

    def test_known_advance_error_preserves_fields_and_allows_one_successful_retry(self):
        self.open_advance()
        self.page.route(
            "**/api/create_employee_salary_transaction",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic advance validation failure"}}
                ),
            ),
        )
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(
            self.page.locator("#employeeSalaryAdvanceAmountInput").input_value(), "17.35"
        )
        self.assertEqual(
            self.page.locator("#employeeSalaryAdvanceCommentInput").input_value(),
            "Synthetic advance UI recovery",
        )
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceAmountInput").is_disabled())
        self.page.unroute("**/api/create_employee_salary_transaction")
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 2)

    def test_uncommitted_advance_retry_reuses_the_native_cashbox_guard(self):
        self.open_advance()
        original_bodies = []

        def fail_before_commit(route):
            original_bodies.append(route.request.post_data_json)
            route.abort("failed")

        self.page.route("**/api/create_employee_salary_transaction", fail_before_commit)
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.page.unroute("**/api/create_employee_salary_transaction")
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        late = self.context.request.post(
            self.runtime.base_url + "/api/create_employee_salary_transaction",
            headers={"X-Operator-Session": self.session_token},
            data=original_bodies[0],
        )
        self.assertEqual(late.status, 409)

    def test_pending_advance_cannot_switch_to_another_money_dialog(self):
        self.open_advance()
        held = []
        self.page.route("**/api/create_employee_salary_transaction", self.hold_request(held))
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("Boolean(state.employeeMoneyMutationOperation)")
        self.assertTrue(self.page.locator("#employeeSalaryPayoutButton").is_disabled())
        self.page.locator("#employeeSalaryPayoutButton").dispatch_event("click")
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertFalse(self.page.evaluate("closeNamedModal('employeeSalary')"))
        self.await_held_request(held)
        self.await_held_request(held)
        held.pop().continue_()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 1)

    def test_logout_releases_pending_advance_controls_and_ignores_late_reply(self):
        self.open_advance()
        held = []
        self.page.route("**/api/create_employee_salary_transaction", self.hold_request(held))
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("Boolean(state.employeeMoneyMutationOperation)")
        self.await_held_request(held)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.evaluate("clearOperatorSession()")
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertIsNone(self.page.evaluate("state.employeeSalarySheet"))
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceAmountInput").is_disabled())
        self.assertFalse(
            self.page.locator("#employeeSalaryModal").evaluate("e=>e.classList.contains('is-open')")
        )

    def test_confirmed_advance_with_failed_sheet_refresh_keeps_the_dialog_and_receipt(self):
        self.open_advance()
        self.page.route(
            "**/api/get_employee_salary_ledger?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic sheet refresh failure"}}
                ),
            ),
        )
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())
        self.assertEqual(
            self.page.locator("#employeeSalaryAdvanceAmountInput").input_value(), "17.35"
        )
        self.page.unroute("**/api/get_employee_salary_ledger?**")
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 1)
        self.assertFalse(self.page.locator("#employeeSalaryAdvanceDialog").is_visible())

    def open_shift(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-id="{self.runtime.employee_id}"]').click()
        self.page.locator("#employeeShiftAccrualButton").click()
        self.page.locator("#employeeShiftAccrualDialog:not([hidden])").wait_for()
        self.page.locator("#employeeShiftAccrualAmountInput").fill("17.35")

    def test_confirmed_shift_with_failed_report_refresh_retries_only_reads(self):
        self.open_shift()
        accruals = []

        def capture_receipt(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            accruals.append(response.json()["data"]["accrual"])
            route.fulfill(response=response)

        self.page.route("**/api/create_employee_shift_accrual", capture_receipt)
        self.page.route(
            "**/api/get_payroll_report?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic report refresh failure"}}
                ),
            ),
        )
        self.page.locator("#employeeShiftAccrualConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertTrue(self.page.locator("#employeeShiftAccrualDialog").is_visible())
        self.assertEqual(
            self.page.locator("#employeeShiftAccrualAmountInput").input_value(), "17.35"
        )
        self.page.unroute("**/api/get_payroll_report?**")
        self.page.locator("#employeeShiftAccrualConfirmButton").click()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertFalse(self.page.locator("#employeeShiftAccrualDialog").is_visible())
        self.assertEqual(self.writes.count("create_employee_shift_accrual"), 1)
        self.assertEqual(len(accruals), 1)
        self.assertEqual(accruals[0]["amount_minor"], 1735)

    def test_lost_shift_accrual_reply_cannot_append_a_second_accrual(self):
        self.open_shift()
        receipt_ids = []

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            receipt_ids.append(response.json()["data"]["accrual"]["id"])
            route.abort("failed")

        self.page.route("**/api/create_employee_shift_accrual", lose_reply)
        for _ in range(2):
            self.page.locator("#employeeShiftAccrualConfirmButton").click()
            self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(self.writes.count("create_employee_shift_accrual"), 1)
        self.assertEqual(len(set(receipt_ids)), 1)

    def test_lost_payout_reply_cannot_create_a_second_cash_receipt(self):
        self.open_advance()
        self.page.locator("#employeeSalaryAdvanceCancelButton").click()
        self.page.locator("#employeeSalaryPayoutButton").click()
        self.page.locator("#employeeSalaryActionDialog:not([hidden])").wait_for()
        self.page.locator("#employeeSalaryAmountInput").fill("17.35")
        self.page.locator("#employeeSalaryCashboxSelect").select_option(self.runtime.cashbox_id)

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_employee_salary_transaction", lose_reply)
        for _ in range(2):
            self.page.locator("#employeeSalaryActionConfirmButton").click()
            self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 1)

    def open_advance(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-salary="{self.runtime.employee_id}"]').click()
        self.page.locator("#employeeSalaryModal.is-open").wait_for()
        self.page.wait_for_function("Boolean(state.employeeSalarySheet)")
        self.page.locator("#employeeSalaryAdvanceButton").click()
        self.page.locator("#employeeSalaryAdvanceDialog:not([hidden])").wait_for()
        self.page.locator("#employeeSalaryAdvanceAmountInput").fill("17.35")
        self.page.locator("#employeeSalaryAdvanceCashboxSelect").select_option(
            self.runtime.cashbox_id
        )
        self.page.locator("#employeeSalaryAdvanceCommentInput").fill(
            "Synthetic advance UI recovery"
        )

    def test_lost_advance_reply_cannot_create_a_second_cash_receipt(self):
        self.open_advance()
        before = self.runtime.service.get_cashbox({"cashbox_id": self.runtime.cashbox_id})

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_employee_salary_transaction", lose_reply)
        for _ in range(2):
            self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
            self.page.wait_for_function("!state.employeeMoneyMutationOperation")
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 1)
        after = self.runtime.service.get_cashbox({"cashbox_id": self.runtime.cashbox_id})
        self.assertEqual(len(after["transactions"]), len(before["transactions"]) + 1)
        self.assertEqual(
            after["cashbox"]["statistics"]["balance_minor"],
            before["cashbox"]["statistics"]["balance_minor"] - 1735,
        )

    def test_pending_advance_freezes_amount_cashbox_and_comment(self):
        self.open_advance()
        held = []
        self.page.route("**/api/create_employee_salary_transaction", self.hold_request(held))
        self.page.locator("#employeeSalaryAdvanceConfirmButton").click()
        self.page.wait_for_function("Boolean(state.employeeMoneyMutationOperation)")
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceAmountInput").is_disabled())
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceCashboxSelect").is_disabled())
        self.assertTrue(self.page.locator("#employeeSalaryAdvanceCommentInput").is_disabled())
        self.await_held_request(held)
        held.pop().continue_()
        self.page.wait_for_function("!state.employeeMoneyMutationOperation")
