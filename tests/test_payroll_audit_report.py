from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "payroll_audit_report.py"


def load_payroll_audit_report_module():
    spec = importlib.util.spec_from_file_location("payroll_audit_report", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("payroll_audit_report.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


def _ok(data: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "data": data}


class PayrollAuditReportTests(unittest.TestCase):
    def test_build_payroll_audit_accepts_balanced_report_and_ledger(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Иван Мастер"}
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "base_salary_accrued_total": "1000",
                            "shift_accrued_total": "500",
                            "work_accrued_total": "2000",
                            "materials_accrued_total": "300",
                            "accrued_total": "3800",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "base_salary",
                            "employee_id": "emp-1",
                            "salary_amount": "1000",
                        },
                        {
                            "row_type": "shift_accrual",
                            "employee_id": "emp-1",
                            "salary_amount": "500",
                        },
                        {"row_type": "work", "employee_id": "emp-1", "salary_amount": "2000"},
                        {
                            "row_type": "material",
                            "employee_id": "emp-1",
                            "material_profit": "3000",
                            "material_percent": "10",
                            "salary_amount": "300",
                        },
                    ],
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Иван Мастер",
                    "accrued_total": "3800",
                    "payout_total": "1000",
                    "advance_total": "500",
                    "balance_total": "2300",
                    "journal_total": 1,
                    "journal_rows": [
                        {
                            "kind": "work_accrual",
                            "employee_id": "emp-1",
                            "card_id": "card-1",
                            "repair_order_number": "42",
                            "amount": "2000",
                        }
                    ],
                }
            ),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            path = urlparse(request.full_url).path
            return FakeResponse(responses[path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=6,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )

        self.assertEqual(result["summary"]["issues_total"], 0)
        self.assertEqual(result["summary"]["employees_total"], 1)

    def test_build_payroll_audit_reports_mismatches_and_duplicates(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Иван Мастер"}
        duplicate_detail = {
            "row_type": "work",
            "employee_id": "emp-1",
            "card_id": "",
            "repair_order_number": "42",
            "closed_at": "29.05.2026 12:00",
            "vehicle": "Toyota",
            "salary_amount": "1000",
        }
        duplicate_ledger = {
            "kind": "work_accrual",
            "employee_id": "emp-1",
            "card_id": "card-1",
            "repair_order_number": "42",
            "closed_at": "29.05.2026 12:00",
            "work_name": "Диагностика",
            "amount": "1000",
        }
        seen_queries: list[dict[str, list[str]]] = []
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "1500",
                            "materials_accrued_total": "0",
                            "accrued_total": "1200",
                        }
                    ],
                    "detail_rows": [duplicate_detail, dict(duplicate_detail)],
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Иван Мастер",
                    "accrued_total": "1200",
                    "payout_total": "100",
                    "advance_total": "100",
                    "balance_total": "900",
                    "journal_total": 2,
                    "journal_rows": [duplicate_ledger, dict(duplicate_ledger)],
                }
            ),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            parsed = urlparse(request.full_url)
            seen_queries.append(parse_qs(parsed.query))
            return FakeResponse(responses[parsed.path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=3,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )
        codes = {issue["code"] for issue in result["issues"]}

        self.assertIn("payroll_summary_total_mismatch", codes)
        self.assertIn("payroll_detail_total_mismatch", codes)
        self.assertIn("payroll_duplicate_detail_row", codes)
        self.assertIn("payroll_ledger_balance_mismatch", codes)
        self.assertIn("payroll_duplicate_ledger_row", codes)
        self.assertIn({"month": ["2026-05"]}, seen_queries)
        self.assertIn({"employee_id": ["emp-1"], "months": ["3"]}, seen_queries)

    def test_build_payroll_audit_reports_material_formula_mismatch(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Иван Снабженец"}
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Иван Снабженец",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "0",
                            "materials_accrued_total": "500",
                            "accrued_total": "500",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "material",
                            "employee_id": "emp-1",
                            "employee_name": "Иван Снабженец",
                            "card_id": "card-1",
                            "repair_order_number": "52",
                            "material_name": "Фильтр",
                            "material_profit": "1000",
                            "material_percent": "10",
                            "salary_amount": "500",
                        }
                    ],
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Иван Снабженец",
                    "accrued_total": "500",
                    "payout_total": "0",
                    "advance_total": "0",
                    "balance_total": "500",
                    "journal_total": 0,
                    "journal_rows": [],
                }
            ),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            return FakeResponse(responses[urlparse(request.full_url).path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=6,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )

        issue = next(
            item
            for item in result["issues"]
            if item["code"] == "payroll_material_salary_formula_mismatch"
        )
        self.assertEqual(issue["data"]["expected_salary_amount"], "100")
        self.assertEqual(issue["data"]["salary_amount"], "500")

    def test_build_payroll_audit_reports_accrual_for_missing_employee(self) -> None:
        module = load_payroll_audit_report_module()
        responses = {
            "/api/list_employees": _ok({"employees": []}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "deleted-employee",
                            "employee_name": "Удаленный мастер",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "1500",
                            "materials_accrued_total": "0",
                            "accrued_total": "1500",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "work",
                            "employee_id": "deleted-employee",
                            "employee_name": "Удаленный мастер",
                            "card_id": "card-1",
                            "repair_order_number": "72",
                            "salary_amount": "1500",
                        }
                    ],
                }
            ),
            "/api/get_cards": _ok({"cards": []}),
            "/api/get_card": _ok({"card": {}}),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            return FakeResponse(responses[urlparse(request.full_url).path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=6,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )

        issue = next(
            item for item in result["issues"] if item["code"] == "payroll_accrual_missing_employee"
        )
        self.assertEqual(issue["severity"], "error")
        self.assertEqual(issue["employee_id"], "deleted-employee")
        self.assertEqual(issue["employee_name"], "Удаленный мастер")
        self.assertEqual(issue["data"]["detail_rows"], 1)
        self.assertEqual(issue["data"]["row_types"], ["summary", "work"])

    def test_build_payroll_audit_reports_work_formula_mismatch_from_card(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Иван Мастер"}
        seen_queries: list[dict[str, list[str]]] = []
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "2000",
                            "materials_accrued_total": "0",
                            "accrued_total": "2000",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "work",
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "card_id": "card-1",
                            "repair_order_number": "72",
                            "closed_at": "29.05.2026 12:00",
                            "vehicle": "Toyota",
                            "salary_amount": "2000",
                        }
                    ],
                }
            ),
            "/api/get_cards": _ok(
                {
                    "cards": [
                        {
                            "id": "card-1",
                            "repair_order": {
                                "number": "72",
                                "works": [
                                    {
                                        "name": "Диагностика",
                                        "quantity": "1",
                                        "price": "10000",
                                        "executor_id": "emp-1",
                                        "work_executor_id_snapshot": "emp-1",
                                        "work_executor_name_snapshot": "Иван Мастер",
                                        "salary_mode_snapshot": "percent_only",
                                        "work_percent_snapshot": "20",
                                        "work_salary_cost_price": "1000",
                                        "salary_amount": "2000",
                                        "salary_accrued_at": "29.05.2026 12:00",
                                    }
                                ],
                            },
                        }
                    ]
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Иван Мастер",
                    "accrued_total": "2000",
                    "payout_total": "0",
                    "advance_total": "0",
                    "balance_total": "2000",
                    "journal_total": 0,
                    "journal_rows": [],
                }
            ),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            parsed = urlparse(request.full_url)
            seen_queries.append(parse_qs(parsed.query))
            return FakeResponse(responses[parsed.path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=6,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )

        issue = next(
            item
            for item in result["issues"]
            if item["code"] == "payroll_work_salary_formula_mismatch"
        )
        self.assertEqual(issue["data"]["expected_salary_amount"], "1800")
        self.assertEqual(issue["data"]["salary_amount"], "2000")
        self.assertIn({"include_archived": ["true"]}, seen_queries)

    def test_build_payroll_audit_accepts_work_override_with_cost_price(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Иван Мастер"}
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "10400",
                            "materials_accrued_total": "0",
                            "accrued_total": "10400",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "work",
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "card_id": "card-1",
                            "repair_order_number": "73",
                            "closed_at": "29.05.2026 12:00",
                            "vehicle": "Toyota",
                            "salary_amount": "10400",
                        }
                    ],
                }
            ),
            "/api/get_cards": _ok(
                {
                    "cards": [
                        {
                            "id": "card-1",
                            "repair_order": {
                                "number": "73",
                                "works": [
                                    {
                                        "name": "Работа с подрядом",
                                        "quantity": "1",
                                        "price": "20000",
                                        "executor_id": "emp-1",
                                        "work_executor_id_snapshot": "emp-1",
                                        "work_executor_name_snapshot": "Иван Мастер",
                                        "salary_mode_snapshot": "percent_only",
                                        "work_percent_snapshot": "45",
                                        "work_salary_override_enabled": "true",
                                        "work_salary_guarantee": "5000",
                                        "work_salary_percent_override": "45",
                                        "work_salary_cost_price": "3000",
                                        "salary_amount": "10400",
                                        "salary_accrued_at": "29.05.2026 12:00",
                                    }
                                ],
                            },
                        }
                    ]
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Иван Мастер",
                    "accrued_total": "10400",
                    "payout_total": "0",
                    "advance_total": "0",
                    "balance_total": "10400",
                    "journal_total": 0,
                    "journal_rows": [],
                }
            ),
        }

        def fake_urlopen(request, timeout):
            _ = timeout
            return FakeResponse(responses[urlparse(request.full_url).path])

        result = module.build_payroll_audit(
            "https://crm.autostopcrm.ru",
            months_back=1,
            ledger_months=6,
            urlopen=fake_urlopen,
            reference=datetime(2026, 5, 29),
        )

        self.assertNotIn(
            "payroll_work_salary_formula_mismatch",
            {item["code"] for item in result["issues"]},
        )


if __name__ == "__main__":
    unittest.main()
