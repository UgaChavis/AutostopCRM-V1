from __future__ import annotations

# The fixture import sets up the src path before importing application modules.
# ruff: noqa: I001
import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
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
AMBIGUOUS = "repair_order_payment_id_ambiguous"


class PaymentReconciliationTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.store.close)

    @contextmanager
    def _case(self, *, fast, amounts=(100, 100), duplicate=True):
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
            box = service.create_cashbox({"name": "Наличный"})["cashbox"]
            card = service.create_card({"vehicle": "TEST", "title": "Сверка старых оплат"})["card"]
            service.update_repair_order(
                {
                    "card_id": card["id"],
                    "actor_name": "ALICE",
                    "source": "api",
                    "repair_order": {
                        "works": [
                            {
                                "id": "work",
                                "name": "Диагностика",
                                "quantity": "1",
                                "price": str(sum(amounts)),
                            }
                        ],
                        "payments": [
                            {
                                "id": f"payment-{i}",
                                "amount": str(amount),
                                "paid_at": "15.10.2026 10:00",
                                "note": "Аванс",
                                "cashbox_id": box["id"],
                            }
                            for i, amount in enumerate(amounts)
                        ],
                    },
                }
            )
            bundle = store.read_bundle()
            if duplicate:
                for payment in bundle["cards"][0].repair_order.payments:
                    payment.id = "legacy-duplicate"
                store.write_bundle(**bundle)
            rows = [p.to_storage_dict() for p in bundle["cards"][0].repair_order.payments]
            yield root, service, store, card["id"], box["id"], rows

    def _files(self, root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def _feed_dump(self, root):
        with closing(sqlite3.connect(root / "change_feed.sqlite3")) as connection:
            return tuple(sorted(connection.iterdump()))

    def _write(self, service, method, card_id, rows, **fields):
        payload = {
            "card_id": card_id,
            "actor_name": "BOB",
            "source": "ui",
            "repair_order": {"payments": rows, "prepayment": "", **fields},
        }
        caller = deepcopy(payload)
        result = getattr(service, method)(payload)
        self.assertEqual(caller, payload)
        return result.get("repair_order") or result["card"]["repair_order"], result["meta"]

    def _assert_no_finance_errors(self, service):
        self.assertEqual(
            [],
            [i["code"] for i in service.get_finance_audit()["issues"] if i["severity"] == "error"],
        )

    def test_exact_old_cash_link_selects_only_one_duplicate_id_row(self):
        for fast, method, amounts in product(
            (False, True), METHODS, ((100, 100), (100, 200, 300), (100, 100, 100))
        ):
            for selected in range(len(amounts)):
                with (
                    self.subTest(fast=fast, method=method, amounts=amounts, selected=selected),
                    self._case(fast=fast, amounts=amounts) as case,
                ):
                    root, service, store, card_id, box_id, rows = case
                    row = deepcopy(rows[selected])
                    before = deepcopy(store.read_bundle())
                    target = next(
                        t for t in before["cash_transactions"] if t.id == row["cash_transaction_id"]
                    )
                    order, meta = self._write(service, method, card_id, [row])
                    after = store.read_bundle()
                    self.assertEqual([target], after["cash_transactions"])
                    self.assertEqual(
                        [row],
                        [p.to_storage_dict() for p in after["cards"][0].repair_order.payments],
                    )
                    self.assertEqual(str(amounts[selected]), order["paid_total"])
                    self.assertTrue(meta["changed"])
                    events = after["events"][len(before["events"]) :]
                    self.assertEqual(
                        ["cash_transaction_deleted"] * (len(amounts) - 1)
                        + ["repair_order_updated"],
                        [e.action for e in events],
                    )
                    self.assertEqual(
                        {p["cash_transaction_id"] for i, p in enumerate(rows) if i != selected},
                        {e.details["cash_transaction_id"] for e in events[:-1]},
                    )
                    self.assertTrue(all(e.actor_name == "BOB" and e.source == "ui" for e in events))
                    self.assertEqual(1, len({e.details["correlation_id"] for e in events}))
                    self._assert_no_finance_errors(service)
                    persisted = deepcopy(after)
                    state_bytes = (root / "state.json").read_bytes()
                    feed_dump = self._feed_dump(root)
                    with closing(JsonStore(root / "state.json", self.logger)) as reloaded_store:
                        reloaded = CardService(reloaded_store, self.logger)
                        self.assertEqual(
                            row["cash_transaction_id"],
                            reloaded.get_card({"card_id": card_id})["card"]["repair_order"][
                                "payments"
                            ][0]["cash_transaction_id"],
                        )
                        self.assertEqual(persisted, reloaded_store.read_bundle())
                        self.assertEqual(state_bytes, (root / "state.json").read_bytes())
                        self.assertEqual(feed_dump, self._feed_dump(root))
                        # Capture after startup/read, including the live SQLite WAL files.
                        saved = self._files(root)
                        _, replay = self._write(reloaded, method, card_id, [row])
                        self.assertFalse(replay["changed"])
                        self.assertEqual(saved, self._files(root))

    def test_ambiguous_old_id_requires_one_owned_reference_without_writes(self):
        for fast, method, reference in product(
            (False, True), METHODS, ("omitted", "missing", "foreign", "shared")
        ):
            with (
                self.subTest(fast=fast, method=method, reference=reference),
                self._case(fast=fast) as case,
            ):
                root, service, store, card_id, box_id, rows = case
                row = deepcopy(rows[0])
                if reference == "omitted":
                    row.pop("cash_transaction_id")
                elif reference == "missing":
                    row["cash_transaction_id"] = "missing-synthetic-reference"
                elif reference == "foreign":
                    row["cash_transaction_id"] = service.create_cash_transaction(
                        {
                            "cashbox_id": box_id,
                            "direction": "income",
                            "amount": "999",
                            "note": "Синтетическое отдельное движение",
                        }
                    )["transaction"]["id"]
                else:
                    bundle = store.read_bundle()
                    bundle["cards"][0].repair_order.payments[1].cash_transaction_id = row[
                        "cash_transaction_id"
                    ]
                    store.write_bundle(**bundle)
                payload = {
                    "card_id": card_id,
                    "title": "Не сохранять",
                    "repair_order": {"payments": [row]},
                }
                caller = deepcopy(payload)
                retained = store.read_bundle()
                cache = deepcopy(retained)
                files = self._files(root)
                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(payload)
                self.assertEqual(409, rejected.exception.status_code)
                self.assertEqual(AMBIGUOUS, rejected.exception.code)
                self.assertEqual(card_id, rejected.exception.details["card_id"])
                self.assertEqual("legacy-duplicate", rejected.exception.details["payment_id"])
                self.assertEqual(caller, payload)
                self.assertEqual(cache, retained)
                self.assertEqual(files, self._files(root))
                with closing(JsonStore(root / "state.json", self.logger)) as reloaded_store:
                    self.assertEqual(cache["cards"], reloaded_store.read_bundle()["cards"])

    def test_unique_old_id_keeps_original_reference_for_all_input_forms(self):
        for fast, method, reference in product(
            (False, True), METHODS, ("omitted", "retained", "foreign")
        ):
            with (
                self.subTest(fast=fast, method=method, reference=reference),
                self._case(fast=fast, duplicate=False) as case,
            ):
                _, service, store, card_id, box_id, rows = case
                row = deepcopy(rows[0])
                manual = service.create_cash_transaction(
                    {
                        "cashbox_id": box_id,
                        "direction": "income",
                        "amount": "999",
                        "note": "Синтетическое отдельное движение",
                    }
                )["transaction"]["id"]
                if reference == "omitted":
                    row.pop("cash_transaction_id")
                elif reference == "foreign":
                    row["cash_transaction_id"] = manual
                order, _ = self._write(service, method, card_id, [row])
                after = store.read_bundle()
                self.assertEqual(
                    rows[0]["cash_transaction_id"], order["payments"][0]["cash_transaction_id"]
                )
                self.assertEqual(
                    {rows[0]["cash_transaction_id"], manual},
                    {t.id for t in after["cash_transactions"]},
                )
                self.assertEqual(109900, sum(t.amount_minor for t in after["cash_transactions"]))
                self._assert_no_finance_errors(service)

    def test_financial_changes_recreate_one_selected_payment_without_orphans(self):
        for fast, method, field in product(
            (False, True), METHODS, ("amount", "paid_at", "note", "cashbox_id")
        ):
            with self.subTest(fast=fast, method=method, field=field), self._case(fast=fast) as case:
                _, service, store, card_id, _, rows = case
                row = deepcopy(rows[0])
                if field == "cashbox_id":
                    row[field] = service.create_cashbox({"name": "Безналичный"})["cashbox"]["id"]
                    row["payment_method"] = "cashless"
                else:
                    row[field] = {
                        "amount": "150",
                        "paid_at": "16.10.2026 11:00",
                        "note": "Изменённый аванс",
                    }[field]
                before = deepcopy(store.read_bundle())
                order, _ = self._write(service, method, card_id, [row])
                after = store.read_bundle()
                self.assertEqual(1, len(after["cash_transactions"]))
                transaction = after["cash_transactions"][0]
                self.assertNotIn(transaction.id, {p["cash_transaction_id"] for p in rows})
                self.assertEqual(row["cashbox_id"], transaction.cashbox_id)
                self.assertEqual(15000 if field == "amount" else 10000, transaction.amount_minor)
                self.assertEqual(transaction.id, order["payments"][0]["cash_transaction_id"])
                self.assertEqual(row[field], order["payments"][0][field])
                events = after["events"][len(before["events"]) :]
                self.assertEqual(
                    [
                        "cash_transaction_deleted",
                        "cash_transaction_deleted",
                        "cash_transaction_created",
                        "repair_order_updated",
                    ],
                    [e.action for e in events],
                )
                self.assertEqual(1, len({e.details["correlation_id"] for e in events}))
                self._assert_no_finance_errors(service)

    def test_missing_selected_cash_row_gets_a_new_exact_payment_link(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                _, service, store, card_id, _, rows = case
                row = deepcopy(rows[0])
                bundle = store.read_bundle()
                bundle["cash_transactions"] = [
                    t for t in bundle["cash_transactions"] if t.id != row["cash_transaction_id"]
                ]
                store.write_bundle(**bundle)
                order, _ = self._write(service, method, card_id, [row])
                after = store.read_bundle()
                self.assertEqual(1, len(after["cash_transactions"]))
                self.assertNotIn(
                    order["payments"][0]["cash_transaction_id"],
                    {p["cash_transaction_id"] for p in rows},
                )
                self.assertEqual(
                    order["payments"][0]["cash_transaction_id"], after["cash_transactions"][0].id
                )
                self.assertEqual(10000, after["cash_transactions"][0].amount_minor)
                self._assert_no_finance_errors(service)

    def test_full_replacement_with_new_unique_ids_keeps_existing_semantics(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                _, service, store, card_id, _, rows = case
                replacements = [{**p, "id": f"new-{i}"} for i, p in enumerate(rows)]
                order, _ = self._write(service, method, card_id, replacements)
                after = store.read_bundle()
                self.assertEqual(["new-0", "new-1"], [p["id"] for p in order["payments"]])
                self.assertEqual("200", order["paid_total"])
                self.assertEqual(20000, sum(t.amount_minor for t in after["cash_transactions"]))
                self.assertFalse(
                    {p["cash_transaction_id"] for p in rows}
                    & {t.id for t in after["cash_transactions"]}
                )
                self._assert_no_finance_errors(service)

    def test_concurrent_selection_keeps_one_winner_and_rejects_stale_replay(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                root, service, store, card_id, _, rows = case
                before = deepcopy(store.read_bundle())
                expected = before["cards"][0].updated_at
                payloads = [
                    {
                        "card_id": card_id,
                        "expected_updated_at": expected,
                        "actor_name": f"OPERATOR-{index}",
                        "source": "ui",
                        "repair_order": {"payments": [deepcopy(row)], "prepayment": ""},
                    }
                    for index, row in enumerate(rows)
                ]
                originals = deepcopy(payloads)
                barrier = threading.Barrier(2)

                def save(index):
                    barrier.wait(timeout=10)
                    try:
                        return index, getattr(service, method)(payloads[index])
                    except ServiceError as error:
                        return index, error

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(save, index) for index in range(2)]
                    outcomes = [future.result(timeout=15) for future in futures]
                winners = [
                    (index, result) for index, result in outcomes if isinstance(result, dict)
                ]
                losers = [
                    (index, result)
                    for index, result in outcomes
                    if isinstance(result, ServiceError)
                ]
                self.assertEqual(len(winners), 1)
                self.assertEqual(len(losers), 1)
                winner, result = winners[0]
                loser, error = losers[0]
                self.assertEqual((error.code, error.status_code), ("card_update_conflict", 409))
                self.assertTrue(result["meta"]["changed"])
                self.assertEqual(payloads, originals)
                after = store.read_bundle()
                target = rows[winner]["cash_transaction_id"]
                self.assertEqual(
                    [transaction.id for transaction in after["cash_transactions"]], [target]
                )
                self.assertEqual(
                    [
                        payment.to_storage_dict()
                        for payment in after["cards"][0].repair_order.payments
                    ],
                    [rows[winner]],
                )
                self.assertEqual(
                    sum(transaction.amount_minor for transaction in after["cash_transactions"]),
                    10000,
                )
                events = after["events"][len(before["events"]) :]
                self.assertEqual(
                    [event.action for event in events],
                    ["cash_transaction_deleted", "repair_order_updated"],
                )
                self.assertEqual(
                    events[0].details["cash_transaction_id"], rows[loser]["cash_transaction_id"]
                )
                self.assertEqual(len({event.details["correlation_id"] for event in events}), 1)
                self._assert_no_finance_errors(service)
                retained = deepcopy(after)
                files = self._files(root)
                with self.assertRaises(ServiceError) as stale:
                    getattr(service, method)(payloads[loser])
                self.assertEqual(
                    (stale.exception.code, stale.exception.status_code),
                    ("card_update_conflict", 409),
                )
                self.assertEqual(after, retained)
                self.assertEqual(files, self._files(root))
                replay = deepcopy(payloads[winner])
                replay.pop("expected_updated_at")
                self.assertFalse(getattr(service, method)(replay)["meta"]["changed"])
                self.assertEqual(files, self._files(root))

    def test_late_save_failure_preserves_cash_order_and_audit_state(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                root, service, store, card_id, _, rows = case
                row = {**rows[0], "amount": "150"}
                payload = {
                    "card_id": card_id,
                    "repair_order": {
                        "payments": [row],
                        "works": [{"name": "Работа", "quantity": "1", "price": "500"}],
                    },
                }
                caller = deepcopy(payload)
                retained = store.read_bundle()
                before = deepcopy(retained)
                files = self._files(root)
                with patch.object(
                    service, "_save_bundle", side_effect=OSError("synthetic final-write failure")
                ) as save:
                    with self.assertRaisesRegex(OSError, "synthetic final-write failure"):
                        getattr(service, method)(payload)
                save.assert_called_once()
                draft = save.call_args.args[0]
                transactions = save.call_args.kwargs.get(
                    "cash_transactions", draft["cash_transactions"]
                )
                self.assertEqual(1, len(transactions))
                self.assertEqual(15000, transactions[0].amount_minor)
                self.assertNotIn(transactions[0].id, {p["cash_transaction_id"] for p in rows})
                self.assertEqual(caller, payload)
                self.assertEqual(before, retained)
                self.assertEqual(files, self._files(root))

    def test_http_routes_reject_ambiguous_old_id_without_saving(self):
        for fast, route, field in product((False, True), METHODS, ("payments", "payment_history")):
            with self.subTest(fast=fast, route=route, field=field), self._case(fast=fast) as case:
                root, service, store, card_id, _, rows = case
                auth = OperatorAuthService(
                    store, service, users_file=root / "users.json", logger=self.logger
                )
                token = auth.login({"username": "admin", "password": "admin"})["session"]["token"]
                server = ApiServer(
                    service, self.logger, operator_service=auth, bearer_token="", start_port=0
                )
                server.start()
                try:
                    row = deepcopy(rows[0])
                    row.pop("cash_transaction_id")
                    payload = {"card_id": card_id, "repair_order": {field: [row]}}
                    retained = store.read_bundle()
                    before = deepcopy(retained)
                    files = self._files(root)
                    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                    try:
                        connection.request(
                            "POST",
                            f"/api/{route}",
                            body=json.dumps(payload).encode("utf-8"),
                            headers={
                                "Content-Type": "application/json",
                                "X-Operator-Session": token,
                            },
                        )
                        response = connection.getresponse()
                        result = json.loads(response.read().decode("utf-8"))
                    finally:
                        connection.close()
                    self.assertEqual(409, response.status)
                    self.assertEqual(AMBIGUOUS, result["error"]["code"])
                    self.assertEqual(files, self._files(root))
                    self.assertEqual(before, retained)
                finally:
                    server.stop()
