"""Salary opening with delayed synthetic ledger reads."""

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeSalaryLoadingBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        self.employee = self.page.evaluate("state.activeEmployeeId")
        self.held = []
        self.page.route(
            "**/api/get_employee_salary_ledger?**", lambda route: self.held.append(route)
        )

    def salary(self):
        return self.page.locator(f'[data-employee-salary="{self.employee}"]')

    def hold_one(self):
        self.salary().click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 1)
        self.page.locator('#employeesList [role="status"]').wait_for()

    def test_pending_survives_rerender_and_error_allows_retry(self):
        self.hold_one()
        self.salary().click()
        self.page.locator(f'[data-employee-id="{self.employee}"]').click()
        self.page.locator('#employeesList [role="status"]').wait_for()
        self.salary().click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 1)
        self.held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic failure"}}
        )
        self.page.wait_for_function("!document.querySelector('#employeesList [role=status]')")
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())
        self.hold_one()
        self.held.pop().continue_()
        self.page.locator("#employeeSalaryModal.is-open").wait_for()
        self.assertEqual(self.page.evaluate("state.employeeSalarySheet.employee_id"), self.employee)
        self.assertEqual(self.page.evaluate("state.activeEmployeeSalaryId"), self.employee)

    def test_employee_roundtrip_allows_retry_and_late_response_is_ignored(self):
        self.page.evaluate(
            """id => {
          state.employees.push({...state.employees.find(item => item.id === id), id: 'synthetic-b'});
        }""",
            self.employee,
        )
        self.page.locator(f'[data-employee-id="{self.employee}"]').click()
        self.hold_one()
        self.page.locator('[data-employee-id="synthetic-b"]').click()
        self.page.locator(f'[data-employee-id="{self.employee}"]').click()
        self.salary().click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 2)
        self.held.pop(0).fulfill(
            status=200, json={"ok": True, "data": {"employee_id": self.employee}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())
        self.assertTrue(self.page.locator('#employeesList [role="status"]').is_visible())
        self.assertEqual(self.page.evaluate("state.activeEmployeeSalaryId"), self.employee)
        self.assertTrue(self.page.evaluate("Boolean(state.employeeSalarySheetRequest)"))
        self.held.pop().continue_()
        self.page.locator("#employeeSalaryModal.is-open").wait_for()

    def test_cancelled_salary_does_not_reopen_on_profile_save(self):
        self.page.locator(f'[data-employee-id="{self.employee}"]').click()
        self.hold_one()
        self.page.locator(f'[data-employee-report="{self.employee}"]').click()
        self.page.locator("#employeeSalaryReconciliationPeriodModal.is-open").wait_for()
        self.held.pop().continue_()
        self.page.wait_for_function("!document.querySelector('#employeesList [role=status]')")
        self.assertEqual(self.page.evaluate("state.activeEmployeeSalaryId"), "")
        self.page.locator("#employeeSalaryReconciliationCancelButton").click()
        self.page.locator("#employeePositionInput").fill("Synthetic saved profile")
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(len(self.held), 0)
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())

    def test_salary_response_after_child_roundtrip_does_not_open(self):
        self.hold_one()
        self.page.locator(f'[data-employee-report="{self.employee}"]').click()
        self.page.locator("#employeeSalaryReconciliationPeriodModal.is-open").wait_for()
        self.page.locator("#employeeSalaryReconciliationCancelButton").click()
        self.held.pop().continue_()
        self.page.wait_for_function("!document.querySelector('#employeesList [role=status]')")
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())
        self.assertEqual(self.page.evaluate("state.activeEmployeeSalaryId"), "")

    def test_month_roundtrip_and_closed_parent_ignore_old_results(self):
        self.hold_one()
        self.page.evaluate("""() => {
          const month = state.payrollMonth;
          prepareEmployeesWorkspaceData(month === '2020-01' ? '2020-02' : '2020-01');
          prepareEmployeesWorkspaceData(month);
        }""")
        self.salary().click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 2)
        self.held.pop(0).fulfill(
            status=503, json={"ok": False, "error": {"message": "Old failure"}}
        )
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('#employeesList [role="status"]').is_visible())
        self.page.locator('#employeesModal [data-close="employees"]').click()
        self.held.pop().fulfill(
            status=200, json={"ok": True, "data": {"employee_id": self.employee}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())

    def test_access_change_and_logout_ignore_old_results(self):
        self.hold_one()
        self.page.evaluate("state.employeesCashboxesAccessRevision += 1")
        self.salary().click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 2)
        self.held.pop(0).fulfill(
            status=503, json={"ok": False, "error": {"message": "Old failure"}}
        )
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('#employeesList [role="status"]').is_visible())
        self.page.evaluate("clearOperatorSession()")
        self.held.pop().fulfill(
            status=200, json={"ok": True, "data": {"employee_id": self.employee}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())

    def test_new_child_dialog_is_not_covered_by_late_salary(self):
        self.hold_one()
        self.page.locator(f'[data-employee-report="{self.employee}"]').click()
        self.page.locator("#employeeSalaryReconciliationPeriodModal.is-open").wait_for()
        self.held.pop().fulfill(
            status=200, json={"ok": True, "data": {"employee_id": self.employee}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#employeeSalaryModal").is_visible())
        self.assertTrue(self.page.locator("#employeeSalaryReconciliationPeriodModal").is_visible())
