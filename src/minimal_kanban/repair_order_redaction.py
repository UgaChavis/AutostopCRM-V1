"""Conservative display values for the redacted repair-order list contract."""

import re
from datetime import datetime
from uuid import UUID

from .models import short_entity_id

_SAFE_ERROR_CODES = frozenset(
    {
        "board_api_error",
        "board_api_malformed_response",
        "board_api_unreachable",
        "capability_failed",
        "forbidden",
        "internal_error",
        "unauthorized",
        "validation_error",
        "state_file_corrupted",
    }
)


def safe_repair_order_error(error: object) -> dict[str, str]:
    code = error.get("code") if isinstance(error, dict) else None
    return {
        "code": code
        if isinstance(code, str) and code in _SAFE_ERROR_CODES
        else "repair_order_read_failed",
        "message": "Repair-order read failed; details are hidden in redacted mode.",
    }


def safe_card_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return ""
    return value if str(parsed) == value.lower() else ""


def safe_order_number(value: str) -> str:
    # Generated order numbers are ASCII decimal. Legacy free text and long
    # digit strings can contain personal information and must not be echoed.
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value) else ""


def safe_order_datetime(value: str) -> str:
    if isinstance(value, str) and re.fullmatch(
        r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}(?: [0-9]{2}:[0-9]{2})?", value
    ):
        try:
            datetime.strptime(value, "%d.%m.%Y %H:%M" if " " in value else "%d.%m.%Y")
        except ValueError:
            return ""
        return value
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?",
        value,
    ):
        return ""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return ""
    return value


def safe_repair_order_filters(filters: dict) -> dict:
    return {
        key: safe_card_id(value)
        if key == "card_id"
        else safe_order_number(value)
        if key == "number"
        else value
        for key, value in filters.items()
    }


def redact_projected_item(item: dict, *, compact: bool) -> dict:
    """Treat the backend as untrusted, including during mixed-version upgrades."""
    projected = {}
    for key in ("card_id", "number"):
        if key in item:
            projected[key] = (
                safe_card_id(item[key]) if key == "card_id" else safe_order_number(item[key])
            )
    if "short_id" in item:
        card_id = projected.get("card_id", "")
        projected["short_id"] = short_entity_id(card_id, prefix="C") if card_id else ""
    for key in ("date", "created_at", "opened_at", "closed_at", "updated_at"):
        if key in item:
            projected[key] = safe_order_datetime(item[key])
    allowed = {
        "status": {"open", "ready", "closed"},
        "status_label": {"Открыт", "Готов", "Закрыт"},
        "payment_status": {"paid", "unpaid"},
        "payment_status_label": {"Оплачен", "Не оплачен"},
        "payment_method": {"cash", "cashless", "card"},
        "payment_method_label": {"Наличный", "Безналичный", "На карту"},
    }
    for key, choices in allowed.items():
        if key in item:
            value = item[key]
            projected[key] = value if isinstance(value, str) and value in choices else ""
    for key in (
        "prepayment",
        "prepayment_display",
        "paid_total",
        "paid_total_display",
        "subtotal_total",
        "taxes_total",
        "works_total",
        "grand_total",
        "due_total",
    ):
        if key in item:
            value = item[key]
            projected[key] = (
                value
                if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,18}(?:\.[0-9]{1,2})?", value)
                else ""
            )
    for key in (
        "is_paid",
        "client_present",
        "phone_present",
        "vin_present",
        "license_plate_present",
    ):
        if key in item and isinstance(item[key], bool):
            projected[key] = item[key]
    for key in ("heading", "vehicle", "file_name"):
        if key in item:
            projected[key] = ""
    if not compact:
        for key in (
            "client",
            "phone",
            "vin",
            "license_plate",
            "mileage",
            "reason",
            "summary",
            "file_path",
        ):
            if key in item:
                projected[key] = ""
        for key in ("payments", "tags"):
            if key in item:
                projected[key] = []
    return projected


def valid_repair_order_items(items: object, *, allow_redacted_id: bool = False) -> bool:
    return isinstance(items, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("card_id"), str)
        and (
            (
                bool(item["card_id"])
                and item["card_id"] == item["card_id"].strip()
                and len(item["card_id"]) <= 128
            )
            or (
                allow_redacted_id and item["card_id"] == "" and item.get("card_id_redacted") is True
            )
        )
        and isinstance(item.get("number"), str)
        and item.get("status") in ("open", "ready", "closed")
        for item in items
    )


def validate_list_response(response: object, *, allow_redacted_id: bool = False) -> dict:
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        invalid = True
    else:
        data = response.get("data")
        invalid = response["ok"] and (
            response.get("error") is not None
            or not isinstance(data, dict)
            or not valid_repair_order_items(
                data.get("repair_orders"), allow_redacted_id=allow_redacted_id
            )
        )
    if invalid:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "board_api_malformed_response",
                "message": "Invalid repair-order list response.",
            },
        }
    return response


def redact_list_response(response: dict, *, compact: bool) -> dict:
    data = response.get("data")
    if not isinstance(data, dict):
        return (
            {"ok": False, "error": safe_repair_order_error(None), "data": None}
            if response.get("ok")
            else response
        )
    items = data.get("repair_orders")
    if response.get("ok") and not valid_repair_order_items(items, allow_redacted_id=True):
        return {"ok": False, "error": safe_repair_order_error(None), "data": None}
    original = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta = {
        key: original[key]
        for key in (
            "limit",
            "total",
            "returned",
            "active_total",
            "open_total",
            "ready_total",
            "archived_total",
            "inconsistent_total",
        )
        if isinstance(original.get(key), int) and not isinstance(original.get(key), bool)
    }
    for key in ("has_more", "compact", "redact_private"):
        if isinstance(original.get(key), bool):
            meta[key] = original[key]
    allowed_text = {
        "status": {"open", "ready", "closed", "all"},
        "search_field": {
            "all",
            "number",
            "date",
            "client",
            "phone",
            "vehicle",
            "summary",
            "license_plate",
        },
        "sort_by": {"number", "opened_at", "closed_at"},
        "sort_dir": {"asc", "desc"},
    }
    for key, values in allowed_text.items():
        value = original.get(key)
        if isinstance(value, str) and value in values:
            meta[key] = value
    filters = original.get("applied_filters")
    if isinstance(filters, dict):
        safe_filters = {
            key: value
            for key, value in filters.items()
            if key in {"card_id", "number"}
            or (key in allowed_text and isinstance(value, str) and value in allowed_text[key])
        }
        meta["applied_filters"] = safe_repair_order_filters(safe_filters)
    meta["query"] = ""
    meta["directory"] = ""
    return {
        **response,
        "data": {
            "repair_orders": [redact_projected_item(item, compact=compact) for item in items or []],
            "meta": meta,
        },
    }


async def call_repair_order_search(board_call, board_api, *, query, limit, filters):
    try:
        return await board_call(
            board_api.list_repair_orders,
            query=query,
            limit=limit,
            compact=True,
            redact_private=True,
            **filters,
        )
    except Exception:
        return {"ok": False, "error": {"code": "board_api_unreachable"}}


def safe_search_output(entity, ok, items, meta, error):
    if entity != "repair_order":
        return items, meta, error
    if not ok:
        return [], {}, safe_repair_order_error(error)
    return [redact_projected_item(item, compact=True) for item in items], {}, error


def safe_search_status(entity, ok, data, error, response):
    if entity == "repair_order" and (
        not isinstance(response, dict) or type(response.get("ok")) is not bool
    ):
        return False
    if not ok or entity != "repair_order":
        return ok
    if error is not None:
        return False
    if isinstance(data, dict):
        data = next(
            (data[key] for key in ("repair_orders", "items", "results") if key in data), None
        )
    return valid_repair_order_items(data, allow_redacted_id=True)


def invalid_raw_redact_flag(name: str, arguments: dict) -> bool:
    return (
        name == "list_repair_orders"
        and "redact_private" in arguments
        and not isinstance(arguments["redact_private"], bool)
    )


def redact_raw_error(name: str, arguments: dict, result: dict) -> dict:
    if (
        name == "list_repair_orders"
        and arguments.get("redact_private") is True
        and not result.get("ok")
    ):
        return {
            "ok": False,
            "data": None,
            "error": safe_repair_order_error(result.get("error")),
            "meta": {},
        }
    return result


def redact_compact_item(item: dict, card) -> None:
    order = card.repair_order
    card_id = safe_card_id(card.id)
    item.update(
        {
            "card_id": card_id,
            "short_id": short_entity_id(card_id, prefix="C") if card_id else "",
            "number": safe_order_number(order.number),
            "date": safe_order_datetime(order.date),
            "updated_at": safe_order_datetime(card.updated_at),
            "heading": "",
            "vehicle": "",
            "file_name": "",
            "client_present": bool(order.client),
            "phone_present": bool(order.phone),
            "vin_present": bool(order.vin),
            "license_plate_present": bool(order.license_plate),
        }
    )
    if not card_id:
        item["card_id_redacted"] = True


def redact_full_item(item: dict, card) -> None:
    for key in (
        "client",
        "phone",
        "vehicle",
        "license_plate",
        "vin",
        "mileage",
        "reason",
        "heading",
        "summary",
        "file_name",
        "file_path",
    ):
        item[key] = ""
    item["payments"] = []
    item["tags"] = []
    item["prepayment"] = card.repair_order.prepayment_amount()
    item["card_id"] = safe_card_id(card.id)
    if not item["card_id"]:
        item["card_id_redacted"] = True
    item["number"] = safe_order_number(card.repair_order.number)
    for key in ("date", "created_at", "opened_at", "closed_at", "updated_at"):
        item[key] = safe_order_datetime(item[key])
