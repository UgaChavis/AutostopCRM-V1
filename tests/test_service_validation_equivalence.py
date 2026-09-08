from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ruff: noqa: E402
from minimal_kanban.models import CashBox, Column
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.services.ready_column import _next_column_id
from tests.services_case import CardServiceCase


class ValidationEquivalenceTests(CardServiceCase):
    def assert_error(self, method, payload, code, message, status, details):
        before = self.state_file.read_bytes()
        with patch.object(self.service, "_save_bundle") as save:
            with self.assertRaises(ServiceError) as caught:
                method(payload)
            error = caught.exception
            self.assertEqual(
                (error.code, error.message, error.status_code, error.details),
                (code, message, status, details),
            )
            save.assert_not_called()
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_required_employee_errors_precede_later_validation_in_five_operations(self):
        self.service.save_employee({"name": "Synthetic employee", "salary_mode": "none"})
        operations = (
            "create_employee_salary_transaction",
            "create_employee_shift_accrual",
            "get_employee_salary_reconciliation",
            "toggle_employee",
            "delete_employee",
        )
        for operation in operations:
            for value in (None, "", "  ", " missing "):
                with self.subTest(operation=operation, employee_id=value):
                    missing = not str(value or "").strip()
                    self.assert_error(
                        getattr(self.service, operation),
                        {
                            "employee_id": value,
                            "amount": "invalid",
                            "date_from": "invalid",
                            "expected_employee_updated_at": "stale",
                            "expected_updated_at": "stale",
                        },
                        "validation_error" if missing else "not_found",
                        "Нужно передать employee_id." if missing else "Сотрудник не найден.",
                        400 if missing else 404,
                        {"field": "employee_id"} if missing else {"employee_id": "missing"},
                    )

    def test_optional_employee_revision_precedes_amount_validation(self):
        employee = self.service.save_employee(
            {"name": "Synthetic employee", "salary_mode": "none"}
        )["employee"]
        for operation in ("create_employee_salary_transaction", "create_employee_shift_accrual"):
            for expected in (None, "", employee["updated_at"], "stale"):
                with self.subTest(operation=operation, expected=expected):
                    method = getattr(self.service, operation)
                    payload = {
                        "employee_id": employee["id"],
                        "expected_employee_updated_at": expected,
                        "transaction_kind": "salary_payout",
                    }
                    with patch.object(
                        self.service, "_validated_cash_amount_minor", side_effect=LookupError
                    ) as amount:
                        if expected == "stale":
                            self.assert_error(
                                method,
                                payload,
                                "employee_update_conflict",
                                "Сотрудник уже изменился. Обновите данные и повторите действие.",
                                409,
                                {"employee_id": employee["id"]},
                            )
                            amount.assert_not_called()
                        else:
                            with self.assertRaises(LookupError):
                                method(payload)
                            amount.assert_called_once()

    def test_card_revision_contract_in_three_operations(self):
        card = self.service.create_card({"title": "Synthetic card"})["card"]
        operations = (
            ("update_card", {"title": "Changed"}),
            ("update_repair_order", {"repair_order": {"client": "Changed"}}),
            ("set_repair_order_status", {"status": "ready"}),
        )
        for operation, values in operations:
            with self.subTest(operation=operation):
                self.assert_error(
                    getattr(self.service, operation),
                    {"card_id": card["id"], "expected_updated_at": " stale ", **values},
                    "card_update_conflict",
                    "Карточка уже изменена другим оператором. Обновите карточку и повторите правку.",
                    409,
                    {
                        "card_id": card["id"],
                        "expected_updated_at": "stale",
                        "current_updated_at": card["updated_at"],
                    },
                )

    def test_optional_cashbox_revision_contract_in_four_operations(self):
        employee = self.service.save_employee(
            {"name": "Synthetic employee", "salary_mode": "none"}
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Synthetic cashbox"})["cashbox"]
        transaction = self.service.create_cash_transaction(
            {"cashbox_id": cashbox["id"], "direction": "income", "amount_minor": 100}
        )["transaction"]
        for operation in (
            "delete_cashbox",
            "create_employee_salary_transaction",
            "cancel_last_cash_transaction",
            "cancel_cash_transaction",
        ):
            with self.subTest(operation=operation):
                self.assert_error(
                    getattr(self.service, operation),
                    {
                        "cashbox_id": cashbox["id"],
                        "transaction_id": transaction["id"],
                        "employee_id": employee["id"],
                        "transaction_kind": "salary_payout",
                        "amount_minor": 100,
                        "expected_cashbox_updated_at": " stale ",
                    },
                    "cashbox_update_conflict",
                    "Касса уже изменилась. Обновите данные и повторите действие.",
                    409,
                    {"cashbox_id": cashbox["id"]},
                )

    def test_column_id_algorithms_match_without_mutating_columns(self):
        for ids in ([], ["done"], ["column_1"], ["column_3", "column_1"], ["column_01"]):
            with self.subTest(ids=ids):
                columns = [Column(id=value, label=value, position=i) for i, value in enumerate(ids)]
                expected = next(
                    f"column_{i}" for i in range(1, len(ids) + 2) if f"column_{i}" not in ids
                )
                self.assertEqual(_next_column_id(columns), expected)
                self.assertEqual(self.service._column_service._next_column_id(columns), expected)
                self.assertEqual([column.id for column in columns], ids)

    def test_required_employee_preserves_first_match_identity_and_normalized_id(self):
        first = {"id": "employee", "name": "First"}
        employees = [first, {"id": "employee", "name": "Duplicate"}]
        employee_id, selected = self.service._required_employee(employees, " employee ")
        self.assertEqual(employee_id, "employee")
        self.assertIs(selected, first)
        selected["is_active"] = False
        self.assertFalse(employees[0]["is_active"])
        self.assertNotIn("is_active", employees[1])

    def test_optional_revision_helpers_preserve_blank_and_current_revisions(self):
        employee = {"id": "employee", "updated_at": "current"}
        cashbox = CashBox(
            id="cashbox", name="Synthetic", order=0, created_at="current", updated_at="current"
        )
        before = deepcopy((employee, cashbox))
        for expected in (None, "", "  ", "current", " current "):
            with self.subTest(expected=expected):
                self.service._ensure_employee_expected_updated_at(
                    employee, {"expected_employee_updated_at": expected}
                )
                self.assertEqual(
                    self.service._ensure_cashbox_expected_updated_at(
                        cashbox, {"expected_cashbox_updated_at": expected}
                    ),
                    str(expected or "").strip(),
                )
        self.assertEqual((employee, cashbox), before)

    def test_correction_close_requires_revision_before_optional_conflict_check(self):
        card_id = self.service.create_card({"title": "Synthetic correction"})["card"]["id"]
        card = deepcopy(next(c for c in self.store.read_bundle()["cards"] if c.id == card_id))
        card.repair_order.active_correction = {"reason_code": "technical_error"}
        for expected in (None, "stale"):
            with (
                self.subTest(expected=expected),
                patch.object(self.service, "_find_card", return_value=card),
                patch.object(self.service, "_ensure_card_expected_updated_at") as optional,
            ):
                self.assert_error(
                    self.service.set_repair_order_status,
                    {"card_id": card_id, "status": "closed", "expected_updated_at": expected},
                    "repair_order_revision_conflict",
                    "Карточка уже изменена или не передана её актуальная ревизия.",
                    409,
                    {
                        "card_id": card_id,
                        "expected_updated_at": expected or "",
                        "current_updated_at": card.updated_at,
                    },
                )
                optional.assert_not_called()
