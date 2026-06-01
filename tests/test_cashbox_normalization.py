from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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


if __name__ == "__main__":
    unittest.main()
