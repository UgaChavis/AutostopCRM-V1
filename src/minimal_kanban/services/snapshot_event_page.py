from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC
from typing import Any

from ..models import AuditEvent, normalize_actor_name, parse_datetime, utc_now


def event_page_key(event: AuditEvent) -> tuple[str, str]:
    occurred_at = (parse_datetime(event.timestamp) or utc_now()).astimezone(UTC).isoformat()
    return occurred_at, event.id


def encode_event_page_cursor(key: tuple[str, str]) -> str:
    raw = json.dumps({"v": 1, "at": key[0], "id": key[1]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def event_page_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redacted_event_page_item(event: AuditEvent) -> dict[str, Any]:
    entity_type, entity_ref = event_page_entity(event)
    details = event.details if isinstance(event.details, dict) else {}
    before_column = details.get("before_column") if event.action == "card_moved" else None
    after_column = details.get("after_column") if event.action == "card_moved" else None
    item = {
        "event_id": event.id,
        "occurred_at_utc": event_page_key(event)[0],
        "actor_key": event_page_actor_key(event.actor_name),
        "source": event.source,
        "action_type": event.action,
        "entity_type": entity_type,
        "entity_ref_hash": event_page_hash(f"{entity_type}:{entity_ref}"),
        "changed_field_categories": event_page_changed_categories(event),
        "is_financial_action": event_page_is_financial(event),
        "is_destructive_action": event_page_is_destructive(event),
        "operation_result": event_page_operation_result(event),
    }
    if isinstance(before_column, str):
        item["card_column_before"] = before_column
    if isinstance(after_column, str):
        item["card_column_after"] = after_column
    return item


def event_page_actor_key(actor_name: str) -> str:
    normalized = normalize_actor_name(actor_name, default="SYSTEM").strip().upper()
    aliases = {
        "КАТЯ": "KATYA",
        "KATYA": "KATYA",
        "UGA": "UGA",
        "CODEX": "CODEX",
        "СИСТЕМА": "SYSTEM",
        "SYSTEM": "SYSTEM",
    }
    return aliases.get(normalized, f"ACTOR_{event_page_hash(normalized)[:12].upper()}")


def event_page_entity(event: AuditEvent) -> tuple[str, str]:
    action = event.action.casefold()
    details = event.details if isinstance(event.details, dict) else {}
    for entity_type, detail_key in (
        ("cashbox", "cashbox_id"),
        ("attachment", "attachment_id"),
        ("sticky", "sticky_id"),
        ("client", "client_id"),
        ("inventory_item", "item_id"),
        ("column", "column_id"),
    ):
        value = details.get(detail_key)
        if isinstance(value, str) and value:
            return entity_type, value
    if event.card_id:
        return "card", event.card_id
    if "cash" in action or "payment" in action or "salary" in action:
        return "finance", "board"
    if "column" in action:
        return "column", "board"
    return "board", "board"


def event_page_changed_categories(event: AuditEvent) -> list[str]:
    action = event.action.casefold()
    details = event.details if isinstance(event.details, dict) else {}
    categories: set[str] = set()
    category_by_field = {
        "title": "card_content",
        "description": "card_content",
        "board_summary": "card_content",
        "vehicle": "vehicle",
        "vehicle_profile": "vehicle",
        "column": "column",
        "before_column": "column",
        "after_column": "column",
        "deadline": "deadline",
        "indicator": "deadline",
        "timer": "deadline",
        "tag": "tags",
        "tags": "tags",
        "attachment": "attachment",
        "attachment_id": "attachment",
        "repair_order": "repair_order",
        "cashbox_id": "finance",
        "amount": "finance",
        "payment": "finance",
        "client_id": "client",
        "client": "client",
        "sticky_id": "sticky",
        "item_id": "inventory",
        "column_id": "column",
    }
    fields = details.get("fields")
    if isinstance(fields, list):
        categories.update(
            category_by_field.get(str(field).casefold(), "unknown") for field in fields
        )
    categories.update(category_by_field[key] for key in details if key in category_by_field)
    action_categories = (
        ("cash", "finance"),
        ("payment", "finance"),
        ("salary", "finance"),
        ("repair_order", "repair_order"),
        ("client", "client"),
        ("vehicle", "vehicle"),
        ("attachment", "attachment"),
        ("sticky", "sticky"),
        ("column", "column"),
        ("card_moved", "column"),
        ("tag", "tags"),
        ("deadline", "deadline"),
        ("timer", "deadline"),
        ("inventory", "inventory"),
    )
    categories.update(category for marker, category in action_categories if marker in action)
    return sorted(categories or {"unknown"})


def event_page_is_financial(event: AuditEvent) -> bool:
    action = event.action.casefold()
    details = event.details if isinstance(event.details, dict) else {}
    return any(
        marker in action for marker in ("cash", "payment", "salary", "payroll", "finance")
    ) or any(
        key in details
        for key in ("amount", "amount_minor", "payment", "payments", "price", "total")
    )


def event_page_is_destructive(event: AuditEvent) -> bool:
    action = event.action.casefold()
    return any(marker in action for marker in ("archived", "deleted", "removed", "write_off"))


def event_page_operation_result(event: AuditEvent) -> str:
    action = event.action.casefold()
    if "failed" in action or "error" in action:
        return "failed"
    return "success"
