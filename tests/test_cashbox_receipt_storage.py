from __future__ import annotations

# Loading the fixture first selects this checkout.
# ruff: noqa: I001
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.cashbox_receipts import (
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY,
    CashboxReceiptStore,
    cashbox_receipt_directory,
    cashbox_receipt_filename,
    iter_cashbox_receipt_rows,
)
from minimal_kanban.storage.json_store import JsonStore, StateWriteConflictError


class CashboxReceiptStorageTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(self.store.close)
        self.box = self.service.create_cashbox({"name": "Synthetic cashbox"})["cashbox"]["id"]
        self.viewer = "synthetic viewer"

    def movement(self, amount: int):
        return self.service.create_cash_transaction(
            {"cashbox_id": self.box, "amount_minor": amount, "actor_name": "Synthetic other"}
        )["transaction"]

    def acknowledgement(self, transaction: dict):
        return {
            "actor_name": self.viewer,
            "exact_boundary": True,
            "through_transaction_id": transaction["id"],
        }

    def open_another_service(self):
        store = JsonStore(self.state_file, self.logger)
        self.addCleanup(store.close)
        return CardService(store, self.logger)

    def test_advancing_uses_legacy_position_without_invalidating_state_or_feed(self):
        old = self.movement(100)
        recent = self.movement(200)
        bundle = self.store.read_bundle()
        settings = {
            **bundle["settings"],
            CASHBOX_NOTIFICATION_SEEN_SETTING_KEY: {
                "  Synthetic   Viewer  ": {
                    "transaction_id": old["id"],
                    "created_at": old["created_at"],
                }
            },
        }
        self.store.write_bundle(**{**bundle, "settings": settings})
        before_state = self.state_file.read_bytes()
        before_version = self.store._repair_order_file_signature()
        before_revision = self.service.get_board_revision()["revision"]
        before_feed = self.store.change_feed_store.raw_events_for_test()
        notice = self.service.list_cashboxes({"actor_name": self.viewer})["notification"]
        self.assertEqual(notice["unread_count"], 1)
        self.assertFalse(cashbox_receipt_directory(self.state_file).exists())

        applied = self.service.mark_cashbox_notifications_seen(self.acknowledgement(recent))
        [stored] = iter_cashbox_receipt_rows(self.state_file)
        self.assertTrue(applied["meta"]["changed"])
        self.assertFalse(applied["notification"]["has_unread"])
        self.assertEqual(stored["transaction_id"], recent["id"])
        self.assertEqual(stored["actor_key"], self.viewer)
        reopened = self.open_another_service()
        duplicate = reopened.mark_cashbox_notifications_seen(self.acknowledgement(old))
        self.assertFalse(duplicate["meta"]["changed"])
        self.assertEqual(duplicate["meta"]["through_transaction_id"], old["id"])
        self.assertFalse(duplicate["notification"]["needs_ack"])
        self.assertEqual(self.state_file.read_bytes(), before_state)
        self.assertEqual(self.store._repair_order_file_signature(), before_version)
        self.assertEqual(self.service.get_board_revision()["revision"], before_revision)
        self.assertEqual(self.store.change_feed_store.raw_events_for_test(), before_feed)

    def test_two_service_instances_keep_the_newer_receipt(self):
        first = self.movement(100)
        last = self.movement(200)
        peer = self.open_another_service()
        ready = Barrier(2)

        def mark(service, transaction):
            ready.wait(timeout=5)
            return service.mark_cashbox_notifications_seen(self.acknowledgement(transaction))

        with ThreadPoolExecutor(max_workers=2) as threads:
            older = threads.submit(mark, self.service, first)
            newer = threads.submit(mark, peer, last)
            older.result(timeout=10)
            self.assertTrue(newer.result(timeout=10)["meta"]["changed"])
        [stored] = iter_cashbox_receipt_rows(self.state_file)
        self.assertEqual(stored["transaction_id"], last["id"])

    def test_peer_removal_between_validation_and_publish_fails_closed(self):
        movement = self.movement(100)
        peer = self.open_another_service()
        actual_fence = self.store.fence_cashbox_receipt_state

        @contextmanager
        def remove_then_check(version):
            peer.cancel_last_cash_transaction(
                {"cashbox_id": self.box, "transaction_id": movement["id"]}
            )
            with actual_fence(version):
                yield

        payload = self.acknowledgement(movement)
        with (
            patch.object(self.store, "fence_cashbox_receipt_state", side_effect=remove_then_check),
            self.assertRaises(ServiceError) as conflict,
        ):
            self.service.mark_cashbox_notifications_seen(payload)
        self.assertEqual(conflict.exception.code, "state_write_conflict")
        self.assertEqual(list(iter_cashbox_receipt_rows(self.state_file)), [])
        with self.assertRaises(ServiceError) as missing:
            self.service.mark_cashbox_notifications_seen(payload)
        self.assertEqual(missing.exception.status_code, 404)

    @unittest.skipUnless(os.name == "posix", "Directory sync is a POSIX operation")
    def test_repeat_ack_syncs_visible_row_after_a_failed_directory_sync(self):
        movement = self.movement(100)
        payload = self.acknowledgement(movement)
        directory = cashbox_receipt_directory(self.state_file)
        before_state = self.state_file.read_bytes()
        actual_sync = CashboxReceiptStore._sync_directory

        def fail_after_replace(path):
            if path == directory:
                raise OSError("Synthetic failure after receipt replacement")
            actual_sync(path)

        with (
            patch.object(CashboxReceiptStore, "_sync_directory", side_effect=fail_after_replace),
            self.assertRaises(ServiceError) as failed,
        ):
            self.service.mark_cashbox_notifications_seen(payload)
        self.assertEqual(failed.exception.code, "cashbox_notification_unavailable")
        [visible] = iter_cashbox_receipt_rows(self.state_file)
        self.assertEqual(visible["transaction_id"], movement["id"])

        # A separate store cannot rely on memory of the first failed sync.
        reopened = self.open_another_service()
        with patch.object(CashboxReceiptStore, "_sync_directory", wraps=actual_sync) as sync:
            retried = reopened.mark_cashbox_notifications_seen(payload)
        self.assertFalse(retried["meta"]["changed"])
        sync.assert_called_once_with(directory)
        self.assertEqual(self.state_file.read_bytes(), before_state)

    def test_corrupt_private_row_does_not_block_balances_or_get_replaced(self):
        movement = self.movement(100)
        receipts = cashbox_receipt_directory(self.state_file)
        receipts.mkdir()
        row = receipts / cashbox_receipt_filename(self.viewer)
        malformed = json.dumps(
            {
                "schema_version": 1,
                "actor_key": self.viewer,
                "transaction_id": movement["id"],
                "created_at": "not a date",
            }
        ).encode("utf-8")
        row.write_bytes(malformed)
        listed = self.service.list_cashboxes({"actor_name": self.viewer})
        self.assertTrue(listed["meta"]["notification_unavailable"])
        self.assertIsNone(listed["notification"])
        self.assertEqual(listed["cashboxes"][0]["statistics"]["balance_minor"], 100)
        with self.assertRaises(ServiceError) as error:
            self.service.mark_cashbox_notifications_seen(self.acknowledgement(movement))
        self.assertEqual(error.exception.code, "cashbox_notification_unavailable")
        self.assertEqual(row.read_bytes(), malformed)

    def test_cold_legacy_date_acknowledgement_never_normalizes_state_json(self):
        movement = self.movement(100)
        raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        raw["cash_transactions"][0]["created_at"] = "19.11.2026 14:00"
        self.state_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.state_file.read_bytes()
        applied = self.service.mark_cashbox_notifications_seen(self.acknowledgement(movement))
        self.assertTrue(applied["meta"]["changed"])
        self.assertEqual(self.state_file.read_bytes(), before)

        # An invalid date must not become a fresh watermark from a fallback clock.
        for bad_date in ("not a date", None):
            with self.subTest(bad_date=bad_date):
                if bad_date is None:
                    raw["cash_transactions"][0].pop("created_at")
                else:
                    raw["cash_transactions"][0]["created_at"] = bad_date
                self.state_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                before = self.state_file.read_bytes()
                listed = self.service.list_cashboxes({"actor_name": self.viewer})
                self.assertTrue(listed["meta"]["notification_unavailable"])
                self.assertIsNone(listed["notification"])
                self.assertEqual(listed["cashboxes"][0]["statistics"]["balance_minor"], 100)
                self.assertIsNone(listed["cashboxes"][0]["statistics"]["last_transaction_at"])
                with self.assertRaises(ServiceError) as error:
                    self.service.mark_cashbox_notifications_seen(self.acknowledgement(movement))
                self.assertEqual(error.exception.code, "cashbox_notification_unavailable")
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_two_tabs_cannot_auto_ack_an_unseen_movement(self):
        first = self.movement(100)
        peer = self.open_another_service()
        original = self.store.cashbox_receipts.read
        later = []

        def read_and_interleave(actor):
            current = original(actor)
            if not later:
                peer.mark_cashbox_notifications_seen(self.acknowledgement(first))
                later.append(self.movement(200))
            return current

        with patch.object(self.store.cashbox_receipts, "read", side_effect=read_and_interleave):
            notice = self.service.list_cashboxes({"actor_name": self.viewer})["notification"]
        self.assertTrue(notice["initialized"])
        self.assertEqual(notice["unread_count"], 1)
        # A background tab may already have received an uninitialized summary.
        duplicate = self.service.mark_cashbox_notifications_seen(
            {**self.acknowledgement(later[0]), "initialize_only": True}
        )
        self.assertFalse(duplicate["meta"]["changed"])
        self.assertEqual(duplicate["notification"]["unread_count"], 1)
        self.assertTrue(
            self.service.mark_cashbox_notifications_seen(self.acknowledgement(later[0]))["meta"][
                "changed"
            ]
        )

    def test_verified_receipt_cache_detects_in_place_edit_with_restored_mtime(self):
        self.movement(100)
        payload = {"actor_name": self.viewer}
        with (
            patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(
                self.store,
                "_repair_order_file_signature",
                return_value=self.store._repair_order_file_signature(),
            ),
        ):
            first = self.service.list_cashboxes(payload)
            self.assertEqual(first["cashboxes"][0]["statistics"]["balance_minor"], 100)
            with patch.object(
                self.store, "_decode_state", wraps=self.store._decode_state
            ) as decode:
                self.service.mark_cashbox_notifications_seen(payload)
                self.service.mark_cashbox_notifications_seen(payload)
                decode.assert_not_called()
                previous = self.state_file.stat()
                raw = self.state_file.read_bytes()
                changed = raw.replace(b'"amount_minor":100', b'"amount_minor":200', 1)
                self.assertNotEqual(raw, changed)
                self.state_file.write_bytes(changed)
                os.utime(self.state_file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                refreshed = self.service.list_cashboxes(payload)
                self.assertEqual(refreshed["cashboxes"][0]["statistics"]["balance_minor"], 200)
                decode.assert_called_once()

    def test_card_write_reuses_ledger_but_advances_the_receipt_fence(self):
        self.movement(100)
        before = self.store.read_cashbox_receipt_state()
        self.service.create_card({"title": "Unrelated card"})
        with (
            patch.object(self.store, "_cashbox_receipt_transactions") as rebuild,
            patch.object(
                self.store, "_cash_transactions_digest", wraps=self.store._cash_transactions_digest
            ) as digest,
        ):
            after = self.store.read_cashbox_receipt_state()
            self.assertIs(after.transactions, before.transactions)
            self.assertIs(after, self.store.read_cashbox_receipt_state())
            digest.assert_called_once()
            rebuild.assert_not_called()
        with (
            self.assertRaises(StateWriteConflictError),
            self.store.fence_cashbox_receipt_state(before),
        ):
            self.fail("Stale receipt snapshot passed its publication fence.")
        with self.store.fence_cashbox_receipt_state(after):
            pass

        bundle = self.store.read_bundle()
        bundle["cards"][-1].title = "Another title"
        bundle = self.store.write_bundle(**bundle)
        self.assertIs(self.store.read_cashbox_receipt_state().transactions, before.transactions)
        bundle["cash_transactions"][0].amount_minor = 200
        self.assertEqual(after.transactions[0].amount_minor, 100)
        self.store.write_bundle(**bundle)
        self.assertEqual(self.store.read_cashbox_receipt_state().transactions[0].amount_minor, 200)

    def test_references_do_not_build_transactions_even_after_a_cash_write(self):
        self.movement(100)
        peer = self.open_another_service()
        restricted = {"_operator_session": {"permissions": []}}
        with patch.object(peer._store, "_cashbox_receipt_transactions") as rebuild:
            self.assertTrue(peer.list_cashboxes(restricted)["meta"]["references_only"])
            self.service.create_cashbox({"name": "Another cashbox"})
            listed = peer.list_cashboxes(restricted)
            self.assertIn("Another cashbox", {box["name"] for box in listed["cashboxes"]})
            self.assertNotIn("statistics", listed["cashboxes"][0])
            rebuild.assert_not_called()

    def test_initialize_only_preserves_empty_legacy_position(self):
        movement = self.movement(100)
        bundle = self.store.read_bundle()
        settings = {
            **bundle["settings"],
            CASHBOX_NOTIFICATION_SEEN_SETTING_KEY: {
                self.viewer: {"transaction_id": "", "created_at": ""}
            },
        }
        self.store.write_bundle(**{**bundle, "settings": settings})
        result = self.service.mark_cashbox_notifications_seen(
            {**self.acknowledgement(movement), "initialize_only": True}
        )
        self.assertFalse(result["meta"]["changed"])
        self.assertEqual(result["notification"]["unread_count"], 1)
        self.assertIsNone(self.store.cashbox_receipts.read(self.viewer))

    def test_verified_fence_detects_same_size_edit_after_resolving_boundary(self):
        movement = self.movement(100)
        fence = self.store.fence_cashbox_receipt_state

        @contextmanager
        def edit_and_fence(snapshot):
            previous = self.state_file.stat()
            raw = self.state_file.read_bytes()
            changed = raw.replace(movement["id"].encode("ascii"), b"x" * len(movement["id"]))
            self.assertNotEqual(raw, changed)
            self.state_file.write_bytes(changed)
            os.utime(self.state_file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            with fence(snapshot):
                yield

        with (
            patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(
                self.store,
                "_repair_order_file_signature",
                return_value=self.store._repair_order_file_signature(),
            ),
            patch.object(self.store, "fence_cashbox_receipt_state", side_effect=edit_and_fence),
            self.assertRaises(ServiceError) as error,
        ):
            self.service.mark_cashbox_notifications_seen(self.acknowledgement(movement))
        self.assertEqual(error.exception.code, "state_write_conflict")
        self.assertIsNone(self.store.cashbox_receipts.read(self.viewer))
