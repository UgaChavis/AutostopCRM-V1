from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for import_path in (SRC, TESTS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import test_api as api_test_module  # noqa: E402

from minimal_kanban.operator_permissions import (  # noqa: E402
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    SALARY_BALANCE_RESET_PERMISSION,
)

RESET_PERMISSIONS = [
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    SALARY_BALANCE_RESET_PERMISSION,
]


class SalaryBalanceResetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = api_test_module.ApiServerTests()
        self.api.setUp()

    def tearDown(self) -> None:
        self.api.tearDown()

    def test_operator_permission_roundtrip_is_persisted_and_revocable(self) -> None:
        status, logged_in = self.api.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        headers = {"X-Operator-Session": logged_in["data"]["session"]["token"]}

        status, profile = self.api.request(
            "/api/get_operator_profile", method="GET", headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["user"]["permissions"], [])

        status, saved = self.api.request(
            "/api/save_operator_user",
            {
                "username": "permission-user",
                "password": "test-password",
                "role": "admin",
                "permissions": RESET_PERMISSIONS,
            },
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            saved["data"]["user"]["permissions"],
            RESET_PERMISSIONS,
        )

        status, role_changed = self.api.request(
            "/api/save_operator_user",
            {"username": "permission-user", "role": "operator"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(role_changed["data"]["user"]["role"], "operator")
        self.assertEqual(
            role_changed["data"]["user"]["permissions"],
            RESET_PERMISSIONS,
        )

        status, revoked = self.api.request(
            "/api/save_operator_user",
            {"username": "permission-user", "permissions": []},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(revoked["data"]["user"]["permissions"], [])

    def test_salary_balance_reset_requires_exact_operator_permission(self) -> None:
        status, admin_login = self.api.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}
        for username, role, permissions in (
            ("UGA", "admin", RESET_PERMISSIONS),
            ("MARIA", "operator", RESET_PERMISSIONS),
            ("CODEX", "admin", [EMPLOYEES_CASHBOXES_ACCESS_PERMISSION]),
        ):
            status, _saved = self.api.request(
                "/api/save_operator_user",
                {
                    "username": username,
                    "password": "test-password",
                    "role": role,
                    "permissions": permissions,
                },
                headers=admin_headers,
            )
            self.assertEqual(status, 200)

        session_headers: dict[str, dict[str, str]] = {}
        for username in ("UGA", "MARIA", "CODEX"):
            status, login = self.api.request(
                "/api/login_operator",
                {"username": username, "password": "test-password"},
            )
            self.assertEqual(status, 200)
            session_headers[username] = {"X-Operator-Session": login["data"]["session"]["token"]}

        employees = []
        for index in range(2):
            employee = self.api.service.save_employee(
                {"name": f"Синтетический API reset {index}", "salary_mode": "none"}
            )["employee"]
            self.api.service.create_employee_shift_accrual(
                {"employee_id": employee["id"], "amount_minor": 1000 + index}
            )
            employees.append(employee)
        ledgers = [
            self.api.service.get_employee_salary_ledger({"employee_id": item["id"]})
            for item in employees
        ]

        spoof_payload = {
            "employee_id": employees[0]["id"],
            "expected_balance_minor": ledgers[0]["balance_minor"],
            "expected_balance_revision": ledgers[0]["balance_revision"],
            "idempotency_key": "api-salary-reset-owner",
            "actor_name": "SPOOFED",
            "permissions": RESET_PERMISSIONS,
            "_operator_session": {
                "username": "SPOOFED",
                "permissions": RESET_PERMISSIONS,
            },
        }
        status, unauthenticated = self.api.request(
            "/api/reset_employee_salary_balance", spoof_payload
        )
        self.assertEqual(status, 401)
        self.assertEqual(unauthenticated["error"]["code"], "unauthorized")

        status, denied = self.api.request(
            "/api/reset_employee_salary_balance",
            spoof_payload,
            headers=session_headers["CODEX"],
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")

        cash_before = [item.to_dict() for item in self.api.store.read_bundle()["cash_transactions"]]
        status, owner_reset = self.api.request(
            "/api/reset_employee_salary_balance",
            spoof_payload,
            headers=session_headers["UGA"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(owner_reset["data"]["ledger"]["balance_minor"], 0)
        self.assertEqual(owner_reset["data"]["balance_reset"]["actor_name"], "UGA")

        maria_payload = {
            "employee_id": employees[1]["id"],
            "expected_balance_minor": ledgers[1]["balance_minor"],
            "expected_balance_revision": ledgers[1]["balance_revision"],
            "idempotency_key": "api-salary-reset-maria",
            "actor_name": "SPOOFED",
        }
        status, maria_reset = self.api.request(
            "/api/reset_employee_salary_balance",
            maria_payload,
            headers=session_headers["MARIA"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(maria_reset["data"]["ledger"]["balance_minor"], 0)
        self.assertEqual(maria_reset["data"]["balance_reset"]["actor_name"], "MARIA")
        self.assertEqual(
            cash_before,
            [item.to_dict() for item in self.api.store.read_bundle()["cash_transactions"]],
        )


if __name__ == "__main__":
    unittest.main()
