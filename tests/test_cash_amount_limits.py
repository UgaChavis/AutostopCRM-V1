from __future__ import annotations

# Fixture import selects this checkout before application imports.
# ruff: noqa: I001
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_api as api_fixtures

from minimal_kanban.models import MONEY_MINOR_ABS_MAX, normalize_money_minor
from minimal_kanban.services.cash_amounts import validated_cash_amount_minor


class CashAmountLimitTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def test_cash_transfer_salary_and_accrual_cannot_silently_cap_new_amounts(self):
        boxes = [
            self.service.create_cashbox({"name": name})["cashbox"] for name in ("Cash A", "Cash B")
        ]
        employee = self.service.save_employee(
            {"name": "Synthetic employee", "salary_mode": "percent_only"}
        )["employee"]
        routes = [
            (
                "create_cash_transaction",
                {
                    "cashbox_id": boxes[0]["id"],
                    "direction": direction,
                    "note": "Synthetic operation test",
                },
            )
            for direction in ("income", "expense")
        ] + [
            (
                "create_cashbox_transfer",
                {"from_cashbox_id": boxes[0]["id"], "to_cashbox_id": boxes[1]["id"]},
            ),
            (
                "create_employee_salary_transaction",
                {
                    "cashbox_id": boxes[0]["id"],
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_advance",
                },
            ),
            ("create_employee_shift_accrual", {"employee_id": employee["id"]}),
        ]
        state_file = Path(self.temp_dir.name) / "state.json"
        before = state_file.read_bytes()
        bundle = copy.deepcopy(self.store.read_bundle())
        with patch.object(self.service, "_save_bundle") as save:
            for route, base in routes:
                for amount in (
                    {"amount_minor": MONEY_MINOR_ABS_MAX + 1},
                    {"amount": "1000000000000.01"},
                    {"amount": 1000000000000.01},
                    {"amount": "1e100"},
                ):
                    with self.subTest(route=route, amount=amount):
                        status, response = self.request("/api/" + route, {**base, **amount})
                        self.assertEqual(status, 400, response)
                        self.assertEqual(response["error"]["code"], "validation_error")
                        self.assertEqual(response["error"]["details"]["field"], "amount")
                        self.assertEqual(state_file.read_bytes(), before)
            save.assert_not_called()
        self.assertEqual(self.store.read_bundle(), bundle)

    def test_existing_cash_input_units_rounding_and_limit_are_preserved(self):
        for value in (
            1,
            100,
            100.0,
            "100",
            "1,25",
            "1 000.50",
            "1.005",
            "16.666666666667",
            MONEY_MINOR_ABS_MAX,
            "1e12",
        ):
            for field in ("amount", "amount_minor"):
                with self.subTest(value=value, field=field):
                    self.assertEqual(
                        validated_cash_amount_minor({field: value}), normalize_money_minor(value)
                    )
        self.assertEqual(validated_cash_amount_minor({"amount_minor": 100, "amount": "wrong"}), 100)
        self.assertEqual(validated_cash_amount_minor({"amount_minor": "", "amount": "1"}), 100)
