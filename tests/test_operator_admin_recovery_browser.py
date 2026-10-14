"""Recover admin writes on disposable users without replaying uncertain mutations."""

from __future__ import annotations

import json

from tests import test_operator_admin_browser as admin_support
from tests.browser_ui_support import UiBrowserTestCase


class OperatorAdminRecoveryBrowserTests(UiBrowserTestCase):
    create_and_open_user = admin_support.OperatorAdminBrowserTests.create_and_open_user
    stored_users = admin_support.OperatorAdminBrowserTests.stored_users
    stored_user = admin_support.OperatorAdminBrowserTests.stored_user
    open_binding = admin_support.OperatorAdminBrowserTests.open_binding
    lose_reply = admin_support.OperatorAdminBrowserTests.lose_reply
    delete_button = admin_support.OperatorAdminBrowserTests.delete_button
    delete = admin_support.OperatorAdminBrowserTests.delete
    wait_delete = admin_support.OperatorAdminBrowserTests.wait_delete
    hold_route = admin_support.OperatorAdminBrowserTests.hold_route
    wait_held = admin_support.OperatorAdminBrowserTests.wait_held

    reject_read = admin_support.OperatorAdminBrowserTests.reject_read
    assert_ready = admin_support.OperatorAdminBrowserTests.assert_ready

    def fill_user(self, username):
        self.page.locator("#adminUserLogin").fill(username)
        self.page.locator("#adminUserPassword").fill("synthetic-probe-changed-password")

    def save_user(self):
        self.page.locator("#adminSaveUserButton").click()
        self.page.wait_for_function("!state.operatorUserSaveRequest")
        self.assertTrue(self.page.locator("#adminSaveUserButton").is_enabled())

    def save_binding(self):
        self.page.locator("#operatorUserEmployeeSaveButton").click()
        self.page.wait_for_function("!state.operatorEmployeeBindingMutationRequest")
        self.assertTrue(self.page.locator("#operatorUserEmployeeSaveButton").is_enabled())

    def test_lost_password_reply_does_not_replay_the_write(self):
        username = self.create_and_open_user("SYNTHETIC_PROBE_PASSWORD", ["employees_read_access"])
        self.fill_user(username)
        self.page.route("**/api/save_operator_user", self.lose_reply)
        self.save_user()
        self.page.unroute("**/api/save_operator_user")
        self.save_user()
        self.assertEqual(self.writes.count("save_operator_user"), 1)
        self.assertEqual(self.page.locator("#adminUserLogin").input_value(), username)
        self.assertEqual(self.stored_user(username)["permissions"], ["employees_read_access"])

    def test_lost_binding_reply_reads_the_assignment_without_repeating_write(self):
        username = "SYNTHETIC_PROBE_BINDING"
        employee = self.open_binding(username)
        self.page.route("**/api/set_operator_user_employee", self.lose_reply)
        self.save_binding()
        self.page.unroute("**/api/set_operator_user_employee")
        if self.page.locator("#operatorUserEmployeeBindingPanel").is_visible():
            self.save_binding()
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)
        self.assertEqual(self.stored_user(username)["employee_id"], employee["id"])
        self.assertFalse(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())

    def test_mismatched_user_receipt_keeps_the_original_editor_draft(self):
        username = self.create_and_open_user("SYNTHETIC_PROBE_BAD_USER")
        self.fill_user(username)

        def mismatch(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            body = response.json()
            body["data"]["user"]["username"] = "SYNTHETIC_WRONG_RECEIPT"
            route.fulfill(response=response, body=json.dumps(body))

        self.page.route("**/api/save_operator_user", mismatch)
        self.save_user()
        self.assertEqual(self.page.locator("#adminUserLogin").input_value(), username)
        self.assertEqual(
            self.page.locator("#adminUserPassword").input_value(),
            "synthetic-probe-changed-password",
        )
        self.assertEqual(self.writes.count("save_operator_user"), 1)

    def test_mismatched_binding_receipt_reconciles_the_actual_assignment(self):
        username = "SYNTHETIC_PROBE_BAD_BINDING"
        employee = self.open_binding(username)

        def mismatch(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            body = response.json()
            body["data"]["user"]["employee_id"] = ""
            route.fulfill(response=response, body=json.dumps(body))

        self.page.route("**/api/set_operator_user_employee", mismatch)
        self.save_binding()
        self.assertEqual(self.stored_user(username)["employee_id"], employee["id"])
        rendered = self.page.evaluate(
            "(name)=>state.operatorUsers.find(user=>user.username===name).employee_id", username
        )
        self.assertEqual(rendered, employee["id"])
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)

    def test_unknown_password_save_blocks_conflicting_delete(self):
        username = self.create_and_open_user("SYNTHETIC_PROBE_SAVE_DELETE")
        self.fill_user(username)
        self.page.route("**/api/save_operator_user", self.lose_reply)
        self.save_user()
        self.delete(username)
        self.assertEqual(self.writes.count("delete_operator_user"), 0)
        self.assertIsNotNone(self.stored_user(username))

    def test_unknown_delete_blocks_conflicting_password_save(self):
        username = self.create_and_open_user("SYNTHETIC_PROBE_DELETE_SAVE")
        self.page.route("**/api/delete_operator_user", lambda route: route.abort("failed"))
        self.delete(username)
        self.fill_user(username)
        self.save_user()
        self.assertEqual(self.writes.count("save_operator_user"), 0)
        self.assertIsNotNone(self.stored_user(username))

    def test_changed_connection_cannot_apply_an_old_save_reply(self):
        username = self.create_and_open_user("SYNTHETIC_PROBE_OLD_CONNECTION")
        self.fill_user(username)
        held = self.hold_route("**/api/save_operator_user")
        self.page.locator("#adminSaveUserButton").click()
        self.wait_held(held)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.evaluate(
            "state.apiToken='synthetic-replacement-token';state.operatorUsers=[{username:'SYNTHETIC_REPLACEMENT',employee_id:'',permissions:[]}]"
        )
        route.fulfill(response=response)
        self.page.wait_for_function("!state.operatorUserSaveRequest")
        self.assertTrue(self.page.locator("#adminSaveUserButton").is_enabled())
        self.assertEqual(
            self.page.evaluate("state.operatorUsers.map(user=>user.username)"),
            ["SYNTHETIC_REPLACEMENT"],
        )

    def test_uncommitted_binding_with_wrong_receipt_keeps_selection(self):
        username = "SYNTHETIC_PROBE_UNCOMMITTED_ACK"
        employee = self.open_binding(username)
        original = self.stored_user(username)
        self.assertEqual(original["employee_id"], "")
        self.page.route(
            "**/api/set_operator_user_employee",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "data": {"user": original, "meta": {"bound": True}}}),
            ),
        )
        self.save_binding()
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), employee["id"]
        )
        self.assertEqual(self.stored_user(username)["employee_id"], "")
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)

    def reject_write(self, route):
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": False,
                    "error": {"code": "synthetic_conflict", "message": "Synthetic rejection"},
                }
            ),
        )

    def test_definite_user_rejection_allows_corrected_explicit_retry(self):
        username = self.create_and_open_user("SYNTHETIC_RECOVERY_SAVE_REJECT")
        before = self.stored_user(username)
        self.fill_user(username)
        self.page.route("**/api/save_operator_user", self.reject_write)
        self.save_user()
        self.assertEqual(self.stored_user(username), before)
        self.assertEqual(self.page.locator("#adminUserLogin").input_value(), username)
        self.page.unroute("**/api/save_operator_user")
        self.save_user()
        self.assertEqual(self.page.locator("#adminUserPassword").input_value(), "")
        self.assertEqual(self.writes.count("save_operator_user"), 2)

    def test_definite_binding_rejection_allows_corrected_explicit_retry(self):
        username = "SYNTHETIC_RECOVERY_BIND_REJECT"
        employee = self.open_binding(username)
        self.page.route("**/api/set_operator_user_employee", self.reject_write)
        self.save_binding()
        self.assertEqual(self.stored_user(username)["employee_id"], "")
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), employee["id"]
        )
        self.page.unroute("**/api/set_operator_user_employee")
        self.save_binding()
        self.assertEqual(self.stored_user(username)["employee_id"], employee["id"])
        self.assertFalse(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(self.writes.count("set_operator_user_employee"), 2)

    def test_lost_binding_and_failed_read_retries_only_the_read(self):
        username = "SYNTHETIC_RECOVERY_BIND_READ"
        employee = self.open_binding(username)
        self.page.route("**/api/set_operator_user_employee", self.lose_reply)
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.save_binding()
        self.assertEqual(self.stored_user(username)["employee_id"], employee["id"])
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), employee["id"]
        )
        self.page.unroute("**/api/list_operator_users")
        self.page.unroute("**/api/set_operator_user_employee")
        self.save_binding()
        self.assertFalse(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)

    def test_changed_selection_is_retained_when_previous_binding_is_reconciled(self):
        second = self.runtime.service.save_employee({"name": "Synthetic second binding target"})[
            "employee"
        ]
        username = "SYNTHETIC_RECOVERY_NEW_SELECTION"
        first = self.open_binding(username)
        self.page.route("**/api/set_operator_user_employee", self.lose_reply)
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.save_binding()
        self.page.unroute("**/api/list_operator_users")
        self.page.unroute("**/api/set_operator_user_employee")
        self.page.locator("#operatorUserEmployeeSelect").select_option(second["id"])
        self.save_binding()
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), second["id"]
        )
        self.assertEqual(self.stored_user(username)["employee_id"], first["id"])
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)
        self.save_binding()
        self.assertEqual(self.stored_user(username)["employee_id"], second["id"])
        self.assertFalse(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(self.writes.count("set_operator_user_employee"), 2)

    def test_recreated_same_username_is_not_old_binding_confirmation(self):
        username = "SYNTHETIC_RECOVERY_RECREATED_BIND"
        employee = self.open_binding(username)
        original = self.stored_user(username)
        self.page.route("**/api/set_operator_user_employee", self.lose_reply)
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.save_binding()
        headers = {"X-Operator-Session": self.session_token}
        removed = self.context.request.post(
            self.runtime.base_url + "/api/delete_operator_user",
            headers=headers,
            data={"username": username},
        ).json()
        self.assertTrue(removed["ok"])
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers=headers,
            data={"username": username, "password": "synthetic-recreated-password"},
        ).json()
        self.assertTrue(created["ok"])
        bound = self.context.request.post(
            self.runtime.base_url + "/api/set_operator_user_employee",
            headers=headers,
            data={"username": username, "employee_id": employee["id"]},
        ).json()
        self.assertTrue(bound["ok"])
        self.assertNotEqual(self.stored_user(username)["created_at"], original["created_at"])
        self.page.unroute("**/api/list_operator_users")
        self.page.unroute("**/api/set_operator_user_employee")
        self.save_binding()
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), employee["id"]
        )
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)

    def test_uncommitted_password_reply_blocks_new_password_replay(self):
        username = self.create_and_open_user("SYNTHETIC_RECOVERY_NO_SAVE")
        before = self.stored_user(username)
        self.fill_user(username)
        self.page.route("**/api/save_operator_user", lambda route: route.abort("failed"))
        self.save_user()
        self.page.unroute("**/api/save_operator_user")
        self.page.locator("#adminUserPassword").fill("synthetic-different-retry-password")
        self.save_user()
        self.assertEqual(self.writes.count("save_operator_user"), 1)
        self.assertEqual(self.stored_user(username), before)
        self.assertEqual(
            self.page.locator("#adminUserPassword").input_value(),
            "synthetic-different-retry-password",
        )

    def test_late_binding_read_does_not_replace_new_user_panel(self):
        username = "SYNTHETIC_RECOVERY_OLD_BINDING_READ"
        other = "SYNTHETIC_RECOVERY_NEXT_BINDING_USER"
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={"username": other, "password": "synthetic-next-user-password"},
        ).json()
        self.assertTrue(created["ok"])
        self.open_binding(username)
        self.page.route("**/api/set_operator_user_employee", self.lose_reply)
        held = self.hold_route("**/api/list_operator_users")
        self.page.locator("#operatorUserEmployeeSaveButton").click()
        self.wait_held(held)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.locator("#operatorAdminCloseButton").click()
        self.page.locator(f'[data-bind-operator-employee="{other}"]').click()
        self.page.wait_for_function("name=>state.operatorEmployeeBindingUser===name", arg=other)
        route.fulfill(response=response)
        self.page.wait_for_function("!state.operatorEmployeeBindingMutationRequest")
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertEqual(self.page.evaluate("state.operatorEmployeeBindingUser"), other)
        self.assertEqual(self.page.locator("#operatorUserEmployeeSelect").input_value(), "")
        self.assertEqual(self.writes.count("set_operator_user_employee"), 1)

    def test_unknown_binding_blocks_password_save_and_delete(self):
        username = "SYNTHETIC_RECOVERY_BINDING_CONFLICT"
        self.open_binding(username)
        before = self.stored_user(username)
        self.page.route("**/api/set_operator_user_employee", lambda route: route.abort("failed"))
        self.save_binding()
        self.page.locator("#operatorAdminCloseButton").click()
        self.fill_user(username)
        self.save_user()
        self.delete(username)
        self.assertEqual(self.writes.count("save_operator_user"), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 0)
        self.assertEqual(self.stored_user(username), before)
        self.assert_ready()

    def test_logout_clears_uncertain_save_owner_before_new_login(self):
        username = self.create_and_open_user("SYNTHETIC_RECOVERY_LOGOUT")
        self.fill_user(username)
        self.page.route("**/api/save_operator_user", lambda route: route.abort("failed"))
        self.save_user()
        self.page.unroute("**/api/save_operator_user")
        self.page.evaluate("clearOperatorSession({openLogin:true})")
        self.page.locator("#identityInput").fill("admin")
        self.page.locator("#identityPassword").fill("admin")
        self.page.locator("#identitySave").click()
        self.page.wait_for_function("state.operatorSessionToken && !state.operatorLoginRequest")
        self.page.locator("#operatorProfileModal.is-open").wait_for()
        self.page.locator("#operatorAdminButton").click()
        self.page.wait_for_function(
            "name=>state.operatorUsers.some(u=>u.username===name)", arg=username
        )
        self.fill_user(username)
        self.save_user()
        self.assertEqual(self.page.locator("#adminUserPassword").input_value(), "")
        self.assertEqual(self.writes.count("save_operator_user"), 2)
