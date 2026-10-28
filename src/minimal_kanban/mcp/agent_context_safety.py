"""Guard legacy values in bounded agent context responses."""

from collections.abc import Mapping
from typing import Any

from .agent_gateway_support import _slim_card
from .gateway_contract import DEFAULT_CARD_FIELDS

_CONTEXT_READ_WARNING_CODES = frozenset(
    {
        "deadline_calculated_from_created_at",
        "repair_order_row_identity_unverified",
        "repair_order_payment_identity_unverified",
        "related_card_client_match_unverified",
    }
)


def _context_read_warnings(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str) and item in _CONTEXT_READ_WARNING_CODES]
        if isinstance(value, list)
        else []
    )


def _guard_unverified_context_values(entity: str, data: Any, source: Any) -> None:
    """Withhold identifiers and deadlines not backed by the stored snapshot."""
    if (
        not isinstance(data, dict)
        or not isinstance(source, Mapping)
        or entity not in {"card", "repair_order", "client"}
    ):
        return
    warning_source = source.get("meta", {}) if entity == "client" else source
    if not isinstance(warning_source, Mapping):
        return
    warnings = set(_context_read_warnings(warning_source.get("read_warnings")))
    if warnings:
        if entity == "client":
            if not isinstance(data.get("meta"), dict):
                data["meta"] = {}
            data["meta"]["read_warnings"] = sorted(warnings)
        else:
            data["read_warnings"] = sorted(warnings)

    def guard_order(order: Any) -> None:
        if not isinstance(order, dict):
            return
        fields = []
        if "repair_order_row_identity_unverified" in warnings:
            fields.extend(("works", "materials"))
        if "repair_order_payment_identity_unverified" in warnings:
            fields.append("payments")
        for field in fields:
            rows = order.get(field)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        row.pop("id", None)

    if entity == "client":
        orders = data.get("repair_orders")
        if isinstance(orders, list):
            for order in orders:
                guard_order(order)
        return
    card = data.get("card")
    if isinstance(card, dict):
        if "deadline_calculated_from_created_at" in warnings:
            for field in tuple(card):
                if field.startswith(("deadline_", "remaining_")) or field in {
                    "signal_timestamp",
                    "status",
                    "indicator",
                    "is_blinking",
                }:
                    card.pop(field, None)
        guard_order(card.get("repair_order"))
    guard_order(data.get("repair_order"))


_ENTITY_CONTEXT_SUMMARY_FIELDS = {
    "card": DEFAULT_CARD_FIELDS,
    "client": ("id", "short_id", "client_type", "updated_at"),
    "cashbox": ("id", "short_id", "name", "updated_at"),
    "inventory": (
        "id",
        "short_id",
        "name",
        "catalog_number",
        "unit",
        "quantity",
        "updated_at",
    ),
    "file": ("id", "extension", "mime_type", "size_bytes", "updated_at", "exists_on_disk"),
}
_REPAIR_ORDER_CONTEXT_SUMMARY_FIELDS = ("number", "status", "opened_at", "closed_at")


def _entity_context_summary(entity: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    if entity == "repair_order":
        card = data.get("card")
        repair_order = data.get("repair_order")
        summary = {
            "card": _slim_card(card, ("id", "short_id", "updated_at"))
            if isinstance(card, Mapping)
            else {},
            "repair_order": _slim_card(repair_order, _REPAIR_ORDER_CONTEXT_SUMMARY_FIELDS)
            if isinstance(repair_order, Mapping)
            else {},
        }
        warnings = _context_read_warnings(data.get("read_warnings"))
        if warnings:
            summary["read_warnings"] = warnings
        return summary
    target_key = {
        "card": "card",
        "client": "client",
        "cashbox": "cashbox",
        "inventory": "item",
        "file": "file",
    }.get(entity)
    if target_key is None:
        return {}
    target = data.get(target_key)
    summary = {
        target_key: _slim_card(target, _ENTITY_CONTEXT_SUMMARY_FIELDS[entity])
        if isinstance(target, Mapping)
        else {}
    }
    warnings = _context_read_warnings(data.get("read_warnings"))
    if warnings:
        summary["read_warnings"] = warnings
    if entity == "client" and isinstance(data.get("meta"), Mapping):
        summary["meta"] = _slim_card(data["meta"], ("vehicles_total", "repair_orders_total"))
        warnings = _context_read_warnings(data["meta"].get("read_warnings"))
        if warnings:
            summary["meta"]["read_warnings"] = warnings
    return summary
