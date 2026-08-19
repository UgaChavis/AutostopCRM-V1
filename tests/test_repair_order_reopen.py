from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderReopenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.logger = logging.getLogger(f"test.repair-order-reopen.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.store = JsonStore(
            state_file=Path(self.temp_dir.name) / "state.json", logger=self.logger
        )
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _employee(self, name: str) -> dict:
        return self.service.save_employee(
            {
                "name": name,
                "salary_mode": "percent_only",
                "work_percent": "50",
                "material_percent": "0",
                "repair_order_percent": "0",
            }
        )["employee"]

    def _stored_order(self, card_id: str):
        return next(
            card.repair_order for card in self.store.read_bundle()["cards"] if card.id == card_id
        )

    def _closed_order(self, employee_id: str) -> dict:
        cashbox = self.service.create_cashbox({"name": "Наличные", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Toyota", "title": "Корректировка ЗН", "deadline": {"hours": 2}}
        )["card"]
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
                            "cashbox_id": cashbox["id"],
                            "paid_at": "19.08.2026 12:00",
                        }
                    ],
                },
            }
        )
        current = self.service.get_card({"card_id": card["id"]})["card"]
        return self.service.set_repair_order_status(
            {
                "card_id": card["id"],
                "status": "closed",
                "expected_updated_at": current["updated_at"],
                "actor_name": "ADMIN",
            }
        )["card"]

    def test_reopen_change_executor_and_reclose_is_financially_stable(self) -> None:
        first = self._employee("Иванов")
        second = self._employee("Петров")
        closed = self._closed_order(first["id"])
        card_id = closed["id"]
        first_cycle = self.service.get_repair_order_cycles({"card_id": card_id})["cycles"][0]
        cash_before = self.store.read_bundle()["cash_transactions"]
        cash_ids_before = [item.id for item in cash_before]
        closed_week = next(
            item for item in self.service.get_display_dashboard()["weeks"] if item["is_current"]
        )
        self.assertEqual(closed_week["amount"], "1000")

        preview = self.service.preview_repair_order_reopen(
            {"card_id": card_id, "expected_updated_at": closed["updated_at"]}
        )
        self.assertEqual(preview["payroll_reversals"][0]["amount_minor"], 50000)
        reopened = self.service.reopen_repair_order(
            {
                "card_id": card_id,
                "expected_updated_at": closed["updated_at"],
                "reason_code": "executor_error",
                "reason_note": "Исполнитель выбран ошибочно",
                "idempotency_key": "reopen-1",
                "actor_name": "ADMIN",
            }
        )["card"]
        replay = self.service.reopen_repair_order(
            {
                "card_id": card_id,
                "expected_updated_at": closed["updated_at"],
                "reason_code": "executor_error",
                "reason_note": "Исполнитель выбран ошибочно",
                "idempotency_key": "reopen-1",
                "actor_name": "ADMIN",
            }
        )["card"]
        self.assertEqual(
            replay["repair_order"]["active_correction"]["id"],
            reopened["repair_order"]["active_correction"]["id"],
        )
        self.assertEqual(reopened["repair_order"]["status"], "open")
        correction_week = next(
            item for item in self.service.get_display_dashboard()["weeks"] if item["is_current"]
        )
        self.assertEqual(correction_week["amount"], "0")
        self.assertEqual(
            [item.id for item in self.store.read_bundle()["cash_transactions"]],
            cash_ids_before,
        )

        with self.assertRaises(ServiceError) as payment_error:
            self.service.update_repair_order(
                {
                    "card_id": card_id,
                    "expected_updated_at": reopened["updated_at"],
                    "repair_order": {"payments": []},
                }
            )
        self.assertEqual(payment_error.exception.code, "repair_order_payment_locked")

        work = dict(reopened["repair_order"]["works"][0])
        work["executor_id"] = second["id"]
        work["executor_name"] = second["name"]
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "expected_updated_at": reopened["updated_at"],
                "repair_order": {"works": [work]},
            }
        )["card"]
        reclosed = self.service.set_repair_order_status(
            {
                "card_id": card_id,
                "status": "closed",
                "expected_updated_at": updated["updated_at"],
                "idempotency_key": "close-2",
                "actor_name": "ADMIN",
            }
        )["card"]
        replayed_close = self.service.set_repair_order_status(
            {
                "card_id": card_id,
                "status": "closed",
                "expected_updated_at": updated["updated_at"],
                "idempotency_key": "close-2",
                "actor_name": "ADMIN",
            }
        )
        self.assertTrue(replayed_close["meta"]["idempotent_replay"])
        self.assertEqual(replayed_close["card"]["updated_at"], reclosed["updated_at"])
        order = reclosed["repair_order"]
        self.assertEqual(order["cycle_count"], 2)
        cycles = self.service.get_repair_order_cycles({"card_id": card_id})["cycles"]
        self.assertEqual(cycles[1]["recognized_at"], first_cycle["recognized_at"])
        self.assertFalse(order["correction_active"])
        reclosed_week = next(
            item for item in self.service.get_display_dashboard()["weeks"] if item["is_current"]
        )
        self.assertEqual(reclosed_week["amount"], "1000")
        self.assertEqual(
            [item.id for item in self.store.read_bundle()["cash_transactions"]],
            cash_ids_before,
        )
        active = self.service._active_line_payroll_postings(
            self._stored_order(card_id).payroll_postings
        )
        self.assertEqual({item["employee_id"] for item in active}, {second["id"]})
        first_ledger = self.service.get_employee_salary_ledger({"employee_id": first["id"]})
        second_ledger = self.service.get_employee_salary_ledger({"employee_id": second["id"]})
        self.assertEqual(first_ledger["accrued_total"], "0")
        self.assertEqual(second_ledger["accrued_total"], "500")

    def test_closed_order_requires_semantic_reopen(self) -> None:
        employee = self._employee("Исполнитель")
        closed = self._closed_order(employee["id"])
        with self.assertRaises(ServiceError) as update_error:
            self.service.update_repair_order(
                {"card_id": closed["id"], "repair_order": {"comment": "Нельзя"}}
            )
        self.assertEqual(update_error.exception.code, "repair_order_closed_read_only")
        with self.assertRaises(ServiceError) as status_error:
            self.service.set_repair_order_status({"card_id": closed["id"], "status": "open"})
        self.assertEqual(status_error.exception.code, "repair_order_closed_read_only")

    def test_archived_order_is_restored_and_reopened_atomically(self) -> None:
        employee = self._employee("Архивный мастер")
        closed = self._closed_order(employee["id"])
        archived = self.service.archive_card({"card_id": closed["id"], "actor_name": "ADMIN"})[
            "card"
        ]
        self.assertTrue(archived["archived"])
        reopened = self.service.reopen_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": archived["updated_at"],
                "reason_code": "other",
                "reason_note": "Исправление архивного заказ-наряда",
                "target_column_id": "inbox",
                "idempotency_key": "archive-reopen-1",
                "actor_name": "ADMIN",
            }
        )["card"]
        self.assertFalse(reopened["archived"])
        self.assertEqual(reopened["column"], "inbox")
        self.assertEqual(reopened["repair_order"]["status"], "open")

    def test_legacy_cycle_migration_has_exact_line_payroll_parity(self) -> None:
        employee = self._employee("Миграционный мастер")
        overall_employee = self.service.save_employee(
            {
                "name": "Миграционный процент",
                "salary_mode": "percent_only",
                "repair_order_percent": "4",
            }
        )["employee"]
        closed = self._closed_order(employee["id"])
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == closed["id"])
        card.repair_order.cycles = []
        card.repair_order.payroll_postings = []
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
        first_virtual_cycle = self.service.get_repair_order_cycles({"card_id": closed["id"]})[
            "cycles"
        ][0]
        second_virtual_cycle = self.service.get_repair_order_cycles({"card_id": closed["id"]})[
            "cycles"
        ][0]
        self.assertEqual(first_virtual_cycle["id"], second_virtual_cycle["id"])
        dry_run = self.service.migrate_repair_order_cycles(apply=False)
        self.assertEqual(dry_run["migrated_count"], 1)
        self.assertTrue(dry_run["parity"]["payroll_exact"])
        self.assertTrue(dry_run["parity"]["revenue_exact"])
        self.assertEqual(
            dry_run["parity"]["old_revenue_by_week"],
            dry_run["parity"]["new_revenue_by_week"],
        )
        applied = self.service.migrate_repair_order_cycles(apply=True)
        self.assertEqual(applied["migrated_count"], 1)
        migrated = self.service.get_repair_order_cycles({"card_id": closed["id"]})
        self.assertEqual(len(migrated["cycles"]), 1)
        self.assertEqual(migrated["cycles"][0]["id"], first_virtual_cycle["id"])
        migrated_order = self._stored_order(closed["id"])
        self.assertEqual(
            {item["posting_type"] for item in migrated_order.payroll_postings},
            {"work", "repair_order"},
        )
        self.assertIn(overall_employee["id"], applied["parity"]["new_payroll_minor"])

    def test_linked_inventory_material_cannot_change_during_correction(self) -> None:
        item = self.service.save_inventory_item(
            {
                "name": "Масло 5W-30",
                "unit": "л",
                "quantity": "10",
                "cost_price": "500",
                "sale_price": "800",
            }
        )["item"]
        card = self.service.create_card({"vehicle": "BMW", "title": "Складская корректировка"})[
            "card"
        ]
        written_off = self.service.write_off_inventory_item(
            {"item_id": item["id"], "card_id": card["id"], "quantity": "2"}
        )
        self.service.update_repair_order(
            {
                "card_id": card["id"],
                "repair_order": {"payments": [{"amount": "1600", "paid_at": "19.08.2026 12:00"}]},
            }
        )
        current = self.service.get_card({"card_id": card["id"]})["card"]
        closed = self.service.set_repair_order_status(
            {
                "card_id": card["id"],
                "status": "closed",
                "expected_updated_at": current["updated_at"],
            }
        )["card"]
        stock_before = self.service.get_inventory_item({"item_id": item["id"]})["item"]
        reopened = self.service.reopen_repair_order(
            {
                "card_id": card["id"],
                "expected_updated_at": closed["updated_at"],
                "reason_code": "material_error",
                "reason_note": "Проверка складской блокировки",
                "idempotency_key": "inventory-reopen",
            }
        )["card"]
        material = dict(reopened["repair_order"]["materials"][0])
        self.assertEqual(material["inventory_movement_id"], written_off["movement"]["id"])
        material["quantity"] = "1"
        with self.assertRaises(ServiceError) as blocked:
            self.service.update_repair_order(
                {
                    "card_id": card["id"],
                    "expected_updated_at": reopened["updated_at"],
                    "repair_order": {"materials": [material]},
                }
            )
        self.assertEqual(blocked.exception.code, "inventory_material_movement_active")
        stock_after = self.service.get_inventory_item({"item_id": item["id"]})["item"]
        self.assertEqual(stock_after["quantity"], stock_before["quantity"])

    def test_stale_revision_and_failed_reclose_leave_correction_intact(self) -> None:
        employee = self._employee("Конкурентный мастер")
        closed = self._closed_order(employee["id"])
        with self.assertRaises(ServiceError) as stale:
            self.service.reopen_repair_order(
                {
                    "card_id": closed["id"],
                    "expected_updated_at": "stale-revision",
                    "reason_code": "other",
                    "reason_note": "Устаревшая версия",
                    "idempotency_key": "stale-reopen",
                }
            )
        self.assertEqual(stale.exception.code, "repair_order_revision_conflict")
        unchanged = self.service.get_card({"card_id": closed["id"]})["card"]
        self.assertEqual(unchanged["repair_order"]["status"], "closed")
        reopened = self.service.reopen_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": unchanged["updated_at"],
                "reason_code": "amount_error",
                "reason_note": "Сумма должна быть исправлена",
                "idempotency_key": "failed-reclose-reopen",
            }
        )["card"]
        work = dict(reopened["repair_order"]["works"][0])
        work["price"] = "2000"
        updated = self.service.update_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": reopened["updated_at"],
                "repair_order": {"works": [work]},
            }
        )["card"]
        with self.assertRaises(ServiceError) as unpaid:
            self.service.set_repair_order_status(
                {
                    "card_id": closed["id"],
                    "status": "closed",
                    "expected_updated_at": updated["updated_at"],
                    "idempotency_key": "failed-reclose",
                }
            )
        self.assertEqual(unpaid.exception.code, "repair_order_payment_required")
        still_open = self.service.get_card({"card_id": closed["id"]})["card"]
        self.assertEqual(still_open["repair_order"]["status"], "open")
        self.assertTrue(still_open["repair_order"]["correction_active"])
        self.assertEqual(still_open["repair_order"]["cycle_count"], 1)

    def test_public_order_hides_financial_journals_and_new_row_ids_are_unique(self) -> None:
        employee = self._employee("Мастер UUID")
        closed = self._closed_order(employee["id"])
        public_order = closed["repair_order"]
        self.assertNotIn("cycles", public_order)
        self.assertNotIn("payroll_postings", public_order)
        reopened = self.service.reopen_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": closed["updated_at"],
                "reason_code": "work_error",
                "reason_note": "Проверка стабильных строк",
                "idempotency_key": "uuid-reopen",
            }
        )["card"]
        existing = dict(reopened["repair_order"]["works"][0])
        new_row = {"name": "Новая работа", "quantity": "1", "price": "1"}
        updated = self.service.update_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": reopened["updated_at"],
                "repair_order": {"works": [existing, new_row]},
            }
        )["card"]
        ids = [row["id"] for row in updated["repair_order"]["works"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row_id and not row_id.startswith("row-") for row_id in ids))
        duplicate = [dict(updated["repair_order"]["works"][0])] * 2
        with self.assertRaises(ServiceError) as duplicated:
            self.service.update_repair_order(
                {
                    "card_id": closed["id"],
                    "expected_updated_at": updated["updated_at"],
                    "repair_order": {"works": duplicate},
                }
            )
        self.assertEqual(duplicated.exception.code, "validation_error")

    def test_paid_salary_is_not_rewritten_and_negative_balance_is_previewed(self) -> None:
        employee = self._employee("Выплаченный мастер")
        closed = self._closed_order(employee["id"])
        cashbox = self.service.list_cashboxes()["cashboxes"][0]
        payout = self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "500",
                "cashbox_id": cashbox["id"],
            }
        )["transaction"]
        current = self.service.get_card({"card_id": closed["id"]})["card"]
        preview = self.service.preview_repair_order_reopen(
            {"card_id": closed["id"], "expected_updated_at": current["updated_at"]}
        )
        employee_preview = next(
            item for item in preview["payroll_reversals"] if item["employee_id"] == employee["id"]
        )
        self.assertEqual(employee_preview["balance_before_minor"], 0)
        self.assertEqual(employee_preview["balance_after_minor"], -50000)
        transaction_ids = [item.id for item in self.store.read_bundle()["cash_transactions"]]
        reopened = self.service.reopen_repair_order(
            {
                "card_id": closed["id"],
                "expected_updated_at": current["updated_at"],
                "reason_code": "executor_error",
                "reason_note": "Начисление уже было выплачено",
                "idempotency_key": "paid-salary-reopen",
            }
        )["card"]
        self.assertEqual(reopened["repair_order"]["status"], "open")
        self.assertEqual(
            [item.id for item in self.store.read_bundle()["cash_transactions"]],
            transaction_ids,
        )
        self.assertIn(payout["id"], transaction_ids)
        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger["balance_total"], "-500")


if __name__ == "__main__":
    unittest.main()
