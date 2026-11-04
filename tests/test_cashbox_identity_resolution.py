"""Cashbox and transaction identifiers must not select a different object."""

from __future__ import annotations

# The fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.errors import ServiceError


FIRST_ID = "12345678-0000-0000-0000-000000000001"
SECOND_ID = "12345678-0000-0000-0000-000000000002"


class CashboxIdentityResolutionTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)
        self.first = self.service.create_cashbox({"name": "Synthetic A"})["cashbox"]["id"]
        self.second = self.service.create_cashbox({"name": "Synthetic B"})["cashbox"]["id"]

    def replace_ids(self, *, cashboxes=None, transactions=None) -> None:
        bundle = self.store.read_bundle()
        if cashboxes:
            bundle = {
                **bundle,
                "cashboxes": [
                    replace(item, id=cashboxes.get(item.id, item.id))
                    for item in bundle["cashboxes"]
                ],
                "cash_transactions": [
                    replace(item, cashbox_id=cashboxes.get(item.cashbox_id, item.cashbox_id))
                    for item in bundle["cash_transactions"]
                ],
            }
        if transactions:
            bundle = {
                **bundle,
                "cash_transactions": [
                    replace(item, id=transactions.get(item.id, item.id))
                    for item in bundle["cash_transactions"]
                ],
            }
        self.store.write_bundle(**bundle)

    def assert_rejected_without_save(self, code, action, *, status=409) -> None:
        self.store.read_bundle()
        before = self.state_file.read_bytes()
        with patch.object(
            self.service, "_save_bundle", side_effect=AssertionError("unexpected write")
        ):
            with self.assertRaises(ServiceError) as rejected:
                action()
        self.assertEqual(rejected.exception.code, code)
        self.assertEqual(rejected.exception.status_code, status)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_exact_cashbox_wins_over_another_cashbox_short_id(self) -> None:
        self.replace_ids(cashboxes={self.first: FIRST_ID, self.second: "CB-12345678"})
        result = self.service.get_cashbox({"cashbox_id": "CB-12345678"})
        self.assertEqual(result["cashbox"]["id"], "CB-12345678")
        self.assert_rejected_without_save(
            "cashbox_ambiguous", lambda: self.service.get_cashbox({"cashbox_id": "cb-12345678"})
        )
        self.assert_rejected_without_save(
            "cashbox_ambiguous",
            lambda: self.service.create_cash_transaction(
                {"cashbox_id": "cb-12345678", "amount_minor": 100, "note": "Synthetic"}
            ),
        )
        created = self.service.create_cash_transaction(
            {"cashbox_id": "CB-12345678", "amount_minor": 100, "note": "Synthetic"}
        )
        self.assertEqual(created["transaction"]["cashbox_id"], "CB-12345678")
        self.assertEqual(self.service.get_cashbox({"cashbox_id": FIRST_ID})["transactions"], [])

    def test_ambiguous_cashbox_alias_fails_read_and_write_without_saving(self) -> None:
        self.replace_ids(cashboxes={self.first: FIRST_ID, self.second: SECOND_ID})
        self.assert_rejected_without_save(
            "cashbox_ambiguous", lambda: self.service.get_cashbox({"cashbox_id": "CB-12345678"})
        )
        self.assert_rejected_without_save(
            "cashbox_ambiguous",
            lambda: self.service.create_cash_transaction(
                {"cashbox_id": "CB-12345678", "amount_minor": 100, "note": "Synthetic"}
            ),
        )

    def test_reorder_checks_target_alias_before_excluding_moved_cashbox(self) -> None:
        self.replace_ids(cashboxes={self.first: FIRST_ID, self.second: SECOND_ID})
        self.assert_rejected_without_save(
            "cashbox_ambiguous",
            lambda: self.service.reorder_cashboxes(
                {"cashbox_id": FIRST_ID, "before_cashbox_id": "CB-12345678"}
            ),
        )
        before = self.state_file.read_bytes()
        result = self.service.reorder_cashboxes(
            {"cashbox_id": FIRST_ID, "before_cashbox_id": FIRST_ID}
        )
        self.assertFalse(result["meta"]["changed"])
        self.assertEqual(self.state_file.read_bytes(), before)
        self.assert_rejected_without_save(
            "not_found",
            lambda: self.service.reorder_cashboxes(
                {"cashbox_id": FIRST_ID, "before_cashbox_id": "CB-NOTFOUND"}
            ),
            status=404,
        )
        self.replace_ids(cashboxes={SECOND_ID: "87654321-0000-0000-0000-000000000002"})
        self.assert_rejected_without_save(
            "not_found",
            lambda: self.service.reorder_cashboxes(
                {"cashbox_id": FIRST_ID, "before_cashbox_id": "CB-12345678"}
            ),
            status=404,
        )

    def test_exact_transaction_wins_over_another_transaction_short_id(self) -> None:
        created = [
            self.service.create_cash_transaction(
                {"cashbox_id": self.first, "amount_minor": amount, "note": "Synthetic"}
            )["transaction"]["id"]
            for amount in (100, 200)
        ]
        self.replace_ids(transactions={created[0]: FIRST_ID, created[1]: "CT-12345678"})
        self.assert_rejected_without_save(
            "cash_transaction_ambiguous",
            lambda: self.service.cancel_cash_transaction(
                {"transaction_id": "ct-12345678", "reason": "Synthetic cancellation"}
            ),
        )
        result = self.service.cancel_cash_transaction(
            {"transaction_id": "CT-12345678", "reason": "Synthetic cancellation"}
        )
        self.assertEqual(result["cancelled_transaction"]["id"], "CT-12345678")
        entries = self.service.get_cashbox({"cashbox_id": self.first})["transactions"]
        first = next(item for item in entries if item["id"] == FIRST_ID)
        self.assertNotEqual(first["transaction_kind"], "cashbox_cancelled")

    def test_ambiguous_transaction_alias_fails_without_saving(self) -> None:
        created = [
            self.service.create_cash_transaction(
                {"cashbox_id": self.first, "amount_minor": amount, "note": "Synthetic"}
            )["transaction"]["id"]
            for amount in (100, 200)
        ]
        self.replace_ids(transactions={created[0]: FIRST_ID, created[1]: SECOND_ID})
        self.assert_rejected_without_save(
            "cash_transaction_ambiguous",
            lambda: self.service.cancel_cash_transaction(
                {"transaction_id": "CT-12345678", "reason": "Synthetic cancellation"}
            ),
        )

    def test_legacy_ambiguous_payment_alias_cannot_rebind_after_deletion(self) -> None:
        created = [
            self.service.create_cash_transaction(
                {"cashbox_id": self.first, "amount_minor": 100, "note": "Synthetic"}
            )["transaction"]["id"]
            for _ in range(2)
        ]
        self.replace_ids(transactions={created[0]: FIRST_ID, created[1]: SECOND_ID})
        card_id = self.service.create_card({"title": "Synthetic legacy order"})["card"]["id"]
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.repair_order = RepairOrder.from_dict(
            {
                "number": "1",
                "payments": [
                    {
                        "id": "first",
                        "amount": "1",
                        "cashbox_id": self.first,
                        "cash_transaction_id": FIRST_ID,
                    },
                    {
                        "id": "second",
                        "amount": "1",
                        "cashbox_id": self.first,
                        "cash_transaction_id": "CT-12345678",
                    },
                ],
            }
        )
        self.store.write_bundle(**bundle)
        for operation in (self.service.update_repair_order, self.service.update_card):
            with self.subTest(operation=operation.__name__):
                self.assert_rejected_without_save(
                    "cash_transaction_ambiguous",
                    lambda: operation({"card_id": card_id, "repair_order": {"payments": []}}),
                )
                ids = {item.id for item in self.store.read_bundle()["cash_transactions"]}
                self.assertEqual(ids, {FIRST_ID, SECOND_ID})

    def test_exact_payment_reference_does_not_rebind_to_alias_after_removal(self) -> None:
        for operation, prefix in (
            (self.service.update_repair_order, "12345678"),
            (self.service.update_card, "87654321"),
        ):
            with self.subTest(operation=operation.__name__):
                cashbox = self.service.create_cashbox({"name": f"Synthetic {prefix}"})["cashbox"][
                    "id"
                ]
                first = self.service.create_cash_transaction(
                    {"cashbox_id": cashbox, "amount_minor": 100, "note": "Synthetic"}
                )["transaction"]["id"]
                second = self.service.create_cash_transaction(
                    {"cashbox_id": cashbox, "amount_minor": 200, "note": "Synthetic"}
                )["transaction"]["id"]
                exact_id = "CT-" + prefix
                other_id = prefix + "-0000-0000-0000-000000000002"
                self.replace_ids(transactions={first: exact_id, second: other_id})
                other_before = next(
                    item.to_dict()
                    for item in self.store.read_bundle()["cash_transactions"]
                    if item.id == other_id
                )
                card_id = self.service.create_card({"title": f"Synthetic {prefix}"})["card"]["id"]
                initial = self.service.update_repair_order(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "payments": [
                                {
                                    "id": "synthetic",
                                    "amount": "1",
                                    "cashbox_id": cashbox,
                                    "cash_transaction_id": exact_id,
                                }
                            ]
                        },
                    }
                )
                payment = {**initial["repair_order"]["payments"][0], "amount": "2"}
                result = operation({"card_id": card_id, "repair_order": {"payments": [payment]}})
                order = result.get("repair_order") or result["card"]["repair_order"]
                resolved = order["payments"][0]["cash_transaction_id"]
                self.assertNotIn(resolved, (exact_id, other_id))
                transactions = self.store.read_bundle()["cash_transactions"]
                self.assertNotIn(exact_id, {item.id for item in transactions})
                self.assertEqual(
                    next(item.amount_minor for item in transactions if item.id == other_id), 200
                )
                self.assertEqual(
                    next(item.amount_minor for item in transactions if item.id == resolved), 200
                )
                self.assertEqual(
                    next(item.to_dict() for item in transactions if item.id == other_id),
                    other_before,
                )
                cashbox_view = self.service.get_cashbox({"cashbox_id": cashbox})
                self.assertEqual(
                    {item["id"] for item in cashbox_view["transactions"]}, {other_id, resolved}
                )
                self.assertEqual(cashbox_view["cashbox"]["statistics"]["balance_minor"], 400)

    def test_unique_aliases_and_duplicate_exact_ids(self) -> None:
        first = SimpleNamespace(id=FIRST_ID)
        self.assertIs(self.service._find_cashbox([first], "cb-12345678"), first)
        self.assertIs(self.service._find_cash_transaction([first], "ct-12345678"), first)
        with self.assertRaises(ServiceError) as missing:
            self.service._find_cashbox([SimpleNamespace(id="CB-12345678")], "cb-12345678")
        self.assertEqual(missing.exception.code, "not_found")
        self.assertIsNone(
            self.service._find_cash_transaction([SimpleNamespace(id="CT-12345678")], "ct-12345678")
        )
        for code, lookup in (
            ("cashbox_ambiguous", self.service._find_cashbox),
            ("cash_transaction_ambiguous", self.service._find_cash_transaction),
        ):
            with self.subTest(code=code), self.assertRaises(ServiceError) as rejected:
                lookup([first, SimpleNamespace(id=FIRST_ID)], FIRST_ID)
            self.assertEqual(rejected.exception.code, code)
            self.assertEqual(rejected.exception.status_code, 409)

    def test_overlong_ids_do_not_resolve_to_a_valid_prefix(self) -> None:
        cashbox_id = "b" * 128
        self.replace_ids(cashboxes={self.first: cashbox_id})
        self.assert_rejected_without_save(
            "validation_error",
            lambda: self.service.get_cashbox({"cashbox_id": cashbox_id + "x"}),
            status=400,
        )
        self.assert_rejected_without_save(
            "validation_error",
            lambda: self.service.create_cash_transaction(
                {"cashbox_id": cashbox_id + "x", "amount_minor": 100, "note": "Synthetic"}
            ),
            status=400,
        )
        created = self.service.create_cash_transaction(
            {"cashbox_id": cashbox_id, "amount_minor": 100, "note": "Synthetic"}
        )["transaction"]["id"]
        transaction_id = "t" * 128
        self.replace_ids(transactions={created: transaction_id})
        for action in (
            lambda: self.service.cancel_cash_transaction(
                {"transaction_id": transaction_id + "x", "reason": "Synthetic cancellation"}
            ),
            lambda: self.service.cancel_last_cash_transaction(
                {"cashbox_id": cashbox_id, "transaction_id": transaction_id + "x"}
            ),
            lambda: self.service.mark_cashbox_notifications_seen(
                {"actor_name": "Synthetic", "through_transaction_id": transaction_id + "x"}
            ),
            lambda: self.service.cancel_cash_transaction(
                {
                    "transaction_id": transaction_id,
                    "cashbox_id": cashbox_id + "x",
                    "reason": "Synthetic cancellation",
                }
            ),
        ):
            with self.subTest(action=action):
                self.assert_rejected_without_save("validation_error", action, status=400)

        card_id = self.service.create_card({"title": "Synthetic order"})["card"]["id"]
        cashbox = self.service.get_cashbox({"cashbox_id": cashbox_id})["cashbox"]
        self.assert_rejected_without_save(
            "validation_error",
            lambda: self.service.update_repair_order(
                {
                    "card_id": card_id,
                    "repair_order": {"note": "Synthetic"},
                    "expected_cashbox_id": cashbox_id + "x",
                    "expected_cashbox_updated_at": cashbox["updated_at"],
                }
            ),
            status=400,
        )

    def test_overlong_payment_links_are_rejected_before_normalization(self) -> None:
        cashbox_id = "b" * 128
        self.replace_ids(cashboxes={self.first: cashbox_id})
        created = self.service.create_cash_transaction(
            {"cashbox_id": cashbox_id, "amount_minor": 100, "note": "Synthetic"}
        )["transaction"]["id"]
        transaction_id = "t" * 128
        self.replace_ids(transactions={created: transaction_id})
        card_id = self.service.create_card({"title": "Synthetic order"})["card"]["id"]
        for operation in (self.service.update_repair_order, self.service.update_card):
            for field in ("cashbox_id", "cashboxId", "cash_transaction_id", "cashTransactionId"):
                for collection in ("payments", "payment_history"):
                    with self.subTest(
                        operation=operation.__name__, field=field, collection=collection
                    ):
                        value = (
                            cashbox_id if field in ("cashbox_id", "cashboxId") else transaction_id
                        )
                        self.assert_rejected_without_save(
                            "validation_error",
                            lambda: operation(
                                {
                                    "card_id": card_id,
                                    "repair_order": {
                                        collection: [{"amount": "1", field: value + "x"}]
                                    },
                                }
                            ),
                            status=400,
                        )
