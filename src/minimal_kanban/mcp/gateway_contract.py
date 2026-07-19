DEFAULT_CARD_FIELDS = (
    "id",
    "short_id",
    "vehicle",
    "title",
    "column",
    "column_label",
    "tags",
    "status",
    "indicator",
    "remaining_seconds",
    "deadline_timestamp",
    "client_id",
    "board_summary",
    "updated_at",
)
CARD_FIELD_ALLOWLIST = frozenset(
    {
        *DEFAULT_CARD_FIELDS,
        "archived",
        "description_preview",
        "vehicle_profile_compact",
        "attachment_count",
        "events_count",
        "is_unread",
        "has_unseen_update",
        "board_summary_stale",
    }
)
BOARD_WORKFLOW_OPERATIONS = frozenset(
    {
        "manager_board_scan",
        "list_ready_unpaid_cards",
        "triage_inbox_cards",
        "list_cards_missing_manager_data",
        "audit_repair_order_consistency",
        "audit_client_links",
        "bulk_set_deadline_if_below",
        "bulk_refresh_board_summaries",
        "cleanup_card",
        "apply_ready_unpaid_followups",
    }
)
FINANCE_WORKFLOW_OPERATIONS = frozenset(
    {
        "list_cashboxes",
        "get_cashbox",
        "get_cash_journal",
        "create_cashbox",
        "delete_cashbox",
        "create_cash_transaction",
        "get_repair_order",
        "update_repair_order",
        "set_repair_order_status",
        "record_repair_order_payment",
        "reorder_cashboxes",
        "create_cashbox_transfer",
        "create_employee_salary_transaction",
        "create_employee_shift_accrual",
        "cancel_cash_transaction",
        "cancel_last_cash_transaction",
        "apply_finance_audit_safe_fixes",
    }
)
FINANCE_VIRTUAL_OPERATIONS = {
    "reorder_cashboxes": "/api/reorder_cashboxes",
    "create_cashbox_transfer": "/api/create_cashbox_transfer",
    "create_employee_salary_transaction": "/api/create_employee_salary_transaction",
    "create_employee_shift_accrual": "/api/create_employee_shift_accrual",
    "cancel_cash_transaction": "/api/cancel_cash_transaction",
    "cancel_last_cash_transaction": "/api/cancel_last_cash_transaction",
    "apply_finance_audit_safe_fixes": "/api/finance_audit/apply_safe_fixes",
}
INVENTORY_WORKFLOW_OPERATIONS = frozenset(
    {
        "list_inventory_items",
        "search_inventory_items",
        "get_inventory_item",
        "list_inventory_movements",
        "save_inventory_item",
        "replenish_inventory_item",
        "write_off_inventory_item",
        "return_inventory_movement",
    }
)
DOCUMENT_WORKFLOW_OPERATIONS = frozenset(
    {
        "download_repair_order_print_pdf",
        "create_document_without_card_pdf",
        "list_shared_files",
        "get_shared_file_info",
        "download_shared_file",
        "upload_shared_file",
        "delete_shared_file",
    }
)


__all__ = [
    "BOARD_WORKFLOW_OPERATIONS",
    "CARD_FIELD_ALLOWLIST",
    "DEFAULT_CARD_FIELDS",
    "DOCUMENT_WORKFLOW_OPERATIONS",
    "FINANCE_VIRTUAL_OPERATIONS",
    "FINANCE_WORKFLOW_OPERATIONS",
    "INVENTORY_WORKFLOW_OPERATIONS",
]
