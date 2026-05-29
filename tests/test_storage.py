from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minimal_kanban.models import AuditEvent  # noqa: E402
from minimal_kanban.storage.audit_archive import AuditArchiveStore  # noqa: E402
from minimal_kanban.storage.financial_history_cleanup import (  # noqa: E402
    sanitize_financial_history_state,
)
from minimal_kanban.storage.json_store import DEFAULT_STATE, JsonStore  # noqa: E402
from scripts.compact_audit_events import compact_state_file  # noqa: E402


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

    def test_recovers_from_broken_json(self) -> None:
        self.state_file.write_text("{broken json", encoding="utf-8")
        store = JsonStore(state_file=self.state_file, logger=self.logger)

        cards = store.read_cards()

        self.assertEqual(cards, [])
        self.assertTrue(self.state_file.exists())
        self.assertTrue(self.state_file.with_suffix(".corrupted.json").exists())

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
                    timestamp="2026-04-28T00:00:00+00:00",
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

    def test_v9_state_with_full_audit_details_reads_without_migration(self) -> None:
        raw_state = deepcopy(DEFAULT_STATE)
        raw_state["schema_version"] = 9
        raw_state["events"] = [
            {
                "id": "legacy-description-event",
                "timestamp": "2026-05-22T00:00:00+00:00",
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

    def test_read_bundle_cache_detects_external_file_changes(self) -> None:
        store = JsonStore(state_file=self.state_file, logger=self.logger)
        first = store.read_bundle()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["events"].append(
            {
                "id": "external-event",
                "action": "external",
                "message": "external change",
                "timestamp": "2026-04-28T00:00:00+00:00",
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


if __name__ == "__main__":
    unittest.main()
