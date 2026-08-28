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
from minimal_kanban.models import AuditEvent  # noqa: E402
from minimal_kanban.operator_permissions import (  # noqa: E402
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
)
from minimal_kanban.services.operator_visibility import (  # noqa: E402
    REPAIR_ORDER_PRIVATE_ROW_FIELDS,
    project_operator_result,
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
PRIVATE_PAYROLL_SENTINEL = "PRIVATE-PAYROLL-SENTINEL"


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

    def _seed_private_repair_order(self) -> tuple[str, dict[str, str]]:
        status, created = self.api.request(
            "/api/create_card",
            {
                "vehicle": "Synthetic access vehicle",
                "title": "Private payroll projection",
                "deadline": {"hours": 2},
            },
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, updated = self.api.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "works": [
                        {
                            "name": "Operational work name",
                            "quantity": "2",
                            "price": "1250",
                            "executor_id": self.employee["id"],
                            "executor_name": self.employee["name"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "321",
                            "work_salary_percent_override": "17",
                            "work_salary_cost_price": "44",
                            "work_salary_note": PRIVATE_PAYROLL_SENTINEL,
                        }
                    ]
                },
            },
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        row = updated["data"]["repair_order"]["works"][0]
        self.assertEqual(row["work_salary_note"], PRIVATE_PAYROLL_SENTINEL)
        return card_id, row

    def _append_card_access_events(self, card_id: str) -> None:
        service = self.api.service
        with service._lock:
            bundle = service._store.read_bundle()
            bundle["events"].extend(
                [
                    AuditEvent(
                        id="synthetic-visible-event",
                        timestamp="2099-08-29T00:00:01+00:00",
                        actor_name="TEST",
                        source="system",
                        action="description_changed",
                        message="Synthetic visible event",
                        details={"after": "visible"},
                        card_id=card_id,
                    ),
                    AuditEvent(
                        id="synthetic-private-event",
                        timestamp="2099-08-29T00:00:02+00:00",
                        actor_name="TEST",
                        source="system",
                        action="employee_salary_private_probe",
                        message="Synthetic private event",
                        details={"salary": PRIVATE_PAYROLL_SENTINEL},
                        card_id=card_id,
                    ),
                ]
            )
            service._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                events=bundle["events"],
            )

    def _append_private_card_event(self, card_id: str) -> None:
        service = self.api.service
        with service._lock:
            bundle = service._store.read_bundle()
            bundle["events"].append(
                AuditEvent(
                    id="synthetic-private-revision-event",
                    timestamp="2099-08-29T00:00:03+00:00",
                    actor_name="TEST",
                    source="system",
                    action="employee_salary_private_revision_probe",
                    message="Synthetic private revision event",
                    details={"salary": PRIVATE_PAYROLL_SENTINEL},
                    card_id=card_id,
                )
            )
            service._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                events=bundle["events"],
            )

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

    def test_private_event_does_not_change_restricted_counts_or_snapshot_revision(self) -> None:
        card_id, _row = self._seed_private_repair_order()
        snapshot_path = "/api/get_board_snapshot?compact=1&include_archive=0"
        revision_path = "/api/get_board_revision?compact=1&include_archive=0"

        status, restricted_before = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        restricted_card_before = next(
            card for card in restricted_before["data"]["cards"] if card["id"] == card_id
        )
        status, restricted_card_response_before = self.api.request(
            f"/api/get_card?card_id={card_id}",
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        status, allowed_before = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        allowed_card_before = next(
            card for card in allowed_before["data"]["cards"] if card["id"] == card_id
        )

        self._append_private_card_event(card_id)

        status, restricted_after = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        restricted_card_after = next(
            card for card in restricted_after["data"]["cards"] if card["id"] == card_id
        )
        self.assertEqual(
            restricted_card_after["events_count"],
            restricted_card_before["events_count"],
        )
        status, restricted_card_response_after = self.api.request(
            f"/api/get_card?card_id={card_id}",
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            restricted_card_response_after["data"]["card"]["events_count"],
            restricted_card_response_before["data"]["card"]["events_count"],
        )
        self.assertEqual(
            restricted_after["data"]["meta"]["revision"],
            restricted_before["data"]["meta"]["revision"],
        )

        status, restricted_revision = self.api.request(
            revision_path,
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            restricted_revision["data"]["revision"],
            restricted_before["data"]["meta"]["revision"],
        )
        status, allowed_after = self.api.request(
            snapshot_path,
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        allowed_card_after = next(
            card for card in allowed_after["data"]["cards"] if card["id"] == card_id
        )
        self.assertEqual(
            allowed_card_after["events_count"],
            allowed_card_before["events_count"] + 1,
        )
        self.assertNotEqual(
            allowed_after["data"]["meta"]["revision"],
            allowed_before["data"]["meta"]["revision"],
        )

    def test_restricted_board_event_views_filter_employee_and_cashbox_events(self) -> None:
        for route in (
            "/api/get_board_events?event_limit=100",
            "/api/get_board_event_page?limit=100",
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
            "/api/get_board_event_page?limit=100",
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
                self.assertIn("cash_transaction_created", allowed_text)
                if "/api/get_board_event_page" not in route:
                    self.assertIn("Синтетическая операция доступа", allowed_text)

    def test_restricted_generic_card_responses_omit_private_payroll_fields(self) -> None:
        card_id, _row = self._seed_private_repair_order()
        read_cases = (
            ("GET", "/api/get_board_snapshot?compact=0&include_archive=1", None),
            ("GET", "/api/get_cards?compact=0&include_archived=1", None),
            ("GET", f"/api/get_card?card_id={card_id}", None),
            (
                "POST",
                "/api/get_card_context",
                {
                    "card_id": card_id,
                    "event_limit": 100,
                    "include_repair_order_text": True,
                },
            ),
            (
                "POST",
                "/api/get_repair_order",
                {"card_id": card_id, "create_if_missing": False},
            ),
            ("POST", "/api/get_repair_order_text", {"card_id": card_id}),
        )
        for method, path, payload in read_cases:
            with self.subTest(method=method, path=path):
                status, response = self.api.request(
                    path,
                    payload,
                    method=method,
                    headers=self.restricted_headers,
                )
                self.assertEqual(status, 200)
                response_text = json.dumps(response["data"], ensure_ascii=False)
                self.assertNotIn(PRIVATE_PAYROLL_SENTINEL, response_text)
                for field_name in REPAIR_ORDER_PRIVATE_ROW_FIELDS:
                    self.assertNotIn(f'"{field_name}"', response_text)
                self.assertIn("Operational work name", response_text)

        status, allowed = self.api.request(
            f"/api/get_card?card_id={card_id}",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        allowed_text = json.dumps(allowed["data"], ensure_ascii=False)
        self.assertIn(PRIVATE_PAYROLL_SENTINEL, allowed_text)
        self.assertIn('"work_salary_note"', allowed_text)

        allowed_status, _headers, allowed_body = self.api.raw_request(
            f"/api/repair_order_text?card_id={card_id}",
            headers=self.allowed_headers,
        )
        restricted_status, _headers, restricted_body = self.api.raw_request(
            f"/api/repair_order_text?card_id={card_id}",
            headers=self.restricted_headers,
        )
        self.assertEqual(allowed_status, 200)
        self.assertEqual(restricted_status, 200)
        self.assertIn(PRIVATE_PAYROLL_SENTINEL, allowed_body.decode("utf-8"))
        restricted_text = restricted_body.decode("utf-8")
        self.assertNotIn(PRIVATE_PAYROLL_SENTINEL, restricted_text)
        self.assertNotIn('"work_salary_note"', restricted_text)
        self.assertIn("Operational work name", restricted_text)

    def test_restricted_card_logs_filter_before_limit_and_sanitize_repair_events(self) -> None:
        card_id, _row = self._seed_private_repair_order()
        self._append_card_access_events(card_id)

        status, restricted_log = self.api.request(
            (f"/api/get_card_log?card_id={card_id}&limit=1&include_full_details=1"),
            method="GET",
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(restricted_log["data"]["events"][0]["id"], "synthetic-visible-event")
        self.assertFalse(restricted_log["data"]["meta"]["include_full_details"])
        restricted_text = json.dumps(restricted_log["data"], ensure_ascii=False)
        self.assertNotIn(PRIVATE_PAYROLL_SENTINEL, restricted_text)
        self.assertNotIn("synthetic-private-event", restricted_text)

        status, restricted_context = self.api.request(
            "/api/get_card_context",
            {"card_id": card_id, "event_limit": 100, "include_repair_order_text": False},
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        restricted_context_text = json.dumps(restricted_context["data"], ensure_ascii=False)
        self.assertNotIn(PRIVATE_PAYROLL_SENTINEL, restricted_context_text)
        self.assertNotIn("synthetic-private-event", restricted_context_text)
        repair_events = [
            event
            for event in restricted_context["data"]["events"]
            if event["action"] == "repair_order_updated"
        ]
        self.assertTrue(repair_events)
        self.assertTrue(
            {"number", "status", "works", "materials"}.issuperset(repair_events[0]["details"])
        )

        status, allowed_log = self.api.request(
            f"/api/get_card_log?card_id={card_id}&limit=1&include_full_details=1",
            method="GET",
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(allowed_log["data"]["events"][0]["id"], "synthetic-private-event")
        self.assertTrue(allowed_log["data"]["meta"]["include_full_details"])
        self.assertIn(
            PRIVATE_PAYROLL_SENTINEL,
            json.dumps(allowed_log["data"], ensure_ascii=False),
        )

    def test_restricted_repair_order_write_preserves_private_fields(self) -> None:
        card_id, original_row = self._seed_private_repair_order()
        malicious_row = {
            **original_row,
            "name": "Operational work updated",
            "work_salary_note": "MALICIOUS-PRIVATE-CHANGE",
            "work_salary_guarantee": "999999",
            "salary_amount": "999999",
        }
        status, restricted_update = self.api.request(
            "/api/replace_repair_order_works",
            {
                "card_id": card_id,
                "rows": [
                    malicious_row,
                    {
                        "name": "New ordinary work",
                        "quantity": "1",
                        "price": "50",
                        "work_salary_note": "MALICIOUS-NEW-ROW",
                        "salary_amount": "777",
                    },
                ],
            },
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 200)
        restricted_text = json.dumps(restricted_update["data"], ensure_ascii=False)
        self.assertIn("Operational work updated", restricted_text)
        self.assertIn("New ordinary work", restricted_text)
        self.assertNotIn("MALICIOUS-PRIVATE-CHANGE", restricted_text)
        self.assertNotIn("MALICIOUS-NEW-ROW", restricted_text)
        for field_name in REPAIR_ORDER_PRIVATE_ROW_FIELDS:
            self.assertNotIn(f'"{field_name}"', restricted_text)

        status, allowed_read = self.api.request(
            "/api/get_repair_order",
            {"card_id": card_id, "create_if_missing": False},
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        stored_rows = allowed_read["data"]["repair_order"]["works"]
        self.assertEqual(stored_rows[0]["work_salary_note"], PRIVATE_PAYROLL_SENTINEL)
        self.assertEqual(stored_rows[0]["work_salary_guarantee"], "321")
        self.assertNotEqual(stored_rows[0]["salary_amount"], "999999")
        self.assertEqual(stored_rows[1]["work_salary_note"], "")
        self.assertEqual(stored_rows[1]["salary_amount"], "")

        allowed_row = {**stored_rows[0], "work_salary_note": "ALLOWED-PRIVATE-CHANGE"}
        status, allowed_update = self.api.request(
            "/api/replace_repair_order_works",
            {"card_id": card_id, "rows": [allowed_row, stored_rows[1]]},
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            allowed_update["data"]["repair_order"]["works"][0]["work_salary_note"],
            "ALLOWED-PRIVATE-CHANGE",
        )

    def test_restricted_repair_order_reidentification_preserves_private_fields(self) -> None:
        for row_id in (None, "attacker-reidentified-row"):
            with self.subTest(row_id=row_id):
                card_id, original_row = self._seed_private_repair_order()
                submitted_row = {
                    **original_row,
                    "work_salary_note": "MALICIOUS-PRIVATE-CLEAR",
                    "salary_amount": "999999",
                }
                if row_id is None:
                    submitted_row.pop("id", None)
                else:
                    submitted_row["id"] = row_id
                status, _updated = self.api.request(
                    "/api/replace_repair_order_works",
                    {"card_id": card_id, "rows": [submitted_row]},
                    headers=self.restricted_headers,
                )
                self.assertEqual(status, 200)
                status, allowed_read = self.api.request(
                    "/api/get_repair_order",
                    {"card_id": card_id, "create_if_missing": False},
                    headers=self.allowed_headers,
                )
                self.assertEqual(status, 200)
                stored = allowed_read["data"]["repair_order"]["works"][0]
                self.assertEqual(stored["id"], original_row["id"])
                self.assertEqual(stored["work_salary_note"], PRIVATE_PAYROLL_SENTINEL)
                self.assertNotEqual(stored["salary_amount"], "999999")

    def test_restricted_repair_order_rejects_ambiguous_row_reidentification(self) -> None:
        card_id, original_row = self._seed_private_repair_order()
        ambiguous_rows = []
        for row_id in ("attacker-row-a", "attacker-row-b"):
            ambiguous_rows.append(
                {
                    **original_row,
                    "id": row_id,
                    "work_salary_note": "MALICIOUS-PRIVATE-CLEAR",
                }
            )
        status, denied = self.api.request(
            "/api/replace_repair_order_works",
            {"card_id": card_id, "rows": ambiguous_rows},
            headers=self.restricted_headers,
        )
        self.assertEqual(status, 400)
        self.assertEqual(denied["error"]["code"], "validation_error")
        self.assertEqual(denied["error"]["details"]["reason"], "row_identity_required")

        status, allowed_read = self.api.request(
            "/api/get_repair_order",
            {"card_id": card_id, "create_if_missing": False},
            headers=self.allowed_headers,
        )
        self.assertEqual(status, 200)
        stored = allowed_read["data"]["repair_order"]["works"][0]
        self.assertEqual(stored["id"], original_row["id"])
        self.assertEqual(stored["work_salary_note"], PRIVATE_PAYROLL_SENTINEL)

    def test_restricted_projection_omits_payroll_reversal_amounts(self) -> None:
        projected = project_operator_result(
            {"_operator_session": {"permissions": []}},
            {
                "payroll_reversals": [{"amount": PRIVATE_PAYROLL_SENTINEL}],
                "status": "closed",
            },
        )
        self.assertEqual(projected, {"status": "closed"})

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
