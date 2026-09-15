from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.operator_permissions import SALARY_BALANCE_RESET_PERMISSION
from scripts.clear_financial_history import _format_text, build_financial_history_cleanup_result
from tests.services_case import CardServiceCase


class FinancialCleanupResetTests(CardServiceCase):
    def _seed_reset(self, *, negative: bool) -> str:
        self.service = self._build_service()
        employee = self.service.save_employee(
            {"name": "Synthetic cleanup employee", "salary_mode": "none"}
        )["employee"]
        if negative:
            box = self.service.create_cashbox({"name": "Synthetic cleanup cash"})["cashbox"]
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_advance",
                    "amount_minor": 10000,
                    "cashbox_id": box["id"],
                }
            )
        else:
            self.service.create_employee_shift_accrual(
                {"employee_id": employee["id"], "amount_minor": 10000}
            )
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        applied = self.service.reset_employee_salary_balance(
            {
                "employee_id": employee["id"],
                "expected_balance_minor": ledger["balance_minor"],
                "expected_balance_revision": ledger["balance_revision"],
                "idempotency_key": "synthetic-cleanup-reset",
                "source": "ui",
                "_operator_session": {
                    "username": "synthetic-admin",
                    "is_admin": True,
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                },
            }
        )
        self.assertEqual(applied["ledger"]["balance_minor"], 0)
        return employee["id"]

    def _assert_cleanup_keeps_zero_balance(self, *, negative: bool) -> None:
        employee_id = self._seed_reset(negative=negative)
        before = self.state_file.read_bytes()
        result = build_financial_history_cleanup_result(self.state_file, apply=True, backup=True)
        self.assertEqual(Path(result["backup"]["path"]).read_bytes(), before)
        ledger = self._build_service().get_employee_salary_ledger({"employee_id": employee_id})
        self.assertEqual(ledger["balance_minor"], 0)
        self.assertEqual(ledger["journal_rows"], [])
        state = json.loads(self.state_file.read_bytes())
        self.assertEqual(state["settings"]["employee_salary_balance_resets"], [])
        self.assertNotIn(
            "employee_salary_balance_reset", {row["action"] for row in state["events"]}
        )
        self.service.create_employee_shift_accrual(
            {"employee_id": employee_id, "amount_minor": 123}
        )
        self.assertEqual(
            self.service.get_employee_salary_ledger({"employee_id": employee_id})["balance_minor"],
            123,
        )

    def test_cleanup_removes_adjustment_with_its_positive_accrual_history(self) -> None:
        self._assert_cleanup_keeps_zero_balance(negative=False)

    def test_cleanup_removes_adjustment_with_its_negative_payment_history(self) -> None:
        self._assert_cleanup_keeps_zero_balance(negative=True)

    def test_dry_run_reports_reset_removal_without_changing_state(self) -> None:
        self._seed_reset(negative=False)
        before = self.state_file.read_bytes()
        result = build_financial_history_cleanup_result(self.state_file)
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(result["summary"]["salary_balance_resets_removed"], 1)
        self.assertIn("salary_balance_resets_removed: 1", _format_text(result))
        self.assertEqual(list(self.state_file.parent.glob("*.backup-*")), [])
