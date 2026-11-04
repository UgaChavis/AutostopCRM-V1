"""Duplicate raw ledger identities must be preserved for explicit recovery."""

# The fixture selects the checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import replace
from threading import Thread
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError, StateWriteConflictError


class RawCashTransactionIdentityTests(CardServiceCase):
    def setUp(self):
        app_data = tempfile.TemporaryDirectory(prefix="synthetic-raw-ledger-")
        self.addCleanup(app_data.cleanup)
        env = patch.dict(os.environ, {"APPDATA": app_data.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)
        self.cashbox = self.service.create_cashbox({"name": "Synthetic"})["cashbox"]["id"]
        self.service.create_cash_transaction(
            {"cashbox_id": self.cashbox, "amount_minor": 100, "note": "Synthetic first"}
        )
        self.card = self.service.create_card({"title": "Synthetic card"})["card"]["id"]
        self.original = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.backup = self.state_file.with_suffix(".corrupted.json")
        self.backup.write_bytes(b"preserve synthetic backup")

    def replace_state(self, state):
        replacement = self.state_file.with_name("synthetic-replacement.tmp")
        replacement.write_text(json.dumps(state), encoding="utf-8")
        os.replace(replacement, self.state_file)

    def protected_files(self):
        paths = [
            self.state_file,
            self.backup,
            *self.state_file.parent.glob("*.sqlite3"),
            *self.state_file.parent.glob("*.sqlite3-wal"),
        ]
        return {
            str(path): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
            for path in paths
            if path.exists()
        }

    def duplicate_state(self, first_id, second_id):
        state = deepcopy(self.original)
        first = state["cash_transactions"][0]
        first["id"] = first_id
        second = dict(first, id=second_id, note="Synthetic second")
        state["cash_transactions"].append(second)
        return state

    def test_duplicate_and_normalized_duplicate_preserve_raw_state_and_feed(self):
        for first_id, second_id in (
            ("same-id", "same-id"),
            ("  same-id  ", "same-id"),
            ("x" * 128 + "A", "x" * 128 + "B"),
        ):
            with self.subTest(first_id=first_id):
                self.replace_state(self.duplicate_state(first_id, second_id))
                before = self.protected_files()
                for action in (self.store.read_bundle, self.store.reconcile_change_feed):
                    with self.assertRaises(StateFileCorruptedError):
                        action()
                    self.assertEqual(self.protected_files(), before)

    def test_clean_cache_then_duplicate_replacement_cannot_use_stale_cache(self):
        first, signature = self.store.read_bundle_with_signature()
        self.assertIs(self.store.read_bundle_with_signature()[0], first)
        self.assertIsNotNone(signature)
        self.replace_state(self.duplicate_state("duplicate", "duplicate"))
        before = self.protected_files()
        with self.assertRaises(StateFileCorruptedError):
            self.store.read_bundle()
        self.assertEqual(self.protected_files(), before)

    def test_audit_and_financial_writes_fail_before_normalization(self):
        self.replace_state(self.duplicate_state("duplicate", "duplicate"))
        before = self.protected_files()
        for action in (
            self.service.get_finance_audit,
            lambda: self.service.apply_finance_audit_safe_fixes({"dry_run": False}),
            lambda: self.service.create_cash_transaction(
                {"cashbox_id": self.cashbox, "amount_minor": 100, "note": "Synthetic"}
            ),
            lambda: self.service.update_card(
                {"card_id": self.card, "repair_order": {"payments": []}}
            ),
        ):
            with self.subTest(action=action), self.assertRaises(StateFileCorruptedError):
                action()
            self.assertEqual(self.protected_files(), before)

    def test_concurrent_replacement_rejects_stale_write_and_preserves_ledger(self):
        bundle, signature = self.store.read_bundle_with_signature()
        duplicate = self.duplicate_state("duplicate", "duplicate")

        def replace_under_lock():
            with self.store._process_lock.acquire():
                self.replace_state(duplicate)

        writer = Thread(target=replace_under_lock)
        writer.start()
        writer.join(timeout=10)
        self.assertFalse(writer.is_alive())
        before = self.protected_files()
        with self.assertRaises(StateWriteConflictError):
            self.store.write_bundle(**bundle, expected_signature=signature)
        self.assertEqual(self.protected_files(), before)
        with self.assertRaises(StateWriteConflictError):
            self.store.write_cached_bundle(bundle, **bundle, expected_signature=signature)
        self.assertEqual(self.protected_files(), before)
        with self.assertRaises(StateFileCorruptedError):
            self.store.read_bundle()
        self.assertEqual(self.protected_files(), before)

    def test_direct_and_cached_writes_reject_duplicate_input_before_changes(self):
        for first_id, second_id in (
            ("same-id", "same-id"),
            (" same-id ", "same-id"),
            ("x" * 128 + "A", "x" * 128 + "B"),
        ):
            for cached in (False, True):
                with self.subTest(first_id=first_id, cached=cached):
                    source, signature = self.store.read_bundle_with_signature()
                    transaction = source["cash_transactions"][0]
                    payload = {
                        **source,
                        "cash_transactions": [
                            replace(transaction, id=first_id),
                            replace(transaction, id=second_id, note="Synthetic second"),
                        ],
                    }
                    before = self.protected_files()
                    with self.assertRaises(ValueError):
                        if cached:
                            self.store.write_cached_bundle(
                                source, **payload, expected_signature=signature
                            )
                        else:
                            self.store.write_bundle(**payload, expected_signature=signature)
                    self.assertEqual(self.protected_files(), before)

    def test_failed_in_place_write_cannot_leave_duplicate_in_read_cache(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                source, signature = self.store.read_bundle_with_signature()
                source["cash_transactions"].append(
                    replace(source["cash_transactions"][0], note="Synthetic duplicate")
                )
                before = self.protected_files()
                with self.assertRaises(ValueError):
                    if cached:
                        self.store.write_cached_bundle(
                            source, **source, expected_signature=signature
                        )
                    else:
                        self.store.write_bundle(**source, expected_signature=signature)
                self.assertEqual(self.protected_files(), before)
                after = self.store.read_bundle()
                self.assertEqual(len(after["cash_transactions"]), 1)
                self.assertEqual(after["cash_transactions"][0].note, "Synthetic first")

    def test_write_without_expected_signature_cannot_replace_duplicate_raw_state(self):
        bundle = self.store.read_bundle()
        self.replace_state(self.duplicate_state("duplicate", "duplicate"))
        before = self.protected_files()
        with self.assertRaises(StateFileCorruptedError):
            self.store.write_bundle(**bundle)
        self.assertEqual(self.protected_files(), before)

    def test_embedded_mcp_start_fails_closed_before_starting_api(self):
        from minimal_kanban.mcp.main import _start_embedded_api_runtime

        self.replace_state(self.duplicate_state("duplicate", "duplicate"))
        before = {
            key: value
            for key, value in self.protected_files().items()
            if key in {str(self.state_file), str(self.backup)}
        }
        with (
            patch("minimal_kanban.mcp.main.JsonStore", return_value=self.store),
            patch(
                "minimal_kanban.mcp.main.OperatorActivityService",
                side_effect=AssertionError("API setup reached"),
            ),
        ):
            with self.assertRaises(StateFileCorruptedError):
                _start_embedded_api_runtime(None, self.logger, None, None, ownership={})
        self.assertEqual(
            {key: value for key, value in self.protected_files().items() if key in before}, before
        )

    def test_distinct_ids_and_readonly_snapshot_remain_available(self):
        state = self.duplicate_state("12345678-first", "12345678-second")
        self.replace_state(state)
        bundle = self.store.read_bundle()
        self.assertEqual(len(bundle["cash_transactions"]), 2)
        self.replace_state(self.duplicate_state("same-id", "same-id"))
        before = self.protected_files()
        snapshot = self.store._read_context_bundle_snapshot(domains=("clients",))
        self.assertIsNotNone(snapshot)
        self.assertEqual(self.protected_files(), before)
