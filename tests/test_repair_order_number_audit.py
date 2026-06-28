from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_PATH = ROOT / "scripts" / "repair_order_number_audit.py"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.repair_order_number_audit import _parse_order_number  # noqa: E402


def load_repair_order_number_audit_module():
    spec = importlib.util.spec_from_file_location("repair_order_number_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("repair_order_number_audit.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepairOrderNumberAuditTests(unittest.TestCase):
    def test_parse_order_number_rejects_signed_values(self) -> None:
        self.assertIsNone(_parse_order_number("-1"))
        self.assertEqual(_parse_order_number("42"), 42)

    def test_read_state_audit_rejects_deeply_nested_state_file(self) -> None:
        module = load_repair_order_number_audit_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
                module._read_state_audit(str(state_file))

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
                            "payments": [
                                {
                                    "id": "payment-1",
                                    "amount": "1000",
                                    "paid_at": "19.05.2026 08:00",
                                    "cash_transaction_id": "tx-1",
                                }
                            ],
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
                    {
                        "id": "card-empty-skeleton",
                        "created_at": "2026-05-19T14:00:00+07:00",
                        "repair_order": {
                            "number": "",
                            "date": "",
                            "status": "open",
                            "opened_at": "",
                            "closed_at": "",
                            "client": "",
                            "phone": "",
                            "vehicle": "",
                            "license_plate": "",
                            "vin": "",
                            "mileage": "",
                            "payment_method": "cash",
                            "prepayment": "",
                            "payments": [],
                            "reason": "",
                            "comment": "",
                            "note": "",
                            "tags": [],
                            "works": [],
                            "materials": [],
                        },
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
        self.assertEqual(data["summary"]["review_required_count"], len(data["issues"]))
        self.assertIn("duplicate_number", codes)
        self.assertIn("missing_number", codes)
        self.assertIn("nonnumeric_number", codes)
        self.assertIn("number_gap", codes)
        self.assertIn("number_time_inversion", codes)
        self.assertIn("payment_note_number_mismatch", codes)
        self.assertFalse(any(issue["card_id"] == "card-empty-skeleton" for issue in data["issues"]))

    def test_same_timestamp_orders_are_sorted_by_number_before_inversion_check(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = module.build_audit(
            {
                "cards": [
                    {
                        "id": "card-2",
                        "created_at": "2026-05-19T08:00:00+07:00",
                        "repair_order": {
                            "number": "2",
                            "client": "Петр",
                            "opened_at": "19.05.2026 08:00",
                        },
                    },
                    {
                        "id": "card-1",
                        "created_at": "2026-05-19T08:00:00+07:00",
                        "repair_order": {
                            "number": "1",
                            "client": "Иван",
                            "opened_at": "19.05.2026 08:00",
                        },
                    },
                ],
                "cash_transactions": [],
            }
        )

        issues = payload["data"]["issues"]

        self.assertFalse(any(issue["code"] == "number_time_inversion" for issue in issues))
        self.assertFalse(any(issue["code"] == "number_gap" for issue in issues))

    def test_digit_only_number_that_cannot_be_parsed_is_reported_without_crashing(self) -> None:
        module = load_repair_order_number_audit_module()
        with patch.object(module, "int", side_effect=ValueError, create=True):
            payload = module.build_audit(
                {
                    "cards": [
                        {
                            "id": "card-huge",
                            "repair_order": {"number": "9" * 32, "client": "Иван"},
                        }
                    ],
                    "cash_transactions": [],
                }
            )

        issues = payload["data"]["issues"]
        self.assertEqual(["nonnumeric_number"], [issue["code"] for issue in issues])
        self.assertEqual("card-huge", issues[0]["card_id"])

    def test_local_opened_at_is_compared_in_business_timezone_against_iso_created_at(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = module.build_audit(
            {
                "cards": [
                    {
                        "id": "card-later-iso",
                        "created_at": "2026-05-28T09:50:00+00:00",
                        "repair_order": {"number": "10", "client": "Позже по UTC"},
                    },
                    {
                        "id": "card-earlier-local",
                        "created_at": "2026-05-28T10:00:00+00:00",
                        "repair_order": {
                            "number": "9",
                            "client": "Раньше по локальному времени",
                            "opened_at": "28.05.2026 16:10",
                        },
                    },
                ],
                "cash_transactions": [],
            }
        )

        issues = payload["data"]["issues"]

        self.assertFalse(any(issue["code"] == "number_time_inversion" for issue in issues))
        self.assertFalse(any(issue["code"] == "number_gap" for issue in issues))

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

    def test_fetch_audit_uses_read_only_repair_order_number_audit_endpoint(self) -> None:
        module = load_repair_order_number_audit_module()
        seen: list[tuple[str, str]] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def read(self, size: int = -1) -> bytes:
                _ = size
                return json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "issues": [],
                            "summary": {"issues_total": 0},
                            "meta": {
                                "schema_version": "repair_order_number_audit.v1",
                                "read_only": True,
                                "dry_run": True,
                            },
                        },
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            _ = timeout
            seen.append((request.full_url, request.get_method()))
            return FakeResponse()

        payload = module.fetch_audit(
            "https://crm.autostopcrm.ru/",
            timeout=5,
            urlopen=fake_urlopen,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(
            seen,
            [("https://crm.autostopcrm.ru/api/repair_order_number_audit", "GET")],
        )

    def test_fetch_audit_rejects_nonstandard_json_constants(self) -> None:
        module = load_repair_order_number_audit_module()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def read(self, size: int = -1) -> bytes:
                _ = size
                return b'{"ok": true, "data": {"score": NaN}}'

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            return FakeResponse()

        with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
            module.fetch_audit("https://crm.autostopcrm.ru/", urlopen=fake_urlopen)

    def test_fetch_audit_rejects_non_object_response(self) -> None:
        module = load_repair_order_number_audit_module()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def read(self, size: int = -1) -> bytes:
                _ = size
                return b"[]"

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            return FakeResponse()

        with self.assertRaisesRegex(ValueError, "API response must be a JSON object"):
            module.fetch_audit("https://crm.autostopcrm.ru/", urlopen=fake_urlopen)

    def test_fetch_audit_rejects_oversized_response(self) -> None:
        module = load_repair_order_number_audit_module()

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
            with self.assertRaisesRegex(
                ValueError, "repair order number audit response is too large"
            ):
                module.fetch_audit("https://crm.autostopcrm.ru/", urlopen=fake_urlopen)

    def test_fetch_audit_rejects_redirect_response(self) -> None:
        module = load_repair_order_number_audit_module()

        def fake_urlopen(request, timeout):
            _ = (request, timeout)
            raise module.urllib.error.HTTPError(
                url="https://crm.autostopcrm.ru/api/repair_order_number_audit",
                code=302,
                msg="Found",
                hdrs={"Location": "https://example.test/api/repair_order_number_audit"},
                fp=None,
            )

        with self.assertRaisesRegex(ValueError, "repair order number audit response redirected"):
            module.fetch_audit("https://crm.autostopcrm.ru/", urlopen=fake_urlopen)

    def test_json_dumps_sanitizes_non_finite_numbers(self) -> None:
        module = load_repair_order_number_audit_module()

        encoded = module._json_dumps({"ok": True, "score": float("nan"), "items": [float("inf")]})

        self.assertEqual(json.loads(encoded), {"ok": True, "score": None, "items": [None]})
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_repair_order_number_audit_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_text_report_includes_actionable_issue_context(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = {
            "ok": True,
            "data": {
                "issues": [
                    {
                        "id": "number_time_inversion:card-1:2",
                        "code": "number_time_inversion",
                        "severity": "warning",
                        "message": "Более поздний заказ-наряд имеет номер меньше уже встреченного в хронологии.",
                        "card_id": "card-1",
                        "repair_order_number": "2",
                        "safe_fix_available": False,
                        "data": {
                            "max_seen_number": 4,
                            "current_number": 2,
                            "opened_sort_value": "2026-04-04T02:37:00+00:00",
                        },
                    }
                ],
                "summary": {"orders_total": 1, "issues_total": 1},
                "meta": {
                    "schema_version": "repair_order_number_audit.v1",
                    "read_only": True,
                    "dry_run": True,
                },
            },
        }

        text = module._format_text(payload, issue_limit=10)

        self.assertIn("id=number_time_inversion:card-1:2", text)
        self.assertIn("card_id=card-1", text)
        self.assertIn("number=2", text)
        self.assertIn("max_seen_number=4", text)
        self.assertIn("current_number=2", text)
        self.assertIn("opened_sort_value=2026-04-04T02:37:00+00:00", text)
        self.assertIn("safe_fix=no", text)

    def test_text_report_includes_fixability_and_count_summaries(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = {
            "ok": True,
            "data": {
                "issues": [],
                "summary": {
                    "orders_total": 3,
                    "issues_total": 2,
                    "safe_fix_count": 0,
                    "review_required_count": 2,
                    "counts_by_severity": {"error": 1, "warning": 1, "info": 0},
                    "counts_by_code": {"missing_number": 1, "number_gap": 1},
                },
                "meta": {
                    "schema_version": "repair_order_number_audit.v1",
                    "read_only": True,
                    "dry_run": True,
                },
            },
        }

        text = module._format_text(payload, issue_limit=10)

        self.assertIn("safe_fixes_available: 0", text)
        self.assertIn("review_required: 2", text)
        self.assertIn("issues_by_severity: error=1, info=0, warning=1", text)
        self.assertIn("issues_by_code: missing_number=1, number_gap=1", text)

    def test_text_report_tolerates_invalid_numeric_summary_values(self) -> None:
        module = load_repair_order_number_audit_module()
        payload = {
            "ok": True,
            "data": {
                "issues": [{"code": "missing_number", "severity": "warning"}],
                "summary": {
                    "orders_total": True,
                    "issues_total": "bad",
                    "safe_fix_count": float("inf"),
                    "review_required_count": 1.5,
                    "counts_by_code": {
                        "missing_number": "1",
                        "huge": 1e308,
                        "bad": True,
                        "fraction": 1.5,
                    },
                },
                "meta": {"schema_version": "repair_order_number_audit.v1"},
            },
        }

        text = module._format_text(payload, issue_limit=10)

        self.assertIn("orders: 0", text)
        self.assertIn("issues: 1", text)
        self.assertIn("safe_fixes_available: 0", text)
        self.assertIn("review_required: 1", text)
        self.assertIn("huge=1000000000", text)
        self.assertIn("missing_number=1", text)
        self.assertNotIn("bad=", text)
        self.assertNotIn("fraction=", text)

    def test_cli_issue_limit_is_bounded(self) -> None:
        module = load_repair_order_number_audit_module()

        self.assertEqual(module._bounded_issue_limit(1e308), 500)
        self.assertEqual(module._bounded_issue_limit(-1e308), 0)
        self.assertEqual(module._bounded_issue_limit("bad"), 50)
        self.assertEqual(module._bounded_timeout_seconds(1e308), 300.0)
        self.assertEqual(module._bounded_timeout_seconds(0), 1.0)
        self.assertEqual(module._bounded_timeout_seconds("bad"), 15.0)

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

    def test_local_state_reader_rejects_nonstandard_json_constants(self) -> None:
        module = load_repair_order_number_audit_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text('{"cards":[{"repair_order":{"number":NaN}}]}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant"):
                module._read_state_audit(str(state_path))

    def test_local_state_reader_rejects_oversized_state_file(self) -> None:
        module = load_repair_order_number_audit_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text("x" * 16, encoding="utf-8")

            with patch.object(module, "AUDIT_STATE_MAX_BYTES", 8):
                with self.assertRaisesRegex(
                    ValueError, "repair order number audit state file is too large"
                ):
                    module._read_state_audit(str(state_path))


if __name__ == "__main__":
    unittest.main()
