from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minimal_kanban.models import AuditEvent, Card, utc_now  # noqa: E402
from minimal_kanban.storage.audit_archive import (  # noqa: E402
    AuditArchiveStore,
    compact_audit_event_details,
)
from minimal_kanban.storage.file_lock import ProcessFileLock  # noqa: E402
from minimal_kanban.storage.financial_history_cleanup import (  # noqa: E402
    sanitize_financial_history_state,
)
from minimal_kanban.storage.json_store import (  # noqa: E402
    DEFAULT_STATE,
    JsonStore,
    StateFileCorruptedError,
)
from scripts.clear_financial_history import (  # noqa: E402
    _cashbox_statistic_needs_reset as cashbox_statistic_needs_reset,
)
from scripts.clear_financial_history import (  # noqa: E402
    _write_state_file as write_financial_history_state_file,
)
from scripts.clear_financial_history import (
    build_financial_history_cleanup_result,
)
from scripts.clear_financial_history import (
    main as clear_financial_history_main,
)
from scripts.compact_audit_events import compact_state_file  # noqa: E402
from scripts.compact_audit_events import main as compact_audit_events_main  # noqa: E402
from scripts.compact_audit_events import (
    write_state_file as write_compact_audit_state_file,  # noqa: E402
)


class JsonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.storage.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_constructor_never_overwrites_state_created_while_waiting_for_lock(
        self,
    ) -> None:
        checked_missing = threading.Event()
        worker_errors: list[BaseException] = []
        original_exists = Path.exists
        business_state = deepcopy(DEFAULT_STATE)
        business_state["settings"]["constructor_race_marker"] = "preserve"

        def tracked_exists(path: Path) -> bool:
            exists = original_exists(path)
            if path == self.state_file and not exists and threading.current_thread() is worker:
                checked_missing.set()
            return exists

        def create_store() -> None:
            try:
                JsonStore(state_file=self.state_file, logger=self.logger)
            except BaseException as exc:  # pragma: no cover - asserted below
                worker_errors.append(exc)

        lock = ProcessFileLock(self.state_file.with_suffix(".lock"))
        with lock.acquire(), patch.object(Path, "exists", tracked_exists):
            worker = threading.Thread(target=create_store, daemon=True)
            worker.start()
            self.assertTrue(checked_missing.wait(timeout=2))
            self.state_file.write_text(
                json.dumps(business_state, ensure_ascii=False),
                encoding="utf-8",
            )

        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], worker_errors)
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual("preserve", persisted["settings"]["constructor_race_marker"])

    def test_card_notification_updated_at_falls_back_to_updated_at(self) -> None:
        card = Card.from_dict(
            {
                "id": "card-1",
                "title": "Legacy card",
                "created_at": "2026-04-02T10:00:00+00:00",
                "updated_at": "2026-04-02T10:30:00+00:00",
                "seen_by_users": {"ALICE": "2026-04-02T10:00:00+00:00"},
            }
        )

        self.assertEqual(card.notification_updated_at, "2026-04-02T10:30:00+00:00")
        self.assertTrue(card.has_unseen_update_for("ALICE"))
        self.assertEqual(
            card.to_storage_dict()["notification_updated_at"],
            "2026-04-02T10:30:00+00:00",
        )
        self.assertNotIn("notification_updated_at", card.to_dict())

    def _write_financial_history_state(self) -> dict:
        raw_state = {
            "schema_version": 7,
            "columns": [],
            "cards": [
                {
                    "id": "card-1",
                    "repair_order": {
                        "payments": [{"id": "payment-1", "cash_transaction_id": "ct-1"}],
                        "payment_history": [{"id": "payment-2", "cash_transaction_id": "ct-2"}],
                        "works": [
                            {
                                "name": "Диагностика",
                                "executor_id": "emp-1",
                                "executor_name": "Иван",
                                "salary_amount": "1500",
                            }
                        ],
                        "materials": [],
                    },
                }
            ],
            "cashboxes": [
                {
                    "id": "cashbox-1",
                    "statistics": {
                        "balance_minor": 1000,
                        "transactions_total": 2,
                        "income_total_minor": 1500,
                        "expense_total_minor": 500,
                    },
                }
            ],
            "cash_transactions": [
                {"id": "ct-1", "amount_minor": 1000},
                {"id": "ct-2", "amount_minor": 500},
            ],
            "events": [
                {"id": "event-1", "action": "cash_transaction_created"},
                {"id": "event-2", "action": "employee_salary_transaction_created"},
                {"id": "event-3", "action": "card_updated"},
            ],
            "stickies": [],
            "settings": {},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        return raw_state

    def test_rejects_broken_json_without_replacing_state_with_empty_board(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        trusted_bundle = store.read_bundle()
        self.state_file.write_text("{broken json", encoding="utf-8")
        previous_backup = self.state_file.with_suffix(".corrupted.json")
        previous_backup.write_text("previous corrupt backup", encoding="utf-8")

        with self.assertRaises(StateFileCorruptedError):
            store.read_bundle()
        with self.assertRaises(StateFileCorruptedError):
            store.read_bundle()
        with self.assertRaises(StateFileCorruptedError):
            JsonStore(state_file=self.state_file, logger=self.logger).read_bundle()
        with self.assertRaises(StateFileCorruptedError):
            store.write_bundle(**trusted_bundle)

        self.assertEqual(self.state_file.read_text(encoding="utf-8"), "{broken json")
        self.assertEqual(previous_backup.read_text(encoding="utf-8"), "previous corrupt backup")
        backups = sorted(self.state_file.parent.glob("state.corrupted*.json"))
        self.assertEqual(len(backups), 2)
        self.assertTrue(any(path.read_text(encoding="utf-8") == "{broken json" for path in backups))

    def test_rejects_nonstandard_json_constants_without_silent_repair(self) -> None:
        self.state_file.write_text('{"settings":{"board_scale":NaN}}', encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with self.assertRaises(StateFileCorruptedError):
            store.read_bundle()

        self.assertTrue(self.state_file.exists())
        backups = sorted(self.state_file.parent.glob("state.corrupted*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("NaN", backups[0].read_text(encoding="utf-8"))

    def test_rejects_non_object_state_json_without_attribute_error(self) -> None:
        self.state_file.write_text("[]", encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with self.assertRaises(StateFileCorruptedError):
            store.read_bundle()

        self.assertTrue(self.state_file.exists())
        backups = sorted(self.state_file.parent.glob("state.corrupted*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "[]")

    def test_rejects_oversized_state_file_without_reading_payload(self) -> None:
        self.state_file.write_text("x" * 16, encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with (
            patch("minimal_kanban.storage.json_store.JSON_STORE_STATE_MAX_BYTES", 8),
            patch.object(Path, "read_text", side_effect=AssertionError("must not read state")),
            self.assertRaises(StateFileCorruptedError),
        ):
            store.read_bundle()

        self.assertTrue(self.state_file.exists())
        backups = sorted(self.state_file.parent.glob("state.corrupted*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_size, 16)

    def test_rejects_deeply_nested_state_json_without_recursion_crash(self) -> None:
        deep_json = "[" * 5000 + "]" * 5000
        self.state_file.write_text(deep_json, encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with self.assertRaises(StateFileCorruptedError):
            store.read_bundle()

        backups = sorted(self.state_file.parent.glob("state.corrupted*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), deep_json)

    def test_read_bundle_reuses_cached_state_until_file_changes(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with patch.object(store, "_read_state", wraps=store._read_state) as read_state:
            first = store.read_bundle()
            second = store.read_bundle()

        self.assertIs(first, second)
        self.assertEqual(read_state.call_count, 1)

    def test_read_bundle_cache_reuses_written_bundle_after_write_bundle(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()

        store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=[
                *bundle["events"],
                AuditEvent(
                    id="cache-event",
                    timestamp=utc_now().isoformat(),
                    actor_name="system",
                    source="system",
                    action="test",
                    message="cache invalidated",
                ),
            ],
            settings=bundle["settings"],
        )

        with patch.object(store, "_read_state", wraps=store._read_state) as read_state:
            refreshed = store.read_bundle()

        self.assertEqual(read_state.call_count, 0)
        self.assertEqual(refreshed["events"][-1].message, "cache invalidated")

    def test_write_state_uses_compact_json_while_remaining_readable(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()

        store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        raw_state = self.state_file.read_text(encoding="utf-8")
        self.assertNotIn("\n  ", raw_state)
        self.assertEqual(json.loads(raw_state)["schema_version"], DEFAULT_STATE["schema_version"])
        self.assertEqual(store.read_bundle()["cards"], bundle["cards"])

    def test_write_state_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()
        fixed_tmp = self.state_file.with_suffix(".tmp")
        fixed_tmp.write_text("sentinel", encoding="utf-8")

        store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_write_state_rejects_payload_that_reader_would_ignore_as_oversized(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()
        before = self.state_file.read_text(encoding="utf-8")

        with (
            patch("minimal_kanban.storage.json_store.JSON_STORE_STATE_MAX_BYTES", 64),
            self.assertRaisesRegex(ValueError, "state file is too large"),
        ):
            store.write_bundle(
                columns=bundle["columns"],
                cards=bundle["cards"],
                clients=bundle["clients"],
                stickies=bundle["stickies"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                inventory_items=bundle["inventory_items"],
                inventory_movements=bundle["inventory_movements"],
                events=bundle["events"],
                settings={"padding": "x" * 256},
            )

        self.assertEqual(self.state_file.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.state_file.parent.glob(".state.json.*.tmp")), [])

    def test_v9_state_with_full_audit_details_reads_without_migration(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["schema_version"] = 9
        raw_state["events"] = [
            {
                "id": "legacy-description-event",
                "timestamp": utc_now().isoformat(),
                "actor_name": "system",
                "source": "system",
                "action": "description_changed",
                "message": "legacy full details",
                "card_id": "card-1",
                "details": {"before": "old", "after": "new"},
            }
        ]
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        bundle = store.read_bundle()
        stored_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertEqual(bundle["events"][0].details, {"before": "old", "after": "new"})
        self.assertEqual(stored_state["events"][0]["details"], {"before": "old", "after": "new"})

    def test_compact_audit_events_dry_run_does_not_mutate_state(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["events"] = [
            {
                "id": "event-1",
                "timestamp": "2026-05-22T00:00:00+00:00",
                "actor_name": "system",
                "source": "system",
                "action": "description_changed",
                "message": "description changed",
                "card_id": "card-1",
                "details": {"before": "old" * 300, "after": "new" * 300},
            }
        ]
        before_text = json.dumps(raw_state, ensure_ascii=False)
        self.state_file.write_text(before_text, encoding="utf-8")

        result = compact_state_file(self.state_file, apply=False)

        self.assertTrue(result.dry_run)
        self.assertEqual(result.events_compacted, 1)
        self.assertEqual(self.state_file.read_text(encoding="utf-8"), before_text)
        self.assertFalse((self.state_file.parent / "audit-archive").exists())

    def test_compact_audit_events_rejects_oversized_state_file(self) -> None:
        self.state_file.write_text("x" * 16, encoding="utf-8")

        with patch("scripts.compact_audit_events.COMPACT_STATE_MAX_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "compact audit events state file is too large"):
                compact_state_file(self.state_file, apply=False)

    def test_compact_audit_events_rejects_oversized_state_write_without_clobbering(
        self,
    ) -> None:
        original_state = {"schema_version": DEFAULT_STATE["schema_version"], "cards": []}
        self.state_file.write_text(json.dumps(original_state), encoding="utf-8")

        with patch("scripts.compact_audit_events.COMPACT_STATE_MAX_BYTES", 64):
            with self.assertRaisesRegex(ValueError, "compact audit events state file is too large"):
                write_compact_audit_state_file(
                    self.state_file,
                    {"schema_version": DEFAULT_STATE["schema_version"], "padding": "x" * 256},
                )

        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8")), original_state)
        self.assertEqual(list(self.state_file.parent.glob("*.compact.tmp")), [])

    def test_compact_audit_events_rejects_deeply_nested_state_file(self) -> None:
        self.state_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
            compact_state_file(self.state_file, apply=False)

    def test_compact_audit_events_apply_creates_backup_archive_and_keeps_event_count(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["events"] = [
            {
                "id": "event-1",
                "timestamp": "2026-05-22T00:00:00+00:00",
                "actor_name": "system",
                "source": "system",
                "action": "repair_order_updated",
                "message": "repair order changed",
                "card_id": "card-1",
                "details": {
                    "before": {"number": "1", "reason": "old", "works": [{"name": "diag"}]},
                    "after": {"number": "1", "reason": "new", "works": []},
                    "number": "1",
                    "status": "open",
                },
            }
        ]
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")

        result = compact_state_file(self.state_file, apply=True, backup=True)
        compacted_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        compacted_event = compacted_state["events"][0]
        archive_store = AuditArchiveStore(self.state_file.parent / "audit-archive")
        archived_details = archive_store.load_details(
            compacted_event["details"]["full_details_ref"],
            event_id="event-1",
        )

        self.assertFalse(result.dry_run)
        self.assertEqual(result.events_total, 1)
        self.assertEqual(result.events_compacted, 1)
        self.assertTrue(Path(result.backup_file).exists())
        self.assertEqual(len(compacted_state["events"]), 1)
        self.assertTrue(compacted_event["details"]["full_details_archived"])
        self.assertNotIn("before", compacted_event["details"])
        self.assertNotIn("after", compacted_event["details"])
        self.assertEqual(archived_details, raw_state["events"][0]["details"])

    def test_compact_audit_events_apply_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["events"] = [
            {
                "id": "event-1",
                "timestamp": "2026-05-22T00:00:00+00:00",
                "actor_name": "system",
                "source": "system",
                "action": "description_changed",
                "message": "description changed",
                "card_id": "card-1",
                "details": {"before": "old" * 300, "after": "new" * 300},
            }
        ]
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        fixed_tmp = self.state_file.with_suffix(".compact.tmp")
        fixed_tmp.write_text("sentinel", encoding="utf-8")

        result = compact_state_file(self.state_file, apply=True)

        self.assertEqual(result.events_compacted, 1)
        self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_compact_audit_events_backup_does_not_overwrite_existing_backup(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["events"] = [
            {
                "id": "event-1",
                "timestamp": "2026-05-22T00:00:00+00:00",
                "actor_name": "system",
                "source": "system",
                "action": "description_changed",
                "message": "description changed",
                "card_id": "card-1",
                "details": {"before": "old" * 300, "after": "new" * 300},
            }
        ]
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        existing_backup = self.state_file.with_name("state.json.backup-20260522-000000.json")
        existing_backup.write_text("previous backup", encoding="utf-8")

        with patch("scripts.compact_audit_events.time.strftime", return_value="20260522-000000"):
            result = compact_state_file(self.state_file, apply=True, backup=True)

        self.assertEqual(existing_backup.read_text(encoding="utf-8"), "previous backup")
        backup_file = self.state_file.with_name("state.json.backup-20260522-000000-002.json")
        self.assertEqual(Path(result.backup_file), backup_file)
        self.assertEqual(json.loads(backup_file.read_text(encoding="utf-8")), raw_state)

    def test_audit_archive_sanitizes_non_finite_details_and_skips_corrupt_lines(self) -> None:
        archive_store = AuditArchiveStore(self.state_file.parent / "audit-archive")

        write = archive_store.archive_details(
            event_id="event-1",
            action="description_changed",
            card_id="card-1",
            timestamp="2026-05-22T00:00:00+00:00",
            details={"score": float("inf"), "items": [float("nan")]},
        )

        archive_file = self.state_file.parent / "audit-archive" / "2026-05.jsonl"
        archive_text = archive_file.read_text(encoding="utf-8")
        self.assertNotIn("Infinity", archive_text)
        self.assertNotIn("NaN", archive_text)
        loaded = archive_store.load_details(write.ref, event_id="event-1")
        self.assertEqual(loaded, {"score": None, "items": [None]})

        archive_file.write_text(
            '{"event_id":"bad","details":{"score":NaN}}\n' + archive_text,
            encoding="utf-8",
        )
        self.assertIsNone(archive_store.load_details("2026-05.jsonl#bad", event_id="bad"))
        self.assertEqual(archive_store.load_details(write.ref, event_id="event-1"), loaded)

    def test_audit_archive_rejects_records_that_loader_would_skip_as_oversized(self) -> None:
        archive_store = AuditArchiveStore(self.state_file.parent / "audit-archive")

        with patch("minimal_kanban.storage.audit_archive.AUDIT_ARCHIVE_LINE_MAX_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "line size limit"):
                archive_store.archive_details(
                    event_id="event-oversized",
                    action="description_changed",
                    card_id="card-1",
                    timestamp="2026-05-22T00:00:00+00:00",
                    details={"payload": "x" * 256},
                )

        self.assertFalse((self.state_file.parent / "audit-archive" / "2026-05.jsonl").exists())

    def test_compact_audit_event_details_returns_json_safe_preview_values(self) -> None:
        cyclic_list: list[object] = []
        cyclic_list.append(cyclic_list)
        cyclic_dict: dict[str, object] = {}
        cyclic_dict["self"] = cyclic_dict

        compact = compact_audit_event_details(
            action="repair_order_updated",
            details={
                "before": cyclic_list,
                "after": {"number": "RO-1", "status": cyclic_dict},
                "score": float("inf"),
                "metadata": cyclic_dict,
            },
            archive_ref="2026-05.jsonl#event-1",
        )

        encoded = json.dumps(compact, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["score"], None)
        self.assertEqual(decoded["before_preview"]["type"], "list")
        self.assertEqual(decoded["before_preview"]["items"], 1)
        self.assertIn("self", decoded["after_preview"]["status"])
        self.assertIn("self", decoded["metadata"])

    def test_audit_archive_skips_oversized_jsonl_lines(self) -> None:
        archive_store = AuditArchiveStore(self.state_file.parent / "audit-archive")
        archive_dir = self.state_file.parent / "audit-archive"
        archive_dir.mkdir(parents=True)
        archive_file = archive_dir / "2026-05.jsonl"
        valid_line = json.dumps(
            {"event_id": "event-1", "details": {"ok": True}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        archive_file.write_bytes(
            b'{"event_id":"bad","details":{"payload":"'
            + (b"x" * 256)
            + b'"}}\n'
            + valid_line.encode("utf-8")
            + b"\n"
        )

        with patch("minimal_kanban.storage.audit_archive.AUDIT_ARCHIVE_LINE_MAX_BYTES", 128):
            self.assertIsNone(archive_store.load_details("2026-05.jsonl#bad", event_id="bad"))
            self.assertEqual(
                archive_store.load_details("2026-05.jsonl#event-1", event_id="event-1"),
                {"ok": True},
            )

    def test_audit_archive_loads_recent_details_from_bounded_tail_window(self) -> None:
        archive_store = AuditArchiveStore(self.state_file.parent / "audit-archive")
        archive_dir = self.state_file.parent / "audit-archive"
        archive_dir.mkdir(parents=True)
        archive_file = archive_dir / "2026-05.jsonl"
        old_lines = [
            json.dumps(
                {"event_id": f"old-{index}", "details": {"value": "x" * 40}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for index in range(12)
        ]
        fresh_line = json.dumps(
            {"event_id": "fresh", "details": {"ok": True}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        archive_file.write_text("\n".join([*old_lines, fresh_line]) + "\n", encoding="utf-8")

        with patch("minimal_kanban.storage.audit_archive.AUDIT_ARCHIVE_SCAN_MAX_BYTES", 128):
            self.assertEqual(
                archive_store.load_details("2026-05.jsonl#fresh", event_id="fresh"),
                {"ok": True},
            )

    def test_compact_audit_events_cli_rejects_nonstandard_json_constants(self) -> None:
        self.state_file.write_text('{"events":[{"details":{"before":NaN}}]}', encoding="utf-8")
        output = StringIO()

        with redirect_stdout(output):
            exit_code = compact_audit_events_main(
                ["--dry-run", "--json", "--state-file", str(self.state_file)]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("Unsupported JSON constant", payload["error"])

    def test_compact_audit_events_cli_reports_invalid_json_without_traceback(self) -> None:
        self.state_file.write_text("{broken", encoding="utf-8")
        output = StringIO()

        with redirect_stdout(output):
            exit_code = compact_audit_events_main(
                ["--dry-run", "--json", "--state-file", str(self.state_file)]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("Expecting property name", payload["error"])

    def test_read_bundle_cache_detects_external_file_changes(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        first = store.read_bundle()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["events"].append(
            {
                "id": "external-event",
                "action": "external",
                "message": "external change",
                "timestamp": utc_now().isoformat(),
            }
        )
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        with patch.object(store, "_read_state", wraps=store._read_state) as read_state:
            second = store.read_bundle()

        self.assertIsNot(first, second)
        self.assertEqual(read_state.call_count, 1)
        self.assertEqual(second["events"][-1].message, "external change")

    def test_repairs_invalid_card_state_and_migrates_legacy_fields(self) -> None:
        raw_state = {
            "schema_version": 2,
            "columns": [
                {"id": "inbox", "label": "Входящие"},
                {"id": "column_1", "label": "Блокеры"},
            ],
            "cards": [
                {
                    "title": "",
                    "description": "x" * 6000,
                    "priority": "urgent",
                    "column": "trash",
                    "archived": "false",
                    "elapsed_seconds": -25,
                    "timer_started_at": "not-a-date",
                    "indicator": "blue",
                }
            ],
            "settings": {"has_seen_onboarding": True},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        cards = store.read_cards()
        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        repaired_card = repaired_state["cards"][0]

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].title, "Без названия")
        self.assertEqual(cards[0].column, "inbox")
        self.assertGreater(cards[0].deadline_total_seconds, 0)
        self.assertIn("deadline_timestamp", repaired_card)
        self.assertIn("deadline_total_seconds", repaired_card)
        self.assertNotIn("priority", repaired_card)
        self.assertNotIn("indicator", repaired_card)
        self.assertNotIn("elapsed_seconds", repaired_card)
        self.assertNotIn("timer_started_at", repaired_card)

    def test_repairs_invalid_columns_and_missing_card_column(self) -> None:
        raw_state = {
            "schema_version": 3,
            "columns": [
                {"id": "inbox", "label": "Входящие"},
                {"id": "column_1", "label": "Блокеры"},
                {"id": "column_1", "label": "Дубль"},
                {"id": "", "label": "Пусто"},
            ],
            "cards": [
                {
                    "title": "Карточка",
                    "description": "",
                    "column": "missing_column",
                    "archived": False,
                    "deadline_timestamp": "2026-03-24T12:00:00+00:00",
                    "deadline_total_seconds": 3600,
                }
            ],
            "settings": {"has_seen_onboarding": False},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        columns = store.read_columns()
        cards = store.read_cards()
        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertTrue(any(column.id == "column_1" for column in columns))
        self.assertEqual(sum(1 for column in columns if column.id == "column_1"), 1)
        self.assertEqual(cards[0].column, "inbox")
        self.assertEqual(repaired_state["cards"][0]["column"], "inbox")

    def test_state_repair_skips_records_that_raise_overflow_error(self) -> None:
        raw_state = {
            "schema_version": 3,
            "columns": [{"id": "bad", "label": "Bad", "position": 0}],
            "cards": [],
            "settings": {"has_seen_onboarding": False},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        with patch(
            "minimal_kanban.storage.json_store.Column.from_dict",
            side_effect=OverflowError("bad numeric value"),
        ):
            columns = store.read_columns()

        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertTrue(columns)
        self.assertEqual(repaired_state["schema_version"], DEFAULT_STATE["schema_version"])
        self.assertTrue(repaired_state["columns"])

    def test_legacy_card_without_vehicle_profile_still_loads(self) -> None:
        raw_state = {
            "schema_version": 5,
            "columns": [
                {"id": "inbox", "label": "Приёмка"},
                {"id": "diag", "label": "Диагностика"},
            ],
            "cards": [
                {
                    "id": "legacy-card",
                    "short_id": "C-LEGACY1",
                    "vehicle": "SUZUKI SWIFT",
                    "title": "Стук спереди",
                    "description": "Старая карточка без вложенной техкарты",
                    "column": "inbox",
                    "tags": ["СТАРОЕ"],
                    "deadline_timestamp": "2026-04-02T12:00:00+00:00",
                    "deadline_total_seconds": 7200,
                    "created_at": "2026-04-02T10:00:00+00:00",
                    "updated_at": "2026-04-02T10:30:00+00:00",
                    "archived": False,
                    "attachments": [],
                }
            ],
            "events": [],
            "stickies": [],
            "settings": {"has_seen_onboarding": True, "board_scale": 1.0},
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")

        store = JsonStore(state_file=self.state_file, logger=self.logger)
        cards = store.read_cards()
        repaired_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].vehicle, "SUZUKI SWIFT")
        self.assertTrue(hasattr(cards[0], "vehicle_profile"))
        self.assertEqual(cards[0].vehicle_profile.make_display, "")
        self.assertIn("vehicle_profile", repaired_state["cards"][0])
        self.assertEqual(repaired_state["cards"][0]["vehicle_profile"]["make_display"], "")
        self.assertEqual(repaired_state["cards"][0]["vehicle_profile"]["model_display"], "")

    def test_sanitize_financial_history_state_clears_cash_and_payroll_history(self) -> None:
        raw_state = {
            "schema_version": 7,
            "columns": [],
            "cards": [
                {
                    "id": "card-1",
                    "repair_order": {
                        "payments": [
                            {
                                "id": "payment-1",
                                "amount": "3000",
                                "paid_at": "12.04.2026 16:43",
                                "cashbox_id": "cashbox-1",
                                "cashbox_name": "Наличный",
                                "cash_transaction_id": "ct-1",
                            }
                        ],
                        "payment_history": [
                            {
                                "id": "payment-2",
                                "amount": "1500",
                                "cash_transaction_id": "ct-2",
                            }
                        ],
                        "works": [
                            {
                                "name": "Диагностика",
                                "executor_id": "emp-1",
                                "executor_name": "Иван Мастер",
                                "work_quantity_snapshot": "1",
                                "work_price_snapshot": "1500",
                                "work_total_snapshot": "1500",
                                "salary_mode_snapshot": "percent_only",
                                "base_salary_snapshot": "0",
                                "work_percent_snapshot": "100",
                                "salary_amount": "1500",
                                "salary_accrued_at": "12.04.2026 16:44",
                            }
                        ],
                    },
                }
            ],
            "cashboxes": [
                {
                    "id": "cashbox-1",
                    "name": "Наличный",
                    "statistics": {
                        "balance_minor": 123456,
                        "transactions_total": 4,
                        "income_total_minor": 99999,
                        "expense_total_minor": 54321,
                    },
                }
            ],
            "cash_transactions": [{"id": "ct-1"}, {"id": "ct-2"}],
            "events": [
                {
                    "id": "event-1",
                    "action": "cashbox_created",
                },
                {
                    "id": "event-2",
                    "action": "cash_transaction_created",
                },
                {
                    "id": "event-3",
                    "action": "employee_salary_transaction_created",
                },
            ],
            "settings": {},
        }

        sanitized = sanitize_financial_history_state(raw_state)

        self.assertEqual(sanitized["cash_transactions"], [])
        self.assertEqual(sanitized["cards"][0]["repair_order"]["works"][0]["executor_id"], "")
        self.assertEqual(sanitized["cards"][0]["repair_order"]["works"][0]["executor_name"], "")
        self.assertEqual(
            sanitized["cards"][0]["repair_order"]["works"][0]["work_quantity_snapshot"], ""
        )
        self.assertEqual(
            sanitized["cards"][0]["repair_order"]["works"][0]["work_price_snapshot"], ""
        )
        self.assertEqual(
            sanitized["cards"][0]["repair_order"]["works"][0]["work_total_snapshot"], ""
        )
        self.assertEqual(sanitized["cards"][0]["repair_order"]["works"][0]["salary_amount"], "")
        self.assertEqual(sanitized["cards"][0]["repair_order"]["works"][0]["salary_accrued_at"], "")
        self.assertEqual(
            sanitized["cards"][0]["repair_order"]["payments"][0]["cash_transaction_id"], ""
        )
        self.assertEqual(
            sanitized["cards"][0]["repair_order"]["payment_history"][0]["cash_transaction_id"],
            "",
        )
        self.assertEqual(sanitized["cashboxes"][0]["statistics"]["balance_minor"], 0)
        self.assertEqual(sanitized["cashboxes"][0]["statistics"]["transactions_total"], 0)
        self.assertEqual(sanitized["cashboxes"][0]["statistics"]["income_total_minor"], 0)
        self.assertEqual(sanitized["cashboxes"][0]["statistics"]["expense_total_minor"], 0)
        self.assertEqual(len(sanitized["events"]), 1)
        self.assertEqual(sanitized["events"][0]["action"], "cashbox_created")

    def test_clear_financial_history_dry_run_reports_without_writing(self) -> None:
        raw_state = self._write_financial_history_state()
        before = self.state_file.read_text(encoding="utf-8")

        result = build_financial_history_cleanup_result(self.state_file)

        self.assertTrue(result["dry_run"])
        self.assertFalse(result["applied"])
        self.assertEqual({}, result["backup"])
        self.assertTrue(result["summary"]["changed"])
        self.assertEqual(2, result["summary"]["cash_transactions_removed"])
        self.assertEqual(2, result["summary"]["financial_events_removed"])
        self.assertEqual(2, result["summary"]["repair_order_payment_links_cleared"])
        self.assertEqual(3, result["summary"]["payroll_fields_cleared"])
        self.assertEqual(1, result["summary"]["cashbox_statistics_reset"])
        self.assertEqual(before, self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(raw_state, json.loads(before))

    def test_clear_financial_history_apply_requires_backup(self) -> None:
        self._write_financial_history_state()

        with self.assertRaises(ValueError):
            build_financial_history_cleanup_result(self.state_file, apply=True)

    def test_clear_financial_history_rejects_deeply_nested_state_file(self) -> None:
        self.state_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
            build_financial_history_cleanup_result(self.state_file)

    def test_clear_financial_history_summary_handles_malformed_cashbox_statistics(self) -> None:
        raw_state = self._write_financial_history_state()
        raw_state["cashboxes"][0]["statistics"] = {
            "balance_minor": "broken",
            "transactions_total": "",
            "income_total_minor": None,
            "expense_total_minor": "0",
        }
        self.state_file.write_text(json.dumps(raw_state, ensure_ascii=False), encoding="utf-8")

        result = build_financial_history_cleanup_result(self.state_file)

        self.assertTrue(result["summary"]["changed"])
        self.assertEqual(1, result["summary"]["cashbox_statistics_reset"])

    def test_clear_financial_history_cashbox_statistic_flag_handles_non_finite(self) -> None:
        self.assertTrue(cashbox_statistic_needs_reset(float("inf")))
        self.assertTrue(cashbox_statistic_needs_reset(float("nan")))
        self.assertTrue(cashbox_statistic_needs_reset(1e308))
        self.assertFalse(cashbox_statistic_needs_reset(0))
        self.assertFalse(cashbox_statistic_needs_reset(""))

    def test_clear_financial_history_apply_writes_backup_and_sanitized_state(self) -> None:
        raw_state = self._write_financial_history_state()

        result = build_financial_history_cleanup_result(self.state_file, apply=True, backup=True)

        self.assertFalse(result["dry_run"])
        self.assertTrue(result["applied"])
        backup_file = Path(result["backup"]["path"])
        self.assertTrue(backup_file.exists())
        self.assertEqual(raw_state, json.loads(backup_file.read_text(encoding="utf-8")))
        sanitized = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(sanitize_financial_history_state(raw_state), sanitized)
        self.assertEqual([], sanitized["cash_transactions"])
        self.assertEqual(["card_updated"], [event["action"] for event in sanitized["events"]])

    def test_clear_financial_history_apply_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
        self._write_financial_history_state()
        fixed_tmp = self.state_file.with_suffix(".financial-history.tmp")
        fixed_tmp.write_text("sentinel", encoding="utf-8")

        result = build_financial_history_cleanup_result(self.state_file, apply=True, backup=True)

        self.assertTrue(result["applied"])
        self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_clear_financial_history_rejects_oversized_state_write_without_clobbering(
        self,
    ) -> None:
        original_state = self._write_financial_history_state()

        with patch("scripts.clear_financial_history.STATE_FILE_MAX_BYTES", 64):
            with self.assertRaisesRegex(ValueError, "financial history state file is too large"):
                write_financial_history_state_file(self.state_file, {"padding": "x" * 256})

        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8")), original_state)
        self.assertEqual(list(self.state_file.parent.glob("*.financial-history.tmp")), [])

    def test_clear_financial_history_cli_rejects_nonstandard_json_constants(self) -> None:
        self.state_file.write_text(
            '{"cashboxes":[{"statistics":{"balance_minor":NaN}}]}', encoding="utf-8"
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = clear_financial_history_main(
                ["--dry-run", "--format", "json", "--state-file", str(self.state_file)]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("Unsupported JSON constant", payload["error"])

    def test_clear_financial_history_cli_rejects_oversized_state_file(self) -> None:
        self.state_file.write_text("x" * 16, encoding="utf-8")
        output = StringIO()

        with patch("scripts.clear_financial_history.STATE_FILE_MAX_BYTES", 8):
            with redirect_stdout(output):
                exit_code = clear_financial_history_main(
                    ["--dry-run", "--format", "json", "--state-file", str(self.state_file)]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("financial history state file is too large", payload["error"])

    def test_clear_financial_history_cli_requires_explicit_mode(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                clear_financial_history_main([])

        self.assertEqual(2, error.exception.code)

    def test_write_bundle_reads_state_once_when_settings_and_stickies_are_missing(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()

        with patch.object(store, "_read_state", wraps=store._read_state) as read_state:
            store.write_bundle(
                columns=bundle["columns"],
                cards=bundle["cards"],
                events=[
                    AuditEvent(
                        id="event-1",
                        timestamp="2026-04-04T12:00:00+00:00",
                        actor_name="ADMIN",
                        source="ui",
                        action="card_created",
                        message="Создал карточку.",
                        card_id="card-1",
                        details={},
                    )
                ],
            )

        self.assertEqual(read_state.call_count, 1)

    def test_write_bundle_skips_non_domain_payload_items_and_sanitizes_json_values(self) -> None:
        class UnsafeValue:
            def __str__(self) -> str:
                return "unsafe-value"

        store = JsonStore(state_file=self.state_file, logger=self.logger)
        bundle = store.read_bundle()
        unsafe_event = AuditEvent(
            id="event-unsafe",
            timestamp=utc_now().isoformat(),
            actor_name="system",
            source="system",
            action="unsafe",
            message="unsafe details",
            details={
                "object": UnsafeValue(),
                "not_finite": float("inf"),
                "nested": {"value": UnsafeValue()},
            },
        )

        store.write_bundle(
            columns=[object(), *bundle["columns"]],
            cards=[object()],
            clients=[object()],
            stickies=[object()],
            cashboxes=[object()],
            cash_transactions=[object()],
            inventory_items=[object()],
            inventory_movements=[object()],
            events=[object(), unsafe_event],
            settings={
                "custom_object": UnsafeValue(),
                "not_finite": float("nan"),
                "ai_board_control": ["bad"],
            },
        )

        stored_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertEqual(
            [column["id"] for column in stored_state["columns"]],
            [column.id for column in bundle["columns"]],
        )
        self.assertEqual([], stored_state["cards"])
        self.assertEqual([], stored_state["clients"])
        self.assertEqual([], stored_state["stickies"])
        self.assertEqual([], stored_state["cashboxes"])
        self.assertEqual([], stored_state["cash_transactions"])
        self.assertEqual([], stored_state["inventory_items"])
        self.assertEqual([], stored_state["inventory_movements"])
        self.assertEqual("unsafe-value", stored_state["events"][0]["details"]["object"])
        self.assertEqual(0.0, stored_state["events"][0]["details"]["not_finite"])
        self.assertEqual("unsafe-value", stored_state["events"][0]["details"]["nested"]["value"])
        self.assertEqual("unsafe-value", stored_state["settings"]["custom_object"])
        self.assertEqual(0.0, stored_state["settings"]["not_finite"])
        self.assertEqual(
            DEFAULT_STATE["settings"]["ai_board_control"],
            stored_state["settings"]["ai_board_control"],
        )

    def test_set_setting_sanitizes_blank_keys_and_non_json_values(self) -> None:
        class UnsafeValue:
            def __str__(self) -> str:
                return "unsafe-setting"

        store = JsonStore(state_file=self.state_file, logger=self.logger)

        store.set_setting("   ", "ignored")
        store.set_setting(
            "custom",
            {
                "object": UnsafeValue(),
                "not_finite": float("-inf"),
                "items": [UnsafeValue()],
            },
        )

        stored_settings = json.loads(self.state_file.read_text(encoding="utf-8"))["settings"]

        self.assertNotIn("", stored_settings)
        self.assertNotIn("   ", stored_settings)
        self.assertEqual("unsafe-setting", stored_settings["custom"]["object"])
        self.assertEqual(0.0, stored_settings["custom"]["not_finite"])
        self.assertEqual(["unsafe-setting"], stored_settings["custom"]["items"])


if __name__ == "__main__":
    unittest.main()
