import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeWorkspaceRecoveryBrowserTests(UiBrowserTestCase):
    def hold(self, held):
        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        return lambda route: held.append(route)

    def await_held(self, held):
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def open_reset(self):
        employee_id = self.runtime.salary_reset_employee_id
        self.runtime.service.create_employee_shift_accrual(
            {"employee_id": employee_id, "amount": "17.35", "note": "Synthetic reset UI case"}
        )
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-salary="{employee_id}"]').click()
        self.page.locator("#employeeSalaryModal.is-open").wait_for()
        self.page.wait_for_function(
            "state.employeeSalarySheet && state.employeeSalarySheet.balance_minor !== 0"
        )
        self.page.on("dialog", lambda dialog: dialog.accept())

    def test_reset_write_deadline_releases_its_owned_controls(self):
        self.context.add_init_script(
            "const schedule=window.setTimeout; window.setTimeout=(fn,ms,...a)=>schedule(fn,ms===90000?150:ms,...a);"
        )
        self.open_reset()
        held = []
        self.page.route("**/api/reset_employee_salary_balance", self.hold(held))
        self.page.locator("#employeeSalaryResetButton").click()
        self.await_held(held)
        self.page.wait_for_function("!state.employeeSalaryResetPending", timeout=3000)
        self.assertFalse(self.page.locator("#employeeSalaryPayoutButton").is_disabled())
        held.pop().abort("failed")

    def test_lost_reset_reply_reads_the_current_ledger_before_another_action(self):
        self.open_reset()
        ledger_reads = []

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/api/reset_employee_salary_balance", lose_reply)
        self.page.on(
            "request",
            lambda request: (
                ledger_reads.append(request.url)
                if "/api/get_employee_salary_ledger?" in request.url
                else None
            ),
        )
        self.page.locator("#employeeSalaryResetButton").click()
        self.page.wait_for_function("!state.employeeSalaryResetPending")
        self.assertTrue(ledger_reads)
        self.assertEqual(self.page.evaluate("state.employeeSalarySheet.balance_minor"), 0)
        self.assertEqual(self.writes.count("reset_employee_salary_balance"), 1)

    def warm_employees(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function(
            "state.employeesWorkspaceLoading === false && state.employeesReferencePromise === null"
        )
        self.page.locator('[data-close="employees"]').click()
        self.assertFalse(
            self.page.locator("#employeesModal").evaluate(
                "node => node.classList.contains('is-open')"
            )
        )
        self.page.evaluate("""() => {
          const module=boardModuleRecords.get('payroll').exports;
          const open=module.openEmployeesModal;
          module.openEmployeesModal=(...args)=>{
            window.employeeOpenDone=false;
            return Promise.resolve(open(...args)).finally(()=>{window.employeeOpenDone=true;});
          };
        }""")

    def test_late_workspace_load_does_not_cover_a_newer_clients_window(self):
        self.warm_employees()
        held = []
        self.page.route("**/api/list_employees?**", self.hold(held))
        self.page.locator("#employeesButton").click()
        self.await_held(held)
        self.page.locator('[data-close="employees"]').click()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for()
        held.pop().continue_()
        self.page.wait_for_function("window.employeeOpenDone === true")
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1)?.key"), "clients")

    def test_old_workspace_error_after_logout_cannot_replace_the_login_status(self):
        self.warm_employees()
        held = []
        self.page.route("**/api/list_employees?**", self.hold(held))
        self.page.locator("#employeesButton").click()
        self.await_held(held)
        self.page.evaluate("clearOperatorSession()")
        message = self.page.locator("#statusLine").text_content()
        held.pop().fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": {"message": "Synthetic stale report failure"}}),
        )
        self.page.wait_for_function("window.employeeOpenDone === true")
        self.assertEqual(self.page.locator("#statusLine").text_content(), message)
