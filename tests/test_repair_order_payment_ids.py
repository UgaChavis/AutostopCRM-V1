from __future__ import annotations

# The fixture import sets up the src path before importing application modules.
# ruff: noqa: I001
import http.client
import json
from copy import deepcopy
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.repair_order import (
    REPAIR_ORDER_PAYMENT_ID_LIMIT,
    REPAIR_ORDER_PAYMENTS_LIMIT,
    RepairOrder,
)
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderPaymentIdentityTests(CardServiceCase):
    def _create_case(self, root: Path, *, seed_payment: bool = False):
        store = JsonStore(root / "state.json", self.logger)
        service = CardService(
            store,
            self.logger,
            attachments_dir=root / "attachments",
            repair_orders_dir=root / "repair-orders",
        )
        cashbox = service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})["cashbox"]
        card = service.create_card({"vehicle": "TEST", "title": "Проверка оплат"})["card"]
        if seed_payment:
            service.update_repair_order(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "payments": [
                            {"id": "existing", "amount": "50", "cashbox_id": cashbox["id"]}
                        ]
                    },
                }
            )
        return service, store, cashbox, card

    def _files(self, root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
        }

    def test_duplicate_stored_ids_are_rejected_without_mutating_any_state(self) -> None:
        prefix = "x" * REPAIR_ORDER_PAYMENT_ID_LIMIT
        collisions = (
            ("identical", ("same-id", "same-id")),
            ("whitespace", ("same-id", " same-id ")),
            ("length_cap", (prefix + "a", prefix + "b")),
            ("roundtrip_trim", (prefix[:-1] + " y", prefix[:-1])),
            ("generated_id", ("payment-2", None)),
        )
        for fast, method, field, (case, ids) in product(
            (False, True),
            ("update_card", "update_repair_order"),
            ("payments", "payment_history"),
            collisions,
        ):
            with (
                self.subTest(fast=fast, method=method, field=field, case=case),
                TemporaryDirectory() as directory,
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast,
                ),
            ):
                root = Path(directory)
                service, store, cashbox, card = self._create_case(root, seed_payment=True)
                payments = [
                    {"amount": "100", "cashbox_id": cashbox["id"], **({"id": id_} if id_ else {})}
                    for id_ in ids
                ]
                payload = {
                    "card_id": card["id"],
                    "title": "Не сохранять",
                    "description": "Отклонённое описание " * 400,
                    "repair_order": {field: payments},
                }
                caller_before = deepcopy(payload)
                retained = store.read_bundle()
                cache_before = deepcopy(retained)
                files_before = self._files(root)

                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)(payload)

                self.assertEqual(400, rejected.exception.status_code)
                self.assertEqual("validation_error", rejected.exception.code)
                self.assertEqual("repair_order.payments", rejected.exception.details["field"])
                self.assertEqual(caller_before, payload)
                self.assertEqual(cache_before, retained)
                self.assertEqual(files_before, self._files(root))
                reloaded = JsonStore(root / "state.json", self.logger).read_bundle()
                self.assertEqual(cache_before["cards"], reloaded["cards"])
                self.assertEqual(cache_before["cash_transactions"], reloaded["cash_transactions"])

    def test_unique_ids_preserve_cash_links_and_raw_payload_replay_is_a_noop(self) -> None:
        prefix = "x" * (REPAIR_ORDER_PAYMENT_ID_LIMIT - 1)
        identities = (
            ("explicit", ("first", "second"), ["first", "second"]),
            ("generated", (None, None), ["payment-1", "payment-2"]),
            ("roundtrip_trim", (prefix + " y", "second"), [prefix, "second"]),
        )
        for fast, method, field, (case, ids, expected) in product(
            (False, True),
            ("update_card", "update_repair_order"),
            ("payments", "payment_history"),
            identities,
        ):
            with (
                self.subTest(fast=fast, method=method, field=field, case=case),
                TemporaryDirectory() as directory,
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast,
                ),
            ):
                root = Path(directory)
                service, store, cashbox, card = self._create_case(root)
                payload = {
                    "card_id": card["id"],
                    "repair_order": {
                        field: [
                            {
                                "amount": "100",
                                "paid_at": "15.10.2026 10:00",
                                "cashbox_id": cashbox["id"],
                                **({"id": id_} if id_ else {}),
                            }
                            for id_ in ids
                        ]
                    },
                }
                caller_before = deepcopy(payload)
                update = getattr(service, method)
                first = update(payload)["card"]["repair_order"]
                self.assertEqual(expected, [payment["id"] for payment in first["payments"]])
                links = {payment["cash_transaction_id"] for payment in first["payments"]}
                self.assertNotIn("", links)
                self.assertEqual(2, len(links))
                before = deepcopy(store.read_bundle())
                state_bytes = (root / "state.json").read_bytes()

                repeated = update(payload)

                self.assertFalse(repeated["meta"]["changed"])
                self.assertEqual(first, repeated["card"]["repair_order"])
                self.assertEqual(before, store.read_bundle())
                self.assertEqual(state_bytes, (root / "state.json").read_bytes())
                self.assertEqual(caller_before, payload)
                transactions = JsonStore(root / "state.json", self.logger).read_bundle()[
                    "cash_transactions"
                ]
                self.assertEqual(links, {transaction.id for transaction in transactions})
                self.assertEqual(
                    20000, sum(transaction.amount_minor for transaction in transactions)
                )
                self.assertEqual("200", repeated["card"]["repair_order"]["paid_total"])

    def test_payment_ids_are_local_to_each_order(self) -> None:
        with TemporaryDirectory() as directory:
            service, store, cashbox, first = self._create_case(Path(directory))
            second = service.create_card({"vehicle": "TEST", "title": "Другой заказ"})["card"]
            links_by_order = []
            for card in (first, second):
                result = service.update_repair_order(
                    {
                        "card_id": card["id"],
                        "repair_order": {
                            "payments": [
                                {"id": id_, "amount": "100", "cashbox_id": cashbox["id"]}
                                for id_ in ("first", "second")
                            ]
                        },
                    }
                )["repair_order"]
                self.assertEqual(["first", "second"], [item["id"] for item in result["payments"]])
                links_by_order.append({item["cash_transaction_id"] for item in result["payments"]})
            self.assertTrue(links_by_order[0].isdisjoint(links_by_order[1]))
            transactions = store.read_bundle()["cash_transactions"]
            self.assertEqual(4, len(transactions))
            self.assertEqual(40000, sum(transaction.amount_minor for transaction in transactions))

    def test_type_limit_and_amount_errors_keep_priority_and_do_not_write(self) -> None:
        cases = (
            ("type", {}, {"field": "repair_order.payments"}),
            (
                "amount_before_ids",
                [{"id": "same", "amount": "0"}, {"id": "same", "amount": "100"}],
                {"field": "repair_order.payments.1.amount", "payment_id": "same"},
            ),
            (
                "limit_before_amount",
                [{"id": "same", "amount": "0"}] * (REPAIR_ORDER_PAYMENTS_LIMIT + 1),
                {"field": "repair_order.payments", "limit": REPAIR_ORDER_PAYMENTS_LIMIT},
            ),
        )
        for method, field, (case, value, details) in product(
            ("update_card", "update_repair_order"), ("payments", "payment_history"), cases
        ):
            with self.subTest(method=method, field=field, case=case):
                card = self.service.create_card({"title": "Отклонённая оплата"})["card"]
                payload = {"card_id": card["id"], "repair_order": {field: value}}
                caller_before = deepcopy(payload)
                before = self.state_file.read_bytes()
                with self.assertRaises(ServiceError) as rejected:
                    getattr(self.service, method)(payload)
                self.assertEqual(400, rejected.exception.status_code)
                self.assertEqual("validation_error", rejected.exception.code)
                self.assertEqual(details, rejected.exception.details)
                self.assertEqual(caller_before, payload)
                self.assertEqual(before, self.state_file.read_bytes())

    def test_empty_legacy_entries_are_still_ignored(self) -> None:
        for method, field in product(
            ("update_card", "update_repair_order"), ("payments", "payment_history")
        ):
            with self.subTest(method=method, field=field):
                card = self.service.create_card({"title": "Пустые оплаты"})["card"]
                self.service.get_repair_order({"card_id": card["id"]})
                before = self.state_file.read_bytes()
                result = getattr(self.service, method)(
                    {
                        "card_id": card["id"],
                        "repair_order": {field: [{"id": "same"}, {"id": "same"}]},
                    }
                )
                self.assertFalse(result["meta"]["changed"])
                self.assertEqual([], result["card"]["repair_order"]["payments"])
                self.assertEqual(before, self.state_file.read_bytes())

    def test_stored_duplicate_ids_remain_readable_without_reconciling_cash(self) -> None:
        actions = (
            ("update_repair_order", {"repair_order": {"comment": "Новый комментарий"}}),
            ("replace_repair_order_works", {"rows": [{"name": "Диагностика", "price": "200"}]}),
        )
        for fast, (method, changes) in product((False, True), actions):
            with (
                self.subTest(fast=fast, method=method),
                TemporaryDirectory() as directory,
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast,
                ),
            ):
                root = Path(directory)
                service, store, cashbox, card = self._create_case(root)
                bundle = store.read_bundle()
                bundle["cards"][0].repair_order = RepairOrder.from_dict(
                    {
                        "number": "1",
                        "payments": [
                            {"id": "same", "amount": "100", "cashbox_id": cashbox["id"]}
                            for _ in range(2)
                        ],
                    }
                )
                store.write_bundle(
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=bundle["events"],
                    settings=bundle["settings"],
                )
                read = service.get_card({"card_id": card["id"]})["card"]["repair_order"]
                self.assertEqual(["same", "same"], [item["id"] for item in read["payments"]])
                retained = store.read_bundle()
                cache_before = deepcopy(retained)
                files_before = self._files(root)

                with self.assertRaises(ServiceError) as rejected:
                    getattr(service, method)({"card_id": card["id"], **deepcopy(changes)})

                self.assertEqual("validation_error", rejected.exception.code)
                self.assertEqual(400, rejected.exception.status_code)
                self.assertEqual("repair_order.payments", rejected.exception.details["field"])
                self.assertEqual(cache_before, retained)
                self.assertEqual(files_before, self._files(root))

    def test_http_routes_reject_duplicate_payment_ids_without_saving(self) -> None:
        operator_service = OperatorAuthService(
            self.store,
            self.service,
            users_file=Path(self.temp_dir.name) / "users.json",
            logger=self.logger,
        )
        session = operator_service.login({"username": "admin", "password": "admin"})["session"][
            "token"
        ]
        server = ApiServer(
            self.service,
            self.logger,
            operator_service=operator_service,
            bearer_token="",
            start_port=0,
        )
        server.start()
        self.addCleanup(server.stop)
        cashbox = self.service.create_cashbox({"name": "Наличный"})["cashbox"]
        prefix = "x" * (REPAIR_ORDER_PAYMENT_ID_LIMIT - 1)
        for route in ("update_card", "update_repair_order"):
            for field in ("payments", "payment_history"):
                for ids in (("same", "same"), (prefix + " y", prefix)):
                    with self.subTest(route=route, field=field, ids=ids):
                        card = self.service.create_card({"title": "Проверка ID оплат"})["card"]
                        before = self.state_file.read_bytes()
                        retained = self.store.read_bundle()
                        transactions = list(retained["cash_transactions"])
                        payload = {
                            "card_id": card["id"],
                            "repair_order": {
                                field: [
                                    {"id": id_, "amount": "100", "cashbox_id": cashbox["id"]}
                                    for id_ in ids
                                ]
                            },
                        }
                        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                        try:
                            connection.request(
                                "POST",
                                f"/api/{route}",
                                body=json.dumps(payload).encode("utf-8"),
                                headers={
                                    "Content-Type": "application/json",
                                    "X-Operator-Session": session,
                                },
                            )
                            response = connection.getresponse()
                            result = json.loads(response.read().decode("utf-8"))
                        finally:
                            connection.close()
                        self.assertEqual(400, response.status)
                        self.assertEqual("validation_error", result["error"]["code"])
                        self.assertEqual(
                            "repair_order.payments", result["error"]["details"]["field"]
                        )
                        self.assertEqual(before, self.state_file.read_bytes())
                        self.assertEqual(transactions, retained["cash_transactions"])
