from __future__ import annotations

import json
import sys
import unittest
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.models import business_timezone, utc_now
from minimal_kanban.operator_permissions import SALARY_BALANCE_RESET_PERMISSION
from tests.services_case import CardServiceCase


class EmployeeSalaryBalanceSummaryTests(CardServiceCase):
    def _ledger_context(self, employee_id: str) -> tuple[dict, dict, dict]:
        bundle = self.store.read_bundle()
        employees = self.service._employees_from_settings(bundle["settings"])
        employees_by_id = {item["id"]: item for item in employees}
        return (
            bundle,
            employees_by_id[employee_id],
            {
                "shift_accruals": self.service._employee_shift_accruals_from_settings(
                    bundle["settings"], employees_by_id=employees_by_id
                ),
                "repair_order_accruals": (
                    self.service._employee_repair_order_accruals_from_settings(
                        bundle["settings"], employees_by_id=employees_by_id
                    )
                ),
                "salary_balance_resets": (
                    self.service._employee_salary_balance_resets_from_settings(
                        bundle["settings"], employees_by_id=employees_by_id
                    )
                ),
            },
        )

    def _assert_balance_summary_matches_full_ledger(self, employee_id: str) -> tuple[dict, dict]:
        bundle, employee, sources = self._ledger_context(employee_id)
        as_of = utc_now()
        full = self.service._build_employee_salary_ledger(
            bundle["cards"],
            bundle["cashboxes"],
            bundle["cash_transactions"],
            employee,
            **sources,
            as_of=as_of,
        )
        summary = self.service._build_employee_salary_balance_summary(
            bundle["cards"],
            bundle["cashboxes"],
            bundle["cash_transactions"],
            employee,
            **sources,
            as_of=as_of,
        )
        self.assertEqual(
            summary,
            {
                "balance_total": full["balance_total"],
                "balance_minor": full["balance_minor"],
            },
        )
        return summary, full

    def _create_employee(self, name: str) -> dict:
        return self.service.save_employee(
            {
                "name": name,
                "position": "Мастер",
                "salary_mode": "percent_only",
                "work_percent": "25",
                "material_percent": "0",
                "repair_order_percent": "0",
            }
        )["employee"]

    def _close_posted_order(self, employee_id: str, cashbox_id: str) -> str:
        card = self.service.create_card(
            {"vehicle": "Toyota", "title": "Баланс сотрудника", "deadline": {"hours": 2}}
        )["card"]
        paid_at = utc_now().astimezone(business_timezone()).strftime("%d.%m.%Y %H:%M")
        self.service.update_repair_order(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee_id,
                        }
                    ],
                    "payments": [
                        {
                            "amount": "1000",
                            "cashbox_id": cashbox_id,
                            "paid_at": paid_at,
                        }
                    ],
                },
            }
        )
        current = self.service.get_card({"card_id": card["id"]})["card"]
        self.service.set_repair_order_status(
            {
                "card_id": card["id"],
                "status": "closed",
                "expected_updated_at": current["updated_at"],
            }
        )
        return card["id"]

    def test_summary_matches_current_accrual_payout_advance_and_postings(self) -> None:
        employee = self._create_employee("Сводный текущий баланс")
        cashbox = self.service.create_cashbox({"name": "Зарплатная касса"})["cashbox"]
        card_id = self._close_posted_order(employee["id"], cashbox["id"])
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 12345}
        )
        for transaction_kind, amount_minor in (
            ("salary_payout", 2000),
            ("salary_advance", 500),
        ):
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": transaction_kind,
                    "amount_minor": amount_minor,
                    "cashbox_id": cashbox["id"],
                }
            )

        summary, full = self._assert_balance_summary_matches_full_ledger(employee["id"])

        self.assertTrue(
            any(
                row["card_id"] == card_id and row["kind"] == "accrual"
                for row in full["journal_rows"]
            )
        )
        self.assertNotEqual(summary["balance_minor"], 0)
        bundle, stored_employee, sources = self._ledger_context(employee["id"])
        with (
            patch.object(
                self.service,
                "_employee_salary_ledger_revision",
                side_effect=AssertionError("summary must not build a revision"),
            ),
            patch.object(
                self.service,
                "_repair_order_sortable_datetime",
                side_effect=AssertionError("summary must not sort journal rows"),
            ),
        ):
            no_details = self.service._build_employee_salary_balance_summary(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                stored_employee,
                **sources,
                as_of=utc_now(),
            )
        self.assertEqual(set(no_details), {"balance_total", "balance_minor"})

    def test_summary_matches_legacy_order_and_sources_older_than_journal_window(self) -> None:
        employee = self._create_employee("Сводный старый баланс")
        cashbox = self.service.create_cashbox({"name": "Старая касса"})["cashbox"]
        card_id = self._close_posted_order(employee["id"], cashbox["id"])
        shift = self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 5000}
        )["accrual"]
        payout = self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 1000,
                "cashbox_id": cashbox["id"],
            }
        )["transaction"]
        old_at = utc_now() - timedelta(days=400)
        old_business_text = old_at.astimezone(business_timezone()).strftime("%d.%m.%Y %H:%M")
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.repair_order.payroll_postings = []
        card.repair_order.cycles = []
        card.repair_order.closed_at = old_business_text
        next(
            item for item in bundle["cash_transactions"] if item.id == payout["id"]
        ).created_at = old_at.isoformat()
        next(
            item
            for item in bundle["settings"]["employee_shift_accruals"]
            if item["id"] == shift["id"]
        )["created_at"] = old_at.isoformat()
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            inventory_items=bundle["inventory_items"],
            inventory_movements=bundle["inventory_movements"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        summary, full = self._assert_balance_summary_matches_full_ledger(employee["id"])

        self.assertNotEqual(summary["balance_minor"], 0)
        self.assertEqual(full["journal_rows"], [])

    def test_reset_and_list_employees_api_keep_exact_balance_without_full_ledger(self) -> None:
        employee = self.service.save_employee(
            {"name": "Сводный баланс после обнуления", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 10000}
        )
        before_reset = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.service.reset_employee_salary_balance(
            {
                "employee_id": employee["id"],
                "expected_balance_minor": before_reset["balance_minor"],
                "expected_balance_revision": before_reset["balance_revision"],
                "idempotency_key": "summary-balance-reset",
                "_operator_session": {
                    "username": "ADMIN",
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                },
            }
        )
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 2500}
        )
        summary, full = self._assert_balance_summary_matches_full_ledger(employee["id"])
        self.assertEqual(summary["balance_minor"], 2500)

        server = ApiServer(self.service, self.logger, start_port=0)
        with patch.object(
            self.service,
            "_employee_salary_ledger_revision",
            side_effect=AssertionError("list_employees must use the summary path"),
        ):
            try:
                server.start()
                with urllib.request.urlopen(
                    f"{server.base_url}/api/list_employees", timeout=10
                ) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))["data"]
            finally:
                server.stop()
        listed = next(item for item in payload["employees"] if item["id"] == employee["id"])
        self.assertEqual(listed["balance_total"], full["balance_total"])


if __name__ == "__main__":
    unittest.main()
