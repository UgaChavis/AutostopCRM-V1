from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "repair_order_number_audit.py"


def load_repair_order_number_audit_module():
    spec = importlib.util.spec_from_file_location("repair_order_number_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("repair_order_number_audit.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepairOrderNumberAuditTests(unittest.TestCase):
    def test_build_audit_reports_number_integrity_issues_without_safe_fixes(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = module.build_audit(
            {
                "cards": [
                    {
                        "id": "card-1",
                        "created_at": "2026-05-19T08:00:00+07:00",
                        "repair_order": {
                            "number": "1",
                            "client": "Иван",
                            "payments": [{"id": "payment-1", "cash_transaction_id": "tx-1"}],
                        },
                    },
                    {
                        "id": "card-2",
                        "created_at": "2026-05-19T09:00:00+07:00",
                        "repair_order": {"number": "1", "client": "Петр"},
                    },
                    {
                        "id": "card-3",
                        "created_at": "2026-05-19T10:00:00+07:00",
                        "repair_order": {"client": "Без номера"},
                    },
                    {
                        "id": "card-4",
                        "created_at": "2026-05-19T11:00:00+07:00",
                        "repair_order": {"number": "A-4", "client": "Нечисловой"},
                    },
                    {
                        "id": "card-5",
                        "created_at": "2026-05-19T12:00:00+07:00",
                        "repair_order": {"number": "10", "client": "Скачок"},
                    },
                    {
                        "id": "card-6",
                        "created_at": "2026-05-19T13:00:00+07:00",
                        "repair_order": {"number": "5", "client": "Инверсия"},
                    },
                ],
                "cash_transactions": [
                    {
                        "id": "tx-1",
                        "note": "Заказ-наряд №9",
                        "transaction_kind": "repair_order_payment",
                    }
                ],
            }
        )

        data = payload["data"]
        codes = {issue["code"] for issue in data["issues"]}

        self.assertTrue(data["meta"]["read_only"])
        self.assertTrue(data["meta"]["dry_run"])
        self.assertEqual(data["summary"]["safe_fix_count"], 0)
        self.assertIn("duplicate_number", codes)
        self.assertIn("missing_number", codes)
        self.assertIn("nonnumeric_number", codes)
        self.assertIn("number_gap", codes)
        self.assertIn("number_time_inversion", codes)
        self.assertIn("payment_note_number_mismatch", codes)

    def test_limited_data_limits_issue_details(self) -> None:
        module = load_repair_order_number_audit_module()
        limited = module._limited_data(
            {
                "ok": True,
                "data": {
                    "issues": [{"id": "first"}, {"id": "second"}, {"id": "third"}],
                    "summary": {"issues_total": 3},
                    "meta": {
                        "schema_version": "repair_order_number_audit.v1",
                        "read_only": True,
                        "dry_run": True,
                    },
                },
            },
            issue_limit=2,
        )

        self.assertEqual(limited["issues"], [{"id": "first"}, {"id": "second"}])
        self.assertEqual(limited["summary"], {"issues_total": 3})
        self.assertTrue(limited["meta"]["read_only"])

    def test_main_reads_state_file_without_modifying_it(self) -> None:
        module = load_repair_order_number_audit_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = {
                "cards": [
                    {
                        "id": "card-1",
                        "repair_order": {"number": "1", "client": "Иван"},
                    }
                ],
                "cash_transactions": [],
            }
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            before = state_path.read_text(encoding="utf-8")

            payload = module.build_audit(json.loads(before))

            self.assertTrue(payload["ok"])
            self.assertEqual(state_path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
