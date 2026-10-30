from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

# The fixture import sets up the source path.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError


class CashboxRevisionIntegrityTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.cashbox = self.service.create_cashbox({"name": "Synthetic cash"})["cashbox"]["id"]
        self.card = self.service.create_card({"title": "Synthetic payment"})["card"]["id"]

    def _view(self):
        return self.service.get_cashbox({"cashbox_id": self.cashbox})

    def _payment(self, payment_id):
        return {
            "id": payment_id,
            "amount": "100",
            "cashbox_id": self.cashbox,
            "paid_at": "15.10.2026 09:00",
        }

    def _update_payments(self, payments):
        return self.service.update_repair_order(
            {"card_id": self.card, "repair_order": {"payments": payments, "prepayment": ""}}
        )

    def _assert_stale_write_rejected(self, revision):
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.service.create_cash_transaction(
                {
                    "cashbox_id": self.cashbox,
                    "direction": "income",
                    "amount": "1",
                    "expected_updated_at": revision,
                }
            )
        self.assertEqual(error.exception.code, "cashbox_update_conflict")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(before, self.state_file.read_bytes())

    def test_deleting_order_payment_invalidates_cashbox_revision(self):
        self._update_payments([self._payment("one")])
        old = self._view()
        self._update_payments([])
        new = self._view()
        self.assertEqual(len(old["transactions"]), 1)
        self.assertEqual(new["transactions"], [])
        self.assertNotEqual(old["cashbox"]["updated_at"], new["cashbox"]["updated_at"])
        self._assert_stale_write_rejected(old["cashbox"]["updated_at"])

    def test_backdated_payments_with_same_business_time_have_distinct_revisions(self):
        first = self._update_payments([self._payment("one")])["repair_order"]["payments"]
        old = self._view()
        self._update_payments(first + [self._payment("two")])
        new = self._view()
        self.assertEqual(len(new["transactions"]), 2)
        self.assertEqual(new["transactions"][0]["created_at"], new["transactions"][1]["created_at"])
        self.assertNotEqual(old["cashbox"]["updated_at"], new["cashbox"]["updated_at"])
        self._assert_stale_write_rejected(old["cashbox"]["updated_at"])

    def test_deleting_latest_transaction_never_reuses_an_old_revision(self):
        self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "direction": "income", "amount": "1"}
        )
        old = self._view()["cashbox"]["updated_at"]
        self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "direction": "income", "amount": "2"}
        )
        self.service.cancel_last_cash_transaction({"cashbox_id": self.cashbox})
        self.assertNotEqual(self._view()["cashbox"]["updated_at"], old)
        self._assert_stale_write_rejected(old)

    def test_same_clock_tick_still_advances_revision(self):
        moment = datetime(2030, 1, 1, tzinfo=UTC)
        with patch("minimal_kanban.models.utc_now", return_value=moment):
            self.service.create_cash_transaction(
                {"cashbox_id": self.cashbox, "direction": "income", "amount": "1"}
            )
            old = self._view()["cashbox"]["updated_at"]
            self.service.create_cash_transaction(
                {"cashbox_id": self.cashbox, "direction": "income", "amount": "2"}
            )
        self.assertNotEqual(self._view()["cashbox"]["updated_at"], old)
        self._assert_stale_write_rejected(old)
