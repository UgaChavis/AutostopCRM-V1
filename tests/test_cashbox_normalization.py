from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import normalize_cashboxes_after_safe_fix as normalization_module
from normalize_cashboxes_after_safe_fix import (
    NORMALIZATION_KIND,
    NORMALIZATION_NOTE,
    TARGET_EVENT_ID,
    calculate_normalization_plan,
    run_normalization,
)

from minimal_kanban.storage.json_store import DEFAULT_STATE


def _minor(rubles: int) -> int:
    return rubles * 100


def _cashbox(cashbox_id: str, name: str, order: int) -> dict[str, object]:
    return {
        "id": cashbox_id,
        "name": name,
        "order": order,
        "created_at": "2026-04-01T00:00:00+00:00",
        "updated_at": "2026-04-01T00:00:00+00:00",
    }


def _transaction(
    transaction_id: str,
    cashbox_id: str,
    amount_minor: int,
    *,
    direction: str = "income",
    created_at: str = "2026-04-14T12:00:00+07:00",
    note: str = "Заказ-наряд",
    kind: str = "repair_order_payment",
) -> dict[str, object]:
    return {
        "id": transaction_id,
        "cashbox_id": cashbox_id,
        "direction": direction,
        "amount_minor": amount_minor,
        "note": note,
        "created_at": created_at,
        "actor_name": "ADMIN",
        "source": "api",
        "transaction_kind": kind,
        "transfer_group_id": "",
        "related_transaction_id": "",
    }


class CashboxNormalizationTests(unittest.TestCase):
    def test_money_minor_rejects_bool_fractional_and_non_finite_values(self) -> None:
        self.assertEqual(normalization_module._money_minor(True), 0)
        self.assertEqual(normalization_module._money_minor(False), 0)
        self.assertEqual(normalization_module._money_minor(1.5), 0)
        self.assertEqual(normalization_module._money_minor(float("inf")), 0)
        self.assertEqual(normalization_module._money_minor(1e308), 0)
        self.assertEqual(normalization_module._money_minor(""), 0)
        self.assertEqual(normalization_module._money_minor("12500"), 12500)

    def test_money_display_falls_back_for_invalid_values(self) -> None:
        self.assertEqual(normalization_module._money_display(float("inf")), "0,00 ₽")
        self.assertEqual(normalization_module._money_display(1e308), "0,00 ₽")
        self.assertEqual(normalization_module._money_display("broken"), "0,00 ₽")

    def test_expected_source_count_bounds_reject_huge_values(self) -> None:
        self.assertEqual(normalization_module._bounded_expected_source_count(1e308), 10_000)
        self.assertEqual(normalization_module._bounded_expected_source_count(-1e308), 0)
        self.assertEqual(normalization_module._bounded_expected_source_count("bad"), 16)

    def test_read_json_rejects_deeply_nested_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
                normalization_module._read_json(state_file)

    def _state(self, *, include_existing_correction: bool = True) -> dict[str, object]:
        state = copy.deepcopy(DEFAULT_STATE)
        cashboxes = [
            _cashbox("cash", "Наличный", 0),
            _cashbox("cashless", "Безналичный", 1),
            _cashbox("maria", "Карта Мария", 2),
            _cashbox("alexey", "Алексей Снаб", 3),
            _cashbox("ivan", "Иван Снаб", 4),
        ]
        source_rows = [
            ("cash-63", "cash", 9855, "63"),
            ("cash-66", "cash", 4500, "66"),
            ("cash-71", "cash", 16500, "71"),
            ("cashless-67", "cashless", 20700, "67"),
            ("maria-88", "maria", 14120, "88"),
            ("maria-84", "maria", 1690, "84"),
            ("maria-69", "maria", 500, "69"),
            ("maria-71", "maria", 2500, "71"),
            ("alexey-68", "alexey", 10600, "68"),
            ("alexey-77", "alexey", 6000, "77"),
            ("alexey-70", "alexey", 16020, "70"),
            ("alexey-83", "alexey", 1000, "83"),
            ("alexey-73", "alexey", 12425, "73"),
            ("alexey-84", "alexey", 27000, "84"),
            ("alexey-72", "alexey", 7500, "72"),
            ("alexey-69", "alexey", 5000, "69"),
        ]
        transactions = [
            _transaction(tx_id, cashbox_id, _minor(amount), note=f"Заказ-наряд №{order}")
            for tx_id, cashbox_id, amount, order in source_rows
        ]
        if include_existing_correction:
            transactions.append(
                _transaction(
                    "alexey-existing-correction",
                    "alexey",
                    _minor(85545),
                    direction="expense",
                    created_at="2026-05-30T10:57:53+07:00",
                    note="корректировка кассы",
                    kind="",
                )
            )
        state["cashboxes"] = cashboxes
        state["cash_transactions"] = transactions
        state["events"] = [
            {
                "id": TARGET_EVENT_ID,
                "timestamp": "2026-05-29T13:37:37.687577+00:00",
                "actor_name": "CODEX",
                "source": "api",
                "action": "finance_audit_safe_fix_applied",
                "message": "CODEX применил безопасные правки финансовой сверки",
                "details": {
                    "count": 16,
                    "applied": [
                        {
                            "kind": "create_missing_payment_cash_transaction",
                            "card_id": f"card-{tx_id}",
                            "repair_order_number": order,
                            "repair_order_payment_id": f"payment-{tx_id}",
                            "cash_transaction_id": tx_id,
                            "cashbox_id": cashbox_id,
                            "amount_minor": _minor(amount),
                        }
                        for tx_id, cashbox_id, amount, order in source_rows
                    ],
                },
                "card_id": None,
            }
        ]
        return state

    def test_dry_run_offsets_existing_manual_correction(self) -> None:
        plan = calculate_normalization_plan(self._state())
        adjustments = {item["cashbox_name"]: item["amount_minor"] for item in plan["adjustments"]}
        totals = {item["cashbox_name"]: item for item in plan["totals_by_cashbox"]}

        self.assertEqual(plan["summary"]["source_transactions"], 16)
        self.assertEqual(plan["summary"]["source_amount_minor"], _minor(155910))
        self.assertEqual(plan["summary"]["existing_correction_minor"], _minor(85545))
        self.assertEqual(plan["summary"]["proposed_adjustment_minor"], _minor(70365))
        self.assertEqual(adjustments["Наличный"], _minor(30855))
        self.assertEqual(adjustments["Безналичный"], _minor(20700))
        self.assertEqual(adjustments["Карта Мария"], _minor(18810))
        self.assertNotIn("Алексей Снаб", adjustments)
        self.assertEqual(totals["Алексей Снаб"]["existing_correction_minor"], _minor(85545))

    def test_state_reader_rejects_nonstandard_json_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text('{"cash_transactions":[{"amount_minor":NaN}]}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant"):
                run_normalization(
                    state_file=state_file,
                    expected_source_count=None,
                )

    def test_state_reader_rejects_oversized_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("x" * 16, encoding="utf-8")

            with patch.object(normalization_module, "STATE_FILE_MAX_BYTES", 8):
                with self.assertRaisesRegex(
                    ValueError, "cashbox normalization state file is too large"
                ):
                    run_normalization(
                        state_file=state_file,
                        expected_source_count=None,
                    )

    def test_archive_reader_skips_nonstandard_json_constant_lines(self) -> None:
        state = self._state()
        archived_event = state["events"].pop()
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_dir = Path(temp_dir) / "audit-archive"
            archive_dir.mkdir()
            (archive_dir / "2026-05.jsonl").write_text(
                '{"event":{"id":"bad","details":{"score":NaN}}}\n'
                + json.dumps({"event": archived_event}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            plan = calculate_normalization_plan(
                state,
                archive_dir=archive_dir,
                expected_source_count=16,
            )

        self.assertEqual(plan["event"]["id"], TARGET_EVENT_ID)
        self.assertEqual(plan["summary"]["source_transactions"], 16)

    def test_archive_reader_skips_oversized_and_bad_utf8_lines(self) -> None:
        state = self._state()
        archived_event = state["events"].pop()
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_dir = Path(temp_dir) / "audit-archive"
            archive_dir.mkdir()
            valid_line = json.dumps({"event": archived_event}, ensure_ascii=False).encode("utf-8")
            line_limit = len(valid_line) + 128
            (archive_dir / "2026-05.jsonl").write_bytes(
                b'{"event":{"id":"oversized","details":{"payload":"'
                + (b"x" * (line_limit + 256))
                + b'"}}\n'
                + b"\xff\xfe\x00\n"
                + valid_line
                + b"\n"
            )

            with patch.object(normalization_module, "ARCHIVE_EVENT_LINE_MAX_BYTES", line_limit):
                plan = calculate_normalization_plan(
                    state,
                    archive_dir=archive_dir,
                    expected_source_count=16,
                )

        self.assertEqual(plan["event"]["id"], TARGET_EVENT_ID)
        self.assertEqual(plan["summary"]["source_transactions"], 16)

    def test_apply_creates_backup_and_three_normalization_expenses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text(json.dumps(self._state(), ensure_ascii=False), encoding="utf-8")

            result = run_normalization(state_file=state_file, apply=True, backup=True)

            self.assertFalse(result["dry_run"])
            self.assertTrue(Path(result["backup"]["path"]).exists())
            self.assertEqual(len(result["backup"]["sha256"]), 64)
            self.assertEqual(len(result["created_transactions"]), 3)
            next_plan = result["post_apply_plan"]
            self.assertEqual(next_plan["summary"]["proposed_adjustment_minor"], 0)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            created = [
                item
                for item in state["cash_transactions"]
                if item.get("transaction_kind") == NORMALIZATION_KIND
            ]
            self.assertEqual(len(created), 3)
            self.assertEqual({item["note"] for item in created}, {NORMALIZATION_NOTE})
            self.assertEqual(sum(int(item["amount_minor"]) for item in created), _minor(70365))

    def test_apply_backup_does_not_overwrite_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text(json.dumps(self._state(), ensure_ascii=False), encoding="utf-8")
            backup_dir = state_file.parent / "backups"
            backup_dir.mkdir()
            existing_backup = (
                backup_dir / "state-before-cashbox-normalization-20260601T010203Z.json"
            )
            existing_backup.write_text("previous backup", encoding="utf-8")

            with (
                patch.object(
                    normalization_module.time,
                    "strftime",
                    return_value="20260601T010203Z",
                ),
                patch.object(normalization_module.time, "gmtime", return_value=object()),
            ):
                result = run_normalization(state_file=state_file, apply=True, backup=True)

            backup_file = (
                backup_dir / "state-before-cashbox-normalization-20260601T010203Z-002.json"
            )
            self.assertEqual(existing_backup.read_text(encoding="utf-8"), "previous backup")
            self.assertEqual(Path(result["backup"]["path"]), backup_file)
            self.assertTrue(backup_file.exists())

    def test_apply_post_plan_survives_source_event_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text(
                json.dumps(self._state(), ensure_ascii=False),
                encoding="utf-8",
            )

            with patch(
                "minimal_kanban.storage.json_store.utc_now",
                return_value=datetime(2027, 1, 1, tzinfo=UTC),
            ):
                result = run_normalization(
                    state_file=state_file,
                    apply=True,
                    backup=True,
                )

            stored_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertFalse(
                any(item.get("id") == TARGET_EVENT_ID for item in stored_state.get("events", []))
            )
            self.assertEqual(
                result["post_apply_plan"]["summary"]["proposed_adjustment_minor"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
