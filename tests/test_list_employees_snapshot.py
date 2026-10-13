from __future__ import annotations

import logging
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import (  # noqa: E402
    _parse_aware_iso_datetime_cached,
    clear_business_timezone_cache,
)
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.payroll_read_snapshot import (  # noqa: E402
    PAYROLL_READ_SETTING_KEYS,
    PayrollReadCard,
    SalaryBalanceReadTransaction,
    detach_employee_list_inputs,
)
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ListEmployeesSnapshotTests(unittest.TestCase):
    def test_detached_projection_matches_deepcopy_and_preserves_shared_nested_sources(self) -> None:
        employee = self.service.save_employee(
            {"name": "Общие вложенные данные", "salary_mode": "none"}
        )["employee"]
        self.service.create_card({"title": "Общий источник"})
        bundle = self.store.read_bundle()
        nested = {"values": [1, {"memo": ["synthetic"]}]}
        bundle["settings"]["employees"][0]["synthetic_extra"] = nested
        bundle["cards"][0].repair_order.payroll_postings = [nested]
        projected = {
            "settings": {key: bundle["settings"].get(key) for key in PAYROLL_READ_SETTING_KEYS},
            "cards": [
                PayrollReadCard(card.id, card.vehicle, card.repair_order)
                for card in bundle["cards"]
            ],
            "cashboxes": [],
            "cash_transactions": [
                SalaryBalanceReadTransaction(
                    item.employee_id, item.transaction_kind, item.amount_minor, item.created_at
                )
                for item in bundle["cash_transactions"]
            ],
        }
        expected = deepcopy(projected)
        actual = detach_employee_list_inputs(bundle)
        self.assertEqual(actual, expected)
        self.assertIs(
            actual["settings"]["employees"][0]["synthetic_extra"],
            actual["cards"][0].repair_order.payroll_postings[0],
        )
        nested["values"][1]["memo"].append("changed")
        self.assertEqual(actual, expected)
        self.assertEqual(actual["settings"]["employees"][0]["id"], employee["id"])

    def test_unrelated_card_and_settings_payloads_are_not_copied(self) -> None:
        class UnrelatedPayload:
            def __deepcopy__(self, memo):
                raise AssertionError("payroll copied unrelated card/settings data")

        employee = self.service.save_employee({"name": "Лёгкий снимок", "salary_mode": "none"})[
            "employee"
        ]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1735}
        )
        self.service.create_card({"title": "Карточка с посторонним содержимым"})
        expected = self._old_full_result("2026-10")
        bundle = self.store.read_bundle()
        bundle["cards"][0].ai_autofill_log.append(UnrelatedPayload())
        bundle["settings"]["unrelated_large_payload"] = UnrelatedPayload()
        self.assertEqual(self.service.list_employees({"month": "2026-10"}), expected)

    def test_payroll_projection_detaches_mutable_order_and_salary_inputs(self) -> None:
        employee = self.service.save_employee(
            {"name": "Независимые данные", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1735}
        )
        self.service.create_card({"title": "Исходный заказ", "vehicle": "Синтетический автомобиль"})
        bundle = self.store.read_bundle()
        detached = detach_employee_list_inputs(bundle)
        before = detached["cards"][0].repair_order.to_storage_dict()
        bundle["cards"][0].repair_order.number = "CHANGED-AFTER-READ"
        bundle["settings"]["employee_shift_accruals"][0]["amount_minor"] = 9999
        self.assertEqual(detached["cards"][0].repair_order.to_storage_dict(), before)
        self.assertEqual(detached["settings"]["employee_shift_accruals"][0]["amount_minor"], 1735)
        self.assertEqual(detached["cards"][0].vehicle, "Синтетический автомобиль")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        logger = logging.getLogger(f"test.list_employees_snapshot.{self._testMethodName}")
        logger.addHandler(logging.NullHandler())
        self.store = JsonStore(state_file=Path(self.temp_dir.name) / "state.json", logger=logger)
        self.service = CardService(self.store, logger)

    def _old_full_result(self, month: str) -> dict:
        """Reference implementation of the former all-under-lock list path."""
        bundle = self.store.read_bundle()
        employees = self.service._employees_from_settings(bundle["settings"])
        employees_by_id = {item["id"]: item for item in employees}
        shifts = self.service._employee_shift_accruals_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        orders = self.service._employee_repair_order_accruals_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        resets = self.service._employee_salary_balance_resets_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        report = self.service._build_payroll_report(
            bundle["cards"],
            employees,
            shift_accruals=shifts,
            repair_order_accruals=orders,
            month=month,
        )
        balances = {
            item["id"]: self.service._build_employee_salary_balance_summary(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                item,
                shift_accruals=shifts,
                repair_order_accruals=orders,
                salary_balance_resets=resets,
                months=6,
            )["balance_total"]
            for item in employees
        }
        return {
            "employees": [
                self.service._employee_with_current_payroll_term(
                    {**item, "balance_total": balances.get(item["id"], "0")}
                )
                for item in employees
            ],
            "month": month,
            "summary": report["summary"],
            "detail_rows": report["detail_rows"],
        }

    def test_grouped_snapshot_matches_original_full_response(self) -> None:
        first = self.service.save_employee(
            {"name": "Механик Один", "salary_mode": "percent_only", "work_percent": "30"}
        )["employee"]
        second = self.service.save_employee({"name": "Механик Два", "salary_mode": "none"})[
            "employee"
        ]
        cashbox = self.service.create_cashbox({"name": "Зарплатная касса"})["cashbox"]
        card = self.service.create_card(
            {"vehicle": "Синтетический автомобиль", "title": "Зарплата", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "SALARY-TEST",
                    "status": "open",
                    "client": "Синтетический клиент",
                    "vehicle": "Синтетический автомобиль",
                    "payments": [
                        {"amount": "10000", "paid_at": "23.09.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Работа",
                            "quantity": "1",
                            "price": "10000",
                            "executor_id": first["id"],
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.service.create_employee_shift_accrual(
            {"employee_id": second["id"], "amount_minor": 13579, "note": "Смена"}
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": first["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 4200,
                "cashbox_id": cashbox["id"],
            }
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": second["id"],
                "transaction_kind": "salary_advance",
                "amount_minor": 2500,
                "cashbox_id": cashbox["id"],
            }
        )
        bundle = self.store.read_bundle()
        settings = dict(bundle["settings"])
        created_at = datetime.now(UTC).isoformat()
        legacy_accrual = {
            "id": "synthetic-order-accrual",
            "kind": "accrual",
            "employee_id": second["id"],
            "card_id": card["id"],
            "repair_order_number": "SALARY-TEST",
            "amount_minor": 6300,
            "base_amount_minor": 210000,
            "percent": "3",
            "created_at": created_at,
        }
        settings["employee_repair_order_accruals"] = [
            legacy_accrual,
            {
                **legacy_accrual,
                "id": "synthetic-order-reversal",
                "kind": "reversal",
                "amount_minor": 1100,
                "related_accrual_id": legacy_accrual["id"],
            },
        ]
        self.store.write_bundle(**{**bundle, "settings": settings})
        month = "2026-09"
        self.assertEqual(
            self.service.list_employees({"month": month}), self._old_full_result(month)
        )

    def test_full_report_releases_lock_and_uses_one_consistent_snapshot(self) -> None:
        employee = self.service.save_employee({"name": "Сотрудник снимка", "salary_mode": "none"})[
            "employee"
        ]
        report_started = threading.Event()
        allow_report = threading.Event()
        build_report = self.service._build_payroll_report

        def delayed_report(*args, **kwargs):
            report_started.set()
            if not allow_report.wait(5):
                raise AssertionError("report did not resume")
            return build_report(*args, **kwargs)

        with patch.object(self.service, "_build_payroll_report", side_effect=delayed_report):
            with ThreadPoolExecutor(max_workers=2) as executor:
                full = executor.submit(self.service.list_employees, {"month": "2026-09"})
                self.assertTrue(report_started.wait(5))
                try:
                    reference = executor.submit(
                        self.service.list_employees,
                        {"references_only": True, "month": "2026-09"},
                    )
                    self.assertEqual(
                        reference.result(timeout=2)["employees"][0]["id"], employee["id"]
                    )
                    self.service.create_employee_shift_accrual(
                        {"employee_id": employee["id"], "amount_minor": 12345}
                    )
                finally:
                    allow_report.set()
                before_write = full.result(timeout=5)

        self.assertEqual(before_write["employees"][0]["balance_total"], "0")
        after_write = self.service.list_employees({"month": "2026-09"})
        self.assertEqual(after_write["employees"][0]["balance_total"], "123.45")

    def test_list_employees_is_identical_with_cold_and_warm_datetime_parse_cache(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Сотрудник кэша",
                "salary_mode": "salary_plus_percent",
                "base_salary": "40000",
            }
        )["employee"]
        bundle = self.store.read_bundle()
        settings = dict(bundle["settings"])
        timestamp = "2026-09-16T12:00:00+07:00"
        settings["employee_shift_accruals"] = [
            {
                "id": f"cache-shift-{index:03d}",
                "employee_id": employee["id"],
                "employee_name": employee["name"],
                "amount_minor": 10000 + index,
                "created_at": timestamp,
                "updated_at": timestamp,
                "note": "Синтетическая смена",
                "actor_name": "TEST",
                "source": "system",
            }
            for index in range(24)
        ]
        self.store.write_bundle(**{**bundle, "settings": settings})

        clear_business_timezone_cache()
        cold = self.service.list_employees({"month": "2026-09"})
        cold_cache_info = _parse_aware_iso_datetime_cached.cache_info()
        warm = self.service.list_employees({"month": "2026-09"})
        warm_cache_info = _parse_aware_iso_datetime_cached.cache_info()

        self.assertEqual(cold, warm)
        self.assertGreater(cold_cache_info.hits, 0)
        self.assertGreater(warm_cache_info.hits, cold_cache_info.hits)


if __name__ == "__main__":
    unittest.main()
