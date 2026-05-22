from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
