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

    def seed_scoped(self):
        box_a, box_b = (
            self.service.create_cashbox({"name": name})["cashbox"]["id"] for name in ("A", "B")
        )
        card = self.service.create_card({"title": "Rollback fixture"})["card"]["id"]
        seen = self.service.create_cash_transaction(
            {"cashbox_id": box_a, "amount_minor": 100, "actor_name": "writer"}
        )["transaction"]["id"]
        self.service.mark_cashbox_notifications_seen(
            {
                "actor_name": "reader",
                "cashbox_id": box_a,
                "through_transaction_id": seen,
                "exact_boundary": True,
            }
        )
        result = self.service.update_repair_order(
            {
                "card_id": card,
                "actor_name": "writer",
                "repair_order": {
                    "payments": [
                        {
                            "id": "late",
                            "amount": "2",
                            "cashbox_id": box_b,
                            "paid_at": "01.01.2020 09:00",
                        }
                    ],
                    "prepayment": "",
                },
            }
        )
        unseen = result["repair_order"]["payments"][0]["cash_transaction_id"]
        return box_a, box_b, card, seen, unseen

    def test_downgrade_twice_then_return_preserves_scopes_and_new_movements(self):
        from minimal_kanban.models import CASH_REGISTRATION_SEQUENCE_SETTING as counter
        from minimal_kanban.storage.cashbox_receipts import cashbox_receipt_directory

        box_a, box_b, card, seen, unseen = self.seed_scoped()
        scoped = self.store.cashbox_receipts.read("reader")
        before = self.store._read_state()
        numbers = {row["id"]: row["registration_seq"] for row in before["cash_transactions"]}
        original = self.state_file.read_bytes()
        self.assertTrue(migrate(self.state_file, to_legacy=True)["change_state_file"])
        self.assertEqual(self.state_file.read_bytes(), original)

        migrate(self.state_file, to_legacy=True, apply=True)
        marker = self.state_file.with_suffix(".cashbox_rollback.json")
        self.assertTrue(marker.exists())
        self.assertEqual(self.store.cashbox_receipts.read("reader")["schema_version"], 1)
        legacy = self.store._read_state()
        self.assertEqual(legacy["settings"][LEGACY_KEY]["reader"]["transaction_id"], "")
        self.assertNotIn(counter, legacy["settings"])
        first_downgrade = self.state_file.read_bytes()
        self.assertFalse(migrate(self.state_file, to_legacy=True, apply=True)["change_state_file"])
        self.assertEqual(self.state_file.read_bytes(), first_downgrade)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            marker.chmod(0o640)
            with self.assertRaises(PermissionError):
                migrate(self.state_file, apply=True)
            marker.chmod(0o600)
            for row in cashbox_receipt_directory(self.state_file).glob("*.json"):
                self.assertEqual(stat.S_IMODE(row.stat().st_mode), 0o600)

        # The previous writer appends to the ledger, omits registration numbers
        # and records a v1 global position when the receipt is acknowledged.
        legacy["cards"][0]["description"] = "Edited during rollback"
        added = {
            **legacy["cash_transactions"][-1],
            "id": "added-by-previous-version",
            "cashbox_id": box_a,
            "actor_name": "writer",
        }
        older = {
            **added,
            "id": "backdated-by-previous-version",
            "created_at": "2019-01-01T09:00:00+07:00",
        }
        legacy["cash_transactions"].extend((added, older))
        with self.store._process_lock.acquire():
            self.store._write_state(legacy)
        later = next(row for row in legacy["cash_transactions"] if row["id"] == seen)
        for actor in ("reader", "new-reader"):
            self.store.cashbox_receipts.advance(
                actor,
                {"transaction_id": seen, "created_at": later["created_at"]},
                publish_fence=self.store._process_lock.acquire,
            )
        self.assertFalse(migrate(self.state_file, to_legacy=True, apply=True)["change_state_file"])
        migrate(self.state_file, apply=True)
        self.assertFalse(marker.exists())
        self.assertEqual(scoped, self.store.cashbox_receipts.read("reader"))
        restored = self.store._read_state()
        self.assertEqual(restored["cards"][0]["id"], card)
        self.assertEqual(restored["cards"][0]["description"], "Edited during rollback")
        self.assertNotIn(LEGACY_KEY, restored["settings"])
        self.assertEqual(
            {row["id"]: row["registration_seq"] for row in restored["cash_transactions"]},
            {
                **numbers,
                older["id"]: before["settings"][counter] + 1,
                added["id"]: before["settings"][counter] + 2,
            },
        )
        for actor in ("reader", "new-reader"):
            by_box = self.service.list_cashboxes({"actor_name": actor})["notification"][
                "by_cashbox"
            ]
            self.assertIn(unseen, by_box[box_b]["unread_transaction_ids"])
            self.assertIn(added["id"], by_box[box_a]["unread_transaction_ids"])
            self.assertIn(older["id"], by_box[box_a]["unread_transaction_ids"])
        self.assertFalse(migrate(self.state_file, apply=True)["change_state_file"])

    def test_retry_after_interrupted_marker_or_private_row_publication(self):
        from scripts import migrate_cashbox_receipts as migration

        self.seed_scoped()
        position = self.store.cashbox_receipts.read("reader")
        original = self.store._read_state()
        marker = migration._manifest_path(self.state_file)
        if os.name == "posix":
            sync = os.fsync

            def fail_first_manifest_directory_sync(fd):
                if marker.exists() and os.path.samestat(os.fstat(fd), marker.parent.stat()):
                    raise OSError("interrupted manifest fsync")
                sync(fd)

            with patch("os.fsync", side_effect=fail_first_manifest_directory_sync):
                with self.assertRaisesRegex(OSError, "manifest fsync"):
                    migrate(self.state_file, to_legacy=True, apply=True)
            self.assertEqual(self.store._read_state(), original)
            self.assertEqual(self.store.cashbox_receipts.read("reader"), position)
        with patch.object(migration.CashboxReceiptStore, "_publish", side_effect=OSError("stop")):
            with self.assertRaisesRegex(OSError, "stop"):
                migrate(self.state_file, to_legacy=True, apply=True)
        self.assertEqual(
            migration._read_manifest(migration._manifest_path(self.state_file))["phase"],
            "preparing",
        )
        self.assertEqual(self.store._read_state(), original)
        migrate(self.state_file, to_legacy=True, apply=True)
        # Simulate a crash after both state and rows were restored, just before
        # the manifest can be retired. A retry must preserve the same numbers.
        original_unlink = type(marker).unlink

        def fail_final_unlink(path, *args, **kwargs):
            if path == marker:
                raise OSError("interrupted cleanup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(type(marker), "unlink", fail_final_unlink):
            with self.assertRaisesRegex(OSError, "interrupted cleanup"):
                migrate(self.state_file, apply=True)
        self.assertEqual(migration._read_manifest(marker)["phase"], "restoring")
        with self.assertRaisesRegex(ValueError, "to-private"):
            migrate(self.state_file, to_legacy=True, apply=True)
        migrate(self.state_file, apply=True)
        self.assertEqual(self.store._read_state(), original)
        self.assertEqual(self.store.cashbox_receipts.read("reader"), position)
        self.assertFalse(marker.exists())
