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
from minimal_kanban.storage.json_store import JsonStore, StateFileCorruptedError


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
        raw["cash_transactions"][0]["created_at"] = "not a date"
        self.state_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.state_file.read_bytes()
        with self.assertRaises(StateFileCorruptedError):
            self.service.mark_cashbox_notifications_seen(self.acknowledgement(movement))
        self.assertEqual(self.state_file.read_bytes(), before)
