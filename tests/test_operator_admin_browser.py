"""Operator-admin UI recovery on disposable users; native access rules are unchanged."""

from __future__ import annotations

import json
import time

from tests.browser_ui_support import UiBrowserTestCase
from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.web_assets import BOARD_WEB_APP_MODULE_MANIFEST  # noqa: E402


class OperatorAdminBrowserTests(UiBrowserTestCase):
    def create_and_open_user(self, username="SYNTHETIC_OPERATOR", permissions=None):
        body = {"username": username, "password": "synthetic-local-password", "source": "ui"}
        if permissions is not None:
            body.update(permissions=permissions, expected_permissions=[])
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data=body,
        ).json()
        self.assertTrue(response["ok"])
        self.load_board()
        self.page.locator("#operatorButton").click()
        self.page.locator("#operatorAdminButton").click()
        self.page.locator("#operatorAdminModal.is-open").wait_for()
        self.page.wait_for_function(
            "name=>state.operatorUsers.some(u=>u.username===name)", arg=username
        )
        return username

    def stored_users(self):
        return self.context.request.get(
            self.runtime.base_url + "/api/list_operator_users",
            headers={"X-Operator-Session": self.session_token},
        ).json()["data"]["users"]

    def stored_user(self, username):
        return next((user for user in self.stored_users() if user["username"] == username), None)

    def delete_button(self, username):
        return self.page.locator(f'[data-delete-operator-user="{username}"]')

    def delete(self, username):
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.delete_button(username).click()
        self.wait_delete()

    def wait_delete(self):
        self.page.wait_for_function("!state.operatorDeleteRequest")

    def hold_route(self, pattern):
        held = []
        self.page.route(pattern, lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        return held

    def wait_held(self, held):
        until = time.monotonic() + 5
        while not held and time.monotonic() < until:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def lose_reply(self, route):
        response = route.fetch()
        self.assertEqual(response.status, 200)
        route.abort("failed")

    def reject_read(self, route):
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": False,
                    "error": {"message": "Synthetic list failure"},
                }
            ),
        )

    def assert_ready(self):
        self.assertTrue(self.page.locator("#adminUserLogin").is_enabled())
        self.assertTrue(self.page.locator("#operatorAdminCloseButton").is_enabled())

    def shorten_write_deadline(self):
        self.page.evaluate("""()=>{const original=globalThis.setTimeout;
          globalThis.setTimeout=(callback,delay,...args)=>original(callback,delay===90000?80:delay,...args)}""")

    def test_pending_delete_locks_actions_and_preserves_other_editor_draft(self):
        username = self.create_and_open_user("SYNTHETIC_PENDING_DELETE")
        self.page.locator("#adminUserLogin").fill("SYNTHETIC_OTHER_DRAFT")
        self.page.locator("#adminUserPassword").fill("synthetic-other-draft")
        held = self.hold_route("**/api/delete_operator_user")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.delete_button(username).click()
        self.wait_held(held)
        self.assertTrue(self.delete_button(username).is_disabled())
        self.assertTrue(self.page.locator("#adminSaveUserButton").is_disabled())
        self.assertTrue(self.page.locator("#adminUserLogin").is_disabled())
        self.assertFalse(self.page.evaluate("closeNamedModal('operator-admin')"))
        self.assertFalse(self.page.evaluate("closeOperatorAdminModal()"))
        self.page.evaluate(
            "name=>{void deleteOperatorUser(name);void saveOperatorUser()}", username
        )
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assertEqual(self.writes.count("save_operator_user"), 0)
        held.pop().continue_()
        self.wait_delete()
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(
            self.page.locator("#adminUserLogin").input_value(), "SYNTHETIC_OTHER_DRAFT"
        )
        self.assertEqual(
            self.page.locator("#adminUserPassword").input_value(), "synthetic-other-draft"
        )
        self.assert_ready()

    def test_definite_rejection_restores_controls_and_keeps_account(self):
        username = self.create_and_open_user("SYNTHETIC_REJECT_DELETE")
        self.page.route(
            "**/api/delete_operator_user",
            lambda route: route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "synthetic_conflict", "message": "Synthetic rejection"},
                    }
                ),
            ),
        )
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.assertTrue(self.delete_button(username).is_enabled())
        self.assert_ready()
        self.page.unroute("**/api/delete_operator_user")
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 2)

    def test_lost_delete_reply_reconciles_absence_without_repeating_delete(self):
        username = self.create_and_open_user("SYNTHETIC_LOST_DELETE")
        self.page.route("**/api/delete_operator_user", self.lose_reply)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.delete_button(username).count(), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assert_ready()

    def test_confirmed_delete_with_failed_refresh_retries_only_read(self):
        username = self.create_and_open_user("SYNTHETIC_CONFIRMED_DELETE")
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.delete_button(username).count(), 1)
        self.assert_ready()
        self.page.unroute("**/api/list_operator_users")
        self.delete(username)
        self.assertEqual(self.delete_button(username).count(), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_unknown_delete_with_unavailable_read_recovers_only_after_fresh_absence(self):
        username = self.create_and_open_user("SYNTHETIC_UNKNOWN_DELETE")
        self.page.route("**/api/delete_operator_user", self.lose_reply)
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assert_ready()
        self.page.unroute("**/api/list_operator_users")
        self.page.unroute("**/api/delete_operator_user")
        self.delete(username)
        self.assertEqual(self.delete_button(username).count(), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_unknown_uncommitted_delete_is_held_without_native_revision_guard(self):
        username = self.create_and_open_user("SYNTHETIC_UNCOMMITTED_DELETE")
        self.page.route("**/api/delete_operator_user", lambda route: route.abort("failed"))
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.page.unroute("**/api/delete_operator_user")
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assert_ready()

    def test_recreated_same_username_is_kept_after_old_confirmed_delete(self):
        username = self.create_and_open_user("SYNTHETIC_RECREATED_OPERATOR")
        self.page.route("**/api/list_operator_users", self.reject_read)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": username,
                "password": "synthetic-new-account-password",
                "source": "ui",
            },
        ).json()
        self.assertTrue(response["ok"])
        self.page.unroute("**/api/list_operator_users")
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assert_ready()

    def test_immediate_peer_recreation_is_kept_after_confirmed_receipt(self):
        username = self.create_and_open_user("SYNTHETIC_FAST_RECREATION")

        def recreate_before_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            recreated = self.context.request.post(
                self.runtime.base_url + "/api/save_operator_user",
                headers={"X-Operator-Session": self.session_token},
                data={
                    "username": username,
                    "password": "synthetic-recreated-password",
                    "source": "ui",
                },
            ).json()
            self.assertTrue(recreated["ok"])
            route.fulfill(response=response)

        self.page.route("**/api/delete_operator_user", recreate_before_reply)
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.delete(username)
        self.assertIsNotNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assert_ready()

    def test_changed_connection_token_releases_owned_controls_without_applying_old_result(self):
        username = self.create_and_open_user("SYNTHETIC_CONNECTION_SWAP")
        held = self.hold_route("**/api/delete_operator_user")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.delete_button(username).click()
        self.wait_held(held)
        self.page.evaluate("state.apiToken='synthetic-replacement-token'")
        held.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "data": {"deleted": True, "username": username},
                }
            ),
        )
        self.page.wait_for_timeout(100)
        self.assert_ready()
        self.assertFalse(self.page.evaluate("Boolean(state.operatorDeleteRequest)"))
        self.assertTrue(
            self.page.evaluate("name=>state.operatorUsers.some(u=>u.username===name)", username)
        )

    def test_write_deadline_aborts_request_restores_controls_and_holds_replay(self):
        username = self.create_and_open_user("SYNTHETIC_DELETE_DEADLINE")
        self.shorten_write_deadline()
        held = self.hold_route("**/api/delete_operator_user")
        failed = []
        self.page.on(
            "requestfailed",
            lambda request: (
                failed.append(request.url)
                if request.url.endswith("/api/delete_operator_user")
                else None
            ),
        )
        self.delete(username)
        self.assertEqual(len(held), 1)
        self.assertEqual(len(failed), 1)
        self.assertIsNotNone(self.stored_user(username))
        self.assert_ready()
        held.pop().abort("failed")
        self.page.unroute("**/api/delete_operator_user")
        self.delete(username)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_mismatched_receipt_requires_fresh_native_absence(self):
        username = self.create_and_open_user("SYNTHETIC_BAD_RECEIPT")

        def malformed(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.fulfill(
                response=response,
                body=json.dumps({"ok": True, "data": {"deleted": True, "username": "OTHER"}}),
            )

        self.page.route("**/api/delete_operator_user", malformed)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.delete_button(username).count(), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_logout_releases_controls_and_late_delete_cannot_replace_new_viewer(self):
        username = self.create_and_open_user("SYNTHETIC_OLD_VIEWER_DELETE")
        held = self.hold_route("**/api/delete_operator_user")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.delete_button(username).click()
        self.wait_held(held)
        self.page.evaluate(
            "clearOperatorSession({openLogin:false});state.operatorUsers=[{username:'SYNTHETIC_REPLACEMENT'}]"
        )
        self.assert_ready()
        held.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "data": {"deleted": True, "username": username},
                }
            ),
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(
            self.page.evaluate("state.operatorUsers[0].username"), "SYNTHETIC_REPLACEMENT"
        )
        self.assertFalse(self.page.evaluate("Boolean(state.operatorDeleteRequest)"))

    def test_cold_module_holds_controls_and_captured_target_until_loaded(self):
        username = self.create_and_open_user("SYNTHETIC_COLD_DELETE")
        held = self.hold_route("**" + BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"])
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.delete_button(username).click()
        self.wait_held(held)
        self.assertTrue(self.delete_button(username).is_disabled())
        self.assertEqual(self.writes.count("delete_operator_user"), 0)
        self.assertFalse(self.page.evaluate("closeNamedModal('operator-admin')"))
        held.pop().continue_()
        self.wait_delete()
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_native_active_admin_guard_preserves_account_and_permissions(self):
        self.create_and_open_user("SYNTHETIC_ADMIN_GUARD")
        before = self.stored_user("ADMIN")
        self.delete("ADMIN")
        self.assertEqual(self.stored_user("ADMIN"), before)
        self.assertTrue(self.delete_button("ADMIN").is_enabled())
        self.assert_ready()

    def test_stalled_module_releases_controls_and_late_load_cannot_delete(self):
        username = self.create_and_open_user("SYNTHETIC_MODULE_TIMEOUT")
        self.page.evaluate("""()=>{const original=globalThis.setTimeout;globalThis.__adminOriginalTimeout=original;
          globalThis.setTimeout=(callback,delay,...args)=>original(callback,delay===15000?80:delay,...args)}""")
        held = self.hold_route("**" + BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"])
        self.delete(username)
        self.assertEqual(len(held), 1)
        self.assert_ready()
        self.assertEqual(self.writes.count("delete_operator_user"), 0)
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertIsNotNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 0)
        self.page.unroute("**" + BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"])
        self.page.evaluate("globalThis.setTimeout=globalThis.__adminOriginalTimeout")
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.writes.count("delete_operator_user"), 1)

    def test_peer_delete_before_click_refreshes_native_absence(self):
        username = self.create_and_open_user("SYNTHETIC_PEER_DELETE")
        response = self.context.request.post(
            self.runtime.base_url + "/api/delete_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={"username": username},
        ).json()
        self.assertTrue(response["ok"])
        self.assertEqual(self.delete_button(username).count(), 1)
        self.delete(username)
        self.assertIsNone(self.stored_user(username))
        self.assertEqual(self.delete_button(username).count(), 0)
        self.assertEqual(self.writes.count("delete_operator_user"), 1)
        self.assert_ready()

    def open_binding(self, username):
        employee = self.runtime.service.save_employee({"name": "Synthetic binding employee"})[
            "employee"
        ]
        self.create_and_open_user(username)
        self.page.locator(f'[data-bind-operator-employee="{username}"]').click()
        self.page.locator("#operatorUserEmployeeBindingPanel:not(.hidden)").wait_for()
        self.page.locator("#operatorUserEmployeeSelect").select_option(employee["id"])
        return employee

    def test_binding_roundtrip_preserves_native_payload_and_assignment(self):
        username = "SYNTHETIC_BIND_ROUNDTRIP"
        employee = self.open_binding(username)
        bodies = []
        self.page.on(
            "request",
            lambda request: (
                bodies.append(request.post_data_json)
                if request.url.endswith("/api/set_operator_user_employee")
                else None
            ),
        )
        self.page.locator("#operatorUserEmployeeSaveButton").click()
        self.page.wait_for_function("!state.operatorEmployeeBindingMutationRequest")
        self.assertEqual(
            bodies, [{"username": username, "employee_id": employee["id"], "source": "ui"}]
        )
        self.assertEqual(self.stored_user(username)["employee_id"], employee["id"])
        self.assertFalse(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())

    def test_binding_deadline_preserves_selection_and_releases_controls(self):
        username = "SYNTHETIC_BIND_TIMEOUT"
        employee = self.open_binding(username)
        self.shorten_write_deadline()
        held = self.hold_route("**/api/set_operator_user_employee")
        self.page.locator("#operatorUserEmployeeSaveButton").click()
        self.page.wait_for_function("!state.operatorEmployeeBindingMutationRequest")
        self.assertEqual(len(held), 1)
        self.assertEqual(self.stored_user(username)["employee_id"], "")
        self.assertTrue(self.page.locator("#operatorUserEmployeeBindingPanel").is_visible())
        self.assertTrue(self.page.locator("#operatorUserEmployeeSelect").is_enabled())
        self.assertEqual(
            self.page.locator("#operatorUserEmployeeSelect").input_value(), employee["id"]
        )

    def test_password_only_save_preserves_permissions_and_native_login(self):
        username = self.create_and_open_user("SYNTHETIC_PASSWORD_ONLY", ["employees_read_access"])
        self.page.locator("#adminUserLogin").fill(username)
        self.page.locator("#adminUserPassword").fill("synthetic-changed-password")
        bodies = []
        self.page.on(
            "request",
            lambda request: (
                bodies.append(request.post_data_json)
                if request.url.endswith("/api/save_operator_user")
                else None
            ),
        )
        self.page.locator("#adminSaveUserButton").click()
        self.page.wait_for_function("!state.operatorUserSaveRequest")
        self.assertEqual(len(bodies), 1)
        self.assertNotIn("permissions", bodies[0])
        self.assertNotIn("expected_permissions", bodies[0])
        self.assertEqual(self.stored_user(username)["permissions"], ["employees_read_access"])
        login = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={
                "username": username,
                "password": "synthetic-changed-password",
            },
        ).json()
        self.assertTrue(login["ok"])

    def test_explicit_permission_save_keeps_original_native_compare_and_swap(self):
        username = self.create_and_open_user("SYNTHETIC_PERMISSION_EDIT", ["employees_read_access"])
        self.page.locator(f'[data-edit-operator-permissions="{username}"]').click()
        self.page.locator("#adminUserEmployeesCashboxesAccess").check()
        bodies = []
        self.page.on(
            "request",
            lambda request: (
                bodies.append(request.post_data_json)
                if request.url.endswith("/api/save_operator_user")
                else None
            ),
        )
        self.page.locator("#adminSaveUserButton").click()
        self.page.wait_for_function("!state.operatorUserSaveRequest")
        self.assertEqual(bodies[0]["expected_permissions"], ["employees_read_access"])
        self.assertEqual(bodies[0]["permissions"], ["employees_cashboxes_access"])
        self.assertEqual(self.stored_user(username)["permissions"], ["employees_cashboxes_access"])

    def test_save_deadline_restores_button_and_preserves_uncommitted_draft(self):
        self.create_and_open_user("SYNTHETIC_SAVE_TIMEOUT_SETUP")
        self.page.locator("#adminUserLogin").fill("SYNTHETIC_SAVE_TIMEOUT_NEW")
        self.page.locator("#adminUserPassword").fill("synthetic-unsaved-password")
        self.shorten_write_deadline()
        held = self.hold_route("**/api/save_operator_user")
        self.page.locator("#adminSaveUserButton").click()
        self.page.wait_for_function("!state.operatorUserSaveRequest")
        self.assertEqual(len(held), 1)
        self.assertIsNone(self.stored_user("SYNTHETIC_SAVE_TIMEOUT_NEW"))
        self.assertEqual(
            self.page.locator("#adminUserLogin").input_value(), "SYNTHETIC_SAVE_TIMEOUT_NEW"
        )
        self.assertEqual(
            self.page.locator("#adminUserPassword").input_value(), "synthetic-unsaved-password"
        )
        self.assertTrue(self.page.locator("#adminSaveUserButton").is_enabled())
