from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.route_registry import (  # noqa: E402
    ADMIN_ONLY_ROUTES,
    OPERATOR_SESSION_ROUTES,
    PROXIED_WRITE_ROUTES,
    build_operator_routes,
    build_service_routes,
)
from minimal_kanban.mcp.tool_registry import PUBLIC_MCP_TOOL_NAMES  # noqa: E402
from scripts.browser_smoke import SMOKE_SCENARIOS  # noqa: E402


class _FakeService:
    def __getattr__(self, name: str):
        def _handler(payload=None):
            return {"ok": True, "handler": name, "payload": payload}

        return _handler


EXPECTED_SERVICE_ROUTES = {
    "/api/add_card_attachment",
    "/api/agent_actions",
    "/api/agent_enqueue_task",
    "/api/agent_scheduled_tasks",
    "/api/agent_status",
    "/api/agent_tasks",
    "/api/archive_card",
    "/api/apply_ready_unpaid_followups",
    "/api/audit_client_links",
    "/api/audit_repair_order_consistency",
    "/api/autofill_inspection_sheet_form",
    "/api/autofill_repair_order",
    "/api/bulk_move_cards",
    "/api/bulk_refresh_board_summaries",
    "/api/bulk_set_deadline_if_below",
    "/api/cancel_cash_transaction",
    "/api/cancel_last_cash_transaction",
    "/api/cleanup_card_content",
    "/api/cleanup_card",
    "/api/copy_shared_file",
    "/api/correct_repair_order_number",
    "/api/create_card",
    "/api/create_cash_transaction",
    "/api/create_cashbox",
    "/api/create_cashbox_transfer",
    "/api/create_client",
    "/api/create_column",
    "/api/create_employee_salary_transaction",
    "/api/create_employee_shift_accrual",
    "/api/create_sticky",
    "/api/delete_agent_scheduled_task",
    "/api/delete_cashbox",
    "/api/delete_client",
    "/api/delete_client_vehicle",
    "/api/delete_column",
    "/api/delete_employee",
    "/api/delete_gateway_attestation_payment_fixture",
    "/api/delete_print_template",
    "/api/delete_shared_file",
    "/api/delete_sticky",
    "/api/duplicate_print_template",
    "/api/export_repair_order_print_pdf",
    "/api/fetch_shared_file",
    "/api/finance_audit",
    "/api/finance_audit/apply_safe_fixes",
    "/api/repair_order_number_audit",
    "/api/get_board_content",
    "/api/get_board_context",
    "/api/get_board_event_page",
    "/api/get_board_events",
    "/api/get_board_revision",
    "/api/get_board_snapshot",
    "/api/get_ai_chat_knowledge",
    "/api/get_card",
    "/api/get_card_attachment",
    "/api/get_card_context",
    "/api/get_card_log",
    "/api/get_cards",
    "/api/get_cash_journal",
    "/api/get_cashbox",
    "/api/get_client",
    "/api/get_client_stats",
    "/api/get_employee_salary_ledger",
    "/api/get_employee_salary_reconciliation",
    "/api/get_employee_salary_report",
    "/api/get_display_dashboard",
    "/api/get_gpt_wall",
    "/api/get_inspection_sheet_form",
    "/api/get_inventory_item",
    "/api/get_payroll_report",
    "/api/get_repair_order",
    "/api/get_repair_order_cycles",
    "/api/get_repair_order_print_workspace",
    "/api/get_repair_order_text",
    "/api/get_shared_file_info",
    "/api/link_card_to_client",
    "/api/list_archived_cards",
    "/api/list_card_attachments",
    "/api/list_cards_missing_manager_data",
    "/api/list_cashboxes",
    "/api/list_clients",
    "/api/list_columns",
    "/api/list_employees",
    "/api/list_inventory_items",
    "/api/list_inventory_movements",
    "/api/list_overdue_cards",
    "/api/list_repair_orders",
    "/api/list_ready_unpaid_cards",
    "/api/list_shared_files",
    "/api/mark_card_ready",
    "/api/manager_board_scan",
    "/api/mark_card_seen",
    "/api/mark_cashbox_notifications_seen",
    "/api/move_card",
    "/api/move_column",
    "/api/move_sticky",
    "/api/paste_shared_file",
    "/api/paste_shared_files_from_clipboard",
    "/api/pause_agent_scheduled_task",
    "/api/preview_repair_order_print_documents",
    "/api/preview_repair_order_reopen",
    "/api/print_repair_order_documents",
    "/api/read_card_attachment",
    "/api/remove_card_attachment",
    "/api/rename_column",
    "/api/rename_shared_file",
    "/api/reopen_repair_order",
    "/api/reorder_cashboxes",
    "/api/replace_repair_order_materials",
    "/api/replace_repair_order_works",
    "/api/replenish_inventory_item",
    "/api/return_inventory_movement",
    "/api/restore_card",
    "/api/resume_agent_scheduled_task",
    "/api/review_board",
    "/api/rollback_manager_run",
    "/api/run_agent_scheduled_task",
    "/api/run_full_card_enrichment",
    "/api/run_manager_operation",
    "/api/save_agent_scheduled_task",
    "/api/save_employee",
    "/api/save_inspection_sheet_form",
    "/api/save_inventory_item",
    "/api/save_print_module_settings",
    "/api/save_print_template",
    "/api/search_cards",
    "/api/search_clients",
    "/api/search_inventory_items",
    "/api/set_card_board_summary",
    "/api/set_card_ai_autofill",
    "/api/set_card_deadline",
    "/api/set_card_indicator",
    "/api/start_card_timer",
    "/api/stop_card_timer",
    "/api/set_default_print_template",
    "/api/set_repair_order_status",
    "/api/suggest_clients_for_card",
    "/api/toggle_employee",
    "/api/triage_inbox_cards",
    "/api/unlink_card_from_client",
    "/api/update_board_settings",
    "/api/update_card",
    "/api/update_client",
    "/api/update_repair_order",
    "/api/update_shared_file_position",
    "/api/update_sticky",
    "/api/upload_shared_file",
    "/api/upsert_client_vehicle",
    "/api/write_off_inventory_item",
}

EXPECTED_OPERATOR_ROUTES = {
    "/api/export_operator_activity",
    "/api/get_operator_activity_aggregates",
    "/api/get_operator_activity_details",
    "/api/get_operator_profile",
    "/api/get_operator_user_report",
    "/api/list_operator_activity",
    "/api/list_operator_users",
    "/api/login_operator",
    "/api/logout_operator",
    "/api/open_card",
    "/api/save_operator_user",
    "/api/set_operator_user_employee",
    "/api/update_personal_board_preferences",
    "/api/delete_operator_user",
}

EXPECTED_SMOKE_SCENARIOS = (
    "login_gate_hides_board_until_operator_login",
    "desktop_board_card_roundtrip",
    "move_card_delta_roundtrip",
    "personal_extra_board_column",
    "display_dashboard_popup_1920x1080",
    "card_timer_start_stop",
    "card_long_description_controls_reachable",
    "cashbox_journal_workspace",
    "cashbox_journal_filters_and_no_audit",
    "cashbox_journal_compact_cleanup",
    "cashbox_journal_mode_and_period_navigation",
    "cashbox_journal_first_render_budget",
    "cashbox_transaction_cancellation",
    "repair_order_payments_modal",
    "repair_order_material_executor_defaults_to_operator_employee",
    "clients_modal",
    "clients_search_selects_realistic_row",
    "files_modal",
    "shared_files_scanability_markup",
    "employees_repair_order_returns_to_employee",
    "employee_shift_accrual_manual_salary",
    "clients_repair_order_returns_to_client",
    "repair_orders_list_returns_to_list",
    "repair_orders_toolbar_stays_available_while_list_scrolls",
    "repair_order_salary_override_popover",
    "payroll_chain_reaches_reports_and_reconciliation",
    "archive_search_filters_visible_rows",
    "cashboxes_journal_transfer_returns_to_cashbox",
    "escape_closes_top_modal_only",
    "operator_admin_employee_binding_returns_to_users",
    "mobile_board_load",
    "mobile_personal_extra_column",
    "mobile_card_detail",
    "mobile_cashboxes_workspace",
    "mobile_repair_orders_workspace",
    "mobile_clients_panel",
    "mobile_employees_panel",
    "mobile_archive_panel",
    "mobile_files_panel",
)


class ContractSnapshotTests(unittest.TestCase):
    def test_http_service_route_table_matches_snapshot(self) -> None:
        service = _FakeService()
        routes = build_service_routes(
            service,
            service,
            paste_shared_files_from_clipboard=service.paste_shared_files_from_clipboard,
        )

        self.assertEqual(EXPECTED_SERVICE_ROUTES, set(routes))

    def test_operator_route_table_matches_snapshot(self) -> None:
        self.assertEqual(EXPECTED_OPERATOR_ROUTES, set(build_operator_routes(_FakeService())))

    def test_auth_route_sets_keep_critical_routes(self) -> None:
        self.assertIn("/api/update_card", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/set_card_ai_autofill", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/open_card", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/delete_employee", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/get_repair_order", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/copy_shared_file", PROXIED_WRITE_ROUTES)
        self.assertIn("/api/finance_audit/apply_safe_fixes", ADMIN_ONLY_ROUTES)
        self.assertIn("/api/get_operator_profile", OPERATOR_SESSION_ROUTES)
        self.assertIn("/api/update_personal_board_preferences", OPERATOR_SESSION_ROUTES)
        self.assertNotIn("/api/update_personal_board_preferences", PROXIED_WRITE_ROUTES)

    def test_auth_route_sets_reference_current_routes(self) -> None:
        service = _FakeService()
        service_routes = build_service_routes(
            service,
            service,
            paste_shared_files_from_clipboard=service.paste_shared_files_from_clipboard,
        )
        all_routes = set(service_routes) | set(build_operator_routes(service))

        self.assertLessEqual(PROXIED_WRITE_ROUTES, all_routes)
        self.assertLessEqual(OPERATOR_SESSION_ROUTES, all_routes)
        self.assertLessEqual(ADMIN_ONLY_ROUTES, all_routes)

    def test_mcp_public_tool_snapshot_keeps_current_surface(self) -> None:
        self.assertEqual(98, len(PUBLIC_MCP_TOOL_NAMES))
        self.assertIn("bootstrap_context", PUBLIC_MCP_TOOL_NAMES)
        self.assertIn("update_repair_order", PUBLIC_MCP_TOOL_NAMES)
        self.assertIn("reopen_repair_order", PUBLIC_MCP_TOOL_NAMES)
        self.assertIn("download_shared_file", PUBLIC_MCP_TOOL_NAMES)

    def test_browser_smoke_scenario_snapshot_matches_current_gate(self) -> None:
        self.assertEqual(EXPECTED_SMOKE_SCENARIOS, SMOKE_SCENARIOS)


if __name__ == "__main__":
    unittest.main()
