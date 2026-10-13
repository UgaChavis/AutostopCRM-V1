"""Employee list reads must render fresh balances without duplicate report requests."""

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeLoadingBrowserTests(UiBrowserTestCase):
    def test_read_deadline_releases_the_request_and_retry_can_load_fresh_data(self):
        self.load_board()
        self.page.evaluate(
            "window.employeeReadTimeoutOriginal=apiReadTimeoutMs; apiReadTimeoutMs=()=>80"
        )
        held = []
        self.page.route("**/api/list_employees?**", lambda route: held.append(route))
        self.page.locator("#employeesButton").click()
        retry = self.page.locator("#employeesList button")
        retry.wait_for(timeout=5000)
        self.assertTrue(held)
        self.assertTrue(self.page.locator("#employeesProfilePanel").evaluate("node => node.inert"))
        self.assertIsNone(self.page.evaluate("state.employeesReferencePromise"))
        self.page.evaluate("apiReadTimeoutMs=window.employeeReadTimeoutOriginal")
        for route in held:
            route.abort("failed")
        self.page.unroute("**/api/list_employees?**")
        retry.click()
        self.page.wait_for_function(
            "state.employeesWorkspaceLoading === false && state.employeesLoadedMonth === state.payrollMonth"
        )
        self.page.locator("#employeesList [data-employee-id]").first.wait_for()
        self.assertEqual(self.writes.count("save_employee"), 0)

    def test_loading_shell_is_closable_and_cannot_send_profile_or_salary_writes(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_employees?**", lambda route: held.append(route))
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open[aria-busy='true']").wait_for()
        self.page.wait_for_timeout(50)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#employeesCreateButton").is_disabled())
        self.assertTrue(self.page.locator("#employeesProfilePanel").evaluate("node => node.inert"))
        self.assertEqual(self.writes.count("save_employee"), 0)
        self.page.locator('[data-close="employees"]').click()
        held.pop().continue_()
        self.page.wait_for_function("state.employeesReferencePromise === null")
        self.assertFalse(
            self.page.locator("#employeesModal").evaluate(
                "node => node.classList.contains('is-open')"
            )
        )

    def test_failed_read_keeps_inert_fields_and_retry_reads_without_writing(self):
        self.load_board()
        self.page.route(
            "**/api/list_employees?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"ok":false,"error":{"message":"Synthetic employee read failure"}}',
            ),
        )
        self.page.locator("#employeesButton").click()
        retry = self.page.locator("#employeesList button")
        retry.wait_for()
        self.assertIn(
            "Synthetic employee read failure", self.page.locator("#employeesList").inner_text()
        )
        self.assertTrue(self.page.locator("#employeesProfilePanel").evaluate("node => node.inert"))
        self.page.unroute("**/api/list_employees?**")
        retry.click()
        self.page.wait_for_function(
            "state.employeesWorkspaceLoading === false && state.employeesLoadedMonth === state.payrollMonth"
        )
        self.page.locator("#employeesList [data-employee-id]").first.wait_for()
        self.assertFalse(self.page.locator("#employeesProfilePanel").evaluate("node => node.inert"))
        self.assertEqual(self.writes.count("save_employee"), 0)
        self.assertEqual(self.writes.count("create_employee_salary_transaction"), 0)

    def open_employees(self):
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")

    def test_reopening_reads_another_users_new_balance(self):
        employee = self.runtime.service.save_employee(
            {"name": "Synthetic fresh balance", "salary_mode": "none"}
        )["employee"]
        self.load_board()
        self.open_employees()
        self.page.locator('[data-close="employees"]').click()
        self.runtime.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1735}
        )
        self.open_employees()
        actual = self.page.evaluate(
            "id => state.employees.find(row=>row.id===id)?.balance_total", employee["id"]
        )
        expected = self.runtime.service.get_employee_salary_ledger({"employee_id": employee["id"]})[
            "balance_total"
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "17.35")

    def test_duplicate_month_events_share_one_full_response_and_its_report(self):
        self.load_board()
        self.open_employees()
        reads = []
        self.page.on(
            "request",
            lambda request: (
                reads.append(request.url)
                if "/api/list_employees?" in request.url
                or "/api/get_payroll_report?" in request.url
                else None
            ),
        )
        held = []
        self.page.route("**/api/list_employees?**", lambda route: held.append(route))
        self.page.locator("#employeesMonthInput").fill("2026-09")
        self.page.locator("#employeesMonthInput").dispatch_event("change")
        self.page.wait_for_function("state.payrollMonth === '2026-09'")
        self.page.wait_for_timeout(50)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("state.payrollReportMonth === '2026-09'")
        self.assertEqual(sum("/api/list_employees?" in path for path in reads), 1)
        self.assertEqual(sum("/api/get_payroll_report?" in path for path in reads), 0)
