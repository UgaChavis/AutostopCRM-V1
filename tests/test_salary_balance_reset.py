from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.logging_setup import close_logger
from minimal_kanban.models import business_timezone
from minimal_kanban.operator_permissions import SALARY_BALANCE_RESET_PERMISSION
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.storage.json_store import JsonStore


class SalaryBalanceResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.salary_balance_reset.{self._testMethodName}")
        close_logger(self.logger)
        self.addCleanup(close_logger, self.logger)
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def _build_service(self) -> CardService:
        return CardService(
            self.store,
            self.logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )

    def _make_legacy_employee(self, employee_id: str, **changes) -> None:
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        employee = next(
            item for item in state["settings"]["employees"] if item["id"] == employee_id
        )
        employee.pop("payroll_terms", None)
        employee.pop("updated_at", None)
        employee.update(changes)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def test_dated_shift_without_id_has_stable_revision_and_reset_replays(self) -> None:
        employee = self.service.save_employee({"name": "Synthetic shift", "salary_mode": "none"})[
            "employee"
        ]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1200}
        )
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        shift = state["settings"]["employee_shift_accruals"][0]
        shift.pop("id")
        state["settings"]["employee_shift_accruals"].append(dict(shift))
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        second = self._build_service().get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(first["balance_minor"], 2400)
        self.assertEqual(first["balance_revision"], second["balance_revision"])
        payload = {
            "employee_id": employee["id"],
            "expected_balance_minor": first["balance_minor"],
            "expected_balance_revision": first["balance_revision"],
            "idempotency_key": "synthetic-shift-reset",
            "_operator_session": {
                "username": "TEST",
                "permissions": [SALARY_BALANCE_RESET_PERMISSION],
            },
        }
        applied = self.service.reset_employee_salary_balance(payload)
        self.assertTrue(applied["meta"]["applied"])
        self.assertEqual(applied["ledger"]["balance_minor"], 0)
        self.assertEqual(
            applied["ledger"]["balance_revision"],
            self.service.get_employee_salary_ledger({"employee_id": employee["id"]})[
                "balance_revision"
            ],
        )
        self.assertTrue(self.service.reset_employee_salary_balance(payload)["meta"]["replayed"])

    def test_dated_shift_revision_preserves_all_sources_and_timezone(self) -> None:
        employee = self.service.save_employee(
            {"name": "Synthetic shift changes", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1200}
        )
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        shift = state["settings"]["employee_shift_accruals"][0]
        shift.pop("id")
        shift["created_at"] = "01.01.2024 10:00"
        state["settings"]["employee_shift_accruals"].append(dict(shift))
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "UTC"}):
            first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
            state["settings"]["employee_shift_accruals"][0]["amount_minor"] += 100
            state["settings"]["employee_shift_accruals"][1]["amount_minor"] -= 100
            self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            changed = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
            self.assertEqual(first["balance_minor"], changed["balance_minor"])
            self.assertNotEqual(first["balance_revision"], changed["balance_revision"])
            with self.assertRaises(ServiceError) as conflict:
                self.service.reset_employee_salary_balance(
                    {
                        "employee_id": employee["id"],
                        "expected_balance_minor": first["balance_minor"],
                        "expected_balance_revision": first["balance_revision"],
                        "idempotency_key": "synthetic-shift-stale",
                        "_operator_session": {
                            "username": "TEST",
                            "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                        },
                    }
                )
            self.assertEqual(conflict.exception.code, "salary_balance_reset_conflict")
        with patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"}):
            other_zone = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(changed["balance_minor"], other_zone["balance_minor"])
        self.assertNotEqual(changed["balance_revision"], other_zone["balance_revision"])

    def test_undated_shift_is_not_given_a_stable_revision(self) -> None:
        employee = self.service.save_employee({"name": "Synthetic undated", "salary_mode": "none"})[
            "employee"
        ]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1200}
        )
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        shift = state["settings"]["employee_shift_accruals"][0]
        dated_without_id = dict(shift)
        dated_without_id.pop("id")
        shift.pop("created_at")
        state["settings"]["employee_shift_accruals"].append(dated_without_id)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        second = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertNotEqual(first["balance_revision"], second["balance_revision"])
        with self.assertRaises(ServiceError) as conflict:
            self.service.reset_employee_salary_balance(
                {
                    "employee_id": employee["id"],
                    "expected_balance_minor": first["balance_minor"],
                    "expected_balance_revision": first["balance_revision"],
                    "idempotency_key": "synthetic-undated",
                    "_operator_session": {
                        "username": "TEST",
                        "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                    },
                }
            )
        self.assertEqual(conflict.exception.code, "salary_balance_reset_conflict")

    def test_legacy_revision_is_stable_and_reset_replays(self) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетический legacy", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 12345}
        )
        self._make_legacy_employee(employee["id"])
        first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        second = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(first["balance_revision"], second["balance_revision"])
        self.assertEqual(
            first["balance_revision"],
            self._build_service().get_employee_salary_ledger({"employee_id": employee["id"]})[
                "balance_revision"
            ],
        )
        payload = {
            "employee_id": employee["id"],
            "expected_balance_minor": first["balance_minor"],
            "expected_balance_revision": first["balance_revision"],
            "idempotency_key": "synthetic-legacy-reset",
            "_operator_session": {
                "username": "TEST",
                "permissions": [SALARY_BALANCE_RESET_PERMISSION],
            },
        }
        applied = self.service.reset_employee_salary_balance(payload)
        self.assertTrue(applied["meta"]["applied"])
        self.assertEqual(applied["ledger"]["balance_minor"], 0)
        self.assertEqual(
            applied["ledger"]["balance_revision"],
            self.service.get_employee_salary_ledger({"employee_id": employee["id"]})[
                "balance_revision"
            ],
        )
        self.assertTrue(self.service.reset_employee_salary_balance(payload)["meta"]["replayed"])

    def test_each_missing_legacy_field_has_a_stable_revision(self) -> None:
        for field in ("payroll_terms", "updated_at"):
            with self.subTest(field=field):
                employee = self.service.save_employee({"name": "Synthetic " + field})["employee"]
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                row = next(
                    item for item in state["settings"]["employees"] if item["id"] == employee["id"]
                )
                row.pop(field)
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
                second = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
                self.assertEqual(first["balance_revision"], second["balance_revision"])

    def test_legacy_revision_rejects_changed_conditions_and_same_balance_operations(self) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетический legacy conflict", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 2000}
        )
        cashbox = self.service.create_cashbox({"name": "Синтетическая касса"})["cashbox"]
        self._make_legacy_employee(employee["id"])
        old = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self._make_legacy_employee(employee["id"], work_percent="17")
        changed = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(old["balance_minor"], changed["balance_minor"])
        self.assertNotEqual(old["balance_revision"], changed["balance_revision"])
        session = {"username": "TEST", "permissions": [SALARY_BALANCE_RESET_PERMISSION]}
        payload = {
            "employee_id": employee["id"],
            "expected_balance_minor": old["balance_minor"],
            "expected_balance_revision": old["balance_revision"],
            "idempotency_key": "synthetic-legacy-conflict",
            "_operator_session": session,
        }
        with self.assertRaises(ServiceError) as conflict:
            self.service.reset_employee_salary_balance(payload)
        self.assertEqual(conflict.exception.code, "salary_balance_reset_conflict")
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 500}
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 500,
                "cashbox_id": cashbox["id"],
            }
        )
        current = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(changed["balance_minor"], current["balance_minor"])
        self.assertNotEqual(changed["balance_revision"], current["balance_revision"])
        payload["expected_balance_revision"] = changed["balance_revision"]
        with self.assertRaises(ServiceError) as conflict:
            self.service.reset_employee_salary_balance(payload)
        self.assertEqual(conflict.exception.code, "salary_balance_reset_conflict")

    def test_inactive_legacy_revision_tracks_a_new_weekly_accrual(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Синтетический inactive",
                "salary_mode": "salary_only",
                "base_salary": "1000",
            }
        )["employee"]
        self._make_legacy_employee(
            employee["id"],
            is_active=False,
            active_periods=[],
            created_at="2026-10-26T00:00:00+07:00",
        )
        before = datetime(2026, 10, 30, 19, 59, tzinfo=business_timezone())
        with (
            patch("minimal_kanban.models.utc_now", return_value=before),
            patch("minimal_kanban.models.utc_now_iso", return_value=before.isoformat()),
        ):
            first = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
            bundle = self.store.read_bundle()
            normalized = next(
                item
                for item in self.service._employees_from_settings(bundle["settings"])
                if item["id"] == employee["id"]
            )
            reference = self.service._build_employee_salary_ledger(
                bundle["cards"], bundle["cashboxes"], bundle["cash_transactions"], normalized
            )
            self.assertEqual(
                {key: value for key, value in first.items() if key != "balance_revision"},
                {key: value for key, value in reference.items() if key != "balance_revision"},
            )
        after = before + timedelta(minutes=2)
        with (
            patch("minimal_kanban.models.utc_now", return_value=after),
            patch("minimal_kanban.models.utc_now_iso", return_value=after.isoformat()),
        ):
            second = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertGreater(second["balance_minor"], first["balance_minor"])
        self.assertNotEqual(first["balance_revision"], second["balance_revision"])

        self._make_legacy_employee(
            employee["id"],
            active_periods=[{"start_at": "2026-10-26T00:00:00+07:00", "end_at": after.isoformat()}],
        )
        with (
            patch("minimal_kanban.models.utc_now", return_value=after),
            patch("minimal_kanban.models.utc_now_iso", return_value=after.isoformat()),
        ):
            explicit = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
            self._make_legacy_employee(
                employee["id"],
                active_periods=[
                    {
                        "start_at": "2026-10-26T00:00:00+07:00",
                        "end_at": (after + timedelta(minutes=1)).isoformat(),
                    }
                ],
            )
            changed_end = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(explicit["balance_minor"], changed_end["balance_minor"])
        self.assertNotEqual(explicit["balance_revision"], changed_end["balance_revision"])

    def test_legacy_revision_rejects_ambiguous_or_undated_employee(self) -> None:
        employee = self.service.save_employee({"name": "Синтетический ambiguous"})["employee"]
        self._make_legacy_employee(employee["id"])
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["settings"]["employees"].append(dict(state["settings"]["employees"][0]))
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(error.exception.code, "salary_balance_revision_unavailable")
        state["settings"]["employees"].pop()
        state["settings"]["employees"][0].pop("created_at", None)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(error.exception.code, "salary_balance_revision_unavailable")

    def test_employee_salary_balance_reset_is_immutable_idempotent_and_persistent(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Синтетический положительный баланс",
                "position": "Тест",
                "salary_mode": "none",
            }
        )["employee"]
        self.service.create_employee_shift_accrual(
            {
                "employee_id": employee["id"],
                "amount_minor": 12345,
                "note": "Синтетическое начисление для теста обнуления",
            }
        )
        ledger_before = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        before = self.store.read_bundle()
        before_cashboxes = [item.to_dict() for item in before["cashboxes"]]
        before_transactions = [item.to_dict() for item in before["cash_transactions"]]
        before_shift_accruals = json.dumps(
            before["settings"].get("employee_shift_accruals", []),
            ensure_ascii=False,
            sort_keys=True,
        )
        before_event_count = len(before["events"])
        payload = {
            "employee_id": employee["id"],
            "expected_balance_minor": 12345,
            "expected_balance_revision": ledger_before["balance_revision"],
            "idempotency_key": "salary-reset-positive-1",
            "source": "ui",
            "_operator_session": {
                "username": "UGA",
                "is_admin": True,
                "permissions": [SALARY_BALANCE_RESET_PERMISSION],
            },
        }

        applied = self.service.reset_employee_salary_balance(payload)

        self.assertTrue(applied["meta"]["applied"])
        self.assertFalse(applied["meta"]["replayed"])
        self.assertEqual(applied["balance_reset"]["amount_minor"], -12345)
        self.assertEqual(applied["balance_reset"]["balance_before_minor"], 12345)
        self.assertEqual(applied["balance_reset"]["balance_after_minor"], 0)
        self.assertEqual(applied["balance_reset"]["actor_name"], "UGA")
        self.assertEqual(applied["ledger"]["balance_minor"], 0)
        reset_row = next(
            row
            for row in applied["ledger"]["journal_rows"]
            if row["kind"] == "salary_balance_reset"
        )
        self.assertEqual(reset_row["kind_label"], "ОБНУЛЕНИЕ БАЛАНСА")
        self.assertEqual(reset_row["actor_name"], "UGA")
        self.assertEqual(reset_row["amount_minor"], -12345)

        after = self.store.read_bundle()
        self.assertEqual(before_cashboxes, [item.to_dict() for item in after["cashboxes"]])
        self.assertEqual(
            before_transactions,
            [item.to_dict() for item in after["cash_transactions"]],
        )
        self.assertEqual(
            before_shift_accruals,
            json.dumps(
                after["settings"].get("employee_shift_accruals", []),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        self.assertEqual(len(after["settings"].get("employee_salary_balance_resets", [])), 1)
        self.assertEqual(len(after["events"]), before_event_count + 1)
        self.assertEqual(after["events"][-1].action, "employee_salary_balance_reset")

        replayed = self.service.reset_employee_salary_balance(payload)
        self.assertTrue(replayed["meta"]["applied"])
        self.assertTrue(replayed["meta"]["replayed"])
        self.assertEqual(replayed["balance_reset"]["id"], applied["balance_reset"]["id"])
        replay_bundle = self.store.read_bundle()
        self.assertEqual(
            len(replay_bundle["settings"].get("employee_salary_balance_resets", [])), 1
        )
        self.assertEqual(len(replay_bundle["events"]), before_event_count + 1)

        fresh_service = self._build_service()
        persisted = fresh_service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(persisted["balance_minor"], 0)
        self.assertEqual(
            [
                row["balance_reset_id"]
                for row in persisted["journal_rows"]
                if row["kind"] == "salary_balance_reset"
            ],
            [applied["balance_reset"]["id"]],
        )
        listed_employee = next(
            item
            for item in fresh_service.list_employees()["employees"]
            if item["id"] == employee["id"]
        )
        self.assertEqual(listed_employee["balance_total"], "0")
        reconciliation = fresh_service.get_employee_salary_reconciliation(
            {"employee_id": employee["id"], "days": 30}
        )
        self.assertEqual(reconciliation["totals"]["adjustment_total_minor"], -12345)
        self.assertEqual(reconciliation["totals"]["amount_due_total_minor"], 0)
        self.assertTrue(
            any(row["kind"] == "salary_balance_reset" for row in reconciliation["rows"])
        )
        with self.assertRaises(ServiceError) as delete_blocked:
            fresh_service.delete_employee({"employee_id": employee["id"]})
        self.assertEqual(delete_blocked.exception.details["usage"]["salary_balance_resets"], 1)

    def test_employee_salary_balance_reset_handles_negative_stale_zero_and_permissions(
        self,
    ) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетический отрицательный баланс", "salary_mode": "none"}
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Синтетическая касса"})["cashbox"]
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount_minor": 5000,
                "cashbox_id": cashbox["id"],
            }
        )
        before = self.store.read_bundle()
        before_transactions = [item.to_dict() for item in before["cash_transactions"]]
        before_cashboxes = [item.to_dict() for item in before["cashboxes"]]
        base_payload = {
            "employee_id": employee["id"],
            "expected_balance_minor": -5000,
            "expected_balance_revision": self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"]}
            )["balance_revision"],
            "idempotency_key": "salary-reset-negative-1",
            "source": "ui",
        }

        for session in (
            None,
            {"username": "AUTOSTOP_SMOKE", "is_admin": True, "permissions": []},
            {"username": "OTHER", "is_admin": False, "permissions": []},
        ):
            with self.subTest(session=session), self.assertRaises(ServiceError) as denied:
                self.service.reset_employee_salary_balance(
                    {**base_payload, "_operator_session": session}
                )
            self.assertEqual(denied.exception.code, "forbidden")

        allowed_session = {
            "username": "MARIA",
            "is_admin": False,
            "permissions": [SALARY_BALANCE_RESET_PERMISSION],
        }
        with self.assertRaises(ServiceError) as stale:
            self.service.reset_employee_salary_balance(
                {
                    **base_payload,
                    "expected_balance_minor": -4000,
                    "_operator_session": allowed_session,
                }
            )
        self.assertEqual(stale.exception.code, "salary_balance_reset_conflict")
        self.assertEqual(stale.exception.details["current_balance_minor"], -5000)

        applied = self.service.reset_employee_salary_balance(
            {**base_payload, "_operator_session": allowed_session}
        )
        self.assertEqual(applied["balance_reset"]["amount_minor"], 5000)
        self.assertEqual(applied["ledger"]["balance_minor"], 0)
        after = self.store.read_bundle()
        self.assertEqual(
            before_transactions, [item.to_dict() for item in after["cash_transactions"]]
        )
        self.assertEqual(before_cashboxes, [item.to_dict() for item in after["cashboxes"]])

        zero_employee = self.service.save_employee(
            {"name": "Синтетический нулевой баланс", "salary_mode": "none"}
        )["employee"]
        zero_before = self.store.read_bundle()
        zero_ledger = self.service.get_employee_salary_ledger({"employee_id": zero_employee["id"]})
        zero_result = self.service.reset_employee_salary_balance(
            {
                "employee_id": zero_employee["id"],
                "expected_balance_minor": 0,
                "expected_balance_revision": zero_ledger["balance_revision"],
                "idempotency_key": "salary-reset-zero-1",
                "_operator_session": allowed_session,
            }
        )
        self.assertFalse(zero_result["meta"]["applied"])
        self.assertIsNone(zero_result["balance_reset"])
        zero_after = self.store.read_bundle()
        self.assertEqual(
            zero_before["settings"].get("employee_salary_balance_resets", []),
            zero_after["settings"].get("employee_salary_balance_resets", []),
        )
        self.assertEqual(len(zero_before["events"]), len(zero_after["events"]))

        with self.assertRaises(ServiceError) as reused:
            self.service.reset_employee_salary_balance(
                {
                    "employee_id": zero_employee["id"],
                    "expected_balance_minor": 0,
                    "expected_balance_revision": zero_ledger["balance_revision"],
                    "idempotency_key": "salary-reset-negative-1",
                    "_operator_session": allowed_session,
                }
            )
        self.assertEqual(reused.exception.code, "salary_balance_reset_idempotency_conflict")

    def test_employee_salary_balance_reset_rejects_same_balance_with_changed_sources(
        self,
    ) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетическая stale revision", "salary_mode": "none"}
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Синтетическая stale касса"})["cashbox"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 2000}
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 1000,
                "cashbox_id": cashbox["id"],
            }
        )
        stale_ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(stale_ledger["balance_minor"], 1000)

        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 500}
        )
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount_minor": 500,
                "cashbox_id": cashbox["id"],
            }
        )
        current_ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(current_ledger["balance_minor"], stale_ledger["balance_minor"])
        self.assertNotEqual(current_ledger["balance_revision"], stale_ledger["balance_revision"])

        with self.assertRaises(ServiceError) as conflict:
            self.service.reset_employee_salary_balance(
                {
                    "employee_id": employee["id"],
                    "expected_balance_minor": stale_ledger["balance_minor"],
                    "expected_balance_revision": stale_ledger["balance_revision"],
                    "idempotency_key": "salary-reset-stale-same-balance",
                    "_operator_session": {
                        "username": "UGA",
                        "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                    },
                }
            )
        self.assertEqual(conflict.exception.code, "salary_balance_reset_conflict")
        self.assertEqual(
            self.store.read_bundle()["settings"].get("employee_salary_balance_resets", []),
            [],
        )

    def test_employee_salary_balance_reset_compare_and_swap_allows_one_commit(self) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетическая CAS гонка", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 7000}
        )
        store_a = JsonStore(state_file=self.state_file, logger=self.logger)
        store_b = JsonStore(state_file=self.state_file, logger=self.logger)
        service_a = CardService(store_a, self.logger)
        service_b = CardService(store_b, self.logger)
        ledger_a = service_a.get_employee_salary_ledger({"employee_id": employee["id"]})
        ledger_b = service_b.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger_a["balance_revision"], ledger_b["balance_revision"])
        session = {
            "username": "UGA",
            "permissions": [SALARY_BALANCE_RESET_PERMISSION],
        }
        payload_a = {
            "employee_id": employee["id"],
            "expected_balance_minor": 7000,
            "expected_balance_revision": ledger_a["balance_revision"],
            "idempotency_key": "salary-reset-cas-a",
            "_operator_session": session,
        }
        payload_b = {
            **payload_a,
            "expected_balance_revision": ledger_b["balance_revision"],
            "idempotency_key": "salary-reset-cas-b",
        }
        original_write = store_a.write_cached_bundle
        competing_applied: dict[str, object] = {}

        def write_after_competitor(source_bundle, **write_arguments):
            competing_applied.update(service_b.reset_employee_salary_balance(payload_b))
            return original_write(source_bundle, **write_arguments)

        with patch.object(
            store_a,
            "write_cached_bundle",
            side_effect=write_after_competitor,
        ):
            with self.assertRaises(ServiceError) as conflict:
                service_a.reset_employee_salary_balance(payload_a)

        self.assertEqual(conflict.exception.code, "state_write_conflict")
        self.assertEqual(competing_applied["ledger"]["balance_minor"], 0)
        persisted = JsonStore(state_file=self.state_file, logger=self.logger).read_bundle()
        resets = persisted["settings"].get("employee_salary_balance_resets", [])
        self.assertEqual([item["idempotency_key"] for item in resets], ["salary-reset-cas-b"])
        self.assertEqual(
            sum(event.action == "employee_salary_balance_reset" for event in persisted["events"]),
            1,
        )

    def test_failed_reset_does_not_leak_audit_event_into_a_later_save(self) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетическая изоляция сбоя", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 4200}
        )
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        retained = self.store.read_bundle()
        event_ids_before = [event.id for event in retained["events"]]

        with (
            patch.object(
                self.service,
                "_prepare_repair_order_artifacts",
                side_effect=OSError("injected before storage"),
            ),
            self.assertRaisesRegex(OSError, "injected before storage"),
        ):
            self.service.reset_employee_salary_balance(
                {
                    "employee_id": employee["id"],
                    "expected_balance_minor": ledger["balance_minor"],
                    "expected_balance_revision": ledger["balance_revision"],
                    "idempotency_key": "salary-reset-failed-isolation",
                    "_operator_session": {
                        "username": "UGA",
                        "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                    },
                }
            )

        self.assertEqual([event.id for event in retained["events"]], event_ids_before)
        self.assertFalse(
            any(event.action == "employee_salary_balance_reset" for event in retained["events"])
        )
        self.assertEqual(
            retained["settings"].get("employee_salary_balance_resets", []),
            [],
        )

        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 100}
        )
        persisted = JsonStore(state_file=self.state_file, logger=self.logger).read_bundle()
        self.assertFalse(
            any(event.action == "employee_salary_balance_reset" for event in persisted["events"])
        )
        self.assertEqual(
            persisted["settings"].get("employee_salary_balance_resets", []),
            [],
        )

    def test_employee_salary_balance_reset_history_fails_closed_when_duplicated(self) -> None:
        employee = self.service.save_employee(
            {"name": "Синтетическая повреждённая история", "salary_mode": "none"}
        )["employee"]
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount_minor": 1000}
        )
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.service.reset_employee_salary_balance(
            {
                "employee_id": employee["id"],
                "expected_balance_minor": 1000,
                "expected_balance_revision": ledger["balance_revision"],
                "idempotency_key": "salary-reset-history-valid",
                "_operator_session": {
                    "username": "UGA",
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                },
            }
        )
        bundle = self.store.read_bundle()
        resets = bundle["settings"]["employee_salary_balance_resets"]
        bundle["settings"]["employee_salary_balance_resets"] = [
            resets[0],
            dict(resets[0]),
        ]
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

        fresh_service = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger),
            self.logger,
        )
        with self.assertRaises(ServiceError) as invalid_history:
            fresh_service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(
            invalid_history.exception.code,
            "salary_balance_reset_history_invalid",
        )

    def test_attestation_cleanup_cannot_orphan_salary_balance_reset(self) -> None:
        run_id = "AST-GWAT-20260828T130000Z"
        actor_name = "codex-owner-agent"
        employee = self.service.save_employee(
            {
                "name": f"{run_id}-salary-reset-cleanup",
                "position": "Synthetic",
                "salary_mode": "none",
                "actor_name": actor_name,
            }
        )["employee"]
        accrual = self.service.create_employee_shift_accrual(
            {
                "employee_id": employee["id"],
                "amount_minor": 100,
                "note": f"{run_id} cleanup shift accrual",
                "expected_employee_updated_at": employee["updated_at"],
                "attestation_run_id": run_id,
                "source": "mcp_agent_gateway_v2",
                "actor_name": actor_name,
            }
        )["accrual"]
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.service.reset_employee_salary_balance(
            {
                "employee_id": employee["id"],
                "expected_balance_minor": 100,
                "expected_balance_revision": ledger["balance_revision"],
                "idempotency_key": "salary-reset-attestation-cleanup-guard",
                "_operator_session": {
                    "username": "UGA",
                    "permissions": [SALARY_BALANCE_RESET_PERMISSION],
                },
            }
        )

        with self.assertRaises(ServiceError) as blocked:
            self.service.delete_employee(
                {
                    "employee_id": employee["id"],
                    "expected_updated_at": employee["updated_at"],
                    "attestation_run_id": run_id,
                    "attestation_cleanup_shift_accrual_ids": [accrual["id"]],
                    "source": "mcp_agent_gateway_v2",
                    "actor_name": actor_name,
                }
            )

        self.assertEqual(blocked.exception.code, "gateway_attestation_shift_cleanup_scope_invalid")
        persisted = self.store.read_bundle()
        self.assertTrue(
            any(item["id"] == employee["id"] for item in self.service.list_employees()["employees"])
        )
        self.assertEqual(
            [
                item["idempotency_key"]
                for item in persisted["settings"].get("employee_salary_balance_resets", [])
            ],
            ["salary-reset-attestation-cleanup-guard"],
        )
        self.assertIn(
            accrual["id"],
            {item["id"] for item in persisted["settings"].get("employee_shift_accruals", [])},
        )
