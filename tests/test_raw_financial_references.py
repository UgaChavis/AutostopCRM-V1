"""Raw finance references must not be truncated or discarded before checks."""

# The fixture selects the checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrderPayment
from minimal_kanban.storage.json_store import StateFileCorruptedError


class RawFinancialReferenceTests(CardServiceCase):
    def setUp(self):
        app_data = tempfile.TemporaryDirectory(prefix="synthetic-finance-raw-")
        self.addCleanup(app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.addCleanup(self.store.close)
        self.service = self._build_service()
        box = self.service.create_cashbox({"name": "Synthetic"})["cashbox"]
        self.service.create_cash_transaction(
            {"cashbox_id": box["id"], "amount_minor": 100, "note": "Synthetic"}
        )
        self.card = self.service.create_card({"title": "Synthetic"})["card"]["id"]
        self.other_card = self.service.create_card({"title": "Other"})["card"]["id"]
        self.original = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.backup = self.state_file.with_suffix(".corrupted.json")
        self.backup.write_bytes(b"synthetic backup")

    def replace_state(self, state):
        path = self.state_file.with_name("synthetic-replacement.tmp")
        path.write_text(json.dumps(state), encoding="utf-8")
        os.replace(path, self.state_file)

    def protected_files(self):
        paths = [
            self.state_file,
            self.backup,
            *self.state_file.parent.glob("*.sqlite3"),
            *self.state_file.parent.glob("*.sqlite3-wal"),
        ]
        return {
            str(path): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
            for path in paths
            if path.exists()
        }

    def test_oversized_finance_references_and_missing_transaction_fields_fail_closed(self):
        variants = (
            "transaction_id",
            "transaction_cashbox_id",
            "related_transaction_id",
            "transfer_group_id",
            "cashbox_id",
            "cashbox_id_missing",
            "payment_cash_transaction_id",
            "payment_cashbox_id",
            "payment_camel_transaction",
            "payment_camel_cashbox",
            "transaction_id_missing",
            "transaction_cashbox_id_missing",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                state = deepcopy(self.original)
                transaction = state["cash_transactions"][0]
                card = next(item for item in state["cards"] if item["id"] == self.card)
                card["repair_order"] = {"payments": [{"id": "payment", "amount": "1"}]}
                payment = card["repair_order"]["payments"][0]
                if variant in {"cashbox_id", "cashbox_id_missing"}:
                    if variant.endswith("_missing"):
                        state["cashboxes"][0].pop("id")
                    else:
                        state["cashboxes"][0]["id"] = "X" * 129
                elif variant.startswith("payment_"):
                    names = {
                        "payment_cash_transaction_id": "cash_transaction_id",
                        "payment_cashbox_id": "cashbox_id",
                        "payment_camel_transaction": "cashTransactionId",
                        "payment_camel_cashbox": "cashboxId",
                    }
                    payment[names[variant]] = "X" * 129
                    if "camel" in variant:
                        card["repair_order"]["payment_history"] = card["repair_order"].pop(
                            "payments"
                        )
                elif variant.endswith("_missing"):
                    transaction.pop("id" if variant == "transaction_id_missing" else "cashbox_id")
                else:
                    names = {"transaction_id": "id", "transaction_cashbox_id": "cashbox_id"}
                    transaction[names.get(variant, variant)] = "X" * 129
                self.replace_state(state)
                before = self.protected_files()
                for action in (
                    self.store.read_bundle,
                    self.store.reconcile_change_feed,
                    self.service.get_finance_audit,
                    lambda: self.service.apply_finance_audit_safe_fixes({"dry_run": False}),
                ):
                    with self.assertRaises(StateFileCorruptedError):
                        action()
                    self.assertEqual(self.protected_files(), before)

    def test_scoped_snapshots_keep_unrelated_cards_accessible(self):
        state = deepcopy(self.original)
        card = next(item for item in state["cards"] if item["id"] == self.card)
        card["repair_order"] = {
            "payments": [{"id": "payment", "amount": "1", "cash_transaction_id": "X" * 129}]
        }
        self.replace_state(state)
        before = self.protected_files()
        self.assertEqual(
            self.service.get_card({"card_id": self.other_card})["card"]["id"], self.other_card
        )
        with self.assertRaises(StateFileCorruptedError):
            self.service.get_card({"card_id": self.card})
        self.assertEqual(self.protected_files(), before)

    def test_boundary_and_primary_field_precedence_are_preserved(self):
        state = deepcopy(self.original)
        state["cash_transactions"][0]["related_transaction_id"] = "X" * 128
        card = next(item for item in state["cards"] if item["id"] == self.card)
        card["repair_order"] = {
            "payments": [
                {
                    "id": "payment",
                    "amount": "1",
                    "cash_transaction_id": None,
                    "cashTransactionId": "X" * 129,
                    "cashbox_id": "A" + " " * 500 + "B",
                }
            ]
        }
        self.replace_state(state)
        bundle = self.store.read_bundle()
        self.assertEqual(len(bundle["cash_transactions"][0].related_transaction_id), 128)
        self.assertEqual(
            next(item for item in bundle["cards"] if item.id == self.card)
            .repair_order.payments[0]
            .cash_transaction_id,
            "",
        )
        self.assertEqual(
            next(item for item in bundle["cards"] if item.id == self.card)
            .repair_order.payments[0]
            .cashbox_id,
            "A B",
        )

    def test_direct_and_cached_writes_reject_mutated_invalid_identifiers(self):
        for variant in (
            "transaction_id",
            "transaction_cashbox",
            "related",
            "group",
            "cashbox",
            "cashbox_missing",
            "payment_cashbox",
            "payment_transaction",
        ):
            for cached in (False, True):
                with self.subTest(variant=variant, cached=cached):
                    source, signature = self.store.read_bundle_with_signature()
                    payload = deepcopy(source)
                    transaction = payload["cash_transactions"][0]
                    if variant == "transaction_id":
                        transaction.id = ""
                    elif variant == "transaction_cashbox":
                        transaction.cashbox_id = ""
                    elif variant == "related":
                        transaction.related_transaction_id = "X" * 129
                    elif variant == "group":
                        transaction.transfer_group_id = "X" * 129
                    elif variant == "cashbox":
                        payload["cashboxes"][0].id = "X" * 129
                    elif variant == "cashbox_missing":
                        payload["cashboxes"][0].id = ""
                    else:
                        card = next(item for item in payload["cards"] if item.id == self.card)
                        payment = RepairOrderPayment(amount="1")
                        setattr(
                            payment,
                            "cashbox_id" if variant == "payment_cashbox" else "cash_transaction_id",
                            "X" * 129,
                        )
                        card.repair_order.payments = [payment]
                    before = self.protected_files()
                    with self.assertRaises(ValueError):
                        if cached:
                            self.store.write_cached_bundle(
                                source, **payload, expected_signature=signature
                            )
                        else:
                            self.store.write_bundle(**payload, expected_signature=signature)
                    self.assertEqual(self.protected_files(), before)
