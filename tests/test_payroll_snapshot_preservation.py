from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class PayrollSnapshotPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.payroll_snapshot.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_correction_work_insertion_posts_reversal_and_one_new_accrual(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Мастер Снимка",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan Note",
                "title": "Вставка строки после закрытия",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "47",
                    "status": "open",
                    "vehicle": "Nissan Note",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )

        current = self.service.get_card({"card_id": card_id})["card"]
        self.service.reopen_repair_order(
            {
                "card_id": card_id,
                "expected_updated_at": current["updated_at"],
                "reason_code": "other",
                "reason_note": "Добавление служебной строки",
                "idempotency_key": "snapshot-preservation-reopen",
            }
        )
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "works": [
                        {"name": "Служебная строка без начисления", "quantity": "1", "price": "0"},
                        closed["repair_order"]["works"][0],
                    ],
                },
            }
        )

        inserted_row, preserved_row = updated["repair_order"]["works"]
        self.assertEqual(inserted_row["salary_amount"], "")
        self.assertEqual(inserted_row["salary_accrued_at"], "")
        self.assertEqual(inserted_row["work_executor_id_snapshot"], "")
        self.assertEqual(preserved_row["salary_amount"], "")

        current = self.service.get_card({"card_id": card_id})["card"]
        reclosed = self.service.set_repair_order_status(
            {
                "card_id": card_id,
                "status": "closed",
                "expected_updated_at": current["updated_at"],
                "idempotency_key": "snapshot-preservation-reclose",
            }
        )
        self.assertEqual(reclosed["repair_order"]["works"][1]["salary_amount"], "100")

        report = self.service.get_payroll_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        self.assertEqual(summary["works_count"], 1)
        self.assertEqual(summary["work_accrued_total"], "100")
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger["accrued_total"], "100")
        self.assertEqual(len(ledger["journal_rows"]), 3)


if __name__ == "__main__":
    unittest.main()
