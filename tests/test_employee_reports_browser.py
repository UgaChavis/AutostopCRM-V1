import time
import unittest

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeReportsBrowserTests(UiBrowserTestCase):
    def test_old_print_reply_cannot_close_a_new_period_dialog(self):
        self.assert_new_period_survives_old_reply(self.runtime.salary_reset_employee_id)

    def test_old_print_reply_cannot_close_a_reopened_period_for_the_same_employee(self):
        self.assert_new_period_survives_old_reply(self.runtime.employee_id)

    def assert_new_period_survives_old_reply(self, new_employee_id):
        self.open_employees()
        held = []

        def hold_response(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            held.append((route, response))

        self.page.route("**/api/get_employee_salary_reconciliation?**", hold_response)
        self.page.locator(f'[data-employee-report="{self.runtime.employee_id}"]').click()
        with self.page.expect_popup() as popup_info:
            self.page.locator("#employeeSalaryReconciliationOpenButton").click()
        popup = popup_info.value
        self.wait_held(held)
        self.page.locator("#employeeSalaryReconciliationCancelButton").click()
        self.page.locator(f'[data-employee-report="{new_employee_id}"]').click()
        self.page.locator("#employeeSalaryReconciliationDaysInput").fill("7")
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(300)
        self.assertTrue(
            self.page.locator("#employeeSalaryReconciliationPeriodModal.is-open").is_visible()
        )
        self.assertEqual(
            self.page.locator("#employeeSalaryReconciliationDaysInput").input_value(), "7"
        )
        popup.close()

    def open_employees(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")

    def wait_held(self, held):
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def test_salary_reply_cannot_cover_a_newer_clients_window(self):
        self.open_employees()
        held = []
        self.page.route("**/api/get_employee_salary_ledger?**", lambda route: held.append(route))
        self.page.locator(f'[data-employee-salary="{self.runtime.employee_id}"]').click()
        self.wait_held(held)
        self.page.locator('[data-close="employees"]').click()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for()
        held.pop().continue_()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1)?.key"), "clients")

    def test_print_reply_after_logout_cannot_display_previous_salary_data(self):
        self.open_employees()
        held = []

        def hold_response(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            held.append((route, response))

        self.page.route("**/api/get_employee_salary_reconciliation?**", hold_response)
        self.page.locator(f'[data-employee-report="{self.runtime.employee_id}"]').click()
        self.page.locator("#employeeSalaryReconciliationPeriodModal.is-open").wait_for()
        with self.page.expect_popup() as popup_info:
            self.page.locator("#employeeSalaryReconciliationOpenButton").click()
        popup = popup_info.value
        self.wait_held(held)
        self.page.evaluate("clearOperatorSession()")
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(300)
        self.assertTrue(popup.is_closed())

    def test_mobile_reentry_reads_a_peers_new_salary_balance(self):
        self.assert_mobile_reentry_reads_a_peers_balance(via_board=True)

    def test_mobile_panel_reopen_reads_a_peers_new_salary_balance(self):
        self.assert_mobile_reentry_reads_a_peers_balance(via_board=False)

    def assert_mobile_reentry_reads_a_peers_balance(self, *, via_board):
        employee = self.runtime.service.save_employee(
            {"name": "Synthetic mobile balance", "salary_mode": "none"}
        )["employee"]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="employees"]').click()
        self.page.wait_for_function(
            "state.employeesLoadedMonth === state.payrollMonth && !state.mobileEmployeesLoading"
        )
        if via_board:
            self.page.locator('[data-mobile-view="board"]').click()
        else:
            self.page.locator("#mobileEmployeesBackButton").click()
        self.runtime.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1735}
        )
        if via_board:
            self.page.locator('[data-mobile-view="more"]').click()
        else:
            self.page.locator('[data-mobile-open="employees"]').click()
        self.page.wait_for_function("!state.mobileEmployeesLoading")
        actual = self.page.evaluate(
            "id => state.employees.find(row=>row.id===id)?.balance_total", employee["id"]
        )
        self.assertEqual(actual, "17.35")


if __name__ == "__main__":
    unittest.main()
