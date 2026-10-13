"""Real profile input limits must preserve drafts and the native stored name."""

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeFieldsBrowserTests(UiBrowserTestCase):
    def open_new_employee(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        self.page.locator("#employeesCreateButton").click()

    def test_empty_name_keeps_new_profile_open_without_a_write(self):
        self.open_new_employee()
        self.page.locator("#employeePositionInput").fill("Синтетический черновик")
        self.page.locator("#employeeSaveButton").click()
        self.assertEqual(self.writes.count("save_employee"), 0)
        self.assertEqual(
            self.page.locator("#employeePositionInput").input_value(), "Синтетический черновик"
        )
        self.assertTrue(self.page.locator("#employeesModal.is-open").is_visible())

    def test_overlong_combined_name_keeps_both_fields_without_a_truncated_write(self):
        self.open_new_employee()
        first, middle = "А" * 60, "Б" * 60
        self.page.locator("#employeeNameInput").fill(first)
        self.page.locator("#employeeMiddleNameInput").fill(middle)
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(self.writes.count("save_employee"), 0)
        self.assertEqual(self.page.locator("#employeeNameInput").input_value(), first)
        self.assertEqual(self.page.locator("#employeeMiddleNameInput").input_value(), middle)
        self.assertFalse(self.page.locator("#employeeSaveButton").is_disabled())

    def test_name_at_the_native_unicode_limit_is_saved_without_loss(self):
        self.open_new_employee()
        first, middle = "🚗" * 40, "Б" * 39
        self.page.locator("#employeeNameInput").fill(first)
        self.page.locator("#employeeMiddleNameInput").fill(middle)
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        employee_id = self.page.evaluate("state.activeEmployeeId")
        stored = next(
            row
            for row in self.runtime.service.list_employees({"references_only": True})["employees"]
            if row["id"] == employee_id
        )
        self.assertEqual(stored["name"], first + " " + middle)
        self.assertEqual(self.writes.count("save_employee"), 1)

    def test_new_employee_without_accruals_has_zero_balance_and_empty_history(self):
        self.open_new_employee()
        self.page.locator("#employeeNameInput").fill("Синтетический новый сотрудник")
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        employee_id = self.page.evaluate("state.activeEmployeeId")
        with self.page.expect_response("**/api/get_employee_salary_ledger?**") as response:
            self.page.locator(f'[data-employee-salary="{employee_id}"]').click()
        self.page.locator("#employeeSalaryModal.is-open").wait_for()
        sheet = self.page.evaluate("state.employeeSalarySheet")
        native = response.value.json()["data"]
        self.assertEqual(sheet, native)
        self.assertEqual(sheet["employee_id"], employee_id)
        self.assertEqual(sheet["balance_minor"], 0)
        self.assertEqual(sheet["journal_rows"], [])
        self.assertIn("движений нет", self.page.locator("#employeeSalaryJournalTable").inner_text())
        self.assertTrue(self.page.locator("#employeeSalaryResetButton").is_disabled())
