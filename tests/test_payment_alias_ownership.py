"""Payment ownership must resolve legacy aliases against the original ledger."""

from __future__ import annotations

# The fixture import selects this checkout before application imports.
# ruff: noqa: I001
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrderPayment
from minimal_kanban.services.errors import ServiceError


class PaymentAliasOwnershipTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)
        self.cashbox = self.service.create_cashbox({"name": "Наличный"})["cashbox"]["id"]
        self.card = self.service.create_card({"title": "Synthetic owner"})["card"]["id"]
        self.transaction = self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "amount_minor": 100, "note": "Synthetic"}
        )["transaction"]["id"]
        self.alias = "CT-" + self.transaction[:8]

    def row(self, ident, reference):
        return {
            "id": ident,
            "amount": "1",
            "cashbox_id": self.cashbox,
            "cash_transaction_id": reference,
        }

    def set_legacy_owner(self, card_id, reference):
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.repair_order.payments = [RepairOrderPayment.from_dict(self.row("old", reference))]
        card.repair_order.prepayment = card.repair_order.prepayment_amount()
        self.store.write_bundle(**bundle)

    def assert_rejected_without_save(self, action, code="repair_order_payment_cash_link_ambiguous"):
        self.store.read_bundle()
        before = deepcopy(self.store.read_bundle())
        with patch.object(
            self.service, "_save_bundle", side_effect=AssertionError("unexpected write")
        ):
            with self.assertRaises(ServiceError) as rejected:
                action()
        self.assertEqual(rejected.exception.code, code)
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.store.read_bundle(), before)

    def test_full_and_short_cannot_both_be_adopted_in_same_order(self):
        for operation in (self.service.update_card, self.service.update_repair_order):
            for references in ((self.transaction, self.alias), (self.alias, self.transaction)):
                with self.subTest(operation=operation.__name__, references=references):
                    rows = [self.row("one", references[0]), self.row("two", references[1])]
                    self.assert_rejected_without_save(
                        lambda: operation(
                            {"card_id": self.card, "repair_order": {"payments": rows}}
                        )
                    )

    def test_full_cannot_be_adopted_when_short_belongs_to_another_order(self):
        other = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.set_legacy_owner(other, self.alias)
        for operation in (self.service.update_card, self.service.update_repair_order):
            with self.subTest(operation=operation.__name__):
                self.assert_rejected_without_save(
                    lambda: operation(
                        {
                            "card_id": self.card,
                            "repair_order": {"payments": [self.row("new", self.transaction)]},
                        }
                    )
                )

    def test_short_cannot_be_adopted_when_full_belongs_to_another_order(self):
        other = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.set_legacy_owner(other, self.transaction)
        for operation in (self.service.update_card, self.service.update_repair_order):
            with self.subTest(operation=operation.__name__):
                self.assert_rejected_without_save(
                    lambda: operation(
                        {
                            "card_id": self.card,
                            "repair_order": {"payments": [self.row("new", self.alias)]},
                        }
                    )
                )

    def test_cancel_shared_transaction_is_rejected(self):
        other = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.set_legacy_owner(self.card, self.alias)
        self.set_legacy_owner(other, self.transaction)
        for action in (
            lambda: self.service.cancel_cash_transaction(
                {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
            ),
            lambda: self.service.cancel_last_cash_transaction({"cashbox_id": self.cashbox}),
        ):
            with self.subTest(action=action):
                self.assert_rejected_without_save(action)

    def test_cancel_ambiguous_legacy_alias_is_rejected(self):
        another = self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "amount_minor": 200, "note": "Synthetic"}
        )["transaction"]["id"]
        bundle = self.store.read_bundle()
        bundle["cash_transactions"] = [
            replace(item, id=self.transaction[:8] + "-0000-0000-0000-000000000002")
            if item.id == another
            else item
            for item in bundle["cash_transactions"]
        ]
        self.store.write_bundle(**bundle)
        self.set_legacy_owner(self.card, self.alias)
        self.assert_rejected_without_save(
            lambda: self.service.cancel_cash_transaction(
                {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
            ),
            code="cash_transaction_ambiguous",
        )

    def test_unrelated_ambiguous_alias_does_not_block_cancellation(self):
        other = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        first = self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "amount_minor": 200, "note": "Synthetic"}
        )["transaction"]["id"]
        second = self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "amount_minor": 300, "note": "Synthetic"}
        )["transaction"]["id"]
        bundle = self.store.read_bundle()
        bundle["cash_transactions"] = [
            replace(
                item,
                id={
                    first: "eeeeeeee-0000-0000-0000-000000000001",
                    second: "eeeeeeee-0000-0000-0000-000000000002",
                }.get(item.id, item.id),
            )
            for item in bundle["cash_transactions"]
        ]
        self.store.write_bundle(**bundle)
        self.set_legacy_owner(other, "CT-eeeeeeee")
        self.set_legacy_owner(self.card, self.alias)
        self.service.cancel_cash_transaction(
            {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
        )
        bundle = self.store.read_bundle()
        peer = next(item for item in bundle["cards"] if item.id == other)
        self.assertEqual(peer.repair_order.payments[0].cash_transaction_id, "CT-eeeeeeee")

    def test_transfer_cancellation_rejects_payment_links_to_either_side(self):
        target = self.service.create_cashbox({"name": "Synthetic target"})["cashbox"]["id"]
        transfer = self.service.create_cashbox_transfer(
            {"from_cashbox_id": self.cashbox, "to_cashbox_id": target, "amount_minor": 100}
        )
        source_tx = transfer["source_transaction"]["id"]
        target_tx = transfer["target_transaction"]["id"]
        source_revision = self.service.get_cashbox({"cashbox_id": self.cashbox})["cashbox"][
            "updated_at"
        ]
        for reference in (source_tx, "CT-" + target_tx[:8]):
            self.set_legacy_owner(self.card, reference)
            for action in (
                lambda: self.service.cancel_cash_transaction(
                    {
                        "transaction_id": target_tx,
                        "reason": "Synthetic cancellation",
                        "expected_related_cashbox_updated_at": source_revision,
                    }
                ),
                lambda: self.service.cancel_last_cash_transaction({"cashbox_id": target}),
            ):
                with self.subTest(reference=reference, action=action):
                    self.assert_rejected_without_save(
                        action, code="repair_order_payment_cash_link_invalid"
                    )

    def test_cancel_legacy_short_removes_the_payment(self):
        self.set_legacy_owner(self.card, self.alias)
        self.service.cancel_cash_transaction(
            {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
        )
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == self.card)
        self.assertEqual(card.repair_order.payments, [])
        self.assertEqual(
            self.service.get_cashbox({"cashbox_id": self.cashbox})["cashbox"]["statistics"][
                "balance_minor"
            ],
            0,
        )

    def test_cancel_last_legacy_short_removes_the_payment(self):
        self.set_legacy_owner(self.card, self.alias)
        self.service.cancel_last_cash_transaction({"cashbox_id": self.cashbox})
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == self.card)
        self.assertEqual(card.repair_order.payments, [])
        self.assertEqual(
            self.service.get_cashbox({"cashbox_id": self.cashbox})["cashbox"]["statistics"][
                "balance_minor"
            ],
            0,
        )

    def test_cancellation_rejects_payment_financial_mismatch(self):
        other_cashbox = self.service.create_cashbox({"name": "Synthetic other"})["cashbox"]["id"]
        for mismatch in ("amount", "cashbox", "direction"):
            with self.subTest(mismatch=mismatch):
                self.set_legacy_owner(self.card, self.alias)
                bundle = self.store.read_bundle()
                payment = next(
                    card for card in bundle["cards"] if card.id == self.card
                ).repair_order.payments[0]
                transaction = next(
                    item for item in bundle["cash_transactions"] if item.id == self.transaction
                )
                transaction.direction = "expense" if mismatch == "direction" else "income"
                if mismatch == "amount":
                    payment.amount = "2"
                elif mismatch == "cashbox":
                    payment.cashbox_id = other_cashbox
                self.store.write_bundle(**bundle)
                for action in (
                    lambda: self.service.cancel_cash_transaction(
                        {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
                    ),
                    lambda: self.service.cancel_last_cash_transaction({"cashbox_id": self.cashbox}),
                ):
                    with self.subTest(action=action):
                        self.assert_rejected_without_save(
                            action, code="repair_order_payment_cash_link_invalid"
                        )

    def test_cancellation_rejects_inexact_and_invalid_legacy_amounts(self):
        for amount, minor in (
            ("1.001", 100),
            ("invalid", 100),
            ("1000000000000.01", 100000000000000),
            ("-1", 100),
            ("0", 100),
        ):
            with self.subTest(amount=amount):
                self.set_legacy_owner(self.card, self.alias)
                bundle = self.store.read_bundle()
                payment = next(
                    card for card in bundle["cards"] if card.id == self.card
                ).repair_order.payments[0]
                payment.amount = amount
                next(
                    item for item in bundle["cash_transactions"] if item.id == self.transaction
                ).amount_minor = minor
                self.store.write_bundle(**bundle)
                for action in (
                    lambda: self.service.cancel_cash_transaction(
                        {"transaction_id": self.transaction, "reason": "Synthetic cancellation"}
                    ),
                    lambda: self.service.cancel_last_cash_transaction({"cashbox_id": self.cashbox}),
                ):
                    with self.subTest(action=action):
                        self.assert_rejected_without_save(
                            action, code="repair_order_payment_cash_link_invalid"
                        )

    def test_exact_kopecks_can_be_cancelled(self):
        for method in ("cancel_cash_transaction", "cancel_last_cash_transaction"):
            with self.subTest(method=method):
                cashbox = self.service.create_cashbox({"name": f"Synthetic {method}"})["cashbox"][
                    "id"
                ]
                card_id = self.service.create_card({"title": "Synthetic decimal"})["card"]["id"]
                transaction = self.service.create_cash_transaction(
                    {"cashbox_id": cashbox, "amount_minor": 101, "note": "Synthetic"}
                )["transaction"]["id"]
                bundle = self.store.read_bundle()
                card = next(item for item in bundle["cards"] if item.id == card_id)
                card.repair_order.payments = [
                    RepairOrderPayment.from_dict(
                        {
                            "id": "decimal",
                            "amount": "1,01",
                            "cashbox_id": cashbox,
                            "cash_transaction_id": "CT-" + transaction[:8],
                        }
                    )
                ]
                self.store.write_bundle(**bundle)
                payload = (
                    {"transaction_id": transaction, "reason": "Synthetic cancellation"}
                    if method == "cancel_cash_transaction"
                    else {"cashbox_id": cashbox}
                )
                getattr(self.service, method)(payload)
                card = next(
                    item for item in self.store.read_bundle()["cards"] if item.id == card_id
                )
                self.assertEqual(card.repair_order.payments, [])
                self.assertEqual(
                    self.service.get_cashbox({"cashbox_id": cashbox})["cashbox"]["statistics"][
                        "balance_minor"
                    ],
                    0,
                )
