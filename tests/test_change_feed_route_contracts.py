from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.route_registry import (  # noqa: E402
    build_operator_routes,
    build_service_routes,
)
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.shared_files_service import SharedFilesService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402
from scripts.crm_change_feed_producer_parity import build_producer_inventory  # noqa: E402

REASONED_ROUTE_CONTRACT_EXEMPTIONS = {
    "/api/agent_enqueue_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/delete_agent_scheduled_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/delete_gateway_attestation_payment_fixture": "strict synthetic-only cleanup route; dedicated payment fixture tests verify its state projection",
    "/api/pause_agent_scheduled_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/resume_agent_scheduled_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/run_agent_scheduled_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/save_agent_scheduled_task": "delegated Manager runtime authority; CRM state/feed must stay unchanged",
    "/api/copy_shared_file": "read-only clipboard metadata; no durable CRM mutation",
    "/api/correct_repair_order_number": "retired compatibility route fails closed before mutation",
    "/api/preview_repair_order_print_documents": "render-only preview; no durable CRM mutation",
    "/api/autofill_inspection_sheet_form": "model-dependent write covered by deterministic print projection producer contract",
    "/api/autofill_repair_order": "model-dependent write covered by deterministic state projection producer contract",
    "/api/finance_audit/apply_safe_fixes": "only mutates a deliberately corrupted legacy fixture; dedicated finance repair tests own it",
    "/api/mark_cashbox_notifications_seen": "private per-operator viewer receipt; deliberately excluded from the business change feed",
    "/api/rollback_manager_run": "manager-led multi-write compensation contract is verified by manager workflow tests",
    "/api/run_full_card_enrichment": "external model and research orchestration boundary",
    "/api/run_manager_operation": "manager-led multi-write orchestration contract is verified by manager workflow tests",
}


class ChangeFeedCanonicalRouteContractTests(unittest.TestCase):
    """Exercise canonical route-registry handlers against an isolated durable feed."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.change_feed.routes.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(self.base_dir / "state.json", logger=self.logger)
        self.service = CardService(self.store, self.logger)
        self.shared = SharedFilesService(
            storage_dir=self.base_dir / "shared-files",
            index_file=self.base_dir / "shared_files_index.json",
            logger=self.logger,
            change_feed_store=self.store.change_feed_store,
        )
        self.clipboard_source = self.base_dir / "clipboard-source.txt"
        self.clipboard_source.write_text("route contract", encoding="utf-8")

        def paste_clipboard(payload: dict | None = None) -> dict:
            request = dict(payload or {})
            return {
                "files": [
                    self.shared.upload_shared_file_from_local_path(
                        {
                            "path": str(self.clipboard_source),
                            "x": request.get("x", 0),
                            "y": request.get("y", 0),
                        }
                    )["file"]
                ]
            }

        self.routes = build_service_routes(
            self.service,
            self.shared,
            paste_shared_files_from_clipboard=paste_clipboard,
        )
        environment = {
            "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": "route-admin",
            "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": "Route-Admin-Password-71A9",
        }
        with patch.dict(os.environ, environment, clear=False):
            self.operators = OperatorAuthService(
                self.store,
                self.service,
                users_file=self.base_dir / "users.json",
                logger=self.logger,
            )
        self.routes.update(build_operator_routes(self.operators))
        self.admin_session = self.operators.login(
            {"username": "route-admin", "password": "Route-Admin-Password-71A9"}
        )["session"]
        self.consumer = "route-contract"
        self.covered: set[str] = set()
        self._drain_feed()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _reconcile(self) -> None:
        self.store.reconcile_change_feed()
        self.service.reconcile_print_change_feed()
        self.shared.reconcile_change_feed()
        self.operators.reconcile_change_feed()

    def _drain_feed(self) -> list[dict]:
        self._reconcile()
        events: list[dict] = []
        while True:
            page = self.store.change_feed_store.read_page(self.consumer, limit=25)
            rows = list(page["events"])
            if not rows:
                return events
            events.extend(rows)
            self.store.change_feed_store.acknowledge(self.consumer, page["ack"])

    def _invoke(
        self,
        route: str,
        payload: dict | None,
        *,
        producers: set[str],
        entity_types: set[str] | None = None,
    ) -> dict:
        self._drain_feed()
        before = self.store.change_feed_store.raw_events_for_test()
        before_sequence = before[-1]["sequence"] if before else 0
        result = self.routes[route](payload)
        delivered = [event for event in self._drain_feed() if event["sequence"] > before_sequence]
        self.assertTrue(delivered, f"{route} committed no durable feed event")
        self.assertTrue(
            any(event["producer"] in producers for event in delivered),
            (route, delivered),
        )
        if entity_types:
            self.assertTrue(
                any(event["entity_type"] in entity_types for event in delivered),
                (route, delivered),
            )
        self.assertEqual(
            list(range(before_sequence + 1, before_sequence + 1 + len(delivered))),
            [event["sequence"] for event in delivered],
            route,
        )
        self.covered.add(route)
        return result

    def _mark_cashbox_notifications_seen(self, transaction_id: str) -> None:
        self.service.mark_cashbox_notifications_seen(
            {
                "actor_name": "ROUTE-OPERATOR",
                "through_transaction_id": transaction_id,
            }
        )

    def _exercise_operator_user_routes(self, employee_id: str) -> None:
        operator_payload = {"_operator_session": self.admin_session}
        self._invoke(
            "/api/save_operator_user",
            {**operator_payload, "username": "route-user", "password": "route-password"},
            producers={"operator_users"},
            entity_types={"operator_user"},
        )
        self._invoke(
            "/api/set_operator_user_employee",
            {
                **operator_payload,
                "username": "route-user",
                "employee_id": employee_id,
            },
            producers={"operator_users"},
            entity_types={"operator_user"},
        )
        self._invoke(
            "/api/delete_operator_user",
            {**operator_payload, "username": "route-user"},
            producers={"operator_users"},
            entity_types={"operator_user"},
        )

    def _exercise_repair_order_reopen_route(self, state_producers: set[str]) -> None:
        card = self.service.create_card({"title": "Route repair-order correction"})["card"]
        self.service.update_repair_order(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "100"}],
                    "payments": [{"amount": "100", "paid_at": "19.08.2026 12:00"}],
                },
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
        self._invoke(
            "/api/reopen_repair_order",
            {
                "card_id": card["id"],
                "expected_updated_at": closed["updated_at"],
                "reason_code": "other",
                "reason_note": "Canonical feed contract",
                "idempotency_key": "canonical-feed-reopen",
            },
            producers=state_producers,
            entity_types={
                "repair_order",
                "repair_order_cycle",
                "repair_order_payroll_posting",
            },
        )

    def test_registry_routes_commit_and_replay_exact_temp_state_mutations(self) -> None:
        state_producers = {"audit_event", "state_projection"}

        created_column = self._invoke(
            "/api/create_column",
            {"label": "ROUTE CONTRACT COLUMN"},
            producers=state_producers,
            entity_types={"column"},
        )["column"]
        sibling_column = self.service.create_column({"label": "ROUTE CONTRACT SIBLING"})["column"]
        self._invoke(
            "/api/rename_column",
            {"column_id": created_column["id"], "label": "ROUTE CONTRACT RENAMED"},
            producers=state_producers,
            entity_types={"column"},
        )
        self._invoke(
            "/api/move_column",
            {
                "column_id": sibling_column["id"],
                "before_column_id": created_column["id"],
            },
            producers=state_producers,
            entity_types={"column"},
        )
        empty_column = self.service.create_column({"label": "ROUTE CONTRACT DELETE"})["column"]
        self._invoke(
            "/api/delete_column",
            {"column_id": empty_column["id"]},
            producers=state_producers,
            entity_types={"column"},
        )

        card = self._invoke(
            "/api/create_card",
            {
                "title": "ROUTE CONTRACT CARD",
                "description": "Нужно проверить и согласовать",
                "deadline": {"hours": 2},
            },
            producers=state_producers,
            entity_types={"card"},
        )["card"]
        card_id = card["id"]
        self._invoke(
            "/api/update_card",
            {"card_id": card_id, "description": "Обновлённый рабочий факт"},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/set_card_board_summary",
            {"card_id": card_id, "summary": "Проверить автомобиль и согласовать работы."},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/set_card_deadline",
            {"card_id": card_id, "deadline": {"hours": 3}},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/set_card_indicator",
            {"card_id": card_id, "indicator": "yellow"},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/start_card_timer",
            {"card_id": card_id, "deadline": {"hours": 4}},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/stop_card_timer",
            {"card_id": card_id},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/mark_card_seen",
            {"card_id": card_id, "actor_name": "ROUTE-OPERATOR"},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/move_card",
            {"card_id": card_id, "column": created_column["id"]},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/replace_repair_order_works",
            {"card_id": card_id, "rows": [{"name": "Диагностика", "quantity": "1"}]},
            producers=state_producers,
            entity_types={"repair_order", "repair_order_work"},
        )
        self._invoke(
            "/api/replace_repair_order_materials",
            {"card_id": card_id, "rows": [{"name": "Фильтр", "quantity": "1"}]},
            producers=state_producers,
            entity_types={"repair_order", "repair_order_material"},
        )
        attachment = self._invoke(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": "route-contract.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"route contract").decode(),
            },
            producers=state_producers,
            entity_types={"attachment"},
        )["attachment"]
        self._invoke(
            "/api/remove_card_attachment",
            {"card_id": card_id, "attachment_id": attachment["id"]},
            producers=state_producers,
            entity_types={"attachment"},
        )
        with patch("minimal_kanban.printing.service.print_html"):
            self._invoke(
                "/api/print_repair_order_documents",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order"],
                    "printer_name": "Route Contract Printer",
                },
                producers={"audit_event"},
                entity_types={"repair_order"},
            )
        self._invoke(
            "/api/cleanup_card_content",
            {"card_id": card_id},
            producers=state_producers,
            entity_types={"card", "vehicle_profile"},
        )
        self._invoke(
            "/api/mark_card_ready",
            {"card_id": card_id},
            producers=state_producers,
            entity_types={"card"},
        )
        archive_target = self.service.create_card({"title": "Route archive target"})["card"]
        self._invoke(
            "/api/archive_card",
            {"card_id": archive_target["id"]},
            producers=state_producers,
            entity_types={"card"},
        )
        self._invoke(
            "/api/restore_card",
            {"card_id": archive_target["id"], "column": created_column["id"]},
            producers=state_producers,
            entity_types={"card"},
        )
        self._exercise_repair_order_reopen_route(state_producers)

        sticky = self._invoke(
            "/api/create_sticky",
            {"text": "Route contract", "x": 10, "y": 20, "deadline": {"hours": 2}},
            producers=state_producers,
            entity_types={"sticky"},
        )["sticky"]
        self._invoke(
            "/api/update_sticky",
            {"sticky_id": sticky["id"], "text": "Route contract updated"},
            producers=state_producers,
            entity_types={"sticky"},
        )
        self._invoke(
            "/api/move_sticky",
            {"sticky_id": sticky["id"], "x": 30, "y": 40},
            producers=state_producers,
            entity_types={"sticky"},
        )
        self._invoke(
            "/api/delete_sticky",
            {"sticky_id": sticky["id"]},
            producers=state_producers,
            entity_types={"sticky"},
        )

        client = self._invoke(
            "/api/create_client",
            {
                "display_name": "Route Contract Client",
                "phone": "+7 900 000-00-01",
                "vehicles": [{"vehicle": "Toyota", "vin": "JN1TANT32U0012345"}],
            },
            producers=state_producers,
            entity_types={"client", "client_vehicle"},
        )["client"]
        client_id = client["id"]
        vehicle_id = client["vehicles"][0]["id"]
        self._invoke(
            "/api/update_client",
            {"client_id": client_id, "notes": "Route contract note"},
            producers=state_producers,
            entity_types={"client"},
        )
        self._invoke(
            "/api/link_card_to_client",
            {
                "card_id": card_id,
                "client_id": client_id,
                "client_vehicle_id": vehicle_id,
            },
            producers=state_producers,
            entity_types={"card", "client"},
        )
        self._invoke(
            "/api/unlink_card_from_client",
            {"card_id": card_id},
            producers=state_producers,
            entity_types={"card", "client"},
        )
        self._invoke(
            "/api/upsert_client_vehicle",
            {
                "client_id": client_id,
                "client_vehicle_id": vehicle_id,
                "vehicle": {"vehicle": "Toyota updated", "vin": "JN1TANT32U0099999"},
            },
            producers=state_producers,
            entity_types={"client_vehicle"},
        )
        self._invoke(
            "/api/delete_client_vehicle",
            {"client_id": client_id, "client_vehicle_id": vehicle_id},
            producers=state_producers,
            entity_types={"client_vehicle"},
        )
        self._invoke(
            "/api/delete_client",
            {"client_id": client_id},
            producers=state_producers,
            entity_types={"client"},
        )

        employee = self._invoke(
            "/api/save_employee",
            {"name": "Route Employee", "position": "Mechanic"},
            producers=state_producers,
            entity_types={"employee"},
        )["employee"]
        employee_id = employee["id"]
        self._invoke(
            "/api/toggle_employee",
            {"employee_id": employee_id},
            producers=state_producers,
            entity_types={"employee"},
        )
        self._invoke(
            "/api/delete_employee",
            {"employee_id": employee_id},
            producers=state_producers,
            entity_types={"employee"},
        )

        operator_employee = self.service.save_employee(
            {"name": "Route Operator Employee", "position": "Manager"}
        )["employee"]
        self._exercise_operator_user_routes(operator_employee["id"])

        template = self._invoke(
            "/api/save_print_template",
            {
                "document_type": "repair_order",
                "name": "Route Template",
                "content": "<div>Route template</div>",
            },
            producers={"print_module"},
            entity_types={"print_template"},
        )["template"]
        duplicate = self._invoke(
            "/api/duplicate_print_template",
            {"template_id": template["id"], "name": "Route Template Copy"},
            producers={"print_module"},
            entity_types={"print_template"},
        )["template"]
        self._invoke(
            "/api/set_default_print_template",
            {"document_type": "repair_order", "template_id": template["id"]},
            producers={"print_module"},
            entity_types={"print_settings"},
        )
        self._invoke(
            "/api/save_print_module_settings",
            {"orientation": "landscape"},
            producers={"print_module"},
            entity_types={"print_settings"},
        )
        self._invoke(
            "/api/save_inspection_sheet_form",
            {"card_id": card_id, "form_data": {"client": "Route Client"}},
            producers={"print_module"},
            entity_types={"inspection_sheet_form"},
        )
        self._invoke(
            "/api/delete_print_template",
            {"template_id": duplicate["id"]},
            producers={"print_module"},
            entity_types={"print_template"},
        )

        uploaded = self.shared.upload_shared_file(
            {
                "file_name": "route-shared.txt",
                "content_base64": base64.b64encode(b"route shared").decode(),
            }
        )["file"]
        pasted = self._invoke(
            "/api/paste_shared_file",
            {"source_id": uploaded["id"], "x": 10, "y": 20},
            producers={"shared_files"},
            entity_types={"shared_file"},
        )["file"]
        self._invoke(
            "/api/rename_shared_file",
            {"file_id": pasted["id"], "file_name": "route-renamed.txt"},
            producers={"shared_files"},
            entity_types={"shared_file"},
        )
        self._invoke(
            "/api/update_shared_file_position",
            {"file_id": pasted["id"], "x": 40, "y": 50},
            producers={"shared_files"},
            entity_types={"shared_file"},
        )
        self._invoke(
            "/api/paste_shared_files_from_clipboard",
            {"x": 60, "y": 70},
            producers={"shared_files"},
            entity_types={"shared_file"},
        )

        cashbox = self.service.create_cashbox({"name": "Route Cashbox", "actor_name": "ROUTE"})[
            "cashbox"
        ]
        second_cashbox = self.service.create_cashbox(
            {"name": "Route Cashbox Two", "actor_name": "ROUTE"}
        )["cashbox"]
        self._invoke(
            "/api/reorder_cashboxes",
            {
                "cashbox_id": second_cashbox["id"],
                "before_cashbox_id": cashbox["id"],
            },
            producers=state_producers,
            entity_types={"cashbox"},
        )
        self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "5000",
                "note": "Route opening balance",
            }
        )
        self._invoke(
            "/api/create_cashbox_transfer",
            {
                "from_cashbox_id": cashbox["id"],
                "to_cashbox_id": second_cashbox["id"],
                "amount": "500",
                "note": "Route contract transfer",
            },
            producers=state_producers,
            entity_types={"cash_transaction", "cashbox"},
        )
        selected_transaction = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "700",
                "note": "Route selected cancellation",
            }
        )["transaction"]
        self._mark_cashbox_notifications_seen(selected_transaction["id"])
        self._invoke(
            "/api/cancel_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": selected_transaction["id"],
                "reason": "Route cancellation reason",
            },
            producers=state_producers,
            entity_types={"cash_transaction", "cashbox"},
        )
        latest_transaction = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "100",
                "note": "Route latest expense",
            }
        )["transaction"]
        self._invoke(
            "/api/cancel_last_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": latest_transaction["id"],
            },
            producers=state_producers,
            entity_types={"cash_transaction", "cashbox"},
        )
        payroll_employee = self.service.save_employee(
            {"name": "Route Payroll Employee", "position": "Mechanic"}
        )["employee"]
        self._invoke(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": payroll_employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "250",
                "cashbox_id": cashbox["id"],
            },
            producers=state_producers,
            entity_types={"cash_transaction", "cashbox"},
        )
        self._invoke(
            "/api/create_employee_shift_accrual",
            {"employee_id": payroll_employee["id"], "amount": "1200"},
            producers=state_producers,
            entity_types={"employee_shift_accrual"},
        )

        self._invoke(
            "/api/update_board_settings",
            {"board_scale": 1.25},
            producers=state_producers,
            entity_types={"board", "board_settings"},
        )

        executor_routes = set(build_producer_inventory()["executor_contract_only_routes"])
        self.assertEqual(
            executor_routes,
            self.covered | set(REASONED_ROUTE_CONTRACT_EXEMPTIONS),
            {
                "unclassified": sorted(
                    executor_routes - self.covered - set(REASONED_ROUTE_CONTRACT_EXEMPTIONS)
                ),
                "stale": sorted(
                    (self.covered | set(REASONED_ROUTE_CONTRACT_EXEMPTIONS)) - executor_routes
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
