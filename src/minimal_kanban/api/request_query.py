"""Query parameters interpreted as booleans by the CRM API."""

BOOLEAN_QUERY_KEYS = frozenset(
    {
        "allow_linked",
        "compact",
        "compact_groups",
        "create_vehicle_from_card",
        "dry_run",
        "include_archive",
        "include_archived",
        "include_base64",
        "include_full_details",
        "include_markdown",
        "include_removed",
        "include_attachment_status",
        "include_repair_order_text",
        "include_stats",
        "only_missing",
        "only_stale",
        "overwrite",
        "overwrite_card_fields",
        "redact_private",
        "references_only",
        "refresh_summary",
        "sync_fields",
        "sync_linked_cards",
        "sync_vehicle_fields",
        "strict_client_links",
    }
)
