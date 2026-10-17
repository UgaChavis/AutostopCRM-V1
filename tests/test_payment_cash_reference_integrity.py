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
from minimal_kanban.repair_order import RepairOrderPayment
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


METHODS = ("update_card", "update_repair_order")
AMBIGUOUS = "repair_order_payment_cash_link_ambiguous"
INVALID = "repair_order_payment_cash_link_invalid"


class CashReferenceIntegrityTests(CardServiceCase):
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
            cash = service.create_cashbox({"name": "Наличный"})["cashbox"]["id"]
            bank = service.create_cashbox({"name": "Безналичный"})["cashbox"]["id"]
            card_id = service.create_card({"vehicle": "TEST", "title": "Проверка кассовой связи"})[
                "card"
            ]["id"]
            yield root, service, store, card_id, cash, bank

    def _files(self, root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def _feed_dump(self, root):
        with closing(sqlite3.connect(root / "change_feed.sqlite3")) as connection:
            return tuple(sorted(connection.iterdump()))

    def _row(self, box, reference, *, id_="new"):
        return {
            "id": id_,
            "amount": "100",
            "cashbox_id": box,
            "cash_transaction_id": reference,
            "paid_at": "15.10.2026 09:00",
            "note": "Синтетическая клиентская оплата",
            "actor_name": "BOB",
        }

    def _payload(self, card_id, rows, *, field="payments", **fields):
        return {
            "card_id": card_id,
            "actor_name": "BOB",
            "source": "ui",
            "repair_order": {field: rows, "prepayment": "", **fields},
        }

    def _reference(self, service, store, cash, bank, case):
        if case == "transfer":
            service.create_cash_transaction(
                {
                    "cashbox_id": bank,
                    "direction": "income",
                    "amount": "1000",
                    "note": "Синтетический стартовый остаток",
                }
            )
            return service.create_cashbox_transfer(
                {
                    "from_cashbox_id": bank,
                    "to_cashbox_id": cash,
                    "amount": "100",
                    "note": "Синтетический внутренний перевод",
                }
            )["target_transaction"]["id"]
        kind = {
            "orphan": "repair_order_payment",
            "custom": "legacy_import",
            "normalization": "cashbox_normalization",
            "salary": "salary_payout",
        }.get(case, "")
        result = service.create_cash_transaction(
            {
                "cashbox_id": bank if case == "cashbox" else cash,
                "direction": "expense" if case in {"direction", "cancellation"} else "income",
                "amount": "999" if case == "amount" else "100",
                "note": "Перемещение из устаревшей кассы"
                if case == "legacy_transfer"
                else "Синтетическое отдельное движение",
                "actor_name": "ALICE",
                "source": "api",
                "transaction_kind": kind,
            }
        )["transaction"]
        if case in {"cancelled", "cancellation", "reset_cancelled"}:
            view = service.get_cashbox({"cashbox_id": cash})
            cancellation = service.cancel_cash_transaction(
                {
                    "cashbox_id": cash,
                    "transaction_id": result["id"],
                    "expected_cashbox_updated_at": view["cashbox"]["updated_at"],
                    "reason": "Синтетическая отмена движения",
                }
            )
            if case == "cancellation":
                return cancellation["cancellation_transaction"]["id"]
            if case == "reset_cancelled":
                bundle = store.read_bundle()
                next(
                    t for t in bundle["cash_transactions"] if t.id == result["id"]
                ).transaction_kind = "repair_order_payment"
                store.write_bundle(**bundle)
        return result["id"]

    def _owned(self, service, card_id, box):
        result = service.update_repair_order(
            self._payload(card_id, [{**self._row(box, ""), "id": "original"}])
        )
        return deepcopy(result["repair_order"]["payments"][0])

    def _seed_shared(self, service, store, card_id, cash, *, same_order, same_payment_id=False):
        original = self._owned(service, card_id, cash)
        peer_id = (
            card_id
            if same_order
            else service.create_card({"title": "Владелец общей ссылки"})["card"]["id"]
        )
        if not same_order:
            service.update_repair_order(
                {"card_id": peer_id, "repair_order": {"comment": "Подготовка старой записи"}}
            )
        bundle = store.read_bundle()
        target = next(c for c in bundle["cards"] if c.id == peer_id)
        peer = RepairOrderPayment.from_dict(
            {**original, "id": original["id"] if same_payment_id else "peer"}
        )
        if same_order:
            target.repair_order.payments.append(peer)
        else:
            target.repair_order.payments = [peer]
        target.repair_order.prepayment = target.repair_order.prepayment_amount()
        store.write_bundle(**bundle)
        target = next(c for c in bundle["cards"] if c.id == card_id)
        return [p.to_storage_dict() for p in target.repair_order.payments], original[
            "cash_transaction_id"
        ]

    def _reject(self, case, method, payload, reference, *, code=AMBIGUOUS, status=409):
        root, service, store, _, _, _ = case
        caller = deepcopy(payload)
        retained = store.read_bundle()
        before = deepcopy(retained)
        files = self._files(root)
        with self.assertRaises(ServiceError) as rejected:
            getattr(service, method)(payload)
        self.assertEqual(status, rejected.exception.status_code)
        self.assertEqual(code, rejected.exception.code)
        self.assertEqual(reference, rejected.exception.details["transaction_id"])
        self.assertEqual(caller, payload)
        self.assertEqual(before, retained)
        self.assertEqual(files, self._files(root))
        with closing(JsonStore(root / "state.json", self.logger)) as reloaded_store:
            reloaded = reloaded_store.read_bundle()
            self.assertEqual(before["cards"], reloaded["cards"])
            self.assertEqual(before["cash_transactions"], reloaded["cash_transactions"])

    def test_new_reference_cannot_reuse_another_payment_or_proposed_reference(self):
        for fast, method, location, field in product(
            (False, True),
            METHODS,
            ("other_order", "archived_owner", "same_order", "two_new"),
            ("payments", "payment_history"),
        ):
            with (
                self.subTest(fast=fast, method=method, location=location, field=field),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                if location == "two_new":
                    reference = self._reference(service, store, cash, bank, "manual")
                    rows = [
                        self._row(cash, reference, id_="first"),
                        self._row(cash, reference, id_="second"),
                    ]
                else:
                    owner = (
                        card_id
                        if location == "same_order"
                        else service.create_card({"title": "Первая оплата"})["card"]["id"]
                    )
                    old = self._owned(service, owner, cash)
                    if location == "archived_owner":
                        service.set_repair_order_status({"card_id": owner, "status": "closed"})
                        service.archive_card({"card_id": owner})
                    reference = old["cash_transaction_id"]
                    rows = ([old] if location == "same_order" else []) + [
                        self._row(cash, reference)
                    ]
                self._reject(case, method, self._payload(card_id, rows, field=field), reference)

    def test_financial_mismatch_cannot_be_adopted_as_a_new_customer_payment(self):
        for fast, method, mismatch, field in product(
            (False, True),
            METHODS,
            ("amount", "direction", "cashbox"),
            ("payments", "payment_history"),
        ):
            with (
                self.subTest(fast=fast, method=method, mismatch=mismatch, field=field),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, mismatch)
                self._reject(
                    case,
                    method,
                    self._payload(card_id, [self._row(cash, reference)], field=field),
                    reference,
                    code=INVALID,
                )

    def test_cancelled_and_reversal_references_keep_native_cancellation_rejection(self):
        for fast, method, provenance in product(
            (False, True), METHODS, ("cancelled", "cancellation", "reset_cancelled")
        ):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, provenance)
                self._reject(
                    case,
                    method,
                    self._payload(card_id, [self._row(cash, reference)]),
                    reference,
                    code="validation_error",
                    status=400,
                )

    def test_transfer_and_known_bookkeeping_rows_are_not_new_customer_receipts(self):
        for fast, method, provenance in product(
            (False, True), METHODS, ("transfer", "legacy_transfer", "normalization", "salary")
        ):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, provenance)
                self._reject(
                    case,
                    method,
                    self._payload(card_id, [self._row(cash, reference)]),
                    reference,
                    code=INVALID,
                )

    def test_coherent_manual_or_orphan_reference_preserves_cash_metadata_and_replay(self):
        for fast, method, provenance in product(
            (False, True), METHODS, ("manual", "orphan", "custom")
        ):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance),
                self._case(fast) as case,
            ):
                root, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, provenance)
                before = deepcopy(store.read_bundle())
                payload = self._payload(card_id, [self._row(cash, reference)])
                caller = deepcopy(payload)
                result = getattr(service, method)(payload)
                order = result.get("repair_order") or result["card"]["repair_order"]
                self.assertEqual(reference, order["payments"][0]["cash_transaction_id"])
                self.assertEqual("100", order["paid_total"])
                self.assertEqual(caller, payload)
                after = store.read_bundle()
                self.assertEqual(before["cash_transactions"], after["cash_transactions"])
                self.assertEqual(
                    ["repair_order_updated"],
                    [e.action for e in after["events"][len(before["events"]) :]],
                )
                self.assertEqual(
                    [],
                    [
                        i["code"]
                        for i in service.get_finance_audit()["issues"]
                        if i["severity"] == "error"
                    ],
                )
                persisted = deepcopy(after)
                state_bytes = (root / "state.json").read_bytes()
                feed_dump = self._feed_dump(root)
                with closing(JsonStore(root / "state.json", self.logger)) as reloaded_store:
                    reloaded = CardService(reloaded_store, self.logger)
                    self.assertEqual(
                        reference,
                        reloaded.get_card({"card_id": card_id})["card"]["repair_order"]["payments"][
                            0
                        ]["cash_transaction_id"],
                    )
                    self.assertEqual(persisted, reloaded_store.read_bundle())
                    self.assertEqual(state_bytes, (root / "state.json").read_bytes())
                    self.assertEqual(feed_dump, self._feed_dump(root))
                    files = self._files(root)
                    self.assertFalse(getattr(reloaded, method)(payload)["meta"]["changed"])
                    self.assertEqual(files, self._files(root))

    def test_shared_old_cash_row_cannot_be_removed_or_recreated(self):
        for fast, method, ownership, action in product(
            (False, True),
            METHODS,
            ("same_order", "other_order", "other_same_id"),
            ("remove", "amount"),
        ):
            with (
                self.subTest(fast=fast, method=method, ownership=ownership, action=action),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, _ = case
                rows, reference = self._seed_shared(
                    service,
                    store,
                    card_id,
                    cash,
                    same_order=ownership == "same_order",
                    same_payment_id=ownership == "other_same_id",
                )
                if action == "remove":
                    rows = rows[1:]
                else:
                    rows[0]["amount"] = "150"
                self._reject(case, method, self._payload(card_id, rows), reference)

    def test_old_invalid_cash_reference_cannot_be_deleted_or_recreated(self):
        invalid = (
            "amount",
            "direction",
            "cashbox",
            "cancelled",
            "cancellation",
            "reset_cancelled",
            "transfer",
            "legacy_transfer",
            "normalization",
            "salary",
        )
        for fast, method, provenance, action in product(
            (False, True), METHODS, invalid, ("remove", "amount")
        ):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance, action=action),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                old = self._owned(service, card_id, cash)
                reference = self._reference(service, store, cash, bank, provenance)
                bundle = store.read_bundle()
                payment = next(c for c in bundle["cards"] if c.id == card_id).repair_order.payments[
                    0
                ]
                payment.cash_transaction_id = reference
                bundle["cash_transactions"] = [
                    t for t in bundle["cash_transactions"] if t.id != old["cash_transaction_id"]
                ]
                store.write_bundle(**bundle)
                row = {**old, "cash_transaction_id": reference, "amount": "150"}
                payload = self._payload(card_id, [] if action == "remove" else [row])
                cancelled = provenance in {"cancelled", "cancellation", "reset_cancelled"}
                self._reject(
                    case,
                    method,
                    payload,
                    reference,
                    code="validation_error" if cancelled else INVALID,
                    status=400 if cancelled else 409,
                )

    def test_owned_coherent_receipt_can_still_be_removed_or_recreated(self):
        for fast, method, provenance, action in product(
            (False, True), METHODS, ("manual", "orphan", "custom"), ("remove", "amount")
        ):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance, action=action),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, provenance)
                initial = getattr(service, method)(
                    self._payload(card_id, [self._row(cash, reference)])
                )
                order = initial.get("repair_order") or initial["card"]["repair_order"]
                row = {**order["payments"][0], "amount": "150"}
                result = getattr(service, method)(
                    self._payload(card_id, [] if action == "remove" else [row])
                )
                updated = result.get("repair_order") or result["card"]["repair_order"]
                after = store.read_bundle()
                self.assertNotIn(reference, {t.id for t in after["cash_transactions"]})
                if action == "remove":
                    self.assertEqual("0", updated["paid_total"])
                    self.assertEqual([], after["cash_transactions"])
                else:
                    self.assertEqual("150", updated["paid_total"])
                    self.assertEqual(1, len(after["cash_transactions"]))
                    self.assertEqual(15000, after["cash_transactions"][0].amount_minor)

    def test_preserved_old_malformed_links_are_not_silently_rewritten(self):
        for fast, method, existing in product(
            (False, True),
            METHODS,
            ("shared_same", "shared_other", "shared_other_same_id", "amount"),
        ):
            with (
                self.subTest(fast=fast, method=method, existing=existing),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, _ = case
                if existing == "amount":
                    row = self._owned(service, card_id, cash)
                    bundle = store.read_bundle()
                    next(
                        t for t in bundle["cash_transactions"] if t.id == row["cash_transaction_id"]
                    ).amount_minor = 99900
                    store.write_bundle(**bundle)
                    rows = [row]
                else:
                    rows, _ = self._seed_shared(
                        service,
                        store,
                        card_id,
                        cash,
                        same_order=existing == "shared_same",
                        same_payment_id=existing == "shared_other_same_id",
                    )
                before = deepcopy(store.read_bundle())
                errors = [
                    i["code"]
                    for i in service.get_finance_audit()["issues"]
                    if i["severity"] == "error"
                ]
                self.assertTrue(errors)
                payload = self._payload(card_id, rows, comment="Только текстовое изменение")
                result = getattr(service, method)(payload)
                self.assertTrue(result["meta"]["changed"])
                after = store.read_bundle()
                self.assertEqual(before["cash_transactions"], after["cash_transactions"])
                self.assertEqual(
                    ["repair_order_updated"],
                    [e.action for e in after["events"][len(before["events"]) :]],
                )
                self.assertEqual(
                    errors,
                    [
                        i["code"]
                        for i in service.get_finance_audit()["issues"]
                        if i["severity"] == "error"
                    ],
                )

    def test_rejected_adoption_does_not_reserve_reference_across_requests(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, "manual")
                self._reject(
                    case,
                    method,
                    self._payload(
                        card_id,
                        [
                            self._row(cash, reference, id_="first"),
                            self._row(cash, reference, id_="second"),
                        ],
                    ),
                    reference,
                )
                result = getattr(service, method)(
                    self._payload(card_id, [self._row(cash, reference)])
                )
                self.assertTrue(result["meta"]["changed"])
                peer = service.create_card({"title": "Следующая попытка владения"})["card"]["id"]
                self._reject(
                    case, method, self._payload(peer, [self._row(cash, reference)]), reference
                )

    def test_late_save_failure_does_not_mutate_the_adopted_manual_row(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                root, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, "manual")
                payload = self._payload(card_id, [self._row(cash, reference)])
                caller = deepcopy(payload)
                retained = store.read_bundle()
                before = deepcopy(retained)
                files = self._files(root)
                with patch.object(
                    service, "_save_bundle", side_effect=OSError("synthetic adoption save failure")
                ) as save:
                    with self.assertRaisesRegex(OSError, "synthetic adoption save failure"):
                        getattr(service, method)(payload)
                save.assert_called_once()
                self.assertEqual(caller, payload)
                self.assertEqual(before, retained)
                self.assertEqual(files, self._files(root))
                self.assertTrue(getattr(service, method)(payload)["meta"]["changed"])
                self.assertEqual(
                    before["cash_transactions"], store.read_bundle()["cash_transactions"]
                )

    def test_peer_adoption_during_save_keeps_the_committed_owner_and_rejects_retry(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                root, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, "manual")
                peer_id = service.create_card({"title": "Параллельный владелец"})["card"]["id"]
                with closing(JsonStore(root / "state.json", self.logger)) as peer_store:
                    peer_service = CardService(peer_store, self.logger)
                    payload = self._payload(card_id, [self._row(cash, reference)])
                    caller = deepcopy(payload)
                    original_save = service._save_bundle
                    peer_files = {}

                    def commit_peer_then_save(*args, **kwargs):
                        peer_service.update_repair_order(
                            self._payload(peer_id, [self._row(cash, reference)])
                        )
                        peer_files.update(self._files(root))
                        return original_save(*args, **kwargs)

                    with patch.object(
                        service, "_save_bundle", side_effect=commit_peer_then_save
                    ) as save:
                        with self.assertRaises(ServiceError) as rejected:
                            getattr(service, method)(payload)
                    save.assert_called_once()
                    self.assertEqual("state_write_conflict", rejected.exception.code)
                    self.assertEqual(409, rejected.exception.status_code)
                    self.assertEqual(caller, payload)
                    self.assertTrue(peer_files)
                    self.assertEqual(peer_files, self._files(root))
                    with closing(JsonStore(root / "state.json", self.logger)) as fresh_store:
                        fresh = fresh_store.read_bundle()
                    self.assertEqual(
                        [], next(c for c in fresh["cards"] if c.id == card_id).repair_order.payments
                    )
                    self.assertEqual(
                        reference,
                        next(c for c in fresh["cards"] if c.id == peer_id)
                        .repair_order.payments[0]
                        .cash_transaction_id,
                    )
                    self._reject(case, method, payload, reference)

    def test_http_routes_keep_native_errors_and_files_for_bad_new_references(self):
        for fast, route, failure in product(
            (False, True), METHODS, ("shared", "amount", "cancelled")
        ):
            with self.subTest(fast=fast, route=route, failure=failure), self._case(fast) as case:
                root, service, store, card_id, cash, bank = case
                if failure == "shared":
                    owner = service.create_card({"title": "Исходная оплата"})["card"]["id"]
                    reference = self._owned(service, owner, cash)["cash_transaction_id"]
                else:
                    reference = self._reference(service, store, cash, bank, failure)
                auth = OperatorAuthService(
                    store, service, users_file=root / "users.json", logger=self.logger
                )
                token = auth.login({"username": "admin", "password": "admin"})["session"]["token"]
                server = ApiServer(
                    service, self.logger, operator_service=auth, bearer_token="", start_port=0
                )
                server.start()
                try:
                    retained = store.read_bundle()
                    before = deepcopy(retained)
                    files = self._files(root)
                    payload = self._payload(card_id, [self._row(cash, reference)])
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
                    self.assertEqual(400 if failure == "cancelled" else 409, response.status)
                    self.assertEqual(
                        "validation_error"
                        if failure == "cancelled"
                        else AMBIGUOUS
                        if failure == "shared"
                        else INVALID,
                        result["error"]["code"],
                    )
                    self.assertEqual(reference, result["error"]["details"]["transaction_id"])
                    self.assertEqual(files, self._files(root))
                    self.assertEqual(before, retained)
                finally:
                    server.stop()

    def test_two_stores_adopting_one_receipt_keep_one_owner_after_conflict(self):
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast) as case:
                root, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, "manual")
                peer_id = service.create_card({"title": "Конкурирующий владелец"})["card"]["id"]
                payloads = [
                    self._payload(owner, [self._row(cash, reference)])
                    for owner in (card_id, peer_id)
                ]
                originals = deepcopy(payloads)
                before = deepcopy(store.read_bundle())
                barrier = threading.Barrier(2)

                def synchronized_save(original):
                    def save(*args, **kwargs):
                        barrier.wait(timeout=10)
                        return original(*args, **kwargs)

                    return save

                def adopt(index, writer):
                    try:
                        return index, getattr(writer, method)(payloads[index])
                    except ServiceError as error:
                        return index, error

                with closing(JsonStore(root / "state.json", self.logger)) as peer_store:
                    peer = CardService(peer_store, self.logger)
                    with (
                        patch.object(
                            service,
                            "_save_bundle",
                            side_effect=synchronized_save(service._save_bundle),
                        ),
                        patch.object(
                            peer, "_save_bundle", side_effect=synchronized_save(peer._save_bundle)
                        ),
                        ThreadPoolExecutor(max_workers=2) as pool,
                    ):
                        futures = [
                            pool.submit(adopt, i, writer)
                            for i, writer in enumerate((service, peer))
                        ]
                        outcomes = [future.result(timeout=20) for future in futures]
                    winners = [
                        (i, result)
                        for i, result in outcomes
                        if not isinstance(result, ServiceError)
                    ]
                    losers = [
                        (i, result) for i, result in outcomes if isinstance(result, ServiceError)
                    ]
                    self.assertEqual(1, len(winners))
                    self.assertEqual(1, len(losers))
                    winner, result = winners[0]
                    loser, error = losers[0]
                    self.assertTrue(result["meta"]["changed"])
                    self.assertEqual(("state_write_conflict", 409), (error.code, error.status_code))
                    self.assertEqual(originals, payloads)
                    persisted = deepcopy(store.read_bundle())
                    self.assertEqual(before["cash_transactions"], persisted["cash_transactions"])
                    self.assertEqual(
                        [payloads[winner]["card_id"]],
                        [card.id for card in persisted["cards"] if card.repair_order.payments],
                    )
                    self.assertEqual(
                        ["repair_order_updated"],
                        [event.action for event in persisted["events"][len(before["events"]) :]],
                    )
                    self._reject(case, method, payloads[loser], reference)
                    state_bytes = (root / "state.json").read_bytes()
                    feed_dump = self._feed_dump(root)
                    with closing(JsonStore(root / "state.json", self.logger)) as fresh_store:
                        fresh = CardService(fresh_store, self.logger)
                        self.assertEqual(persisted, fresh_store.read_bundle())
                        self.assertEqual(state_bytes, (root / "state.json").read_bytes())
                        self.assertEqual(feed_dump, self._feed_dump(root))
                        files = self._files(root)
                        self.assertFalse(
                            getattr(fresh, method)(payloads[winner])["meta"]["changed"]
                        )
                        self.assertEqual(files, self._files(root))

    def test_transfer_metadata_and_advance_cannot_be_adopted_as_customer_payments(self):
        provenances = ("salary_advance", "transfer_group_only", "related_only", "transfer_metadata")
        for fast, method, provenance in product((False, True), METHODS, provenances):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance),
                self._case(fast) as case,
            ):
                _, service, store, card_id, cash, bank = case
                reference = self._reference(service, store, cash, bank, "manual")
                bundle = store.read_bundle()
                transaction = next(t for t in bundle["cash_transactions"] if t.id == reference)
                if provenance == "salary_advance":
                    transaction.transaction_kind = "SALARY_ADVANCE"
                if provenance in {"transfer_group_only", "transfer_metadata"}:
                    transaction.transfer_group_id = "synthetic-transfer-group"
                if provenance in {"related_only", "transfer_metadata"}:
                    transaction.related_transaction_id = "synthetic-transfer-peer"
                store.write_bundle(**bundle)
                self._reject(
                    case,
                    method,
                    self._payload(card_id, [self._row(cash, reference)]),
                    reference,
                    code=INVALID,
                )

    def test_text_edits_preserve_historical_cancelled_and_bookkeeping_links(self):
        provenances = (
            "cancelled",
            "cancellation",
            "reset_cancelled",
            "transfer",
            "legacy_transfer",
            "normalization",
            "salary",
        )
        for fast, method, provenance in product((False, True), METHODS, provenances):
            with (
                self.subTest(fast=fast, method=method, provenance=provenance),
                self._case(fast) as case,
            ):
                root, service, store, card_id, cash, bank = case
                original = self._owned(service, card_id, cash)
                reference = self._reference(service, store, cash, bank, provenance)
                bundle = store.read_bundle()
                payment = next(c for c in bundle["cards"] if c.id == card_id).repair_order.payments[
                    0
                ]
                payment.cash_transaction_id = reference
                bundle["cash_transactions"] = [
                    t
                    for t in bundle["cash_transactions"]
                    if t.id != original["cash_transaction_id"]
                ]
                store.write_bundle(**bundle)
                before = deepcopy(store.read_bundle())
                payload = self._payload(
                    card_id,
                    [{**original, "cash_transaction_id": reference}],
                    comment="Только текст, историческая кассовая связь остаётся прежней",
                )
                self.assertTrue(getattr(service, method)(payload)["meta"]["changed"])
                after = store.read_bundle()
                self.assertEqual(before["cash_transactions"], after["cash_transactions"])
                self.assertEqual(
                    ["repair_order_updated"],
                    [event.action for event in after["events"][len(before["events"]) :]],
                )
                self.assertEqual(
                    reference, after["cards"][0].repair_order.payments[0].cash_transaction_id
                )
                files = self._files(root)
                self.assertFalse(getattr(service, method)(payload)["meta"]["changed"])
                self.assertEqual(files, self._files(root))


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
