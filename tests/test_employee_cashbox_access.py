from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for import_path in (SRC, TESTS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import test_api as api_test_module  # noqa: E402

from minimal_kanban.api.route_registry import (  # noqa: E402
    EMPLOYEES_CASHBOXES_PERMISSION_ROUTES,
    policy_for_route,
)
from minimal_kanban.operator_permissions import (  # noqa: E402
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
)

EXPECTED_PROTECTED_ROUTES = frozenset(
    {
        "/api/get_cash_journal",
        "/api/finance_audit",
        "/api/finance_audit/apply_safe_fixes",
        "/api/get_cashbox",
        "/api/create_cashbox",
        "/api/reorder_cashboxes",
        "/api/create_cashbox_transfer",
        "/api/mark_cashbox_notifications_seen",
        "/api/delete_cashbox",
        "/api/create_cash_transaction",
        "/api/cancel_cash_transaction",
        "/api/cancel_last_cash_transaction",
        "/api/save_employee",
        "/api/toggle_employee",
        "/api/delete_employee",
        "/api/get_payroll_report",
        "/api/get_employee_salary_ledger",
        "/api/get_employee_salary_report",
        "/api/get_employee_salary_reconciliation",
        "/api/create_employee_salary_transaction",
        "/api/create_employee_shift_accrual",
        "/api/reset_employee_salary_balance",
    }
)
PRIVATE_SNAPSHOT_SETTING_KEYS = frozenset(
    {
        "employees",
        "employee_shift_accruals",
        "employee_repair_order_accruals",
        "employee_salary_balance_resets",
    }
)


class EmployeeCashboxAccessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = api_test_module.ApiServerTests()
        self.api.setUp()
        status, admin_login = self.api.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        self.admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}
        self.employee = self.api.service.save_employee(
            {
                "name": "Синтетический сотрудник доступа",
                "position": "Механик",
                "salary_mode": "salary_only",
                "base_salary": "50000",
            }
        )["employee"]
        self.cashbox = self.api.service.create_cashbox(
            {"name": "Синтетическая касса доступа", "actor_name": "TEST"}
        )["cashbox"]
        self.api.service.create_cash_transaction(
            {
                "cashbox_id": self.cashbox["id"],
                "direction": "income",
                "amount": "100",
                "note": "Синтетическая операция доступа",
                "actor_name": "TEST",
            }
        )
        self.restricted_headers = self._create_and_login("restricted-user", [])
        self.allowed_headers = self._create_and_login(
            "allowed-user", [EMPLOYEES_CASHBOXES_ACCESS_PERMISSION]
        )

    def tearDown(self) -> None:
        self.api.tearDown()

    def _create_and_login(
        self,
        username: str,
        permissions: list[str],
        *,
        role: str = "operator",
    ) -> dict[str, str]:
        status, _saved = self.api.request(
            "/api/save_operator_user",
            {
                "username": username,
                "password": "test-password",
                "role": role,
                "permissions": permissions,
            },
            headers=self.admin_headers,
        )
        self.assertEqual(status, 200)
        status, logged_in = self.api.request(
            "/api/login_operator",
            {"username": username, "password": "test-password"},
        )
        self.assertEqual(status, 200)
        return {
            "X-Operator-Session": logged_in["data"]["session"]["token"],
            "X-Real-IP": "203.0.113.20",
        }

    def test_permission_overlay_applies_to_existing_bearer_route(self) -> None:
        route = "/api/get_cashbox"
        spec = policy_for_route(route)
        self.assertEqual(spec.auth_kind, "bearer")
        self.assertEqual(
            spec.required_permission,
            EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
        )

        status, denied = self.api.request(
            f"{route}?cashbox_id={self.cashbox['id']}",
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")

    def test_protected_route_inventory_is_explicit_and_complete(self) -> None:
        self.assertEqual(
            EMPLOYEES_CASHBOXES_PERMISSION_ROUTES,
            EXPECTED_PROTECTED_ROUTES,
        )
        for route in EXPECTED_PROTECTED_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(
                    policy_for_route(route).required_permission,
                    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
                )
                status, response = self.api.request(
                    route,
                    {},
                    headers=self.restricted_headers,
                )
                self.assertEqual(status, 403)
                self.assertEqual(response["error"]["code"], "forbidden")
        for reference_route in ("/api/list_employees", "/api/list_cashboxes"):
            with self.subTest(reference_route=reference_route):
                self.assertEqual(policy_for_route(reference_route).required_permission, "")

    def test_admin_role_without_permission_is_also_restricted(self) -> None:
        status, denied = self.api.request(
            f"/api/get_cashbox?cashbox_id={self.cashbox['id']}",
            method="GET",
            headers={**self.admin_headers, "X-Real-IP": "203.0.113.21"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")

    def test_finance_audit_requires_permission(self) -> None:
        audit_spec = policy_for_route("/api/finance_audit")
        self.assertEqual(audit_spec.auth_kind, "bearer")
        self.assertEqual(
            audit_spec.required_permission,
            EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
        )

        status, denied = self.api.request(
            "/api/finance_audit",
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")

        status, allowed = self.api.request(
            "/api/finance_audit",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(allowed["data"]["meta"]["schema_version"], "finance_audit.v1")

    def test_finance_audit_safe_fixes_require_admin_and_permission(self) -> None:
        route = "/api/finance_audit/apply_safe_fixes"
        spec = policy_for_route(route)
        self.assertEqual(spec.auth_kind, "admin")
        self.assertEqual(
            spec.required_permission,
            EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
        )

        for headers in (self.admin_headers, self.allowed_headers):
            with self.subTest(headers=headers):
                status, denied = self.api.request(
                    route,
                    {"dry_run": True},
                    headers=headers,
                )
                self.assertEqual(status, 403)
                self.assertEqual(denied["error"]["code"], "forbidden")

        allowed_admin_headers = self._create_and_login(
            "allowed-admin",
            [EMPLOYEES_CASHBOXES_ACCESS_PERMISSION],
            role="admin",
        )
        status, allowed = self.api.request(
            route,
            {"dry_run": True},
            headers=allowed_admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(allowed["data"]["meta"]["dry_run"])
        self.assertFalse(allowed["data"]["meta"]["changed"])

    def test_restricted_operator_receives_only_reference_lists(self) -> None:
        status, employees = self.api.request(
            "/api/list_employees", method="GET", headers=self.restricted_headers
        )
        self.assertEqual(status, 200)
        self.assertTrue(employees["data"]["meta"]["references_only"])
        employee = next(
            item for item in employees["data"]["employees"] if item["id"] == self.employee["id"]
        )
        self.assertEqual(
            set(employee),
            {"id", "name", "position", "is_active"},
        )
        self.assertEqual(employees["data"]["summary"], {})
        self.assertEqual(employees["data"]["detail_rows"], [])

        status, cashboxes = self.api.request(
            "/api/list_cashboxes?limit=20",
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(cashboxes["data"]["meta"]["references_only"])
        cashbox = next(
            item for item in cashboxes["data"]["cashboxes"] if item["id"] == self.cashbox["id"]
        )
        self.assertEqual(
            set(cashbox),
            {"id", "short_id", "name", "order", "created_at", "updated_at"},
        )
        self.assertNotIn("transactions_total", cashboxes["data"]["meta"])
        self.assertIsNone(cashboxes["data"]["notification"])

    def test_restricted_operator_cannot_read_or_mutate_protected_sections(self) -> None:
        cases = (
            (
                "GET",
                f"/api/get_cashbox?cashbox_id={self.cashbox['id']}",
                None,
            ),
            ("GET", "/api/get_payroll_report", None),
            (
                "POST",
                "/api/save_employee",
                {"name": "Не должен сохраниться"},
            ),
            (
                "POST",
                "/api/create_cash_transaction",
                {
                    "cashbox_id": self.cashbox["id"],
                    "direction": "expense",
                    "amount": "1",
                },
            ),
            (
                "GET",
                f"/employee_salary_reconciliation_print?employee_id={self.employee['id']}",
                None,
            ),
        )
        for method, path, payload in cases:
            with self.subTest(method=method, path=path):
                status, response = self.api.request(
                    path,
                    payload,
                    method=method,
                    headers=self.restricted_headers,
                )
                self.assertEqual(status, 403)
                self.assertEqual(response["error"]["code"], "forbidden")

    def test_allowed_operator_receives_full_data_and_protected_reads(self) -> None:
        status, employees = self.api.request(
            "/api/list_employees", method="GET", headers=self.allowed_headers
        )
        self.assertEqual(status, 200)
        employee = next(
            item for item in employees["data"]["employees"] if item["id"] == self.employee["id"]
        )
        self.assertIn("balance_total", employee)
        self.assertNotIn("meta", employees["data"])

        status, cashboxes = self.api.request(
            "/api/list_cashboxes?limit=20",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        cashbox = next(
            item for item in cashboxes["data"]["cashboxes"] if item["id"] == self.cashbox["id"]
        )
        self.assertEqual(cashbox["statistics"]["balance_minor"], 10000)
        self.assertIn("transactions_total", cashboxes["data"]["meta"])

        status, _cashbox = self.api.request(
            f"/api/get_cashbox?cashbox_id={self.cashbox['id']}",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        status, _payroll = self.api.request(
            "/api/get_payroll_report",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)

    def test_permission_revocation_applies_to_existing_session(self) -> None:
        status, _saved = self.api.request(
            "/api/save_operator_user",
            {"username": "allowed-user", "permissions": []},
            headers=self.admin_headers,
        )
        self.assertEqual(status, 200)

        status, denied = self.api.request(
            f"/api/get_cashbox?cashbox_id={self.cashbox['id']}",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")
        status, listed = self.api.request(
            "/api/list_cashboxes", method="GET", headers=self.allowed_headers
        )
        self.assertEqual(status, 200)
        self.assertTrue(listed["data"]["meta"]["references_only"])

    def test_restricted_snapshot_omits_private_payroll_settings_and_scopes_revision(self) -> None:
        snapshot_path = "/api/get_board_snapshot?compact=1&include_archive=0"
        revision_path = "/api/get_board_revision?compact=1&include_archive=0"

        status, allowed = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertIn("employees", allowed["data"]["settings"])
        allowed_revision = allowed["data"]["meta"]["revision"]

        status, _saved = self.api.request(
            "/api/save_operator_user",
            {"username": "allowed-user", "permissions": []},
            headers=self.admin_headers,
        )
        self.assertEqual(status, 200)

        status, restricted = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(PRIVATE_SNAPSHOT_SETTING_KEYS.isdisjoint(restricted["data"]["settings"]))
        restricted_revision = restricted["data"]["meta"]["revision"]
        self.assertNotEqual(restricted_revision, allowed_revision)

        status, revision = self.api.request(
            revision_path,
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(revision["data"]["revision"], restricted_revision)
        self.assertEqual(revision["data"]["meta"]["revision"], restricted_revision)

    def test_restricted_board_event_views_filter_employee_and_cashbox_events(self) -> None:
        for route in (
            "/api/get_board_events?event_limit=100",
            "/api/get_gpt_wall?event_limit=100",
            "/api/review_board?recent_event_limit=50",
        ):
            with self.subTest(route=route):
                status, restricted = self.api.request(
                    route,
                    method="GET",
                    headers=self.restricted_headers,
                )
                self.assertEqual(status, 200)
                restricted_text = json.dumps(restricted["data"], ensure_ascii=False)
                self.assertNotIn("Синтетическая операция доступа", restricted_text)
                self.assertNotIn("cash_transaction_created", restricted_text)
                self.assertNotIn("employee_saved", restricted_text)

        for route in (
            "/api/get_board_events?event_limit=100",
            "/api/get_gpt_wall?event_limit=100",
        ):
            with self.subTest(allowed_route=route):
                status, allowed = self.api.request(
                    route,
                    method="GET",
                    headers=self.allowed_headers,
                )
                self.assertEqual(status, 200)
                allowed_text = json.dumps(allowed["data"], ensure_ascii=False)
                self.assertIn("Синтетическая операция доступа", allowed_text)
                self.assertIn("cash_transaction_created", allowed_text)

    def test_local_internal_calls_without_human_session_keep_full_contract(self) -> None:
        status, cashboxes = self.api.request("/api/list_cashboxes", method="GET")
        self.assertEqual(status, 200)
        self.assertIn("statistics", cashboxes["data"]["cashboxes"][0])
        status, details = self.api.request(
            f"/api/get_cashbox?cashbox_id={self.cashbox['id']}", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["balance_minor"], 10000)

    def test_trusted_gateway_service_identity_keeps_full_reference_and_read_contracts(
        self,
    ) -> None:
        token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": token,
        }
        headers = {
            "X-Autostop-Agent-Identity": "codex-owner-agent",
            "X-Autostop-Agent-Token": token,
        }
        source = {"source": "mcp_agent_gateway_v2"}
        with patch.dict(os.environ, gateway_env, clear=False):
            cashbox_status, cashboxes = self.api.request(
                "/api/list_cashboxes",
                source,
                headers=headers,
            )
            employee_status, employees = self.api.request(
                "/api/list_employees",
                source,
                headers=headers,
            )
            detail_status, details = self.api.request(
                "/api/get_cashbox",
                {**source, "cashbox_id": self.cashbox["id"]},
                headers=headers,
            )
            audit_status, audit = self.api.request(
                "/api/finance_audit",
                source,
                headers=headers,
            )
            safe_fix_status, safe_fixes = self.api.request(
                "/api/finance_audit/apply_safe_fixes",
                {**source, "dry_run": True},
                headers=headers,
            )

        self.assertEqual(cashbox_status, 200)
        cashbox = next(
            item for item in cashboxes["data"]["cashboxes"] if item["id"] == self.cashbox["id"]
        )
        self.assertIn("statistics", cashbox)
        self.assertFalse(cashboxes["data"]["meta"].get("references_only", False))

        self.assertEqual(employee_status, 200)
        employee = next(
            item for item in employees["data"]["employees"] if item["id"] == self.employee["id"]
        )
        self.assertIn("balance_total", employee)
        self.assertNotIn("meta", employees["data"])

        self.assertEqual(detail_status, 200)
        self.assertEqual(
            details["data"]["cashbox"]["statistics"]["balance_minor"],
            10000,
        )
        self.assertEqual(audit_status, 200)
        self.assertEqual(audit["data"]["meta"]["schema_version"], "finance_audit.v1")
        self.assertEqual(safe_fix_status, 200)
        self.assertTrue(safe_fixes["data"]["meta"]["dry_run"])
        self.assertFalse(safe_fixes["data"]["meta"]["changed"])


if __name__ == "__main__":
    unittest.main()
