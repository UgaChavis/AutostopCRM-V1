from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

from scripts.migrate_cashbox_receipts import LEGACY_KEY, migrate
from tests.services_case import CardServiceCase


class CashboxReceiptMigrationTests(CardServiceCase):
    def set_legacy(self, legacy):
        with self.store._process_lock.acquire():
            state = self.store._read_state(preserve_corrupted=False)
            state.setdefault("settings", {})[LEGACY_KEY] = legacy
            self.store._write_state(state)

    def test_offline_round_trip_preserves_inactive_empty_and_newer_private_positions(self):
        from minimal_kanban.storage.cashbox_receipts import CashboxReceiptStore

        empty = {"created_at": "", "transaction_id": ""}
        earlier = {"created_at": "2026-11-18T18:00:00+07:00", "transaction_id": "first"}
        later = {"created_at": "2026-11-18T18:00:00+07:00", "transaction_id": "second"}
        self.set_legacy({"Inactive": empty, "Worker": earlier})
        receipts = CashboxReceiptStore(self.state_file)
        receipts.advance("worker", later, publish_fence=self.store._process_lock.acquire)

        original = self.state_file.read_bytes()
        expected_business = deepcopy(self.store._read_state())
        del expected_business["settings"][LEGACY_KEY]
        self.assertEqual(migrate(self.state_file)["positions_to_copy"], 1)
        self.assertIsNone(receipts.read("inactive"))
        self.assertEqual(self.state_file.read_bytes(), original)
        migrate(self.state_file, apply=True)
        self.assertEqual(self.store._read_state(), expected_business)
        self.assertFalse(migrate(self.state_file, apply=True)["change_state_file"])

        migrate(self.state_file, to_legacy=True, apply=True)
        restored = self.store._read_state()
        self.assertEqual(restored["settings"].pop(LEGACY_KEY), {"inactive": empty, "worker": later})
        self.assertEqual(restored, expected_business)
        self.assertFalse(migrate(self.state_file, to_legacy=True, apply=True)["change_state_file"])

    def test_interrupted_copy_keeps_legacy_and_retry_finishes(self):
        from minimal_kanban.storage.cashbox_receipts import CashboxReceiptStore

        position = {"created_at": "", "transaction_id": ""}
        self.set_legacy({"One": position, "Two": position})
        original = self.state_file.read_bytes()
        real_advance = CashboxReceiptStore.advance

        def fail_second(receipts, actor, *args, **kwargs):
            if actor == "two":
                raise OSError("injected write failure")
            return real_advance(receipts, actor, *args, **kwargs)

        with patch.object(CashboxReceiptStore, "advance", fail_second), self.assertRaises(OSError):
            migrate(self.state_file, apply=True)
        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertEqual(migrate(self.state_file, apply=True)["positions_to_copy"], 1)
        self.assertNotIn(LEGACY_KEY, self.store._read_state()["settings"])

    def test_unrepresentable_legacy_date_preserves_original_state(self):
        self.set_legacy({"Inactive": {"created_at": "bad date", "transaction_id": "old"}})
        original = self.state_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "losslessly"):
            migrate(self.state_file, apply=True)
        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertIsNone(self.store.cashbox_receipts.read("inactive"))

    def test_retry_confirms_durability_even_when_all_private_rows_already_exist(self):
        from minimal_kanban.storage.cashbox_receipts import CashboxReceiptStore

        self.set_legacy({"One": {"created_at": "", "transaction_id": ""}})
        original = self.state_file.read_bytes()
        with (
            patch.object(
                CashboxReceiptStore, "sync_committed_directory", side_effect=OSError("sync failed")
            ),
            self.assertRaises(OSError),
        ):
            migrate(self.state_file, apply=True)
        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertEqual(migrate(self.state_file)["positions_to_copy"], 0)
        with patch.object(
            CashboxReceiptStore,
            "sync_committed_directory",
            autospec=True,
            side_effect=CashboxReceiptStore.sync_committed_directory,
        ) as sync:
            migrate(self.state_file, apply=True)
        sync.assert_called_once()
        self.assertNotIn(LEGACY_KEY, self.store._read_state()["settings"])
