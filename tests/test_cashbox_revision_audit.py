from __future__ import annotations

# The fixture import establishes the application source path.
# ruff: noqa: I001
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.services_case import CardServiceCase

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class CashboxRevisionAuditTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.store.close)

    @contextmanager
    def _case(self, fast, variant):
        with (
            TemporaryDirectory() as directory,
            patch(
                "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                return_value=fast,
            ),
            closing(JsonStore(Path(directory) / "state.json", self.logger)) as store,
        ):
            service = CardService(store, self.logger)
            cashbox = service.create_cashbox({"name": "Synthetic audit cashbox"})["cashbox"]
            card = service.create_card({"title": "Synthetic audit payment"})["card"]
            service.update_repair_order(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "number": "214",
                        "payments": [
                            {"id": "audit-payment", "amount": "1", "cashbox_id": cashbox["id"]}
                        ],
                    },
                }
            )
            bundle = deepcopy(store.read_bundle())
            transaction = bundle["cash_transactions"][0]
            if variant == "kind":
                transaction.transaction_kind = ""
            else:
                transaction.note = "Заказ-наряд №213"
            store.write_bundle(**bundle)
            yield service, store, Path(directory) / "state.json", cashbox["id"]

    def test_applied_metadata_fix_invalidates_previous_cashbox_revision(self):
        for fast in (False, True):
            for variant in ("kind", "note"):
                with (
                    self.subTest(fast=fast, variant=variant),
                    self._case(fast, variant) as (service, store, state_file, cashbox_id),
                ):
                    old = service.get_cashbox({"cashbox_id": cashbox_id})
                    before_transaction = deepcopy(store.read_bundle()["cash_transactions"][0])
                    result = service.apply_finance_audit_safe_fixes({"dry_run": False})
                    self.assertTrue(result["meta"]["changed"])
                    field = "transaction_kind" if variant == "kind" else "note"
                    current = service.get_cashbox({"cashbox_id": cashbox_id})
                    after_transaction = store.read_bundle()["cash_transactions"][0]
                    self.assertNotEqual(
                        getattr(before_transaction, field), getattr(after_transaction, field)
                    )
                    self.assertEqual(
                        old["transactions"][0]["amount_minor"],
                        current["transactions"][0]["amount_minor"],
                    )
                    self.assertEqual(
                        old["transactions"][0]["created_at"],
                        current["transactions"][0]["created_at"],
                    )
                    self.assertNotEqual(
                        old["cashbox"]["updated_at"], current["cashbox"]["updated_at"]
                    )
                    before = state_file.read_bytes()
                    with self.assertRaises(ServiceError) as caught:
                        service.create_cash_transaction(
                            {
                                "cashbox_id": cashbox_id,
                                "amount_minor": 100,
                                "expected_updated_at": old["cashbox"]["updated_at"],
                            }
                        )
                    self.assertEqual(caught.exception.code, "cashbox_update_conflict")
                    self.assertEqual(caught.exception.status_code, 409)
                    self.assertEqual(state_file.read_bytes(), before)
                    with closing(JsonStore(state_file, self.logger)) as reloaded:
                        persisted = reloaded.read_bundle()["cashboxes"][0]
                        self.assertEqual(persisted.updated_at, current["cashbox"]["updated_at"])

    def test_preview_and_replay_leave_cashbox_revision_unchanged(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast, "kind") as case:
                service, _, state_file, cashbox_id = case
                before = state_file.read_bytes()
                old = service.get_cashbox({"cashbox_id": cashbox_id})
                preview = service.apply_finance_audit_safe_fixes()
                self.assertFalse(preview["meta"]["changed"])
                self.assertEqual(state_file.read_bytes(), before)
                self.assertEqual(service.get_cashbox({"cashbox_id": cashbox_id}), old)
                service.apply_finance_audit_safe_fixes({"dry_run": False})
                applied = service.get_cashbox({"cashbox_id": cashbox_id})
                before = state_file.read_bytes()
                replay = service.apply_finance_audit_safe_fixes({"dry_run": False})
                self.assertFalse(replay["meta"]["changed"])
                self.assertEqual(state_file.read_bytes(), before)
                self.assertEqual(service.get_cashbox({"cashbox_id": cashbox_id}), applied)

    def test_failed_fix_does_not_publish_a_new_revision_to_disk_or_cached_bundle(self):
        for fast in (False, True):
            with self.subTest(fast=fast), self._case(fast, "kind") as case:
                service, store, state_file, cashbox_id = case
                before = state_file.read_bytes()
                retained = store.read_bundle()
                retained_before = deepcopy(retained)
                old = service.get_cashbox({"cashbox_id": cashbox_id})
                with patch.object(
                    store, "_write_state", side_effect=OSError("synthetic disk fault")
                ):
                    with self.assertRaises(OSError):
                        service.apply_finance_audit_safe_fixes({"dry_run": False})
                self.assertEqual(state_file.read_bytes(), before)
                self.assertEqual(retained, retained_before)
                self.assertEqual(service.get_cashbox({"cashbox_id": cashbox_id}), old)
