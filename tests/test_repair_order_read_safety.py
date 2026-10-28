from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class RepairOrderReadSafetyTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="m2-repair-order-appdata-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        card = self.service.create_card(
            {
                "title": "PRIVATE_TITLE_SENTINEL",
                "vehicle": "PRIVATE_VEHICLE_SENTINEL",
                "description": "PRIVATE_DESCRIPTION_SENTINEL",
            }
        )["card"]
        self.card_id = card["id"]
        bundle = self.store.read_bundle()
        stored = next(item for item in bundle["cards"] if item.id == self.card_id)
        stored.repair_order = RepairOrder.from_dict(
            {
                "number": "",
                "client": "PRIVATE_CLIENT_SENTINEL",
                "phone": "+79991112233",
                "vin": "TESTVIN1234567890",
                "license_plate": "A123BC24",
                "vehicle": "PRIVATE_ORDER_VEHICLE_SENTINEL",
                "reason": "PRIVATE_REASON_SENTINEL",
                "comment": "PRIVATE_COMMENT_SENTINEL",
                "note": "PRIVATE_NOTE_SENTINEL",
                "works": [{"name": "PRIVATE_WORK_SENTINEL", "quantity": "1", "price": "10"}],
            }
        )
        self.store.write_bundle(**bundle)
        self.orders = Path(self.temp_dir.name) / "repair-orders"
        self.orders.mkdir(exist_ok=True)
        (self.orders / "orphan__C-ORPHAN.txt").write_text("sentinel orphan", encoding="utf-8")
        (self.orders / "unrelated.txt").write_text("sentinel unrelated", encoding="utf-8")

    def state_and_files(self):
        roots = {"state": Path(self.temp_dir.name), "appdata": Path(self.app_data.name)}
        return {
            (label, str(path.relative_to(root))): path.read_bytes()
            for label, root in roots.items()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_list_is_read_only_and_keeps_legacy_number_and_derived_files(self):
        before = self.state_and_files()
        with (
            patch.object(self.service, "_save_bundle", side_effect=AssertionError("save")),
            patch.object(
                self.service,
                "_cleanup_repair_orders_directory",
                side_effect=AssertionError("cleanup"),
            ),
            patch.object(
                self.service,
                "_ensure_repair_order_text_file",
                side_effect=AssertionError("materialize"),
            ),
        ):
            for compact in (False, True):
                for _ in range(2):
                    result = self.service.list_repair_orders({"status": "all", "compact": compact})
                    self.assertEqual(len(result["repair_orders"]), 1)
                    item = result["repair_orders"][0]
                    self.assertEqual(item["card_id"], self.card_id)
                    self.assertEqual(item["number"], "")
                    self.assertIn("file_name", item)
                    self.assertEqual(result["meta"]["status"], "all")
                    self.assertEqual(before, self.state_and_files())
            missing = self.service.list_repair_orders({"status": "all", "card_id": "missing"})
            self.assertEqual(missing["repair_orders"], [])
        self.assertEqual(before, self.state_and_files())

    def test_redact_private_applies_to_full_and_compact_without_free_text_leaks(self):
        sentinels = ("PRIVATE_", "+79991112233", "TESTVIN1234567890", "A123BC24")
        before = self.state_and_files()
        for compact in (True, False):
            with self.subTest(compact=compact):
                result = self.service.list_repair_orders(
                    {
                        "status": "all",
                        "compact": compact,
                        "redact_private": True,
                    }
                )
                item = result["repair_orders"][0]
                self.assertEqual(item["card_id"], self.card_id)
                self.assertEqual(item["number"], "")
                for text in sentinels:
                    self.assertNotIn(text, json.dumps(result, ensure_ascii=False))
                for key in ("heading", "vehicle", "file_name"):
                    self.assertEqual(item[key], "")
                if compact:
                    for key in ("client", "phone", "vin", "license_plate"):
                        self.assertNotIn(key, item)
                else:
                    for key in (
                        "client",
                        "phone",
                        "vin",
                        "license_plate",
                        "summary",
                        "reason",
                        "file_path",
                        "mileage",
                    ):
                        self.assertEqual(item[key], "")
                    for key in ("payments", "tags"):
                        self.assertEqual(item[key], [])
                self.assertEqual(before, self.state_and_files())

    def test_invalid_filters_and_failed_storage_read_leave_state_unchanged(self):
        before = self.state_and_files()
        for invalid in ({"card_id": ""}, {"number": ""}):
            with self.subTest(invalid=invalid), self.assertRaises(ServiceError):
                self.service.list_repair_orders(invalid)
            self.assertEqual(before, self.state_and_files())
        with patch.object(
            self.store,
            "read_repair_order_cards_snapshot",
            side_effect=OSError("synthetic read failure"),
            create=True,
        ):
            with self.assertRaises(OSError):
                self.service.list_repair_orders({"status": "all"})
        self.assertEqual(before, self.state_and_files())

    def test_corrupted_json_fails_without_creating_backup_or_touching_files(self):
        self.state_file.write_bytes(b"{not valid json")
        before = self.state_and_files()
        with self.assertRaises(StateFileCorruptedError):
            self.service.list_repair_orders({"status": "all"})
        self.assertEqual(before, self.state_and_files())

    def test_redacted_legacy_free_text_and_unsafe_filters_never_echo(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        order = card["repair_order"]
        for field in ("number", "date", "opened_at", "closed_at", "mileage", "prepayment"):
            order[field] = "PRIVATE_" + field.upper() + "_SENTINEL"
        order["tags"] = [{"id": "tag-1", "label": "PRIVATE_TAG_SENTINEL"}]
        order["payments"] = [{"id": "p1", "amount": "1", "note": "PRIVATE_PAYMENT_SENTINEL"}]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_and_files()
        for compact in (False, True):
            result = self.service.list_repair_orders(
                {"status": "all", "compact": compact, "redact_private": True}
            )
            self.assertNotIn("PRIVATE_", json.dumps(result, ensure_ascii=False))
            item = result["repair_orders"][0]
            for field in ("number", "date"):
                self.assertEqual(item[field], "")
            if not compact:
                for field in ("opened_at", "closed_at", "mileage"):
                    self.assertEqual(item[field], "")
                self.assertEqual(item["tags"], [])
                self.assertEqual(item["payments"], [])
            self.assertEqual(before, self.state_and_files())
        for field in ("card_id", "number"):
            result = self.service.list_repair_orders(
                {"status": "all", "redact_private": True, field: "PRIVATE_FILTER_SENTINEL"}
            )
            self.assertNotIn("PRIVATE_FILTER_SENTINEL", json.dumps(result, ensure_ascii=False))
            self.assertEqual(result["repair_orders"], [])
            self.assertEqual(before, self.state_and_files())

    def test_redacted_normal_uuid_number_and_iso_dates_remain(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        order = card["repair_order"]
        order.update(
            {"number": "42", "date": "2026-10-28", "opened_at": "2026-10-28T10:30:00+07:00"}
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_and_files()
        for compact in (False, True):
            result = self.service.list_repair_orders(
                {
                    "status": "all",
                    "compact": compact,
                    "redact_private": True,
                    "card_id": self.card_id,
                    "number": "42",
                }
            )
            item = result["repair_orders"][0]
            self.assertEqual(item["card_id"], self.card_id)
            self.assertEqual(item["number"], "42")
            self.assertEqual(item["date"], "2026-10-28")
            if not compact:
                self.assertEqual(item["opened_at"], "2026-10-28T10:30:00+07:00")
            self.assertEqual(result["meta"]["applied_filters"]["card_id"], self.card_id)
            self.assertEqual(result["meta"]["applied_filters"]["number"], "42")
            self.assertEqual(before, self.state_and_files())

    def test_snapshot_normalizes_legacy_in_memory_without_persisting(self):
        state = json.loads(self.state_file.read_text())
        state["columns"].append({"bad": "legacy"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_and_files()
        cards = self.store.read_repair_order_cards_snapshot()
        self.assertTrue(any(card.id == self.card_id for card in cards))
        self.assertEqual(before, self.state_and_files())

    def test_snapshot_does_not_overwrite_concurrent_state_replacement(self):
        state = json.loads(self.state_file.read_text())
        replacement = json.loads(self.state_file.read_text())
        next(card for card in replacement["cards"] if card["id"] == self.card_id)["title"] = (
            "CONCURRENT_UPDATE"
        )
        normalize = self.store._normalize_cards

        def replace_after_read(*args, **kwargs):
            value = normalize(*args, **kwargs)
            tmp = self.state_file.with_suffix(".concurrent")
            tmp.write_text(json.dumps(replacement), encoding="utf-8")
            tmp.replace(self.state_file)
            return value

        with patch.object(self.store, "_normalize_cards", side_effect=replace_after_read):
            cards = self.store.read_repair_order_cards_snapshot()
        self.assertEqual(
            next(c for c in cards if c.id == self.card_id).title,
            next(c for c in state["cards"] if c["id"] == self.card_id)["title"],
        )
        self.assertEqual(json.loads(self.state_file.read_text()), replacement)
        self.assertEqual(
            next(
                c for c in self.store.read_repair_order_cards_snapshot() if c.id == self.card_id
            ).title,
            "CONCURRENT_UPDATE",
        )

    def test_missing_legacy_card_identity_or_timestamps_fails_closed_without_writes(self):
        original = json.loads(self.state_file.read_text())
        for missing in ("id", "created_at", "updated_at"):
            with self.subTest(missing=missing):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.card_id)
                del card[missing]
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.state_and_files()
                for payload in (
                    {"status": "all", "redact_private": True},
                    {"status": "all", "card_id": self.card_id},
                ):
                    for _ in range(2):
                        with self.assertRaises(StateFileCorruptedError):
                            self.service.list_repair_orders(payload)
                        self.assertEqual(before, self.state_and_files())

    def test_redacted_preserves_valid_local_calendar_dates(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["repair_order"].update(
            {
                "number": "42",
                "date": "28.10.2026",
                "opened_at": "28.10.2026 10:30",
                "closed_at": "28.10.2026 11:40",
            }
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_and_files()
        for compact in (False, True):
            result = self.service.list_repair_orders(
                {"status": "all", "compact": compact, "redact_private": True}
            )
            item = result["repair_orders"][0]
            self.assertEqual(item["date"], "28.10.2026")
            if not compact:
                self.assertEqual(item["opened_at"], "28.10.2026 10:30")
                self.assertEqual(item["closed_at"], "28.10.2026 11:40")
            self.assertEqual(before, self.state_and_files())

    def test_unrelated_legacy_card_without_identity_does_not_block_valid_order(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["repair_order"]["number"] = "42"
        state["cards"].append({"title": "Unrelated synthetic card"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_and_files()
        for payload in (
            {"status": "all"},
            {"status": "all", "card_id": self.card_id, "number": "42"},
        ):
            for _ in range(2):
                result = self.service.list_repair_orders(payload)
                self.assertEqual(
                    [item["card_id"] for item in result["repair_orders"]], [self.card_id]
                )
                self.assertEqual(result["repair_orders"][0]["number"], "42")
                self.assertEqual(before, self.state_and_files())

    def test_windows_snapshot_requires_existing_lock_without_modification(self):
        lock_path = self.state_file.with_suffix(".lock")
        before = self.state_and_files()
        lock_stat = lock_path.stat()
        with patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True):
            result = self.service.list_repair_orders({"status": "all"})
            self.assertEqual([item["card_id"] for item in result["repair_orders"]], [self.card_id])
            self.assertEqual(before, self.state_and_files())
            self.assertEqual(lock_path.stat().st_mtime_ns, lock_stat.st_mtime_ns)
            lock_path.unlink()
            without_lock = self.state_and_files()
            with self.assertRaises(FileNotFoundError):
                self.service.list_repair_orders({"status": "all"})
            self.assertFalse(lock_path.exists())
            self.assertEqual(without_lock, self.state_and_files())

    def test_windows_snapshot_waits_for_writer_using_same_existing_lock(self):
        before = self.state_and_files()
        waiting = threading.Event()
        entered_read = threading.Event()
        finished = threading.Event()
        outcome = []
        original_read = self.store._read_state

        def observe_read(*args, **kwargs):
            entered_read.set()
            return original_read(*args, **kwargs)

        def run_read():
            waiting.set()
            try:
                outcome.append(self.service.list_repair_orders({"status": "all"}))
            except Exception as exc:
                outcome.append(exc)
            finally:
                finished.set()

        with (
            patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True),
            patch.object(self.store, "_read_state", side_effect=observe_read),
        ):
            with self.store._process_lock.acquire():
                worker = threading.Thread(target=run_read)
                worker.start()
                self.assertTrue(waiting.wait(2))
                self.assertFalse(entered_read.wait(0.3))
                self.assertFalse(finished.is_set())
            self.assertTrue(finished.wait(3))
            worker.join(timeout=3)
        self.assertTrue(entered_read.is_set())
        self.assertEqual(len(outcome), 1)
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        self.assertEqual([item["card_id"] for item in outcome[0]["repair_orders"]], [self.card_id])
        self.assertEqual(before, self.state_and_files())

    def test_missing_state_fails_closed_without_creating_or_changing_files(self):
        self.state_file.unlink()
        before = self.state_and_files()
        for _ in range(2):
            with self.assertRaises(FileNotFoundError):
                self.service.list_repair_orders({"status": "all"})
            self.assertFalse(self.state_file.exists())
            self.assertEqual(before, self.state_and_files())
