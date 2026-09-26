from __future__ import annotations

import json

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.storage.json_store import JsonStore
from tests.services_case import CardServiceCase


class StorageMovementIntegrityTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        cashbox = self.service.create_cashbox({"name": "Synthetic cashbox"})["cashbox"]
        self.service.create_cash_transaction(
            {"cashbox_id": cashbox["id"], "direction": "income", "amount": "100"}
        )
        item = self.service.save_inventory_item({"name": "Synthetic stock"})["item"]
        self.service.replenish_inventory_item({"item_id": item["id"], "quantity": "2"})

    def _remove_parent(self, collection: str) -> bytes:
        state = json.loads(self.state_file.read_bytes())
        state[collection] = []
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return self.state_file.read_bytes()

    def test_finance_audit_reports_orphan_without_erasing_history(self) -> None:
        before = self._remove_parent("cashboxes")
        for _ in range(2):
            self.store = JsonStore(state_file=self.state_file, logger=self.logger)
            audit = self._build_service().get_finance_audit()
            self.assertIn(
                "cash_transaction_missing_cashbox", {issue["code"] for issue in audit["issues"]}
            )
            self.assertEqual(self.state_file.read_bytes(), before)
            self.assertEqual(len(self.store.read_bundle()["cash_transactions"]), 1)

    def test_inventory_read_preserves_orphan_movement_across_reload(self) -> None:
        before = self._remove_parent("inventory_items")
        for _ in range(2):
            self.store = JsonStore(state_file=self.state_file, logger=self.logger)
            movements = self.store.read_bundle()["inventory_movements"]
            self.assertEqual(len(movements), 1)
            self.assertEqual(movements[0].quantity_delta, "2")
            self.assertEqual(self.state_file.read_bytes(), before)

    def test_write_paths_reject_removing_parent_of_existing_movement(self) -> None:
        original = self.state_file.read_bytes()
        for collection in ("cashboxes", "inventory_items"):
            for cached in (False, True):
                with self.subTest(collection=collection, cached=cached):
                    self.state_file.write_bytes(original)
                    bundle = self.store.read_bundle()
                    before = self.state_file.read_bytes()
                    payload = {**bundle, collection: []}
                    with self.assertRaisesRegex(ValueError, "unknown"):
                        if cached:
                            self.store.write_cached_bundle(bundle, **payload)
                        else:
                            self.store.write_bundle(**payload)
                    self.assertEqual(self.state_file.read_bytes(), before)
                    self.assertEqual(len(self.store.read_bundle()[collection]), 1)

    def test_partial_write_cannot_silently_discard_existing_orphan_history(self) -> None:
        for collection in ("cashboxes", "inventory_items"):
            with self.subTest(collection=collection):
                original = self.state_file.read_bytes()
                before = self._remove_parent(collection)
                bundle = self.store.read_bundle()
                with self.assertRaisesRegex(ValueError, "unknown"):
                    self.store.write_bundle(
                        columns=bundle["columns"], cards=bundle["cards"], events=bundle["events"]
                    )
                self.assertEqual(self.state_file.read_bytes(), before)
                self.state_file.write_bytes(original)

    def test_unrelated_legacy_normalization_retains_orphan_history(self) -> None:
        state = json.loads(self._remove_parent("cashboxes"))
        state["inventory_items"] = []
        state["settings"]["board_scale"] = "invalid"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        bundle = self.store.read_bundle()
        stored = json.loads(self.state_file.read_bytes())
        for collection in ("cash_transactions", "inventory_movements"):
            self.assertEqual(len(bundle[collection]), 1)
            self.assertEqual(stored[collection], state[collection])
