from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from ..models import parse_business_datetime
from ..repair_order import RepairOrder

DEFAULT_NOTE_RE = re.compile(r"заказ-наряд\s*№\s*(?P<number>\S+)", re.IGNORECASE)
MAX_REPAIR_ORDER_NUMBER_DIGITS = 12
MAX_REPAIR_ORDER_AUDIT_COUNT = 1_000_000_000


def _text(value: object) -> str:
    return str(value or "").strip()


def _number_key(value: object) -> str:
    return _text(value).casefold()


def _parse_order_number(value: str) -> int | None:
    text = str(value or "").strip()
    if not text.isdecimal() or len(text) > MAX_REPAIR_ORDER_NUMBER_DIGITS:
        return None
    try:
        return int(text)
    except (OverflowError, ValueError):
        return None


def _repair_order_from_payload(order: dict[str, Any]) -> RepairOrder:
    return RepairOrder.from_dict(order)


def _repair_order_has_data(order: dict[str, Any]) -> bool:
    return not _repair_order_from_payload(order).is_empty()


def _parse_datetime(value: object) -> datetime | None:
    return parse_business_datetime(_text(value))


def _sort_datetime(card: dict[str, Any], order: dict[str, Any]) -> datetime:
    for value in (
        order.get("opened_at"),
        order.get("openedAt"),
        order.get("date"),
        card.get("created_at"),
        card.get("updated_at"),
    ):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    card_id: object = "",
    repair_order_number: object = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue_id = ":".join(
        part
        for part in (
            code,
            _text(card_id),
            _text(repair_order_number),
            _text((data or {}).get("cash_transaction_id")),
        )
        if part
    )
    return {
        "id": issue_id or code,
        "code": code,
        "severity": severity,
        "message": message,
        "card_id": _text(card_id),
        "repair_order_number": _text(repair_order_number),
        "safe_fix_available": False,
        "suggestion": "Только dry-run: перед исправлением нужен backup, owner approval и отдельный maintenance pass.",
        "data": data or {},
    }


def _state_cards(state: dict[str, Any]) -> list[dict[str, Any]]:
    cards = state.get("cards")
    return [card for card in cards if isinstance(card, dict)] if isinstance(cards, list) else []


def _state_transactions(state: dict[str, Any]) -> list[dict[str, Any]]:
    transactions = state.get("cash_transactions")
    if not isinstance(transactions, list):
        return []
    return [item for item in transactions if isinstance(item, dict)]


def _iter_order_cards(state: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for card in _state_cards(state):
        order = card.get("repair_order")
        if not isinstance(order, dict) or not _repair_order_has_data(order):
            continue
        result.append((card, _repair_order_from_payload(order).to_storage_dict()))
    return result


def build_repair_order_number_audit(state: dict[str, Any]) -> dict[str, Any]:
    order_cards = _iter_order_cards(state)
    issues: list[dict[str, Any]] = []
    numbers: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    numeric_orders: list[tuple[datetime, int, dict[str, Any], dict[str, Any]]] = []
    numeric_orders_by_number: dict[int, list[tuple[datetime, dict[str, Any], dict[str, Any]]]] = {}

    for card, order in order_cards:
        card_id = card.get("id", "")
        number = _text(order.get("number"))
        if not number:
            issues.append(
                _issue(
                    "missing_number",
                    "error",
                    "У заказ-наряда есть данные, но нет номера.",
                    card_id=card_id,
                )
            )
            continue
        numbers.setdefault(_number_key(number), []).append((card, order))
        if number.isdigit():
            numeric_number = _parse_order_number(number)
            if numeric_number is None:
                issues.append(
                    _issue(
                        "nonnumeric_number",
                        "warning",
                        "Номер заказ-наряда не может быть обработан как числовой.",
                        card_id=card_id,
                        repair_order_number=number,
                    )
                )
            else:
                opened_at = _sort_datetime(card, order)
                numeric_orders.append((opened_at, numeric_number, card, order))
                numeric_orders_by_number.setdefault(numeric_number, []).append(
                    (opened_at, card, order)
                )
        else:
            issues.append(
                _issue(
                    "nonnumeric_number",
                    "warning",
                    "Номер заказ-наряда не является числовым.",
                    card_id=card_id,
                    repair_order_number=number,
                )
            )

    for same_number_cards in numbers.values():
        if len(same_number_cards) < 2:
            continue
        duplicate_number = _text(same_number_cards[0][1].get("number"))
        issues.append(
            _issue(
                "duplicate_number",
                "error",
                f"Номер заказ-наряда №{duplicate_number} используется в нескольких карточках.",
                repair_order_number=duplicate_number,
                data={"card_ids": [_text(card.get("id")) for card, _order in same_number_cards]},
            )
        )

    previous_number: int | None = None
    for number in sorted(numeric_orders_by_number):
        if previous_number is not None and number - previous_number > 1:
            first_entry = sorted(
                numeric_orders_by_number[number],
                key=lambda item: (item[0], _text(item[1].get("id"))),
            )[0]
            _opened_at, card, order = first_entry
            missing_start = previous_number + 1
            missing_end = number - 1
            missing_count = missing_end - missing_start + 1
            issues.append(
                _issue(
                    "number_gap",
                    "warning",
                    "В последовательности номеров заказ-нарядов есть пропущенные номера.",
                    card_id=card.get("id", ""),
                    repair_order_number=order.get("number", ""),
                    data={
                        "previous_number": previous_number,
                        "current_number": number,
                        "missing_start": missing_start,
                        "missing_end": missing_end,
                        "missing_count": missing_count,
                        "missing_numbers": (
                            list(range(missing_start, missing_end + 1))
                            if missing_count <= 20
                            else []
                        ),
                    },
                )
            )
        previous_number = number

    max_seen_number = 0
    for opened_at, number, card, order in sorted(
        numeric_orders,
        key=lambda item: (item[0], item[1], _text(item[2].get("id"))),
    ):
        card_id = card.get("id", "")
        order_number = order.get("number", "")
        if number < max_seen_number:
            issues.append(
                _issue(
                    "number_time_inversion",
                    "warning",
                    "Более поздний заказ-наряд имеет номер меньше уже встреченного в хронологии.",
                    card_id=card_id,
                    repair_order_number=order_number,
                    data={
                        "max_seen_number": max_seen_number,
                        "current_number": number,
                        "opened_sort_value": opened_at.isoformat(),
                    },
                )
            )
        max_seen_number = max(max_seen_number, number)

    transactions_by_id = {
        _text(transaction.get("id")): transaction
        for transaction in _state_transactions(state)
        if _text(transaction.get("id"))
    }
    for card, order in order_cards:
        payments = order.get("payments")
        if not isinstance(payments, list):
            payments = (
                order.get("payment_history")
                if isinstance(order.get("payment_history"), list)
                else []
            )
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            transaction_id = _text(payment.get("cash_transaction_id"))
            if not transaction_id:
                continue
            transaction = transactions_by_id.get(transaction_id)
            if transaction is None:
                issues.append(
                    _issue(
                        "payment_transaction_missing",
                        "warning",
                        "Оплата заказ-наряда ссылается на отсутствующее движение кассы.",
                        card_id=card.get("id", ""),
                        repair_order_number=order.get("number", ""),
                        data={
                            "payment_id": _text(payment.get("id")),
                            "cash_transaction_id": transaction_id,
                        },
                    )
                )
                continue
            note_match = DEFAULT_NOTE_RE.fullmatch(_text(transaction.get("note")).casefold())
            if note_match is None:
                continue
            note_number = note_match.group("number")
            order_number = _text(order.get("number"))
            if order_number and _number_key(note_number) != _number_key(order_number):
                issues.append(
                    _issue(
                        "payment_note_number_mismatch",
                        "warning",
                        "Номер в стандартной заметке движения кассы не совпадает с текущим связанным заказ-нарядом.",
                        card_id=card.get("id", ""),
                        repair_order_number=order_number,
                        data={
                            "payment_id": _text(payment.get("id")),
                            "cash_transaction_id": transaction_id,
                            "transaction_note": _text(transaction.get("note")),
                            "note_number": note_number,
                        },
                    )
                )

    counts_by_code: dict[str, int] = {}
    counts_by_severity = {"error": 0, "warning": 0, "info": 0}
    safe_fix_count = 0
    for issue in issues:
        code = _text(issue.get("code")) or "unknown"
        severity = _text(issue.get("severity")) or "info"
        counts_by_code[code] = counts_by_code.get(code, 0) + 1
        if severity in counts_by_severity:
            counts_by_severity[severity] += 1
        if issue.get("safe_fix_available"):
            safe_fix_count += 1

    return {
        "issues": issues,
        "summary": {
            "orders_total": len(order_cards),
            "issues_total": len(issues),
            "counts_by_code": dict(sorted(counts_by_code.items())),
            "counts_by_severity": counts_by_severity,
            "numeric_min": min(numeric_orders_by_number) if numeric_orders_by_number else 0,
            "numeric_max": max(numeric_orders_by_number) if numeric_orders_by_number else 0,
            "safe_fix_count": safe_fix_count,
            "review_required_count": len(issues) - safe_fix_count,
        },
        "meta": {
            "schema_version": "repair_order_number_audit.v1",
            "read_only": True,
            "dry_run": True,
        },
    }


def repair_order_number_audit_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": build_repair_order_number_audit(state)}


def limited_repair_order_number_audit_data(
    payload: dict[str, Any], *, issue_limit: int
) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    limited = dict(data)
    issues = data.get("issues")
    if isinstance(issues, list):
        limited["issues"] = issues[: max(0, issue_limit)]
    return limited


def format_repair_order_number_audit_text(payload: dict[str, Any], *, issue_limit: int) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    issues_total = _safe_non_negative_int(summary.get("issues_total"), default=len(issues))
    safe_fix_count = _safe_non_negative_int(summary.get("safe_fix_count"), default=0)
    review_required = _safe_non_negative_int(
        summary.get("review_required_count"), default=issues_total - safe_fix_count
    )
    lines = [
        "AutoStop CRM repair order number audit",
        f"schema: {meta.get('schema_version', '')}",
        f"read_only: {bool(meta.get('read_only'))}",
        f"dry_run: {bool(meta.get('dry_run'))}",
        f"orders: {_safe_non_negative_int(summary.get('orders_total'), default=0)}",
        f"issues: {issues_total}",
        f"safe_fixes_available: {safe_fix_count}",
        f"review_required: {review_required}",
    ]
    counts_by_severity = _format_counts(summary.get("counts_by_severity"))
    counts_by_code = _format_counts(summary.get("counts_by_code"))
    if counts_by_severity:
        lines.append(f"issues_by_severity: {counts_by_severity}")
    if counts_by_code:
        lines.append(f"issues_by_code: {counts_by_code}")
    for issue in issues[: max(0, issue_limit)]:
        if not isinstance(issue, dict):
            continue
        lines.append(
            "- [{severity}] {code}: {message} {context}".format(
                severity=issue.get("severity") or "info",
                code=issue.get("code") or "unknown",
                message=issue.get("message") or "",
                context=format_repair_order_number_issue_context(issue),
            ).rstrip()
        )
    return "\n".join(lines)


def _format_counts(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key in sorted(value):
        number = _coerce_non_negative_int(value.get(key))
        if number is None:
            continue
        parts.append(f"{key}={number}")
    return ", ".join(parts)


def _coerce_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    if numeric > MAX_REPAIR_ORDER_AUDIT_COUNT:
        return MAX_REPAIR_ORDER_AUDIT_COUNT
    if numeric < 0:
        return 0
    number = int(numeric)
    return max(0, number)


def _safe_non_negative_int(value: object, *, default: int) -> int:
    parsed = _coerce_non_negative_int(value)
    return max(0, default) if parsed is None else parsed


def format_repair_order_number_issue_context(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    for field_name, label in (
        ("id", "id"),
        ("card_id", "card_id"),
        ("repair_order_number", "number"),
    ):
        value = _text(issue.get(field_name))
        if value:
            parts.append(f"{label}={value}")
    data = issue.get("data")
    if isinstance(data, dict):
        for field_name in (
            "previous_number",
            "current_number",
            "max_seen_number",
            "missing_start",
            "missing_end",
            "missing_count",
            "opened_sort_value",
            "payment_id",
            "cash_transaction_id",
            "transaction_note",
            "note_number",
        ):
            value = _text(data.get(field_name))
            if value:
                parts.append(f"{field_name}={value}")
    parts.append(f"safe_fix={'yes' if issue.get('safe_fix_available') else 'no'}")
    return " ".join(parts)
