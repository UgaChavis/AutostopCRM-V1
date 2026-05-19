from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file


DEFAULT_NOTE_RE = re.compile(r"заказ-наряд\s*№\s*(?P<number>\S+)", re.IGNORECASE)
REPAIR_ORDER_DATA_FIELDS = (
    "number",
    "date",
    "opened_at",
    "openedAt",
    "closed_at",
    "closedAt",
    "client",
    "phone",
    "vehicle",
    "license_plate",
    "licensePlate",
    "vin",
    "mileage",
    "odometer",
    "payment_method",
    "paymentMethod",
    "prepayment",
    "advance_payment",
    "advancePayment",
    "payments",
    "payment_history",
    "reason",
    "comment",
    "client_information",
    "clientInformation",
    "note",
    "master_comment",
    "masterComment",
    "tags",
    "works",
    "materials",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _number_key(value: object) -> str:
    return _text(value).casefold()


def _is_non_empty(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None and value != ""


def _repair_order_has_data(order: dict[str, Any]) -> bool:
    return any(_is_non_empty(order.get(field)) for field in REPAIR_ORDER_DATA_FIELDS)


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


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
        result.append((card, order))
    return result


def build_audit(state: dict[str, Any]) -> dict[str, Any]:
    order_cards = _iter_order_cards(state)
    issues: list[dict[str, Any]] = []
    numbers: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    numeric_orders: list[tuple[datetime, int, dict[str, Any], dict[str, Any]]] = []

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
            numeric_orders.append((_sort_datetime(card, order), int(number), card, order))
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
                data={
                    "card_ids": [_text(card.get("id")) for card, _order in same_number_cards],
                },
            )
        )

    previous_number = 0
    max_seen_number = 0
    for opened_at, number, card, order in sorted(numeric_orders, key=lambda item: item[0]):
        card_id = card.get("id", "")
        order_number = order.get("number", "")
        if previous_number and number - previous_number > 1:
            issues.append(
                _issue(
                    "number_gap",
                    "warning",
                    "В хронологии заказ-нарядов есть скачок номера.",
                    card_id=card_id,
                    repair_order_number=order_number,
                    data={
                        "previous_number": previous_number,
                        "current_number": number,
                        "opened_sort_value": opened_at.isoformat(),
                    },
                )
            )
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
        previous_number = number
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
    for issue in issues:
        code = _text(issue.get("code")) or "unknown"
        severity = _text(issue.get("severity")) or "info"
        counts_by_code[code] = counts_by_code.get(code, 0) + 1
        if severity in counts_by_severity:
            counts_by_severity[severity] += 1

    return {
        "ok": True,
        "data": {
            "issues": issues,
            "summary": {
                "orders_total": len(order_cards),
                "issues_total": len(issues),
                "counts_by_code": dict(sorted(counts_by_code.items())),
                "counts_by_severity": counts_by_severity,
                "safe_fix_count": 0,
            },
            "meta": {
                "schema_version": "repair_order_number_audit.v1",
                "read_only": True,
                "dry_run": True,
            },
        },
    }


def _limited_data(payload: dict[str, Any], *, issue_limit: int) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    limited = dict(data)
    issues = data.get("issues")
    if isinstance(issues, list):
        limited["issues"] = issues[: max(0, issue_limit)]
    return limited


def _format_text(payload: dict[str, Any], *, issue_limit: int) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    lines = [
        "AutoStop CRM repair order number audit",
        f"schema: {meta.get('schema_version', '')}",
        f"read_only: {bool(meta.get('read_only'))}",
        f"dry_run: {bool(meta.get('dry_run'))}",
        f"orders: {int(summary.get('orders_total') or 0)}",
        f"issues: {int(summary.get('issues_total') or len(issues))}",
    ]
    for issue in issues[: max(0, issue_limit)]:
        if not isinstance(issue, dict):
            continue
        lines.append(
            "- [{severity}] {code}: {message} {card_id} {number}".format(
                severity=issue.get("severity") or "info",
                code=issue.get("code") or "unknown",
                message=issue.get("message") or "",
                card_id=issue.get("card_id") or "",
                number=issue.get("repair_order_number") or "",
            ).rstrip()
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dry-run audit of AutoStop CRM repair order numbers."
    )
    parser.add_argument("--state-file", default=str(get_state_file()))
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--issue-limit", type=int, default=50)
    args = parser.parse_args()

    state_path = Path(args.state_file)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"Invalid JSON: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    payload = build_audit(state if isinstance(state, dict) else {})
    if args.format == "text":
        print(_format_text(payload, issue_limit=args.issue_limit))
    else:
        print(
            json.dumps(
                {
                    "ok": payload["ok"],
                    "data": _limited_data(payload, issue_limit=args.issue_limit),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
