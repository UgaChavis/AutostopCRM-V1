from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "finance_audit_report.py"


def load_finance_audit_report_module():
    spec = importlib.util.spec_from_file_location("finance_audit_report", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("finance_audit_report.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


class FinanceAuditReportTests(unittest.TestCase):
    def test_summarize_audit_counts_issue_severity_and_safe_fixes(self) -> None:
        module = load_finance_audit_report_module()
        summary = module.summarize_audit(
            {
                "ok": True,
                "data": {
                    "issues": [
                        {"code": "missing", "severity": "error", "safe_fix_available": False},
                        {"code": "legacy", "severity": "warning", "safe_fix_available": True},
                        {"code": "legacy", "severity": "warning", "safe_fix_available": True},
                        {"code": "note", "severity": "info", "safe_fix_available": False},
                    ],
                    "summary": {"issues_total": 4, "safe_fix_count": 2},
                    "meta": {"schema_version": "finance_audit.v1", "read_only": True},
                },
            }
        )

        self.assertEqual(summary["schema_version"], "finance_audit.v1")
        self.assertTrue(summary["read_only"])
        self.assertEqual(summary["issues_total"], 4)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["warnings"], 2)
        self.assertEqual(summary["info"], 1)
        self.assertEqual(summary["safe_fix_count"], 2)
        self.assertEqual(summary["counts_by_code"], {"legacy": 2, "missing": 1, "note": 1})

    def test_fetch_audit_uses_read_only_finance_audit_endpoint(self) -> None:
        module = load_finance_audit_report_module()
        seen: list[tuple[str, str]] = []

        def fake_urlopen(request, timeout):
            _ = timeout
            seen.append((request.full_url, request.get_method()))
            return FakeResponse(
                {
                    "ok": True,
                    "data": {
                        "issues": [],
                        "summary": {"issues_total": 0, "safe_fix_count": 0},
                        "meta": {"schema_version": "finance_audit.v1", "read_only": True},
                    },
                }
            )

        payload = module.fetch_audit(
            "https://crm.autostopcrm.ru/",
            timeout=5,
            urlopen=fake_urlopen,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(seen, [("https://crm.autostopcrm.ru/api/finance_audit", "GET")])

    def test_limited_data_keeps_summary_and_limits_issue_details(self) -> None:
        module = load_finance_audit_report_module()

        limited = module._limited_data(
            {
                "ok": True,
                "data": {
                    "issues": [{"id": "first"}, {"id": "second"}, {"id": "third"}],
                    "summary": {"issues_total": 3},
                    "meta": {"schema_version": "finance_audit.v1", "read_only": True},
                },
            },
            issue_limit=2,
        )

        self.assertEqual(limited["issues"], [{"id": "first"}, {"id": "second"}])
        self.assertEqual(limited["summary"], {"issues_total": 3})
        self.assertEqual(limited["meta"], {"schema_version": "finance_audit.v1", "read_only": True})


if __name__ == "__main__":
    unittest.main()
