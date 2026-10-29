from __future__ import annotations

# Fixture import selects this checkout before application imports.
# ruff: noqa: I001
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.models import MONEY_MINOR_ABS_MAX
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.services.cash_amounts import cash_payment_minor


class RepairOrderPaymentAmountTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.store.close)
        self.boxes = [
            self.service.create_cashbox({"name": name})["cashbox"]
            for name in ("Наличный", "На карту", "Безналичный")
        ]
        self.card_id = self.service.create_card({"title": "Synthetic ledger precision"})["card"][
            "id"
        ]

    def test_new_ledger_payments_reject_lossy_amounts_before_creating_any_transaction(self):
        before = self.state_file.read_bytes()
        bundle = deepcopy(self.store.read_bundle())
        for method in ("update_card", "update_repair_order"):
            for box in self.boxes:
                for amount in ("0.001", "1.005", "16.666666666667", "1000000000000000"):
                    with self.subTest(method=method, box=box["name"], amount=amount):
                        with (
                            patch.object(self.service, "_save_bundle") as save,
                            patch("minimal_kanban.services.card_service.CashTransaction") as create,
                            self.assertRaises(ServiceError) as caught,
                        ):
                            getattr(self.service, method)(
                                {
                                    "card_id": self.card_id,
                                    "repair_order": {
                                        "payments": [
                                            {
                                                "id": "valid",
                                                "amount": "10",
                                                "cashbox_id": box["id"],
                                            },
                                            {
                                                "id": "bad",
                                                "amount": amount,
                                                "cashbox_id": box["id"],
                                            },
                                        ]
                                    },
                                }
                            )
                        self.assertEqual(caught.exception.code, "validation_error")
                        create.assert_not_called()
                        save.assert_not_called()
                        self.assertEqual(self.state_file.read_bytes(), before)
                        self.assertEqual(self.store.read_bundle(), bundle)

    def test_exact_cents_and_legacy_prepayment_rounding_match_the_ledger(self):
        for amount in ("0.01", "1.01", "16.67", "1\u00a0000,01", "1e12"):
            with self.subTest(amount=amount):
                result = self.service.update_repair_order(
                    {
                        "card_id": self.card_id,
                        "repair_order": {
                            "payments": [
                                {"id": "p", "amount": amount, "cashbox_id": self.boxes[0]["id"]}
                            ]
                        },
                    }
                )
                transactions = self.store.read_bundle()["cash_transactions"]
                self.assertEqual(len(transactions), 1)
                self.assertEqual(
                    Decimal(result["repair_order"]["paid_total"]),
                    Decimal(transactions[0].amount_minor) / 100,
                )
        self.assertEqual(transactions[0].amount_minor, MONEY_MINOR_ABS_MAX)
        result = self.service.update_repair_order(
            {
                "card_id": self.card_id,
                "repair_order": {"payments": [], "prepayment": "1.005", "payment_method": "cash"},
            }
        )
        self.assertEqual(result["repair_order"]["paid_total"], "1.01")
        self.assertEqual(self.store.read_bundle()["cash_transactions"][0].amount_minor, 101)

    def test_historical_link_is_not_rewritten_and_native_cancellation_still_works(self):
        self.service.update_repair_order(
            {
                "card_id": self.card_id,
                "repair_order": {
                    "payments": [{"id": "p", "amount": "1", "cashbox_id": self.boxes[0]["id"]}]
                },
            }
        )
        bundle = self.store.read_bundle()
        bundle["cards"][0].repair_order.payments[0].amount = "1.005"
        self.store.write_bundle(**bundle)
        before = deepcopy(self.store.read_bundle())
        result = self.service.update_repair_order(
            {"card_id": self.card_id, "repair_order": {"comment": "Synthetic unrelated edit"}}
        )
        self.assertEqual(result["repair_order"]["payments"][0]["amount"], "1.005")
        self.assertEqual(self.store.read_bundle()["cash_transactions"], before["cash_transactions"])
        transaction = before["cash_transactions"][0]
        view = self.service.get_cashbox({"cashbox_id": transaction.cashbox_id})
        self.service.cancel_cash_transaction(
            {
                "cashbox_id": transaction.cashbox_id,
                "transaction_id": transaction.id,
                "expected_cashbox_updated_at": view["cashbox"]["updated_at"],
                "reason": "Synthetic historical payment cancellation",
            }
        )
        self.assertFalse(self.store.read_bundle()["cards"][0].repair_order.payments)
        cancelled = self.store.read_bundle()["cash_transactions"]
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(
            {item.transaction_kind for item in cancelled},
            {"cashbox_cancelled", "cashbox_cancellation"},
        )
        self.assertEqual(
            sum(
                item.amount_minor * (1 if item.direction == "income" else -1) for item in cancelled
            ),
            0,
        )

    def test_decimal_precision_cannot_round_subcent_input_into_an_exact_cent(self):
        for value in ("1.000000000000000000000000000001", "1E100000", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ServiceError):
                cash_payment_minor(value)
