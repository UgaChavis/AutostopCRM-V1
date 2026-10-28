from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class EntityContextReadSafetyTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="m2-context-appdata-")
        self.addCleanup(self.app_data.cleanup)
        env = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "Synthetic context"})["card"]["id"]
        self.calls = (
            lambda: self.service.get_card({"card_id": self.card_id}),
            lambda: self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            ),
            lambda: self.service.get_repair_order(
                {"card_id": self.card_id, "create_if_missing": False}
            ),
        )

    def snapshot(self):
        roots = {"state": Path(self.temp_dir.name), "appdata": Path(self.app_data.name)}
        return {
            (name, str(path.relative_to(root))): (
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for name, root in roots.items()
            for path in root.rglob("*")
            if path.is_file()
        }

    def make_legacy(self):
        state = json.loads(self.state_file.read_text())
        state["columns"].append({"bad": "synthetic legacy"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_read_contexts_never_normalize_state_or_create_artifacts(self):
        self.make_legacy()
        before = self.snapshot()
        for call in self.calls:
            for _ in range(2):
                result = call()
                self.assertEqual(result["card"]["id"], self.card_id)
                self.assertEqual(result["card"]["title"], "Synthetic context")
                self.assertTrue(result["card"]["created_at"])
                self.assertTrue(result["card"]["updated_at"])
                self.assertEqual(before, self.snapshot())
        context = self.calls[1]()
        self.assertIsNone(context["repair_order_text"])
        order = self.calls[2]()
        self.assertFalse(order["meta"]["created"])
        self.assertFalse(order["meta"]["has_any_data"])
        self.assertEqual(before, self.snapshot())

    def test_missing_target_never_rewrites_legacy_state(self):
        self.make_legacy()
        before = self.snapshot()
        calls = (
            lambda: self.service.get_card({"card_id": "missing-synthetic"}),
            lambda: self.service.get_card_context(
                {"card_id": "missing-synthetic", "include_repair_order_text": False}
            ),
            lambda: self.service.get_repair_order(
                {"card_id": "missing-synthetic", "create_if_missing": False}
            ),
        )
        for call in calls:
            with self.assertRaises(ServiceError):
                call()
            self.assertEqual(before, self.snapshot())

    def test_corrupt_state_fails_without_repair_or_backup(self):
        self.state_file.write_bytes(b"{invalid json")
        before = self.snapshot()
        for call in self.calls:
            with self.assertRaises(StateFileCorruptedError):
                call()
            self.assertEqual(before, self.snapshot())

    def test_missing_target_identity_or_timestamps_never_invented(self):
        original = json.loads(self.state_file.read_text())
        for missing in ("id", "created_at", "updated_at"):
            with self.subTest(missing=missing):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.card_id)
                del card[missing]
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for call in self.calls:
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        call()
                    self.assertEqual(before, self.snapshot())

    def test_explicit_create_if_missing_still_creates_order(self):
        before = self.state_file.read_bytes()
        created = self.service.get_repair_order(
            {"card_id": self.card_id, "create_if_missing": True}
        )
        self.assertTrue(created["meta"]["created"])
        self.assertTrue(created["repair_order"]["number"])
        self.assertNotEqual(before, self.state_file.read_bytes())

    def test_full_context_rejects_selected_attachment_without_id_or_date(self):
        original = json.loads(self.state_file.read_text())
        for missing in ("id", "created_at"):
            with self.subTest(missing=missing):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.card_id)
                attachment = {
                    "id": "synthetic-attachment-1",
                    "created_at": "2026-10-28T10:00:00+00:00",
                    "file_name": "synthetic.txt",
                    "stored_name": "synthetic.txt",
                    "mime_type": "text/plain",
                    "size_bytes": 1,
                    "created_by": "synthetic",
                }
                del attachment[missing]
                card["attachments"] = [attachment]
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises(StateFileCorruptedError):
                        self.service.get_card_context(
                            {"card_id": self.card_id, "include_repair_order_text": False}
                        )
                    self.assertEqual(before, self.snapshot())

    def test_full_context_rejects_selected_event_without_id_or_timestamp(self):
        original = json.loads(self.state_file.read_text())
        for missing in ("id", "timestamp"):
            with self.subTest(missing=missing):
                state = json.loads(json.dumps(original))
                event = {
                    "id": "synthetic-event-1",
                    "timestamp": "2026-10-28T10:00:00+00:00",
                    "actor_name": "synthetic",
                    "source": "ui",
                    "action": "card_created",
                    "message": "synthetic",
                    "details": {},
                    "card_id": self.card_id,
                }
                del event[missing]
                state["events"].insert(0, event)
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises(StateFileCorruptedError):
                        self.service.get_card_context(
                            {"card_id": self.card_id, "include_repair_order_text": False}
                        )
                    self.assertEqual(before, self.snapshot())

    def test_full_context_ignores_unrelated_bad_attachment_and_event(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        other = json.loads(json.dumps(card))
        other["id"] = "839fb486-f581-4c73-bc7a-5fb0bf3c886b"
        other["attachments"] = [{"file_name": "other.txt", "stored_name": "other.txt"}]
        state["cards"].append(other)
        state["events"].insert(
            0, {"card_id": other["id"], "action": "card_created", "message": "synthetic"}
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for _ in range(2):
            result = self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            )
            self.assertEqual(result["card"]["id"], self.card_id)
            self.assertEqual(before, self.snapshot())

    def test_legacy_derived_deadline_and_item_ids_are_signalled_without_write(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["deadline_timestamp"] = None
        card["repair_order"] = {
            "number": "42",
            "works": [{"name": "SYNTHETIC", "qty": "1", "price": "10"}],
            "payments": [
                {"amount": "2"},
                {"id": "duplicate", "amount": "3"},
                {"id": "duplicate", "amount": "4"},
            ],
        }
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        expected = {
            "deadline_calculated_from_created_at",
            "repair_order_row_identity_unverified",
            "repair_order_payment_identity_unverified",
        }
        for call in self.calls:
            first = call()
            self.assertEqual(set(first["read_warnings"]), expected)
            self.assertTrue(first["card"]["deadline_timestamp"])
            self.assertEqual(first["card"]["repair_order"]["works"][0]["id"], "row-1")
            self.assertEqual(first["card"]["repair_order"]["payments"][0]["id"], "payment-1")
            second = call()
            self.assertEqual(
                first["card"]["deadline_timestamp"], second["card"]["deadline_timestamp"]
            )
            self.assertEqual(expected, set(second["read_warnings"]))
            self.assertEqual(before, self.snapshot())

    def test_legacy_prepayment_is_marked_as_derived(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["repair_order"] = {"number": "42", "prepayment": "12"}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        result = self.service.get_repair_order(
            {"card_id": self.card_id, "create_if_missing": False}
        )
        self.assertIn("repair_order_payment_identity_unverified", result["read_warnings"])
        self.assertEqual(result["repair_order"]["payments"][0]["id"], "legacy-prepayment")
        self.assertEqual(before, self.snapshot())

    def test_selected_malformed_financial_data_fails_without_filesystem_changes(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["repair_order"] = {"number": "42", "works": ["SYNTHETIC_INVALID"]}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for call in self.calls:
            with self.assertRaises(StateFileCorruptedError):
                call()
            self.assertEqual(before, self.snapshot())

    def test_normalized_card_id_is_not_returned_as_saved_identity(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["id"] = "x" * 128 + "A"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(StateFileCorruptedError):
            self.service.get_card({"card_id": "x" * 128})
        self.assertEqual(before, self.snapshot())

    def test_skipped_malformed_attachment_cannot_look_like_empty_list(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["attachments"] = [{"id": "synthetic-bad", "file_name": "missing-stored-name"}]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for call in self.calls:
            with self.assertRaises(StateFileCorruptedError):
                call()
            self.assertEqual(before, self.snapshot())
