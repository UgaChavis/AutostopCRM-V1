"""Profile save ownership and recovery against disposable native employee data."""

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeProfileBrowserTests(UiBrowserTestCase):
    def open_profile(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-id="{self.runtime.employee_id}"]').click()
        self.page.locator("#employeePositionInput").fill("Synthetic profile draft")

    def test_confirmed_profile_delete_does_not_restore_the_removed_employee_fields(self):
        employee = self.runtime.service.save_employee(
            {"name": "Synthetic removable UI employee", "salary_mode": "none"}
        )["employee"]
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-id="{employee["id"]}"]').click()
        self.page.on("dialog", lambda dialog: dialog.accept())
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertNotEqual(self.page.locator("#employeeNameInput").input_value(), employee["name"])
        self.assertEqual(self.writes.count("delete_employee"), 1)

    def test_profile_write_deadline_restores_controls_and_keeps_the_draft(self):
        self.context.add_init_script(
            "const schedule=window.setTimeout; window.setTimeout=(fn,ms,...a)=>schedule(fn,ms===90000?150:ms,...a);"
        )
        self.open_profile()
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route("**/api/save_employee", lambda route: held.append(route))
        self.page.locator("#employeeSaveButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertFalse(self.page.locator("#employeePositionInput").is_disabled())
        self.assertEqual(
            self.page.locator("#employeePositionInput").input_value(), "Synthetic profile draft"
        )
        release()

    def test_late_profile_reply_after_logout_does_not_restore_the_old_employee(self):
        self.open_profile()
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route("**/api/save_employee", lambda route: held.append(route))
        self.page.locator("#employeeSaveButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.evaluate("clearOperatorSession()")
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.activeEmployeeId"), "")
        self.assertEqual(self.page.evaluate("state.employees.length"), 0)
        self.assertFalse(
            self.page.locator("#employeesModal").evaluate("e=>e.classList.contains('is-open')")
        )
        self.assertFalse(self.page.locator("#employeePositionInput").is_disabled())

    def test_lost_profile_create_reply_cannot_create_another_employee(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator("#employeesCreateButton").click()
        self.page.locator("#employeeNameInput").fill("Synthetic uncertain employee")
        self.page.locator("#employeePositionInput").fill("Synthetic draft")
        ids = []

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            ids.append(response.json()["data"]["employee"]["id"])
            route.abort("failed")

        self.page.route("**/api/save_employee", lose_reply)
        for _ in range(2):
            self.page.locator("#employeeSaveButton").click()
            self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(self.writes.count("save_employee"), 1)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(
            self.page.locator("#employeeNameInput").input_value(), "Synthetic uncertain employee"
        )

    def test_known_profile_validation_error_retains_the_draft_for_a_safe_retry(self):
        self.open_profile()
        self.page.route(
            "**/api/save_employee",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic profile validation failure"}}
                ),
            ),
        )
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(
            self.page.locator("#employeePositionInput").input_value(), "Synthetic profile draft"
        )
        self.assertFalse(self.page.locator("#employeePositionInput").is_disabled())
        self.page.unroute("**/api/save_employee")
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(self.writes.count("save_employee"), 2)

    def test_pending_profile_save_freezes_fields_and_parent_navigation(self):
        self.open_profile()
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route("**/api/save_employee", lambda route: held.append(route))
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("Boolean(state.employeeEditOperation)")
        self.assertTrue(self.page.locator("#employeePositionInput").is_disabled())
        self.assertTrue(self.page.locator("#employeeNameInput").is_disabled())
        self.assertFalse(self.page.evaluate("closeNamedModal('employees')"))
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("!state.employeeEditOperation")

    def test_confirmed_profile_save_with_failed_report_refresh_retries_only_reads(self):
        self.open_profile()
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
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(
            self.page.locator("#employeePositionInput").input_value(), "Synthetic profile draft"
        )
        self.page.unroute("**/api/get_payroll_report?**")
        self.page.locator("#employeeSaveButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(self.writes.count("save_employee"), 1)
