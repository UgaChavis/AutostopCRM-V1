from __future__ import annotations

# The fixture import establishes the application source path.
# ruff: noqa: I001
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


FROZEN_TIME = datetime(2030, 1, 1, tzinfo=UTC)


class CashboxRevisionTransferTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.store.close)

    @contextmanager
    def _case(self, fast):
        with (
            TemporaryDirectory() as directory,
            patch(
                "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                return_value=fast,
            ),
            patch("minimal_kanban.models.utc_now", return_value=FROZEN_TIME),
            closing(JsonStore(Path(directory) / "state.json", self.logger)) as store,
        ):
            service = CardService(store, self.logger)
            first = service.create_cashbox({"name": "Synthetic first"})["cashbox"]
            second = service.create_cashbox({"name": "Synthetic second"})["cashbox"]
            yield service, Path(directory) / "state.json", first, second

    def _assert_advanced(self, before, after):
        self.assertGreater(
            datetime.fromisoformat(after["updated_at"]),
            datetime.fromisoformat(before["updated_at"]),
        )

    def _assert_persisted(self, state_file, *cashboxes):
        with closing(JsonStore(state_file, self.logger)) as store:
            reloaded = CardService(store, self.logger)
            for cashbox in cashboxes:
                view = reloaded.get_cashbox({"cashbox_id": cashbox["id"]})
                self.assertEqual(view["cashbox"]["updated_at"], cashbox["updated_at"])

    def _assert_stale_write_rejected(self, service, state_file, cashbox):
        before = state_file.read_bytes()
        with self.assertRaises(ServiceError) as caught:
            service.create_cash_transaction(
                {
                    "cashbox_id": cashbox["id"],
                    "amount_minor": 100,
                    "expected_updated_at": cashbox["updated_at"],
                }
            )
        self.assertEqual(caught.exception.code, "cashbox_update_conflict")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(state_file.read_bytes(), before)

    def _transfer_payload(self, first, second):
        return {
            "from_cashbox_id": first["id"],
            "to_cashbox_id": second["id"],
            "expected_from_updated_at": first["updated_at"],
            "expected_to_updated_at": second["updated_at"],
            "amount_minor": 100,
            "note": "Synthetic revision transfer",
        }

    def test_selected_cancellation_of_future_payment_preserves_business_dates(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast) as (service, state_file, first, _):
                card = service.create_card({"title": "Synthetic future payment"})["card"]
                order = service.update_repair_order(
                    {
                        "card_id": card["id"],
                        "repair_order": {
                            "payments": [
                                {
                                    "id": "future-payment",
                                    "amount": "1",
                                    "cashbox_id": first["id"],
                                    "paid_at": "01.01.2040 12:00",
                                }
                            ]
                        },
                    }
                )["repair_order"]
                old = service.get_cashbox({"cashbox_id": first["id"]})
                original = old["transactions"][0]
                self.assertEqual(datetime.fromisoformat(original["created_at"]).year, 2040)
                cancelled = service.cancel_cash_transaction(
                    {
                        "cashbox_id": first["id"],
                        "transaction_id": order["payments"][0]["cash_transaction_id"],
                        "expected_cashbox_updated_at": old["cashbox"]["updated_at"],
                        "reason": "Synthetic future payment cancellation",
                    }
                )
                self._assert_advanced(old["cashbox"], cancelled["cashbox"])
                self.assertEqual(
                    cancelled["cancelled_transaction"]["created_at"], original["created_at"]
                )
                self.assertEqual(
                    datetime.fromisoformat(cancelled["cancellation_transaction"]["created_at"]),
                    FROZEN_TIME,
                )
                self.assertEqual(cancelled["cashbox"]["statistics"]["balance_minor"], 0)
                self._assert_persisted(state_file, cancelled["cashbox"])
                self._assert_stale_write_rejected(service, state_file, old["cashbox"])
                accepted = service.create_cash_transaction(
                    {
                        "cashbox_id": first["id"],
                        "amount_minor": 100,
                        "expected_updated_at": cancelled["cashbox"]["updated_at"],
                    }
                )
                self._assert_advanced(cancelled["cashbox"], accepted["cashbox"])

    def test_legacy_transfer_cancel_does_not_restore_either_initial_revision(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast) as (service, state_file, first, second):
                transfer = service.create_cashbox_transfer(self._transfer_payload(first, second))
                self._assert_advanced(first, transfer["from_cashbox"])
                self._assert_advanced(second, transfer["to_cashbox"])
                cancelled = service.cancel_last_cash_transaction(
                    {
                        "cashbox_id": first["id"],
                        "expected_cashbox_updated_at": transfer["from_cashbox"]["updated_at"],
                    }
                )
                self._assert_advanced(transfer["from_cashbox"], cancelled["cashbox"])
                self._assert_advanced(transfer["to_cashbox"], cancelled["related_cashbox"])
                for initial in (first, second):
                    self.assertEqual(
                        service.get_cashbox({"cashbox_id": initial["id"]})["transactions"], []
                    )
                    self._assert_stale_write_rejected(service, state_file, initial)
                self._assert_persisted(
                    state_file, cancelled["cashbox"], cancelled["related_cashbox"]
                )
                repeated = service.create_cashbox_transfer(
                    self._transfer_payload(cancelled["cashbox"], cancelled["related_cashbox"])
                )
                self._assert_advanced(cancelled["cashbox"], repeated["from_cashbox"])
                self._assert_advanced(cancelled["related_cashbox"], repeated["to_cashbox"])

    def test_selected_transfer_cancel_checks_peer_revision_and_advances_both(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast) as (service, state_file, first, second):
                transfer = service.create_cashbox_transfer(self._transfer_payload(first, second))
                request = {
                    "cashbox_id": first["id"],
                    "transaction_id": transfer["source_transaction"]["id"],
                    "expected_cashbox_updated_at": transfer["from_cashbox"]["updated_at"],
                    "expected_related_cashbox_updated_at": second["updated_at"],
                    "reason": "Synthetic paired cancellation",
                }
                before = state_file.read_bytes()
                with self.assertRaises(ServiceError) as caught:
                    service.cancel_cash_transaction(request)
                self.assertEqual(caught.exception.code, "cashbox_update_conflict")
                self.assertEqual(state_file.read_bytes(), before)
                request["expected_related_cashbox_updated_at"] = transfer["to_cashbox"][
                    "updated_at"
                ]
                cancelled = service.cancel_cash_transaction(request)
                self._assert_advanced(transfer["from_cashbox"], cancelled["cashbox"])
                self._assert_advanced(transfer["to_cashbox"], cancelled["related_cashbox"])
                for previous, current in (
                    (transfer["from_cashbox"], cancelled["cashbox"]),
                    (transfer["to_cashbox"], cancelled["related_cashbox"]),
                ):
                    view = service.get_cashbox({"cashbox_id": current["id"]})
                    self.assertEqual(len(view["transactions"]), 2)
                    self.assertEqual(view["cashbox"]["statistics"]["balance_minor"], 0)
                    self._assert_stale_write_rejected(service, state_file, previous)
                self._assert_persisted(
                    state_file, cancelled["cashbox"], cancelled["related_cashbox"]
                )

    def test_transfer_revision_advances_even_when_wall_clock_moves_backwards(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast) as (service, state_file, first, second):
                earlier = datetime(2029, 1, 1, tzinfo=UTC)
                with patch("minimal_kanban.models.utc_now", return_value=earlier):
                    transfer = service.create_cashbox_transfer(
                        self._transfer_payload(first, second)
                    )
                self._assert_advanced(first, transfer["from_cashbox"])
                self._assert_advanced(second, transfer["to_cashbox"])
                for key in ("source_transaction", "target_transaction"):
                    self.assertEqual(datetime.fromisoformat(transfer[key]["created_at"]), earlier)
                self._assert_persisted(state_file, transfer["from_cashbox"], transfer["to_cashbox"])
