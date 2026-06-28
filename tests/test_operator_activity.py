from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import utc_now  # noqa: E402
from minimal_kanban.operator_activity import OperatorActivityService  # noqa: E402


class OperatorActivityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.activity_dir = Path(self.temp_dir.name) / "operator-activity"
        self.logger = logging.getLogger(f"test.operator_activity.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = OperatorActivityService(activity_dir=self.activity_dir, logger=self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_records_lists_filters_and_loads_details(self) -> None:
        first = self.service.record_activity(
            {
                "timestamp": "2026-05-23T10:41:00+07:00",
                "username": "admin",
                "module": "cashbox",
                "action": "cash_transaction_created",
                "action_label": "Расход",
                "object_type": "cashbox",
                "object_id": "cashbox-main",
                "object_label": "Основная касса",
                "summary": "Покупка расходников; комментарий указан",
                "amount": "-1250",
                "source": "ui",
                "details": {"cashbox_id": "cashbox-main", "note": "Покупка расходников"},
            }
        )["activity"]
        self.service.record_activity(
            {
                "timestamp": "2026-05-23T11:24:00+07:00",
                "username": "mekh",
                "module": "card",
                "action": "card_opened",
                "action_label": "Открыл карточку",
                "object_type": "card",
                "object_id": "card-bmw",
                "object_label": "BMW X5 / диагностика N63",
                "summary": "Просмотр без изменения данных",
                "source": "ui",
            }
        )

        listed = self.service.list_activity({"limit": 10})
        self.assertEqual([row["username"] for row in listed["activities"]], ["MEKH", "ADMIN"])
        self.assertEqual(listed["meta"]["total"], 2)

        admin_cashbox = self.service.list_activity(
            {"username": "admin", "module": "cashbox", "query": "расходников"}
        )
        self.assertEqual(admin_cashbox["meta"]["total"], 1)
        self.assertEqual(admin_cashbox["activities"][0]["id"], first["id"])
        self.assertNotIn("details", admin_cashbox["activities"][0])

        details = self.service.get_activity_details({"activity_id": first["id"]})
        self.assertEqual(details["activity"]["id"], first["id"])
        self.assertEqual(details["details"]["note"], "Покупка расходников")

    def test_record_activity_sanitizes_nonfinite_details(self) -> None:
        recorded = self.service.record_activity(
            {
                "timestamp": "2026-05-23T10:41:00+07:00",
                "username": "admin",
                "module": "card",
                "action": "card_opened",
                "summary": "Просмотр",
                "details": {"score": float("nan"), "items": [float("inf")]},
            }
        )["activity"]

        raw_details = (self.activity_dir / "details" / "2026-05.jsonl").read_text(encoding="utf-8")
        loaded = self.service.get_activity_details({"activity_id": recorded["id"]})

        self.assertNotIn("NaN", raw_details)
        self.assertNotIn("Infinity", raw_details)
        self.assertEqual(loaded["details"], {"score": None, "items": [None]})

    def test_record_activity_redacts_sensitive_detail_values(self) -> None:
        recorded = self.service.record_activity(
            {
                "timestamp": "2026-05-23T10:41:00+07:00",
                "username": "admin",
                "module": "auth",
                "action": "login",
                "summary": "Вход",
                "details": {
                    "password": "plain-secret",
                    "nested": {
                        "Authorization": "Bearer token-secret",
                        "client_secret": "oauth-secret",
                    },
                    "card_id": "card-1",
                },
            }
        )["activity"]

        raw_details = (self.activity_dir / "details" / "2026-05.jsonl").read_text(encoding="utf-8")
        loaded = self.service.get_activity_details({"activity_id": recorded["id"]})

        self.assertNotIn("plain-secret", raw_details)
        self.assertNotIn("token-secret", raw_details)
        self.assertNotIn("oauth-secret", raw_details)
        self.assertEqual(loaded["details"]["password"], "<redacted>")
        self.assertEqual(loaded["details"]["nested"]["Authorization"], "<redacted>")
        self.assertEqual(loaded["details"]["nested"]["client_secret"], "<redacted>")
        self.assertEqual(loaded["details"]["card_id"], "card-1")

    def test_record_activity_handles_self_referential_details(self) -> None:
        details: dict[str, object] = {"kind": "cyclic"}
        details["self"] = details
        recorded = self.service.record_activity(
            {
                "timestamp": "2026-05-23T10:41:00+07:00",
                "username": "admin",
                "module": "card",
                "action": "card_opened",
                "summary": "Просмотр",
                "details": details,
            }
        )["activity"]

        loaded = self.service.get_activity_details({"activity_id": recorded["id"]})
        node = loaded["details"]
        for _ in range(7):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_bad_jsonl_rows_with_nonstandard_constants_are_skipped(self) -> None:
        current_file = self.activity_dir / "current" / "2026-05.jsonl"
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(
            '{"id":"bad","timestamp":"2026-05-23T10:00:00+07:00","username":"ADMIN","module":"card","action":"card_opened","summary":NaN}\n'
            + json.dumps(
                {
                    "id": "good",
                    "timestamp": "2026-05-23T11:00:00+07:00",
                    "username": "ADMIN",
                    "module": "card",
                    "action": "card_opened",
                    "summary": "ok",
                    "source": "ui",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        listed = self.service.list_activity({"limit": 10})

        self.assertEqual([row["id"] for row in listed["activities"]], ["good"])

    def test_oversized_jsonl_reads_bounded_tail(self) -> None:
        current_file = self.activity_dir / "current" / "2026-05.jsonl"
        current_file.parent.mkdir(parents=True, exist_ok=True)
        old_line = json.dumps(
            {
                "id": "old",
                "timestamp": "2026-05-23T10:00:00+07:00",
                "username": "ADMIN",
                "module": "card",
                "action": "card_opened",
            },
            ensure_ascii=False,
        )
        fresh_line = json.dumps(
            {
                "id": "fresh",
                "timestamp": "2026-05-23T11:00:00+07:00",
                "username": "ADMIN",
                "module": "card",
                "action": "card_opened",
                "source": "ui",
            },
            ensure_ascii=False,
        )
        current_file.write_text((old_line + "\n") * 20 + fresh_line + "\n", encoding="utf-8")

        with patch("minimal_kanban.operator_activity.OPERATOR_ACTIVITY_JSONL_TAIL_MAX_BYTES", 512):
            listed = self.service.list_activity({"limit": 10})

        self.assertGreaterEqual(len(listed["activities"]), 1)
        self.assertEqual(listed["activities"][0]["id"], "fresh")

    def test_jsonl_reader_skips_oversized_lines(self) -> None:
        current_file = self.activity_dir / "current" / "2026-05.jsonl"
        current_file.parent.mkdir(parents=True, exist_ok=True)
        oversized_line = json.dumps(
            {
                "id": "oversized",
                "timestamp": "2026-05-23T10:00:00+07:00",
                "username": "ADMIN",
                "blob": "x" * 512,
            },
            ensure_ascii=False,
        )
        good_line = json.dumps(
            {
                "id": "good",
                "timestamp": "2026-05-23T11:00:00+07:00",
                "username": "ADMIN",
                "module": "card",
                "action": "card_opened",
            },
            ensure_ascii=False,
        )
        current_file.write_text(oversized_line + "\n" + good_line + "\n", encoding="utf-8")

        with patch("minimal_kanban.operator_activity.OPERATOR_ACTIVITY_JSONL_LINE_MAX_BYTES", 256):
            listed = self.service.list_activity({"limit": 10})

        self.assertEqual([row["id"] for row in listed["activities"]], ["good"])

    def test_oversized_details_record_is_not_written_as_unreadable_jsonl(self) -> None:
        with patch("minimal_kanban.operator_activity.OPERATOR_ACTIVITY_JSONL_LINE_MAX_BYTES", 1024):
            recorded = self.service.record_activity(
                {
                    "timestamp": "2026-05-23T10:00:00+07:00",
                    "username": "admin",
                    "module": "card",
                    "action": "card_opened",
                    "details": {"blob": "x" * 2048},
                }
            )["activity"]

        self.assertEqual(recorded["details_ref"], "")
        listed = self.service.list_activity({"limit": 10})
        self.assertEqual([row["id"] for row in listed["activities"]], [recorded["id"]])
        loaded = self.service.get_activity_details({"activity_id": recorded["id"]})
        self.assertEqual(loaded["details"], {})

    def test_export_returns_readable_text(self) -> None:
        self.service.record_activity(
            {
                "timestamp": "2026-05-23T10:58:00+07:00",
                "username": "admin",
                "module": "repair_order",
                "action": "repair_order_updated",
                "action_label": "Обновил работы",
                "object_type": "repair_order",
                "object_id": "order-124",
                "object_label": "ЗН 000124 • Toyota Camry",
                "summary": "Добавлена работа: замена масла; исполнитель: Иван",
                "amount": "4800",
                "source": "ui",
            }
        )

        exported = self.service.export_activity({"username": "ADMIN"})

        self.assertEqual(exported["file_name"], "operator-activity-admin.txt")
        self.assertIn("ЖУРНАЛ ДЕЙСТВИЙ ОПЕРАТОРОВ", exported["text"])
        self.assertIn("ADMIN | Заказ-наряд | Обновил работы", exported["text"])
        self.assertIn("ЗН 000124", exported["text"])

    def test_compact_activity_dry_run_and_apply_updates_aggregates(self) -> None:
        old_timestamp = (utc_now() - timedelta(days=120)).isoformat()
        recent_timestamp = (utc_now() - timedelta(days=2)).isoformat()
        old = self.service.record_activity(
            {
                "timestamp": old_timestamp,
                "username": "admin",
                "module": "card",
                "action": "card_opened",
                "action_label": "Открыл карточку",
                "object_type": "card",
                "object_id": "old-card",
                "object_label": "Старая карточка",
                "summary": "Старый просмотр",
                "source": "ui",
                "details": {"card_id": "old-card", "snapshot": "old"},
            }
        )["activity"]
        recent = self.service.record_activity(
            {
                "timestamp": recent_timestamp,
                "username": "mekh",
                "module": "repair_order",
                "action": "repair_order_updated",
                "action_label": "Обновил работы",
                "object_type": "repair_order",
                "object_id": "recent-order",
                "object_label": "Свежий ЗН",
                "summary": "Свежая строка",
                "source": "ui",
                "details": {"repair_order_id": "recent-order", "snapshot": "recent"},
            }
        )["activity"]

        dry_run = self.service.compact_activity({"dry_run": True, "retention_days": 90})
        self.assertTrue(dry_run["dry_run"])
        self.assertEqual(dry_run["eligible_rows"], 1)
        self.assertEqual(dry_run["removed_rows"], 0)
        self.assertEqual(self.service.list_activity({"limit": 10})["meta"]["total"], 2)

        applied = self.service.compact_activity(
            {"apply": True, "backup": True, "retention_days": 90}
        )
        self.assertFalse(applied["dry_run"])
        self.assertEqual(applied["eligible_rows"], 1)
        self.assertEqual(applied["removed_rows"], 1)
        self.assertTrue(applied["backup_dir"])

        listed = self.service.list_activity({"limit": 10})
        self.assertEqual([row["id"] for row in listed["activities"]], [recent["id"]])
        aggregates = self.service.get_activity_aggregates({})
        self.assertEqual(aggregates["by_user"]["ADMIN"], 1)
        self.assertEqual(aggregates["by_user"]["MEKH"], 1)
        self.assertEqual(aggregates["by_action"]["card_opened"], 1)
        self.assertEqual(aggregates["by_action"]["repair_order_updated"], 1)
        admin_aggregates = self.service.get_activity_aggregates({"username": "admin"})
        self.assertEqual(admin_aggregates["by_user"], {"ADMIN": 1})
        self.assertEqual(admin_aggregates["by_action"], {"card_opened": 1})
        self.assertNotIn("repair_order_updated", admin_aggregates["by_action"])
        recent_details = self.service.get_activity_details({"activity_id": recent["id"]})
        self.assertEqual(recent_details["details"]["snapshot"], "recent")
        detail_records = []
        for detail_file in (self.activity_dir / "details").glob("*.jsonl"):
            for line in detail_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    detail_records.append(json.loads(line))
        self.assertEqual([record["activity_id"] for record in detail_records], [recent["id"]])
        self.assertNotEqual(old["id"], recent["id"])

    def test_oversized_aggregate_file_is_ignored(self) -> None:
        aggregate_dir = self.activity_dir / "aggregates"
        aggregate_dir.mkdir(parents=True)
        aggregate_path = aggregate_dir / "2026-05.json"
        aggregate_path.write_text(
            '{"schema_version":1,"month":"2026-05","by_user":{"ADMIN":1},"padding":"xxxxxxxx"}',
            encoding="utf-8",
        )

        with patch("minimal_kanban.operator_activity.OPERATOR_ACTIVITY_AGGREGATE_MAX_BYTES", 8):
            aggregates = self.service.get_activity_aggregates({})

        self.assertEqual(aggregates["by_user"], {})
        self.assertEqual(aggregates["by_action"], {})

    def test_numeric_activity_inputs_reject_bool_fractional_and_nonfinite_values(self) -> None:
        listed = self.service.list_activity({"offset": True, "limit": float("inf")})
        huge = self.service.list_activity({"offset": 1e308, "limit": 1e308})
        compacted = self.service.compact_activity(
            {"retention_days": 1.5, "dry_run": True, "backup": False}
        )
        capped_retention = self.service.compact_activity(
            {"retention_days": 1e308, "dry_run": True, "backup": False}
        )

        self.assertEqual(listed["meta"]["offset"], 0)
        self.assertEqual(listed["meta"]["limit"], 100)
        self.assertEqual(huge["meta"]["offset"], 1_000_000)
        self.assertEqual(huge["meta"]["limit"], 500)
        self.assertEqual(compacted["retention_days"], 90)
        self.assertEqual(capped_retention["retention_days"], 3650)

    def test_aggregate_counts_skip_bool_fractional_and_invalid_values(self) -> None:
        aggregate_dir = self.activity_dir / "aggregates"
        aggregate_dir.mkdir(parents=True)
        aggregate_path = aggregate_dir / "2026-05.json"
        aggregate_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "month": "2026-05",
                    "rows_total": 1e308,
                    "by_user": {
                        "ADMIN": True,
                        "MEKH": 2,
                        "FRACTION": 1.5,
                        "HUGE": 1e308,
                        "TEXT": "bad",
                    },
                    "by_action": {"card_opened": "3"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        aggregates = self.service.get_activity_aggregates({})

        self.assertEqual(aggregates["by_user"], {"MEKH": 2, "HUGE": 1_000_000_000})
        self.assertEqual(aggregates["by_action"], {"card_opened": 3})
        self.assertEqual(aggregates["meta"]["total"], 1_000_000_002)

    def test_aggregate_write_keeps_existing_file_when_temp_write_fails(self) -> None:
        aggregate_dir = self.activity_dir / "aggregates"
        aggregate_dir.mkdir(parents=True)
        aggregate_path = aggregate_dir / "2026-05.json"
        original_payload = {"schema_version": 1, "month": "2026-05", "rows_total": 1}
        aggregate_path.write_text(json.dumps(original_payload), encoding="utf-8")
        original_write_text = Path.write_text

        def partial_temp_write(path: Path, data: str, *args, **kwargs) -> int:
            original_write_text(path, "partial", *args, **kwargs)
            raise OSError("disk full")

        with (
            patch.object(Path, "write_text", partial_temp_write),
            self.assertRaises(OSError),
        ):
            self.service._write_aggregates(
                [
                    {
                        "timestamp": "2026-05-23T10:41:00+07:00",
                        "username": "admin",
                        "module": "card",
                        "action": "card_opened",
                        "source": "ui",
                    }
                ]
            )

        self.assertEqual(json.loads(aggregate_path.read_text(encoding="utf-8")), original_payload)
        self.assertEqual(list(aggregate_dir.glob("*.tmp")), [])

    def test_aggregate_write_rejects_payload_larger_than_read_limit_without_clobbering(
        self,
    ) -> None:
        aggregate_dir = self.activity_dir / "aggregates"
        aggregate_dir.mkdir(parents=True)
        aggregate_path = aggregate_dir / "2026-05.json"
        original_payload = {"schema_version": 1, "month": "2026-05", "rows_total": 1}
        aggregate_path.write_text(json.dumps(original_payload), encoding="utf-8")

        with patch("minimal_kanban.operator_activity.OPERATOR_ACTIVITY_AGGREGATE_MAX_BYTES", 256):
            with self.assertRaisesRegex(ValueError, "operator activity aggregate is too large"):
                self.service._write_aggregates(
                    [
                        {
                            "timestamp": "2026-05-23T10:41:00+07:00",
                            "username": "ADMIN" + "X" * 512,
                            "module": "card",
                            "action": "card_opened",
                            "source": "ui",
                        }
                    ]
                )

        self.assertEqual(json.loads(aggregate_path.read_text(encoding="utf-8")), original_payload)
        self.assertEqual(list(aggregate_dir.glob("*.tmp")), [])

    def test_activity_backup_preserves_symlinks_without_following_targets(self) -> None:
        self.activity_dir.mkdir(parents=True)
        (self.activity_dir / ".operator-activity.lock").write_text("", encoding="utf-8")

        with patch("minimal_kanban.operator_activity.shutil.copytree") as copytree:
            backup_dir = self.service._backup_activity_dir()

        self.assertTrue(backup_dir)
        copytree.assert_called_once()
        self.assertTrue(copytree.call_args.kwargs["symlinks"])


if __name__ == "__main__":
    unittest.main()
