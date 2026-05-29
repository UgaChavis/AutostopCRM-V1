from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class PayrollUnaccruedWorkRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.payroll_unaccrued.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch_time(self, moment: datetime):
        return (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso",
                return_value=moment.isoformat(),
            ),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        )

    def test_legacy_unpaid_closed_work_is_not_reported_as_salary_accrual(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Исполнитель",
                "salary_mode": "percent_only",
                "work_percent": "50",
                "material_percent": "10",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Исторически закрыт без оплаты",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фильтр",
                            "quantity": "1",
                            "cost_price": "500",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card_id)
        stored_card.repair_order.status = "closed"
        stored_card.repair_order.closed_at = "18.05.2026 12:39"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        edited = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"comment": "Историческая правка"}}
        )["repair_order"]
        work = edited["works"][0]
        material = edited["materials"][0]
        self.assertEqual(work["salary_amount"], "")
        self.assertEqual(work["salary_accrued_at"], "")
        self.assertEqual(material["material_salary_amount"], "")
        self.assertEqual(material["material_salary_accrued_at"], "")

        report = self.service.get_payroll_report(
            {"month": "2026-05", "employee_id": employee["id"]}
        )
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        self.assertEqual(summary["works_count"], 0)
        self.assertEqual(summary["work_accrued_total"], "0")
        self.assertFalse(any(row["card_id"] == card_id for row in report["detail_rows"]))

        salary_report = self.service.get_employee_salary_report(
            {"month": "2026-05", "employee_id": employee["id"]}
        )
        self.assertEqual(salary_report["totals"]["repair_order_count"], 0)
        self.assertEqual(salary_report["totals"]["work_count"], 0)
        self.assertEqual(salary_report["totals"]["work_accrued_total"], "0")

        patches = self._patch_time(datetime(2026, 5, 29, 12, 0, tzinfo=UTC))
        with patches[0], patches[1], patches[2]:
            ledger = self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"], "months": 1}
            )
            reconciliation = self.service.get_employee_salary_reconciliation(
                {"employee_id": employee["id"]}
            )

        self.assertEqual(ledger["accrued_total"], "0")
        self.assertFalse(any(row["card_id"] == card_id for row in ledger["journal_rows"]))
        self.assertFalse(any(row["card_id"] == card_id for row in reconciliation["rows"]))


if __name__ == "__main__":
    unittest.main()
