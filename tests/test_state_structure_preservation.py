"""Malformed domain structures cannot turn a read into irreversible data loss."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore, StateFileCorruptedError  # noqa: E402
from minimal_kanban.storage.state_shape import STATE_RECORD_COLLECTIONS  # noqa: E402
from tests import test_api  # noqa: E402


class StateStructurePreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "state.json"
        self.logger = logging.getLogger("test.state.structure")
        self.logger.addHandler(logging.NullHandler())
        self.store = JsonStore(self.path, self.logger)
        self.addCleanup(self.store.close)
        self.service = CardService(
            self.store,
            self.logger,
            attachments_dir=self.root / "attachments",
            repair_orders_dir=self.root / "repair-orders",
        )
        self.card_id = self.service.create_card({"title": "Synthetic preservation"})["card"]["id"]
        self.trusted = self.store.read_bundle()
        self.original = self.path.read_bytes()
        self.feed_before = self.store.change_feed_store.raw_events_for_test()

    def damage(self, name, value) -> bytes:
        payload = json.loads(self.original)
        payload[name] = value
        damaged = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(damaged)
        return damaged

    def assert_preserved(self, damaged: bytes) -> None:
        self.assertEqual(self.path.read_bytes(), damaged)
        self.assertEqual(self.store.change_feed_store.raw_events_for_test(), self.feed_before)
        self.assertTrue(
            any(path.read_bytes() == damaged for path in self.root.glob("state.corrupted*.json"))
        )

    def test_wrong_collection_or_record_types_block_reads_writes_and_feed_reconciliation(self):
        for name in sorted(STATE_RECORD_COLLECTIONS):
            for value in (None, "recoverable", {"saved": [{"id": "preserve"}]}, [None], ["saved"]):
                with self.subTest(domain=name, value=value):
                    damaged = self.damage(name, value)
                    for operation in (
                        self.store.read_bundle,
                        self.store.reconcile_change_feed,
                        lambda: self.store.write_bundle(**self.trusted),
                    ):
                        with self.assertRaises(StateFileCorruptedError):
                            operation()
                        self.assert_preserved(damaged)
                    self.path.write_bytes(self.original)
                    self.assertEqual(self.store.read_bundle()["cards"][0].id, self.card_id)

    def test_malformed_settings_survive_restart_and_do_not_publish_false_deletes(self):
        for value in (None, [], "saved settings"):
            with self.subTest(value=value):
                damaged = self.damage("settings", value)
                cold = JsonStore(self.path, self.logger)
                try:
                    with self.assertRaises(StateFileCorruptedError):
                        cold.read_bundle()
                    with self.assertRaises(StateFileCorruptedError):
                        CardService(cold, self.logger).get_cards({})
                    self.assert_preserved(damaged)
                    backups = list(self.root.glob("state.corrupted*.json"))
                    with self.assertRaises(StateFileCorruptedError):
                        cold.read_bundle()
                    self.assertEqual(list(self.root.glob("state.corrupted*.json")), backups)
                    self.path.write_bytes(self.original)
                    cold.reconcile_change_feed()
                    self.assertEqual(cold.read_bundle()["cards"][0].title, "Synthetic preservation")
                    self.assertEqual(cold.change_feed_store.raw_events_for_test(), self.feed_before)
                finally:
                    cold.close()

    def test_missing_legacy_domains_and_legacy_field_migrations_remain_supported(self):
        legacy = {
            "schema_version": 2,
            "columns": [{"id": "inbox", "label": "Legacy"}],
            "cards": [
                {"id": "legacy-card", "title": "", "column": "missing", "priority": "urgent"}
            ],
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        result = self.store.read_bundle()
        self.assertEqual(result["cards"][0].id, "legacy-card")
        self.assertEqual(result["cards"][0].title, "Без названия")
        self.assertEqual(result["cards"][0].column, "inbox")
        self.assertEqual(result["clients"], [])
        self.assertEqual(result["cash_transactions"], [])
        self.assertEqual(list(self.root.glob("state.corrupted*.json")), [])


class StateStructureApiTests(unittest.TestCase):
    setUp = test_api.ApiServerTests.setUp
    request = test_api.ApiServerTests.request

    def test_corrupt_state_has_controlled_api_error_and_recovers_without_rewriting_data(self):
        status, created = self.request("/api/create_card", {"title": "Synthetic stored card"})
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        path = self.store.base_dir / "state.json"
        original = path.read_bytes()
        state = json.loads(original)
        state["cash_transactions"] = {"recoverable": [{"id": "ledger", "amount": "777.25"}]}
        damaged = json.dumps(state).encode("utf-8")
        path.write_bytes(damaged)
        before_feed = self.store.change_feed_store.raw_events_for_test()
        for route, payload in (
            ("/api/get_cards", {}),
            ("/api/update_card", {"card_id": card_id, "title": "Must not commit"}),
            ("/api/get_repair_order_print_workspace", {"card_id": card_id}),
        ):
            status, result = self.request(route, payload)
            self.assertEqual(status, 503, result)
            self.assertEqual(result["error"]["code"], "state_file_corrupted")
            self.assertEqual(path.read_bytes(), damaged)
            self.assertEqual(self.store.change_feed_store.raw_events_for_test(), before_feed)
        path.write_bytes(original)
        self.assertEqual(self.request("/api/get_cards", {})[0], 200)
        self.assertEqual(self.store.read_bundle()["cards"][0].title, "Synthetic stored card")
