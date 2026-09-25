from __future__ import annotations

from dataclasses import replace

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.services.errors import ServiceError
from tests.services_case import CardServiceCase


class CashCancellationIntegrityTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        self.first = self.service.create_cashbox({"name": "Synthetic first"})["cashbox"]
        self.second = self.service.create_cashbox({"name": "Synthetic second"})["cashbox"]

    def _cancel_transfer(self) -> dict:
        transferred = self.service.create_cashbox_transfer(
            {
                "from_cashbox_id": self.first["id"],
                "to_cashbox_id": self.second["id"],
                "amount": "100",
                "note": "Synthetic transfer",
            }
        )
        return self.service.cancel_cash_transaction(
            {
                "transaction_id": transferred["source_transaction"]["id"],
                "expected_related_cashbox_updated_at": transferred["to_cashbox"]["updated_at"],
                "reason": "Synthetic cancellation",
            }
        )

    def _reject_legacy_cancel(self, cashbox_id: str) -> None:
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as caught:
            self.service.cancel_last_cash_transaction({"cashbox_id": cashbox_id})
        self.assertEqual(caught.exception.code, "validation_error")
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assertEqual(
            self.service.get_cashbox({"cashbox_id": cashbox_id})["cashbox"]["statistics"][
                "balance_minor"
            ],
            0,
        )

    def test_legacy_cancel_cannot_delete_manual_reversal(self) -> None:
        created = self.service.create_cash_transaction(
            {"cashbox_id": self.first["id"], "direction": "income", "amount": "100"}
        )
        self.service.cancel_cash_transaction(
            {"transaction_id": created["transaction"]["id"], "reason": "Synthetic cancellation"}
        )
        self._reject_legacy_cancel(self.first["id"])
        self.assertEqual(len(self.store.read_bundle()["cash_transactions"]), 2)

    def test_legacy_cancel_cannot_delete_either_transfer_reversal(self) -> None:
        self._cancel_transfer()
        for cashbox in (self.first, self.second):
            with self.subTest(cashbox=cashbox["name"]):
                self._reject_legacy_cancel(cashbox["id"])
        self.assertEqual(len(self.store.read_bundle()["cash_transactions"]), 4)

    def test_cancelled_transfer_stays_internal_in_all_journal_totals(self) -> None:
        self._cancel_transfer()
        for compact in (False, True):
            with self.subTest(compact=compact):
                journal = self.service.get_cash_journal({"compact_groups": compact})
                groups = [
                    journal["totals"],
                    *journal["days"],
                    *journal["weeks"],
                    *journal["months"],
                ]
                for group in groups:
                    self.assertEqual(group["external_income_minor"], 0)
                    self.assertEqual(group["external_expense_minor"], 0)
                    self.assertEqual(group["transfer_income_minor"], 20000)
                    self.assertEqual(group["transfer_expense_minor"], 20000)
                    self.assertEqual(group["balance_minor"], 0)
                self.assertEqual(len(journal["entries"]), 4)
                self.assertEqual(
                    {entry["source_label"] for entry in journal["entries"]}, {"отмена", "отменено"}
                )

    def test_legacy_cancel_still_removes_ordinary_latest_movement(self) -> None:
        self.service.create_cash_transaction(
            {"cashbox_id": self.first["id"], "direction": "income", "amount": "100"}
        )
        result = self.service.cancel_last_cash_transaction({"cashbox_id": self.first["id"]})
        self.assertTrue(result["meta"]["cancelled"])
        self.assertEqual(result["cashbox"]["statistics"]["balance_minor"], 0)
        self.assertEqual(self.store.read_bundle()["cash_transactions"], [])

    def test_legacy_cancel_rejects_original_sorted_after_its_reversal(self) -> None:
        created = self.service.create_cash_transaction(
            {"cashbox_id": self.first["id"], "direction": "income", "amount": "100"}
        )["transaction"]
        self.service.cancel_cash_transaction(
            {"transaction_id": created["id"], "reason": "Synthetic cancellation"}
        )
        for kind in ("cashbox_cancelled", ""):
            with self.subTest(kind=kind):
                bundle = self.store.read_bundle()
                # A legacy row can lack its marker, or have a later business timestamp.
                transactions = [
                    replace(item, created_at="2099-01-01T00:00:00+00:00", transaction_kind=kind)
                    if item.id == created["id"]
                    else item
                    for item in bundle["cash_transactions"]
                ]
                self.store.write_bundle(**{**bundle, "cash_transactions": transactions})
                self._reject_legacy_cancel(self.first["id"])

    def test_manual_reversal_remains_external_turnover(self) -> None:
        created = self.service.create_cash_transaction(
            {"cashbox_id": self.first["id"], "direction": "income", "amount": "100"}
        )["transaction"]
        self.service.cancel_cash_transaction(
            {"transaction_id": created["id"], "reason": "Synthetic cancellation"}
        )
        totals = self.service.get_cash_journal()["totals"]
        self.assertEqual(totals["external_income_minor"], 10000)
        self.assertEqual(totals["external_expense_minor"], 10000)
        self.assertEqual(totals["transfer_income_minor"], 0)
        self.assertEqual(totals["transfer_expense_minor"], 0)
