from __future__ import annotations

# The fixture import sets up the source path before application imports.
# ruff: noqa: I001
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

from tests.services_case import CardServiceCase

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore

CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 1, 3, tzinfo=UTC)
AS_OF = datetime(2026, 1, 5, tzinfo=UTC)


class PayrollHistoryPreservationTests(CardServiceCase):
    def _at(self, moment: datetime, operation, payload: dict) -> dict:
        with ExitStack() as stack:
            for clock_patch in self._patch_time(moment):
                stack.enter_context(clock_patch)
            return operation(payload)

    def _employee(self, name: str) -> dict:
        return self._at(
            CREATED_AT,
            self.service.save_employee,
            {
                "name": name,
                "salary_mode": "salary_only",
                "base_salary": "1000",
                "work_percent": "0",
            },
        )["employee"]

    def _reload(self) -> None:
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def _balance(self, employee_id: str) -> dict:
        return self._at(
            AS_OF, self.service.get_employee_salary_ledger, {"employee_id": employee_id}
        )

    def test_reactivation_preserves_all_periods_and_prior_weekly_accrual_after_reload(self) -> None:
        employee = self._employee("Active period history")
        before = self._balance(employee["id"])
        self.assertEqual(before["accrued_total"], "1000")
        for index in range(40):
            self._at(
                CHANGED_AT + timedelta(minutes=index),
                self.service.toggle_employee,
                {"employee_id": employee["id"]},
            )

        self._reload()
        stored = self.store.read_bundle()["settings"]["employees"][0]
        with self.subTest(contract="complete active history on disk"):
            self.assertEqual(len(stored["active_periods"]), 21)
            self.assertEqual(stored["active_periods"][0]["start_at"], CREATED_AT.isoformat())
        with self.subTest(contract="historical accrued amount"):
            after = self._balance(employee["id"])
            self.assertEqual(after["accrued_total"], before["accrued_total"])
            self.assertEqual(after["balance_minor"], before["balance_minor"])

    def test_term_changes_preserve_all_terms_and_prior_weekly_accrual_after_reload(self) -> None:
        employee = self._employee("Payroll term history")
        before = self._balance(employee["id"])
        self.assertEqual(before["accrued_total"], "1000")
        # Percentage changes create history entries, but do not change this employee's
        # salary-only weekly amount or the already earned Friday accrual.
        for index in range(1, 51):
            self._at(
                CHANGED_AT + timedelta(minutes=index),
                self.service.save_employee,
                {"employee_id": employee["id"], "work_percent": str(index)},
            )

        self._reload()
        stored = self.store.read_bundle()["settings"]["employees"][0]
        with self.subTest(contract="complete payroll history on disk"):
            self.assertEqual(len(stored["payroll_terms"]), 51)
            self.assertEqual(stored["payroll_terms"][0]["effective_from"], CREATED_AT.isoformat())
        with self.subTest(contract="historical accrued amount"):
            after = self._balance(employee["id"])
            self.assertEqual(after["accrued_total"], before["accrued_total"])
            self.assertEqual(after["balance_minor"], before["balance_minor"])
