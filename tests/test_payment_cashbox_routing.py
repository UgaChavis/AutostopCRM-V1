from __future__ import annotations

# Fixture import selects this checkout before application imports.
# ruff: noqa: I001
import http.client
import json
from contextlib import closing, contextmanager
from copy import deepcopy
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


METHODS = ("update_card", "update_repair_order")


class PaymentCashboxRoutingTests(CardServiceCase):
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
            closing(JsonStore(Path(directory) / "state.json", self.logger)) as store,
        ):
            root = Path(directory)
            service = CardService(store, self.logger)
            old_box = service.create_cashbox({"name": "Сменная касса"})["cashbox"]
            card_id = service.create_card({"title": "Synthetic payment routing"})["card"]["id"]
            order = service.update_repair_order(
                {
                    "card_id": card_id,
                    "repair_order": {
                        "payments": [
                            {
                                "id": "old-payment",
                                "amount": "100",
                                "paid_at": "15.10.2026 09:00",
                                "cashbox_id": old_box["id"],
                            }
                        ]
                    },
                }
            )["repair_order"]
            yield root, service, store, card_id, old_box, order["payments"][0]

    def _assert_ledger_unchanged(self, case, before, payment):
        root, service, store, card_id, _, _ = case
        after = store.read_bundle()
        self.assertEqual(before["cash_transactions"], after["cash_transactions"])
        self.assertEqual(before["cashboxes"], after["cashboxes"])
        self.assertEqual(
            [payment],
            service.get_repair_order({"card_id": card_id})["repair_order"]["payments"],
        )
        with closing(JsonStore(root / "state.json", self.logger)) as reloaded:
            loaded = reloaded.read_bundle()
            self.assertEqual(before["cash_transactions"], loaded["cash_transactions"])
            self.assertEqual(before["cashboxes"], loaded["cashboxes"])
            self.assertEqual(
                [payment],
                [p.to_storage_dict() for p in loaded["cards"][0].repair_order.payments],
            )

    def test_comment_edits_preserve_payment_before_and_after_a_default_cashbox_is_added(self):
        for fast, method, echo_payment in product((False, True), METHODS, (False, True)):
            with (
                self.subTest(fast=fast, method=method, echo_payment=echo_payment),
                self._case(fast) as case,
            ):
                _, service, store, card_id, _, payment = case
                for canonical_exists in (False, True):
                    if canonical_exists:
                        service.create_cashbox({"name": "Наличный"})
                    before = deepcopy(store.read_bundle())
                    fields = {"comment": f"Unrelated text: {canonical_exists}"}
                    if echo_payment:
                        fields["payments"] = [deepcopy(payment)]
                    result = getattr(service, method)({"card_id": card_id, "repair_order": fields})
                    self.assertTrue(result["meta"]["changed"])
                    self._assert_ledger_unchanged(case, before, payment)

    def test_new_payments_use_the_new_default_without_moving_the_existing_payment(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                _, service, store, card_id, _, payment = case
                canonical = service.create_cashbox({"name": "Наличный"})["cashbox"]
                old_transaction = deepcopy(store.read_bundle()["cash_transactions"][0])
                result = getattr(service, method)(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "payments": [deepcopy(payment), {"id": "new", "amount": "50"}]
                        },
                    }
                )
                payments = result["card"]["repair_order"]["payments"]
                self.assertEqual(payment, payments[0])
                self.assertEqual(canonical["id"], payments[1]["cashbox_id"])
                transactions = store.read_bundle()["cash_transactions"]
                self.assertEqual(2, len(transactions))
                self.assertIn(old_transaction, transactions)
                self.assertEqual(15000, sum(t.amount_minor for t in transactions))

    def test_explicit_payment_cashbox_changes_still_replace_the_selected_movement(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                _, service, store, card_id, _, payment = case
                canonical = service.create_cashbox({"name": "Наличный"})["cashbox"]
                result = getattr(service, method)(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "payments": [
                                {**payment, "cashbox_id": canonical["id"], "amount": "150"}
                            ]
                        },
                    }
                )
                changed = result["card"]["repair_order"]["payments"][0]
                self.assertEqual(canonical["id"], changed["cashbox_id"])
                self.assertEqual("150", changed["amount"])
                self.assertNotEqual(payment["cash_transaction_id"], changed["cash_transaction_id"])
                transactions = store.read_bundle()["cash_transactions"]
                self.assertEqual(1, len(transactions))
                self.assertEqual(changed["cash_transaction_id"], transactions[0].id)
                self.assertEqual(canonical["id"], transactions[0].cashbox_id)
                self.assertEqual(15000, transactions[0].amount_minor)

    def test_amount_and_note_edits_keep_the_payment_in_its_original_cashbox(self):
        for fast, method, field in product((False, True), METHODS, ("amount", "note")):
            with self.subTest(fast=fast, method=method, field=field), self._case(fast) as case:
                _, service, store, card_id, old_box, payment = case
                value = "150" if field == "amount" else "Corrected note"
                service.create_cashbox({"name": "Наличный"})
                result = getattr(service, method)(
                    {
                        "card_id": card_id,
                        "repair_order": {"payments": [{**payment, field: value}]},
                    }
                )
                changed = result["card"]["repair_order"]["payments"][0]
                self.assertEqual(old_box["id"], changed["cashbox_id"])
                self.assertEqual(value, changed[field])
                transactions = store.read_bundle()["cash_transactions"]
                self.assertEqual(1, len(transactions))
                self.assertEqual(old_box["id"], transactions[0].cashbox_id)
                self.assertNotEqual(payment["cash_transaction_id"], transactions[0].id)
                self.assertEqual(
                    15000 if field == "amount" else 10000, transactions[0].amount_minor
                )

    def test_explicit_payment_method_changes_still_choose_the_matching_default(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                _, service, store, card_id, _, payment = case
                bank = service.create_cashbox({"name": "Безналичный"})["cashbox"]
                result = getattr(service, method)(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "payments": [
                                {**payment, "payment_method": "cashless", "cashbox_name": ""}
                            ]
                        },
                    }
                )
                changed = result["card"]["repair_order"]["payments"][0]
                self.assertEqual(bank["id"], changed["cashbox_id"])
                self.assertEqual("cashless", changed["payment_method"])
                self.assertEqual(1, len(store.read_bundle()["cash_transactions"]))

    def test_legacy_duplicate_payment_ids_match_the_selected_cash_link(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                _, service, store, card_id, old_box, payment = case
                second_box = service.create_cashbox({"name": "Вторая смена"})["cashbox"]
                service.update_repair_order(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "payments": [
                                payment,
                                {"id": "second", "amount": "50", "cashbox_id": second_box["id"]},
                            ]
                        },
                    }
                )
                bundle = store.read_bundle()
                for old in bundle["cards"][0].repair_order.payments:
                    old.id = "duplicate"
                store.write_bundle(**bundle)
                service.create_cashbox({"name": "Наличный"})
                before = deepcopy(store.read_bundle())
                ambiguous = {**payment, "id": "duplicate", "cash_transaction_id": ""}
                with self.assertRaises(ServiceError) as caught:
                    getattr(service, method)(
                        {"card_id": card_id, "repair_order": {"payments": [ambiguous]}}
                    )
                self.assertEqual("repair_order_payment_id_ambiguous", caught.exception.code)
                self.assertEqual(before, store.read_bundle())
                selected = {**payment, "id": "duplicate"}
                result = getattr(service, method)(
                    {"card_id": card_id, "repair_order": {"payments": [selected]}}
                )
                self.assertEqual(selected, result["card"]["repair_order"]["payments"][0])
                transactions = store.read_bundle()["cash_transactions"]
                self.assertEqual(1, len(transactions))
                self.assertEqual(old_box["id"], transactions[0].cashbox_id)
                self.assertEqual(payment["cash_transaction_id"], transactions[0].id)

    def test_http_comment_edits_preserve_the_original_payment_and_cashbox_revision(self):
        for fast, route in product((False, True), METHODS):
            with self.subTest(fast=fast, route=route), self._case(fast) as case:
                root, service, store, card_id, _, payment = case
                service.create_cashbox({"name": "Наличный"})
                auth = OperatorAuthService(
                    store, service, users_file=root / "users.json", logger=self.logger
                )
                token = auth.login({"username": "admin", "password": "admin"})["session"]["token"]
                server = ApiServer(
                    service, self.logger, operator_service=auth, bearer_token="", start_port=0
                )
                server.start()
                try:
                    before = deepcopy(store.read_bundle())
                    with closing(
                        http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                    ) as connection:
                        connection.request(
                            "POST",
                            f"/api/{route}",
                            body=json.dumps(
                                {"card_id": card_id, "repair_order": {"comment": "HTTP text"}}
                            ).encode("utf-8"),
                            headers={
                                "Content-Type": "application/json",
                                "X-Operator-Session": token,
                            },
                        )
                        response = connection.getresponse()
                        result = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(200, response.status, result)
                    self._assert_ledger_unchanged(case, before, payment)
                finally:
                    server.stop()
