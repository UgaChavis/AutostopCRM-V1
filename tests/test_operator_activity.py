from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
        old = self.service.record_activity(
            {
                "timestamp": "2026-01-01T10:00:00+00:00",
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
                "timestamp": "2026-05-23T10:00:00+07:00",
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


if __name__ == "__main__":
    unittest.main()
