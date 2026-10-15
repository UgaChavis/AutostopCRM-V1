from __future__ import annotations

# The fixture import sets up the src path before importing application modules.
# ruff: noqa: I001
import http.client
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count, product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.operator_permissions import EMPLOYEES_CASHBOXES_ACCESS_PERMISSION
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


METHODS = ("cancel_last_cash_transaction", "cancel_cash_transaction")


class CashPaymentCancellationTests(CardServiceCase):
    @contextmanager
    def _case(
        self,
        *,
        fast: bool,
        amounts=(100, 200),
        legacy=False,
        equal=False,
        closed=False,
        work_total=None,
    ):
        moment = datetime.now(UTC)
        ticks = count()
        with (
            TemporaryDirectory() as directory,
            patch(
                "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                return_value=fast,
            ),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso",
                side_effect=lambda: (moment + timedelta(microseconds=next(ticks))).isoformat(),
            ),
        ):
            root = Path(directory)
            store = JsonStore(root / "state.json", self.logger)
            service = CardService(
                store,
                self.logger,
                attachments_dir=root / "attachments",
                repair_orders_dir=root / "repair-orders",
            )
            box = service.create_cashbox({"name": "Наличный"})["cashbox"]
            card = service.create_card({"vehicle": "TEST", "title": "Отмена оплаты"})["card"]
            service.update_repair_order(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "works": [
                            {
                                "id": "work",
                                "name": "Диагностика",
                                "quantity": "1",
                                "price": str(sum(amounts) if work_total is None else work_total),
                            }
                        ],
                        "payments": [
                            {
                                "id": f"payment-{index}",
                                "amount": str(amount),
                                "paid_at": f"15.10.2026 {10 if equal else 10 + index:02}:00",
                                "cashbox_id": box["id"],
                            }
                            for index, amount in enumerate(amounts)
                        ],
                    },
                }
            )
            if closed:
                service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
            if legacy:
                bundle = store.read_bundle()
                for payment in bundle["cards"][0].repair_order.payments:
                    payment.id = "legacy-duplicate"
                store.write_bundle(**bundle)
            yield root, service, store, card["id"], box["id"]

    def _request(self, service, box_id, method):
        view = service.get_cashbox({"cashbox_id": box_id})
        target = view["transactions"][0 if method == METHODS[0] else -1]
        return {
            "cashbox_id": box_id,
            "transaction_id": target["id"],
            "expected_cashbox_updated_at": view["cashbox"]["updated_at"],
            "reason": "Синтетическая отмена оплаты",
            "actor_name": "BOB",
            "source": "ui",
        }

    def _share_link(self, service, store, card_id, target_id, *, other_card=False):
        if other_card:
            second = service.create_card({"title": "Вторая ссылка на движение"})["card"]
        bundle = store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == card_id)
        target = next(p for p in card.repair_order.payments if p.cash_transaction_id == target_id)
        if other_card:
            second_card = next(card for card in bundle["cards"] if card.id == second["id"])
            second_card.repair_order = RepairOrder.from_dict(
                {"number": "2", "payments": [{**target.to_storage_dict(), "id": "other"}]}
            )
        else:
            other = next(
                p for p in card.repair_order.payments if p.cash_transaction_id != target_id
            )
            other.cash_transaction_id = target_id
        store.write_bundle(**bundle)

    def _files(self, root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_only_target_payment_is_cancelled_with_original_other_links_and_audit(self) -> None:
        shapes = (((100, 200), False), ((100, 100), True), ((100, 200, 300), False))
        for fast, method, legacy, (amounts, equal) in product(
            (False, True), METHODS, (False, True), shapes
        ):
            with (
                self.subTest(fast=fast, method=method, legacy=legacy, amounts=amounts),
                self._case(fast=fast, amounts=amounts, equal=equal, legacy=legacy) as case,
            ):
                root, service, store, card_id, box_id = case
                request = self._request(service, box_id, method)
                caller_before = deepcopy(request)
                before = deepcopy(store.read_bundle())
                target_id = request["transaction_id"]
                target = next(t for t in before["cash_transactions"] if t.id == target_id)
                remaining = [
                    p.to_storage_dict()
                    for p in before["cards"][0].repair_order.payments
                    if p.cash_transaction_id != target_id
                ]
                expected_balance = (
                    sum(t.amount_minor for t in before["cash_transactions"]) - target.amount_minor
                )

                response = getattr(service, method)(request)

                after = JsonStore(root / "state.json", self.logger).read_bundle()
                self.assertEqual(
                    remaining,
                    [p.to_storage_dict() for p in after["cards"][0].repair_order.payments],
                )
                self.assertEqual(
                    expected_balance, response["cashbox"]["statistics"]["balance_minor"]
                )
                self.assertEqual(
                    expected_balance,
                    int(
                        sum(p.amount_value() for p in after["cards"][0].repair_order.payments) * 100
                    ),
                )
                self.assertEqual(target_id, response["cancelled_transaction"]["id"])
                self.assertEqual(card_id, response["meta"]["repair_order_card_id"])
                self.assertEqual(caller_before, request)
                transactions = {t.id: t for t in after["cash_transactions"]}
                for old in before["cash_transactions"]:
                    if old.id != target_id:
                        self.assertEqual(old, transactions[old.id])
                if method == METHODS[0]:
                    self.assertNotIn(target_id, transactions)
                    self.assertEqual(len(amounts) - 1, len(transactions))
                    actions = ["cash_transaction_deleted", "repair_order_updated"]
                else:
                    self.assertEqual(
                        replace(target, transaction_kind="cashbox_cancelled"),
                        transactions[target_id],
                    )
                    reversal = response["cancellation_transaction"]
                    self.assertEqual(target_id, reversal["related_transaction_id"])
                    self.assertEqual(target.amount_minor, reversal["amount_minor"])
                    self.assertEqual("expense", reversal["direction"])
                    self.assertEqual(len(amounts) + 1, len(transactions))
                    actions = ["repair_order_updated", "cash_transaction_cancelled"]
                old_events = {event.id for event in before["events"]}
                new_events = [event for event in after["events"] if event.id not in old_events]
                self.assertEqual(actions, [event.action for event in new_events])
                self.assertTrue(
                    all(event.actor_name == "BOB" and event.source == "ui" for event in new_events)
                )
                money_event = next(
                    event
                    for event in new_events
                    if event.action == actions[0 if method == METHODS[0] else 1]
                )
                self.assertEqual(target_id, money_event.details["cash_transaction_id"])
                self.assertEqual(target.amount_minor, money_event.details["amount_minor"])
                correlation = money_event.details["correlation_id"]
                self.assertEqual(
                    correlation,
                    next(
                        event for event in new_events if event.action == "repair_order_updated"
                    ).details["correlation_id"],
                )
                retry = {
                    **request,
                    "expected_cashbox_updated_at": response["cashbox"]["updated_at"],
                }
                saved = self._files(root)
                with self.assertRaises(ServiceError) as repeated:
                    getattr(service, method)(retry)
                self.assertEqual(
                    "not_found" if method == METHODS[0] else "validation_error",
                    repeated.exception.code,
                )
                self.assertEqual(saved, self._files(root))

    def test_shared_cash_link_is_rejected_without_changes_within_or_across_orders(self) -> None:
        for fast, method, other_card in product((False, True), METHODS, (False, True)):
            with (
                self.subTest(fast=fast, method=method, other_card=other_card),
                self._case(fast=fast) as case,
            ):
                root, service, store, card_id, box_id = case
                request = self._request(service, box_id, method)
                self._share_link(
                    service, store, card_id, request["transaction_id"], other_card=other_card
                )
                audit = service.get_finance_audit()
                self.assertIn(
                    "duplicate_repair_order_payment_cash_link",
                    [issue["code"] for issue in audit["issues"]],
                )
                retained = store.read_bundle()
                before = deepcopy(retained)
                saved = self._files(root)
                caller_before = deepcopy(request)
                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(request)
                self.assertEqual(409, rejected.exception.status_code)
                self.assertEqual(
                    "repair_order_payment_cash_link_ambiguous", rejected.exception.code
                )
                self.assertEqual(
                    request["transaction_id"], rejected.exception.details["transaction_id"]
                )
                self.assertEqual(before, retained)
                self.assertEqual(saved, self._files(root))
                self.assertEqual(caller_before, request)

    def test_closed_paid_order_reopens_when_one_payment_is_cancelled(self) -> None:
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast, closed=True) as case:
                root, service, store, card_id, box_id = case
                request = self._request(service, box_id, method)
                target = next(
                    t
                    for t in store.read_bundle()["cash_transactions"]
                    if t.id == request["transaction_id"]
                )
                response = getattr(service, method)(request)
                order = (
                    JsonStore(root / "state.json", self.logger)
                    .read_bundle()["cards"][0]
                    .repair_order
                )
                self.assertEqual("open", order.status)
                self.assertEqual("", order.closed_at)
                self.assertEqual(1, len(order.payments))
                self.assertEqual(
                    30000 - target.amount_minor, response["cashbox"]["statistics"]["balance_minor"]
                )

    def test_closed_order_guard_rolls_back_late_cancellation(self) -> None:
        for fast, method in product((False, True), METHODS):
            with (
                self.subTest(fast=fast, method=method),
                self._case(fast=fast, closed=True, work_total=0) as case,
            ):
                root, service, store, card_id, box_id = case
                request = self._request(service, box_id, method)
                caller = deepcopy(request)
                retained = store.read_bundle()
                before = deepcopy(retained)
                saved = self._files(root)
                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(request)
                self.assertEqual("repair_order_closed_read_only", rejected.exception.code)
                self.assertEqual(409, rejected.exception.status_code)
                self.assertEqual(before, retained)
                self.assertEqual(saved, self._files(root))
                self.assertEqual(caller, request)

    def test_existing_preflight_errors_keep_priority_over_shared_link_ambiguity(self) -> None:
        cases = (
            (METHODS[0], "stale"),
            (METHODS[1], "stale"),
            (METHODS[0], "not_latest"),
            (METHODS[1], "reason"),
        )
        for fast, (method, invalid) in product((False, True), cases):
            with (
                self.subTest(fast=fast, method=method, invalid=invalid),
                self._case(fast=fast) as case,
            ):
                root, service, store, card_id, box_id = case
                request = self._request(service, box_id, method)
                self._share_link(service, store, card_id, request["transaction_id"])
                if invalid == "stale":
                    request["expected_cashbox_updated_at"] = "stale"
                elif invalid == "reason":
                    request["reason"] = "short"
                else:
                    request["transaction_id"] = service.get_cashbox({"cashbox_id": box_id})[
                        "transactions"
                    ][-1]["id"]
                before = deepcopy(store.read_bundle())
                saved = self._files(root)
                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(request)
                self.assertEqual(
                    "cashbox_update_conflict" if invalid == "stale" else "validation_error",
                    rejected.exception.code,
                )
                self.assertEqual(before, store.read_bundle())
                self.assertEqual(saved, self._files(root))

    def test_payload_cannot_enable_internal_cancellation_mode(self) -> None:
        for fast, method in product((False, True), ("update_card", "update_repair_order")):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                root, service, store, card_id, box_id = case
                request = {
                    "card_id": card_id,
                    "operation": "cash_cancellation",
                    "repair_order": {
                        "payments": [
                            {"id": "same", "amount": "100", "cashbox_id": box_id} for _ in range(2)
                        ]
                    },
                }
                before = deepcopy(store.read_bundle())
                saved = self._files(root)
                caller = deepcopy(request)
                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(request)
                self.assertEqual("validation_error", rejected.exception.code)
                self.assertEqual(400, rejected.exception.status_code)
                self.assertEqual(before, store.read_bundle())
                self.assertEqual(saved, self._files(root))
                self.assertEqual(caller, request)

    def test_http_routes_report_ambiguous_link_without_saving(self) -> None:
        for fast, method in product((False, True), METHODS):
            with self.subTest(fast=fast, method=method), self._case(fast=fast) as case:
                root, service, store, card_id, box_id = case
                operators = OperatorAuthService(
                    store, service, users_file=root / "users.json", logger=self.logger
                )
                admin = operators.login({"username": "admin", "password": "admin"})["session"][
                    "token"
                ]
                operators.save_user(
                    {
                        "_operator_session": operators.resolve_session(admin),
                        "username": "cash-test",
                        "password": "test-password",
                        "role": "operator",
                        "permissions": [EMPLOYEES_CASHBOXES_ACCESS_PERMISSION],
                    }
                )
                allowed = operators.login({"username": "cash-test", "password": "test-password"})[
                    "session"
                ]["token"]
                request = self._request(service, box_id, method)
                self._share_link(
                    service, store, card_id, request["transaction_id"], other_card=True
                )
                request.pop("actor_name")
                server = ApiServer(
                    service, self.logger, operator_service=operators, bearer_token="", start_port=0
                )
                server.start()
                before = (root / "state.json").read_bytes()
                try:
                    for token, expected_status, expected_code in (
                        (admin, 403, "forbidden"),
                        (allowed, 409, "repair_order_payment_cash_link_ambiguous"),
                    ):
                        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                        try:
                            connection.request(
                                "POST",
                                f"/api/{method}",
                                body=json.dumps(request).encode("utf-8"),
                                headers={
                                    "Content-Type": "application/json",
                                    "X-Operator-Session": token,
                                },
                            )
                            response = connection.getresponse()
                            payload = json.loads(response.read().decode("utf-8"))
                        finally:
                            connection.close()
                        self.assertEqual(expected_status, response.status, payload)
                        self.assertEqual(expected_code, payload["error"]["code"])
                        self.assertEqual(before, (root / "state.json").read_bytes())
                finally:
                    server.stop()
