from __future__ import annotations

# The fixture import establishes the application source path.
# ruff: noqa: I001
import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.operator_permissions import EMPLOYEES_CASHBOXES_ACCESS_PERMISSION
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


PROTECTED = (
    "cashbox_cancelled",
    "CASHBOX_CANCELLED",
    "cashbox_cancellation",
    "CASHBOX_CANCELLATION",
    "cancellation_backref",
    "cancellation_backref_upper",
    "transfer_group",
    "transfer_related",
    "transfer_note_in",
    "transfer_note_out",
)
TAGGING_CODES = {"linked_payment_transaction_missing_kind", "legacy_order_note_without_kind"}


class FinanceAuditPaymentFixTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.store.close)

    @contextmanager
    def _case(self, variant="legacy", *, fast=False, archived=False, default_note=True):
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
            card = service.create_card({"vehicle": "TEST", "title": "Аудит старой оплаты"})["card"]
            service.update_repair_order(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "number": "214",
                        "works": [{"name": "Работа", "quantity": "1", "price": "100"}],
                        "payments": [
                            {
                                "id": "payment",
                                "amount": "100",
                                "cashbox_id": box["id"],
                                "paid_at": "16.10.2026 10:00",
                            }
                        ],
                    },
                }
            )
            bundle = store.read_bundle()
            payment = deepcopy(bundle["cards"][0].repair_order.payments[0])
            transaction_id = payment.cash_transaction_id
            if variant == "native_cancelled":
                service.cancel_cash_transaction(
                    {
                        "transaction_id": transaction_id,
                        "reason": "Синтетическая отмена платежа",
                    }
                )
                bundle = store.read_bundle()
                bundle["cards"][0].repair_order.payments = [payment]
            elif variant == "native_transfer":
                other_box = service.create_cashbox({"name": "Карта"})["cashbox"]
                transfer = service.create_cashbox_transfer(
                    {
                        "from_cashbox_id": box["id"],
                        "to_cashbox_id": other_box["id"],
                        "amount_minor": 10000,
                    }
                )
                transaction_id = transfer["target_transaction"]["id"]
                bundle = store.read_bundle()
                payment = bundle["cards"][0].repair_order.payments[0]
                payment.cash_transaction_id = transaction_id
                payment.cashbox_id = other_box["id"]
            transaction = next(t for t in bundle["cash_transactions"] if t.id == transaction_id)
            if not variant.startswith("native_"):
                transaction.transaction_kind = ""
                transaction.note = "Заказ-наряд №214" if default_note else "Старая ручная оплата"
                if variant.startswith(("cashbox_", "CASHBOX_")):
                    transaction.transaction_kind = variant
                elif variant.startswith("cancellation_backref"):
                    bundle["cash_transactions"].append(
                        replace(
                            transaction,
                            id="cancellation-peer",
                            direction="expense",
                            transaction_kind=(
                                "CASHBOX_CANCELLATION"
                                if variant.endswith("upper")
                                else "cashbox_cancellation"
                            ),
                            related_transaction_id=transaction.id,
                            note="Синтетическая компенсация",
                        )
                    )
                elif variant == "transfer_group":
                    transaction.transfer_group_id = "legacy-transfer"
                elif variant == "transfer_related":
                    transaction.related_transaction_id = "legacy-peer"
                elif variant.startswith("transfer_note_"):
                    transaction.note = (
                        "ПЕРЕМЕЩЕНИЕ ИЗ кассы" if variant.endswith("in") else "Перемещение в кассу"
                    )
                elif variant == "custom_legacy":
                    transaction.transaction_kind = "custom_legacy_payment"
            bundle["cards"][0].archived = archived
            store.write_bundle(**bundle)
            # Real WAL on both platforms; keep an idle connection through every
            # comparison. Opening/recovery precedes the byte-stability snapshot.
            with closing(sqlite3.connect(root / "change_feed.sqlite3")) as anchor:
                self.assertEqual("wal", anchor.execute("PRAGMA journal_mode").fetchone()[0])
                service.get_finance_audit()
                self.assertTrue((root / "change_feed.sqlite3-wal").exists())
                yield root, service, store, transaction_id

    def _files(self, root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def _tagging_issues(self, audit, transaction_id):
        return [
            issue
            for issue in audit["issues"]
            if issue["cash_transaction_id"] == transaction_id and issue["code"] in TAGGING_CODES
        ]

    def _assert_no_tagging(self, audit, transaction_id):
        issues = self._tagging_issues(audit, transaction_id)
        self.assertTrue(issues)
        self.assertFalse(any(issue["safe_fix_available"] for issue in issues))
        self.assertTrue(all(issue["safe_fix"] == {} for issue in issues))

    def test_preview_keeps_protected_findings_without_offering_kind_changes(self):
        for fast, variant, default_note, archived in product(
            (False, True), PROTECTED, (False, True), (False, True)
        ):
            with (
                self.subTest(fast=fast, variant=variant, note=default_note, archived=archived),
                self._case(
                    variant, fast=fast, default_note=default_note, archived=archived
                ) as case,
            ):
                root, service, store, transaction_id = case
                retained = store.read_bundle()
                before = deepcopy(retained)
                files = self._files(root)
                audit = service.get_finance_audit()
                self._assert_no_tagging(audit, transaction_id)
                preview = service.apply_finance_audit_safe_fixes()
                self._assert_no_tagging(preview, transaction_id)
                self.assertFalse(preview["meta"]["changed"])
                self.assertFalse(
                    any(
                        fix.get("cash_transaction_id") == transaction_id
                        for fix in preview["safe_fixes"]
                    )
                )
                self.assertEqual(before, retained)
                self.assertEqual(files, self._files(root))

    def test_apply_selected_or_all_and_replay_cannot_reclassify_protected_movements(self):
        for fast, variant, selected in product((False, True), PROTECTED, (False, True)):
            with (
                self.subTest(fast=fast, variant=variant, selected=selected),
                self._case(variant, fast=fast) as case,
            ):
                root, service, store, transaction_id = case
                audit = service.get_finance_audit()
                payload = {"dry_run": False, "actor_name": "ADMIN"}
                if selected:
                    payload["issue_ids"] = [
                        issue["id"] for issue in self._tagging_issues(audit, transaction_id)
                    ]
                caller = deepcopy(payload)
                before = deepcopy(store.read_bundle())
                files = self._files(root)
                for _ in range(2):
                    result = service.apply_finance_audit_safe_fixes(payload)
                    self.assertFalse(result["meta"]["changed"])
                    self.assertEqual([], result["applied"])
                    self.assertEqual(before, store.read_bundle())
                    self.assertEqual(files, self._files(root))
                    self.assertEqual(caller, payload)
                store.close()
                with closing(JsonStore(root / "state.json", self.logger)) as restarted_store:
                    restarted = CardService(restarted_store, self.logger)
                    self.assertEqual(before, restarted_store.read_bundle())
                    self._assert_no_tagging(restarted.get_finance_audit(), transaction_id)
                    warm_files = self._files(root)
                    self.assertFalse(
                        restarted.apply_finance_audit_safe_fixes(payload)["meta"]["changed"]
                    )
                    self.assertEqual(warm_files, self._files(root))

    def test_native_cancellation_and_transfer_keep_history_balances_and_guards(self):
        for fast, variant, archived in product(
            (False, True), ("native_cancelled", "native_transfer"), (False, True)
        ):
            with (
                self.subTest(fast=fast, variant=variant, archived=archived),
                self._case(variant, fast=fast, archived=archived) as case,
            ):
                root, service, store, transaction_id = case
                before = deepcopy(store.read_bundle())
                files = self._files(root)
                self._assert_no_tagging(service.get_finance_audit(), transaction_id)
                result = service.apply_finance_audit_safe_fixes({"dry_run": False})
                self.assertFalse(result["meta"]["changed"])
                self.assertEqual(before, store.read_bundle())
                self.assertEqual(files, self._files(root))
                if variant == "native_cancelled":
                    with self.assertRaises(ServiceError) as rejected:
                        service.cancel_cash_transaction(
                            {"transaction_id": transaction_id, "reason": "Повторная отмена платежа"}
                        )
                    self.assertEqual(
                        ("validation_error", 400),
                        (rejected.exception.code, rejected.exception.status_code),
                    )
                    self.assertEqual(files, self._files(root))

    def test_legitimate_legacy_tagging_is_preserved_with_one_audit_and_stable_replay(self):
        for fast, variant, default_note in product(
            (False, True), ("legacy", "custom_legacy"), (False, True)
        ):
            with (
                self.subTest(fast=fast, variant=variant, note=default_note),
                self._case(variant, fast=fast, default_note=default_note) as case,
            ):
                root, service, store, transaction_id = case
                before = deepcopy(store.read_bundle())
                audit = service.get_finance_audit()
                self.assertTrue(
                    all(
                        issue["safe_fix_available"]
                        for issue in self._tagging_issues(audit, transaction_id)
                    )
                )
                payload = {"dry_run": False, "actor_name": "ADMIN", "source": "api"}
                result = service.apply_finance_audit_safe_fixes(payload)
                self.assertEqual(1, result["meta"]["applied"])
                self.assertEqual("set_transaction_kind", result["applied"][0]["kind"])
                after = deepcopy(store.read_bundle())
                expected = deepcopy(before)
                next(
                    t for t in expected["cash_transactions"] if t.id == transaction_id
                ).transaction_kind = "repair_order_payment"
                self.assertEqual(expected["cash_transactions"], after["cash_transactions"])
                for key in set(before) - {"events", "cash_transactions"}:
                    self.assertEqual(before[key], after[key])
                events = after["events"][len(before["events"]) :]
                self.assertEqual(
                    ["finance_audit_safe_fix_applied"], [event.action for event in events]
                )
                self.assertEqual("ADMIN", events[0].actor_name)
                files = self._files(root)
                self.assertFalse(service.apply_finance_audit_safe_fixes(payload)["meta"]["changed"])
                self.assertEqual(files, self._files(root))
                store.close()
                with closing(JsonStore(root / "state.json", self.logger)) as restarted_store:
                    restarted = CardService(restarted_store, self.logger)
                    self.assertEqual(after, restarted_store.read_bundle())
                    restarted.get_finance_audit()
                    warm_files = self._files(root)
                    self.assertFalse(
                        restarted.apply_finance_audit_safe_fixes(payload)["meta"]["changed"]
                    )
                    self.assertEqual(warm_files, self._files(root))

    def test_stale_preview_rebuilds_eligibility_before_applying_selected_issue(self):
        for fast, variant in product((False, True), ("cashbox_cancelled", "transfer_group")):
            with (
                self.subTest(fast=fast, variant=variant),
                self._case(fast=fast, default_note=False) as case,
            ):
                root, service, store, transaction_id = case
                preview = service.apply_finance_audit_safe_fixes()
                payload = {
                    "dry_run": False,
                    "issue_ids": [issue["id"] for issue in preview["issues"]],
                    "expected_issue_ids": [issue["id"] for issue in preview["issues"]],
                }
                with closing(JsonStore(root / "state.json", self.logger)) as other:
                    bundle = other.read_bundle()
                    transaction = next(
                        t for t in bundle["cash_transactions"] if t.id == transaction_id
                    )
                    if variant == "cashbox_cancelled":
                        transaction.transaction_kind = variant
                    else:
                        transaction.transfer_group_id = "concurrent-transfer"
                    other.write_bundle(**bundle)
                current = service.get_finance_audit()
                before = deepcopy(store.read_bundle())
                files = self._files(root)
                if payload["expected_issue_ids"] != [issue["id"] for issue in current["issues"]]:
                    with self.assertRaises(ServiceError) as rejected:
                        service.apply_finance_audit_safe_fixes(payload)
                    self.assertEqual(
                        ("finance_audit_snapshot_conflict", 409),
                        (rejected.exception.code, rejected.exception.status_code),
                    )
                else:
                    self.assertFalse(
                        service.apply_finance_audit_safe_fixes(payload)["meta"]["changed"]
                    )
                self.assertEqual(before, store.read_bundle())
                self.assertEqual(files, self._files(root))

    def test_late_save_failure_does_not_mutate_retained_state_or_files(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast=fast) as case:
                root, service, store, _ = case
                retained = store.read_bundle()
                before = deepcopy(retained)
                files = self._files(root)
                with patch.object(
                    service, "_save_bundle", side_effect=OSError("synthetic final-save failure")
                ) as save:
                    with self.assertRaisesRegex(OSError, "synthetic final-save failure"):
                        service.apply_finance_audit_safe_fixes({"dry_run": False})
                save.assert_called_once()
                self.assertEqual(before, retained)
                self.assertEqual(files, self._files(root))

    def test_two_independent_writers_keep_one_fix_and_native_state_conflict(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast=fast) as case:
                root, service, store, _ = case
                with closing(JsonStore(root / "state.json", self.logger)) as second_store:
                    services = (service, CardService(second_store, self.logger))
                    before = deepcopy(store.read_bundle())
                    barrier = threading.Barrier(2)
                    originals = [item._read_bundle_for_update for item in services]

                    def read(index, *args, **kwargs):
                        bundle = originals[index](*args, **kwargs)
                        barrier.wait(timeout=10)
                        return bundle

                    def apply(index):
                        try:
                            return services[index].apply_finance_audit_safe_fixes(
                                {"dry_run": False}
                            )
                        except ServiceError as error:
                            return error

                    with (
                        patch.object(
                            services[0],
                            "_read_bundle_for_update",
                            side_effect=lambda *a, **kw: read(0, *a, **kw),
                        ),
                        patch.object(
                            services[1],
                            "_read_bundle_for_update",
                            side_effect=lambda *a, **kw: read(1, *a, **kw),
                        ),
                        ThreadPoolExecutor(max_workers=2) as pool,
                    ):
                        results = [
                            future.result(timeout=15)
                            for future in [pool.submit(apply, 0), pool.submit(apply, 1)]
                        ]
                    errors = [item for item in results if isinstance(item, ServiceError)]
                    self.assertEqual(1, len(errors))
                    self.assertEqual(
                        ("state_write_conflict", 409), (errors[0].code, errors[0].status_code)
                    )
                    winners = [item for item in results if isinstance(item, dict)]
                    self.assertEqual(1, winners[0]["meta"]["applied"])
                    after = store.read_bundle()
                    self.assertEqual(
                        ["finance_audit_safe_fix_applied"],
                        [event.action for event in after["events"][len(before["events"]) :]],
                    )

    def test_http_preview_apply_and_replay_preserve_protected_movements(self):
        for fast, variant in product((False, True), ("cashbox_cancelled", "transfer_group")):
            with self.subTest(fast=fast, variant=variant), self._case(variant, fast=fast) as case:
                root, service, store, transaction_id = case
                auth = OperatorAuthService(
                    store, service, users_file=root / "users.json", logger=self.logger
                )
                admin = auth.login({"username": "admin", "password": "admin"})["session"]["token"]
                auth.save_user(
                    {
                        "_operator_session": auth.resolve_session(admin),
                        "username": "audit-admin",
                        "password": "test-password",
                        "role": "admin",
                        "permissions": [EMPLOYEES_CASHBOXES_ACCESS_PERMISSION],
                    }
                )
                allowed = auth.login({"username": "audit-admin", "password": "test-password"})[
                    "session"
                ]["token"]
                server = ApiServer(
                    service, self.logger, operator_service=auth, bearer_token="", start_port=0
                )
                server.start()
                try:
                    before = deepcopy(store.read_bundle())
                    files = self._files(root)
                    for token, dry_run, status in (
                        (admin, True, 403),
                        (allowed, True, 200),
                        (allowed, False, 200),
                        (allowed, False, 200),
                    ):
                        with closing(
                            http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
                        ) as connection:
                            connection.request(
                                "POST",
                                "/api/finance_audit/apply_safe_fixes",
                                body=json.dumps({"dry_run": dry_run}).encode("utf-8"),
                                headers={
                                    "Content-Type": "application/json",
                                    "X-Operator-Session": token,
                                },
                            )
                            response = connection.getresponse()
                            result = json.loads(response.read().decode("utf-8"))
                        self.assertEqual(status, response.status, result)
                        if status == 403:
                            self.assertEqual("forbidden", result["error"]["code"])
                        else:
                            self.assertTrue(result["ok"])
                            self.assertFalse(result["data"]["meta"]["changed"])
                            self._assert_no_tagging(result["data"], transaction_id)
                        self.assertEqual(before, store.read_bundle())
                        self.assertEqual(files, self._files(root))
                finally:
                    server.stop()
