from __future__ import annotations

import os
import stat
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

    def test_preview_works_on_state_only_copy_and_creates_no_files(self):
        self.set_legacy({"Inactive": {"created_at": "", "transaction_id": ""}})
        directory = self.state_file.parent / "state-only-copy"
        directory.mkdir()
        original = self.state_file.read_bytes()
        source = directory / "state.json"
        source.write_bytes(original)
        if os.name == "posix":
            source.chmod(0o400)
            directory.chmod(0o500)
        try:
            self.assertEqual(migrate(source)["positions_to_copy"], 1)
            self.assertFalse(migrate(source, to_legacy=True)["change_state_file"])
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(directory.iterdir()), [source])
        finally:
            if os.name == "posix":
                directory.chmod(0o700)

    def test_offline_round_trip_preserves_inactive_empty_and_newer_private_positions(self):
        from minimal_kanban.storage.cashbox_receipts import CashboxReceiptStore

        empty = {"created_at": "", "transaction_id": ""}
        earlier = {"created_at": "2026-11-18T18:00:00+07:00", "transaction_id": "first"}
        later = {"created_at": "2026-11-18T18:00:00+07:00", "transaction_id": "second"}
        self.set_legacy({"Inactive": empty, "Worker": earlier})
        if os.name == "posix":
            self.state_file.chmod(0o640)
            metadata = self.state_file.stat()
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
        if os.name == "posix":
            actual = self.state_file.stat()
            self.assertEqual((actual.st_uid, actual.st_gid), (metadata.st_uid, metadata.st_gid))
            self.assertEqual(stat.S_IMODE(actual.st_mode), 0o640)

    def test_apply_checks_profile_before_initializing_the_store(self):
        from minimal_kanban.storage.cashbox_receipts import cashbox_receipt_directory

        self.set_legacy({"Inactive": {"created_at": "", "transaction_id": ""}})
        directory = self.state_file.parent / "offline"
        directory.mkdir()
        state = directory / "state.json"
        state.write_bytes(self.state_file.read_bytes())
        if os.name == "posix":
            with patch("os.geteuid", return_value=state.stat().st_uid + 1):
                for to_legacy in (False, True):
                    with self.assertRaises(PermissionError):
                        migrate(state, to_legacy=to_legacy, apply=True)
            self.assertEqual(list(directory.iterdir()), [state])

            receipts = cashbox_receipt_directory(state)
            receipts.mkdir(mode=0o700)
            for suffix in (".lock", ".json"):
                path = receipts / ("a" * 64 + suffix)
                path.touch()
                path.chmod(0o400)
                with self.assertRaises(PermissionError):
                    migrate(state, apply=True)
                path.unlink()
            self.assertFalse((directory / "change_feed.sqlite3").exists())
        self.assertEqual(migrate(state)["positions_to_copy"], 1)

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

        if os.name == "posix":
            fsync = os.fsync
            directory_syncs = []

            def fail_once(fd):
                if os.path.samestat(os.fstat(fd), self.state_file.parent.stat()):
                    directory_syncs.append(fd)
                    if len(directory_syncs) == 1:
                        raise OSError("directory sync failed after state replace")
                fsync(fd)

            with patch("os.fsync", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "after state replace"):
                    migrate(self.state_file, to_legacy=True, apply=True)
                self.assertIn(LEGACY_KEY, self.store._read_state()["settings"])
                retry = migrate(self.state_file, to_legacy=True, apply=True)
                self.assertFalse(retry["change_state_file"])
            self.assertEqual(len(directory_syncs), 2)
