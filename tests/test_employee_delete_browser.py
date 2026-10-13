import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeDeleteRecoveryBrowserTests(UiBrowserTestCase):
    def open_target(self):
        employee = self.runtime.service.save_employee(
            {"name": "Synthetic removable recovery employee", "salary_mode": "none"}
        )["employee"]
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.locator(f'[data-employee-id="{employee["id"]}"]').click()
        self.page.on("dialog", lambda dialog: dialog.accept())
        return employee["id"]

    def test_unknown_profile_save_prevents_conflicting_delete_until_its_result_is_known(self):
        self.context.add_init_script(
            "const schedule=window.setTimeout; window.setTimeout=(fn,ms,...a)=>schedule(fn,ms===90000?150:ms,...a);"
        )
        employee_id = self.open_target()
        self.page.locator("#employeePositionInput").fill("Synthetic pending profile write")
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
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertEqual(self.writes.count("delete_employee"), 0)
        self.assertTrue(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        release()

    def test_native_delete_conflict_preserves_the_record_and_can_retry_after_readback(self):
        employee_id = self.open_target()
        self.runtime.service.save_employee(
            {
                "employee_id": employee_id,
                "name": "Synthetic removable recovery employee",
                "position": "Synthetic peer edit",
            }
        )
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertTrue(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.assertFalse(self.page.locator("#employeeNameInput").is_disabled())
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertFalse(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.assertEqual(self.writes.count("delete_employee"), 2)

    def test_native_linked_employee_delete_guard_stays_unchanged(self):
        employee_id = self.open_target()
        self.runtime.service.create_employee_shift_accrual(
            {
                "employee_id": employee_id,
                "amount": "17.35",
                "note": "Synthetic protected delete case",
            }
        )
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertTrue(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.assertFalse(self.page.locator("#employeeNameInput").is_disabled())
        self.assertIn("нельзя удалить", self.page.locator("#statusLine").text_content())

    def test_uncommitted_delete_can_retry_with_its_existing_native_version_guard(self):
        employee_id = self.open_target()
        bodies = []

        def lose_before_commit(route):
            bodies.append(route.request.post_data_json)
            route.abort("failed")

        self.page.route("**/api/delete_employee", lose_before_commit)
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertTrue(bodies[0]["expected_updated_at"])
        self.assertTrue(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.page.unroute("**/api/delete_employee")
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertFalse(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        late = self.context.request.post(
            self.runtime.base_url + "/api/delete_employee",
            headers={"X-Operator-Session": self.session_token},
            data=bodies[0],
        )
        self.assertEqual(late.status, 404)

    def test_confirmed_delete_with_failed_report_refresh_keeps_the_removed_record_out(self):
        employee_id = self.open_target()
        self.page.route(
            "**/api/get_payroll_report?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic report failure after delete"}}
                ),
            ),
        )
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertFalse(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.assertNotEqual(
            self.page.locator("#employeeNameInput").input_value(),
            "Synthetic removable recovery employee",
        )
        self.assertEqual(self.writes.count("delete_employee"), 1)

    def test_logout_ignores_a_late_confirmed_delete_reply(self):
        self.open_target()
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route("**/api/delete_employee", lambda route: held.append(route))
        self.page.locator("#employeeDeleteButton").click()
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
        self.assertEqual(self.page.evaluate("state.employees.length"), 0)
        self.assertEqual(self.page.evaluate("state.activeEmployeeId"), "")
        self.assertFalse(self.page.locator("#employeeNameInput").is_disabled())

    def test_delete_deadline_releases_fields_without_a_blind_repeat(self):
        self.context.add_init_script(
            "const schedule=window.setTimeout; window.setTimeout=(fn,ms,...a)=>schedule(fn,ms===90000?150:ms,...a);"
        )
        self.open_target()
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route("**/api/delete_employee", lambda route: held.append(route))
        self.page.locator("#employeeDeleteButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.page.wait_for_function("!state.employeeEditOperation", timeout=3000)
        self.assertFalse(self.page.locator("#employeeNameInput").is_disabled())
        self.assertEqual(self.writes.count("delete_employee"), 1)
        release()

    def test_lost_confirmed_delete_reply_reconciles_the_known_employee_id(self):
        employee_id = self.open_target()

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/api/delete_employee", lose_reply)
        self.page.locator("#employeeDeleteButton").click()
        self.page.wait_for_function("!state.employeeEditOperation")
        self.assertFalse(self.page.evaluate("id=>state.employees.some(e=>e.id===id)", employee_id))
        self.assertEqual(self.writes.count("delete_employee"), 1)
