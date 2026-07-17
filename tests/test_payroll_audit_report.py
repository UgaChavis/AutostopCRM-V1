from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch
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

    def read(self, size: int = -1) -> bytes:
        body = json.dumps(self._payload, ensure_ascii=False).encode("utf-8")
        if size is None or size < 0:
            return body
        return body[:size]


class RawResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]


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

    def test_audit_ledger_reports_invalid_journal_total_without_crashing(self) -> None:
        module = load_payroll_audit_report_module()

        issues = module._audit_ledger(
            {
                "employee_id": "emp-1",
                "employee_name": "Иван Мастер",
                "accrued_total": "0",
                "payout_total": "0",
                "advance_total": "0",
                "balance_total": "0",
                "journal_total": 1e308,
                "journal_rows": [],
            },
            {"id": "emp-1", "name": "Иван Мастер"},
        )

        self.assertEqual(
            [item["code"] for item in issues], ["payroll_ledger_journal_count_mismatch"]
        )

    def test_build_payroll_audit_default_urlopen_does_not_follow_redirects(self) -> None:
        module = load_payroll_audit_report_module()
        responses = {
            "/api/list_employees": _ok({"employees": []}),
            "/api/get_payroll_report": _ok({"summary": [], "detail_rows": []}),
        }
        seen_paths: list[str] = []

        def safe_urlopen(request, timeout):
            _ = timeout
            path = urlparse(request.full_url).path
            seen_paths.append(path)
            return FakeResponse(responses[path])

        with (
            patch.object(module, "_urlopen_no_redirect", side_effect=safe_urlopen) as opener,
            patch.object(module.urllib.request, "urlopen") as urlopen,
        ):
            result = module.build_payroll_audit(
                "https://crm.autostopcrm.ru",
                months_back=1,
                ledger_months=1,
                reference=datetime(2026, 5, 29),
            )

        self.assertEqual(result["summary"]["issues_total"], 0)
        self.assertEqual(seen_paths, ["/api/list_employees", "/api/get_payroll_report"])
        self.assertEqual(opener.call_count, 2)
        urlopen.assert_not_called()

    def test_fetch_json_rejects_nonstandard_json_constants(self) -> None:
        module = load_payroll_audit_report_module()

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            return FakeResponse({"ok": True, "data": {"employees": [{"score": float("nan")}]}})

        with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
            module._fetch_json(
                "https://crm.autostopcrm.ru",
                "/api/list_employees",
                timeout=5,
                urlopen=fake_urlopen,
            )

    def test_fetch_json_rejects_deeply_nested_response(self) -> None:
        module = load_payroll_audit_report_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            return RawResponse(deep_json)

        with self.assertRaisesRegex(
            ValueError,
            "payroll audit response JSON is too deeply nested",
        ):
            module._fetch_json(
                "https://crm.autostopcrm.ru",
                "/api/list_employees",
                timeout=5,
                urlopen=fake_urlopen,
            )

    def test_fetch_json_rejects_oversized_response(self) -> None:
        module = load_payroll_audit_report_module()

        class HugeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def read(self, size: int = -1) -> bytes:
                return b"x" * max(0, size)

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            return HugeResponse()

        with patch.object(module, "AUDIT_RESPONSE_MAX_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "payroll audit response is too large"):
                module._fetch_json(
                    "https://crm.autostopcrm.ru",
                    "/api/list_employees",
                    timeout=5,
                    urlopen=fake_urlopen,
                )

    def test_fetch_json_rejects_redirect_response(self) -> None:
        module = load_payroll_audit_report_module()

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            raise module.urllib.error.HTTPError(
                url="https://crm.autostopcrm.ru/api/list_employees",
                code=302,
                msg="Found",
                hdrs={"Location": "https://example.test/api/list_employees"},
                fp=None,
            )

        with self.assertRaisesRegex(ValueError, "payroll audit response redirected"):
            module._fetch_json(
                "https://crm.autostopcrm.ru",
                "/api/list_employees",
                timeout=5,
                urlopen=fake_urlopen,
            )

    def test_json_dumps_sanitizes_nonfinite_values(self) -> None:
        module = load_payroll_audit_report_module()

        encoded = module._json_dumps({"ok": True, "value": float("nan"), "ratio": 1.25})

        self.assertNotIn("NaN", encoded)
        self.assertEqual(json.loads(encoded), {"ok": True, "value": None, "ratio": 1.25})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_payroll_audit_report_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

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

    def test_build_payroll_audit_compares_order_accrual_with_cash_price(self) -> None:
        module = load_payroll_audit_report_module()
        employee = {"id": "emp-1", "name": "Сергей Мастер"}
        responses = {
            "/api/list_employees": _ok({"employees": [employee]}),
            "/api/get_payroll_report": _ok(
                {
                    "summary": [
                        {
                            "employee_id": "emp-1",
                            "employee_name": "Сергей Мастер",
                            "base_salary_accrued_total": "0",
                            "shift_accrued_total": "0",
                            "work_accrued_total": "0",
                            "materials_accrued_total": "0",
                            "repair_order_accrued_total": "60",
                            "accrued_total": "60",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "repair_order_accrual",
                            "employee_id": "emp-1",
                            "employee_name": "Сергей Мастер",
                            "card_id": "card-1",
                            "repair_order_number": "75",
                            "base_amount": "1500",
                            "repair_order_percent": "4",
                            "salary_amount": "60",
                            "accrual_id": "accrual-1",
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
                                "number": "75",
                                "status": "closed",
                                "is_paid": True,
                                "subtotal_total": "2000",
                                "works": [],
                            },
                        }
                    ]
                }
            ),
            "/api/get_employee_salary_ledger": _ok(
                {
                    "employee_id": "emp-1",
                    "employee_name": "Сергей Мастер",
                    "accrued_total": "60",
                    "payout_total": "0",
                    "advance_total": "0",
                    "balance_total": "60",
                    "journal_total": 1,
                    "journal_rows": [
                        {
                            "kind": "repair_order_accrual",
                            "employee_id": "emp-1",
                            "card_id": "card-1",
                            "repair_order_number": "75",
                            "amount": "60",
                        }
                    ],
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
            if item["code"] == "payroll_repair_order_cash_base_mismatch"
        )
        self.assertEqual(issue["data"]["recorded_base"], "1500")
        self.assertEqual(issue["data"]["cash_price_subtotal_total"], "2000")

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

    def test_build_payroll_audit_uses_work_sale_snapshot_after_closed_edit(self) -> None:
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
                            "work_accrued_total": "100",
                            "materials_accrued_total": "0",
                            "accrued_total": "100",
                        }
                    ],
                    "detail_rows": [
                        {
                            "row_type": "work",
                            "employee_id": "emp-1",
                            "employee_name": "Иван Мастер",
                            "card_id": "card-1",
                            "repair_order_number": "74",
                            "closed_at": "29.05.2026 12:00",
                            "vehicle": "Toyota",
                            "salary_amount": "100",
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
                                "number": "74",
                                "works": [
                                    {
                                        "name": "Диагностика",
                                        "quantity": "5",
                                        "price": "1000",
                                        "executor_id": "emp-1",
                                        "work_executor_id_snapshot": "emp-1",
                                        "work_executor_name_snapshot": "Иван Мастер",
                                        "work_quantity_snapshot": "1",
                                        "work_price_snapshot": "1000",
                                        "work_total_snapshot": "1000",
                                        "salary_mode_snapshot": "percent_only",
                                        "work_percent_snapshot": "10",
                                        "salary_amount": "100",
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
                    "accrued_total": "100",
                    "payout_total": "0",
                    "advance_total": "0",
                    "balance_total": "100",
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

    def test_main_reports_invalid_json_without_traceback(self) -> None:
        module = load_payroll_audit_report_module()
        output = StringIO()

        with (
            patch.object(sys, "argv", ["payroll_audit_report.py"]),
            patch.object(
                module,
                "build_payroll_audit",
                side_effect=json.JSONDecodeError("bad json", "{", 0),
            ),
            redirect_stdout(output),
        ):
            exit_code = module.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("bad json", payload["error"])

    def test_cli_numeric_bounds_reject_huge_values(self) -> None:
        module = load_payroll_audit_report_module()

        self.assertEqual(module._bounded_int(1e308, default=1, minimum=1, maximum=24), 24)
        self.assertEqual(module._bounded_int(-1e308, default=1, minimum=1, maximum=24), 1)
        self.assertEqual(module._bounded_int("bad", default=6, minimum=1, maximum=24), 6)
        self.assertEqual(module._bounded_timeout_seconds(1e308), 300.0)
        self.assertEqual(module._bounded_timeout_seconds(0), 1.0)
        self.assertEqual(module._bounded_timeout_seconds("bad"), 15.0)


if __name__ == "__main__":
    unittest.main()
