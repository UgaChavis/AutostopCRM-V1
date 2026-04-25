from __future__ import annotations

from typing import Any

_PROMISE_MARKERS = (
    "сейчас пришлю",
    "сейчас отправлю",
    "потом пришлю",
    "позже пришлю",
    "следующим сообщением",
    "вернусь с результатом",
    "вернусь позже",
    "пришлю позже",
    "отправлю позже",
    "i'll send",
    "i will send",
    "send later",
    "come back later",
)


def build_execution_response(
    *,
    model_decision: dict[str, Any],
    tool_results: list[dict[str, Any]],
    status: str,
    error: str = "",
) -> str:
    if status == "failed":
        return f"Не выполнил.\nПричина: {error or 'ошибка выполнения'}"
    if not tool_results:
        response = _sanitize_model_response(model_decision.get("telegram_response"))
        return response or "Принял. Действий по CRM не требуется."
    lines = ["Сделано."]
    for item in tool_results[:8]:
        tool_name = str(item.get("tool") or "")
        verify = item.get("verify") if isinstance(item.get("verify"), dict) else {}
        mark = "проверено" if verify.get("passed") else "без проверки"
        lines.append(f"- {tool_name}: {mark}")
        detail = _tool_result_detail(item)
        if detail:
            lines.append(detail)
    response = _sanitize_model_response(model_decision.get("telegram_response"))
    if response:
        lines.append(response)
    return "\n".join(lines)


def _tool_result_detail(item: dict[str, Any]) -> str:
    tool_name = str(item.get("tool") or "")
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if tool_name in {"get_card", "get_card_context", "create_card", "update_card"}:
        card = data.get("card") if isinstance(data.get("card"), dict) else data
        return _format_card_detail(card) if isinstance(card, dict) else ""
    if tool_name == "get_repair_order_text":
        text = _truncate(str(data.get("text") or "").strip(), limit=1800)
        heading = str(data.get("heading") or "").strip()
        if text and heading:
            return f"  Заказ-наряд: {heading}\n{text}"
        return text
    if tool_name in {
        "get_cards",
        "search_cards",
        "list_overdue_cards",
        "list_archived_cards",
        "list_repair_orders",
    }:
        return _format_list_detail(data)
    if tool_name in {"get_board_snapshot", "get_board_context", "review_board"}:
        return _format_board_detail(data)
    if tool_name in {"get_board_content", "get_gpt_wall"}:
        return _truncate(str(data.get("text") or data.get("content") or "").strip(), limit=1800)
    if tool_name == "internet_search":
        answer = str(
            data.get("answer") or data.get("text") or data.get("content") or ""
        ).strip()
        return _truncate(answer, limit=1800)
    if tool_name == "analyze_card_image_attachment":
        facts = data.get("image_facts") if isinstance(data.get("image_facts"), dict) else {}
        if not facts:
            return ""
        compact = []
        for key in ("vin", "license_plate", "make", "model", "mileage", "confidence", "notes"):
            value = facts.get(key)
            if value:
                compact.append(f"{key}: {value}")
        return "  Фото: " + "; ".join(compact[:7]) if compact else ""
    if tool_name == "attach_telegram_photo_to_card":
        attachment = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
        file_name = str(attachment.get("file_name") or "").strip()
        return f"  Вложение: {file_name}" if file_name else ""
    return ""


def _sanitize_model_response(value: Any) -> str:
    response = str(value or "").strip()
    lowered = response.lower()
    if any(marker in lowered for marker in _PROMISE_MARKERS):
        return ""
    return response


def _format_card_detail(card: dict[str, Any]) -> str:
    if not card:
        return ""
    lines: list[str] = []
    title = str(card.get("title") or card.get("heading") or "").strip()
    vehicle = str(card.get("vehicle") or "").strip()
    column = str(card.get("column") or card.get("column_label") or "").strip()
    card_id = str(card.get("id") or "").strip()
    header_parts = [part for part in (title, vehicle, column) if part]
    if header_parts:
        lines.append("  Карточка: " + " | ".join(header_parts))
    elif card_id:
        lines.append("  Карточка: " + card_id)
    description = str(card.get("description") or "").strip()
    if description:
        lines.append("  Описание:\n" + _truncate(description, limit=1600))
    profile = card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
    if not profile:
        compact_profile = (
            card.get("vehicle_profile_compact")
            if isinstance(card.get("vehicle_profile_compact"), dict)
            else {}
        )
        profile = compact_profile if compact_profile else {}
    profile_lines = _vehicle_profile_lines(profile)
    if profile_lines:
        lines.append("  Паспорт авто: " + "; ".join(profile_lines))
    return "\n".join(lines)


def _vehicle_profile_lines(profile: dict[str, Any]) -> list[str]:
    labels = {
        "vin": "VIN",
        "make_display": "марка",
        "model_display": "модель",
        "production_year": "год",
        "engine_model": "двигатель",
        "gearbox_model": "КПП",
        "drivetrain": "привод",
    }
    lines: list[str] = []
    for key, label in labels.items():
        value = profile.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"{label}: {value}")
    return lines[:8]


def _format_list_detail(data: dict[str, Any]) -> str:
    rows = _first_list(data, ("cards", "results", "items", "repair_orders", "overdue_cards"))
    if rows is None:
        return _format_board_detail(data)
    lines = [f"  Найдено: {len(rows)}"]
    for card in rows[:8]:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or card.get("heading") or card.get("name") or "").strip()
        vehicle = str(card.get("vehicle") or "").strip()
        column = str(card.get("column") or card.get("status") or "").strip()
        label = " | ".join(part for part in (title, vehicle, column) if part)
        if label:
            lines.append("  - " + label)
    return "\n".join(lines)


def _format_board_detail(data: dict[str, Any]) -> str:
    text = str(data.get("text") or data.get("summary") or "").strip()
    if text:
        return _truncate(text, limit=1800)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    counters = data.get("counters") if isinstance(data.get("counters"), dict) else {}
    fields = {**counters, **meta}
    if not fields:
        return ""
    compact = []
    for key, value in fields.items():
        if value not in (None, "", [], {}):
            compact.append(f"{key}: {value}")
    return "  " + "; ".join(compact[:10]) if compact else ""


def _first_list(data: dict[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _truncate(text: str, *, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def build_recent_actions_response(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "AI пока не записал действий в журнал."
    lines = ["Последние действия AI:"]
    for row in rows[:10]:
        status = row.get("final_status") or "-"
        command = row.get("normalized_command") or row.get("raw_text") or "-"
        lines.append(f"- {row.get('created_at') or '-'} | {status} | {str(command)[:90]}")
    return "\n".join(lines)
