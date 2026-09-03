from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Card, business_timezone, normalize_text, parse_datetime
from ..performance import measure_timing
from ..repair_order import (
    REPAIR_ORDER_STATUS_CLOSED,
    REPAIR_ORDER_STATUS_READY,
    RepairOrderPayment,
    RepairOrderRow,
    repair_order_payment_method_label,
)
from .errors import ServiceError
from .operator_visibility import project_operator_result


def render_repair_order_text(
    card: Card,
    *,
    json_dumps: Callable[..., str],
    operator_payload: dict[str, Any] | None = None,
) -> str:
    order = card.repair_order
    lines = [
        "ЗАКАЗ-НАРЯД",
        "",
        f"Номер: {order.number or '-'}",
        f"Дата: {order.date or '-'}",
        f"Карточка: {card.heading()}",
        f"Card ID: {card.id}",
        "",
        f"Status: {_repair_order_status_label(order.status)}",
        f"Opened at: {order.opened_at or _repair_order_card_datetime(card.created_at) or '-'}",
        f"Closed at: {order.closed_at or '-'}",
        f"Форма оплаты: {order.to_dict()['payment_method_label']}",
        f"Предоплата: {order.prepayment_amount()}",
        "",
        "Оплаты:",
    ]
    if order.payments:
        lines.extend(_render_repair_order_payments(order.payments))
    else:
        lines.append("-")
    lines.extend(
        [
            "",
            f"Клиент: {order.client or '-'}",
            f"Телефон: {order.phone or '-'}",
            f"Автомобиль: {order.vehicle or '-'}",
            f"Госномер: {order.license_plate or '-'}",
            "",
            "Информация для клиента:",
            order.comment or "-",
            "",
            "Master note:",
            order.note or "-",
            "",
            "Работы:",
        ]
    )
    lines.extend(_render_repair_order_rows(order.works))
    lines.append(f"Итого работы: {order.works_total_amount()}")
    lines.extend(["", "Материалы:"])
    lines.extend(_render_repair_order_rows(order.materials))
    lines.append(f"Итого материалы: {order.materials_total_amount()}")
    payment_summary = order.payment_summary_value()
    payment_summary_amounts = order.payment_summary_amounts()
    cash_like_prepayment = payment_summary["base_paid_cash"]
    cashless_prepayment = payment_summary["base_paid_noncash"]
    lines.extend(
        [
            "",
            f"Стоимость заказ-наряда за наличный расчет: {order.subtotal_amount()}",
            f"Стоимость заказ-наряда по безналичному расчету: {order.noncash_total_amount()}",
            f"Предоплата всего: {order.prepayment_amount()}",
        ]
    )
    if cash_like_prepayment:
        lines.append(f"Предоплата за наличные: {payment_summary_amounts['base_paid_cash']}")
    if cashless_prepayment:
        lines.append(f"Предоплата по безналу: {payment_summary_amounts['base_paid_noncash']}")
        lines.append(f"Налоги и сборы: {order.taxes_amount()}")
    lines.extend(
        [
            f"Доплата по безналичному расчету: {order.noncash_due_amount()}",
            f"Доплата по наличному расчету: {order.due_total_amount()}",
            "",
            "JSON:",
            json_dumps(
                project_operator_result(operator_payload, order.to_storage_dict()),
                indent=2,
            ),
            "",
            f"Обновлено: {card.updated_at or card.created_at or '-'}",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_bounded_repair_order_text(
    card: Card,
    *,
    json_dumps: Callable[..., str],
    file_name: str,
    max_bytes: int,
    operator_payload: dict[str, Any] | None = None,
) -> str:
    with measure_timing("repair_order_text"):
        text = render_repair_order_text(
            card,
            json_dumps=json_dumps,
            operator_payload=operator_payload,
        )
        if len(text.encode("utf-8")) > max_bytes:
            raise ServiceError(
                "repair_order_text_too_large",
                "Текстовый файл заказ-наряда слишком большой.",
                status_code=413,
                details={"file_name": file_name, "max_size_bytes": max_bytes},
            )
        return text


def _repair_order_status_label(status: str) -> str:
    if status == REPAIR_ORDER_STATUS_CLOSED:
        return "Закрыт"
    if status == REPAIR_ORDER_STATUS_READY:
        return "Готов"
    return "Открыт"


def _repair_order_card_datetime(value: str | None) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.astimezone(business_timezone()).strftime("%d.%m.%Y %H:%M")


def _render_repair_order_rows(rows: list[RepairOrderRow]) -> list[str]:
    if not rows:
        return ["-"]
    return [
        (
            f"{index}. {row.name or '-'} | кол-во: {row.quantity or '-'} | "
            f"цена: {row.price or '-'} | сумма: {row.total or '-'}"
        )
        for index, row in enumerate(rows, start=1)
    ]


def _render_repair_order_payments(payments: list[RepairOrderPayment]) -> list[str]:
    lines: list[str] = []
    for index, payment in enumerate(payments, start=1):
        parts = [
            payment.paid_at or "-",
            repair_order_payment_method_label(payment.payment_method),
            payment.amount or "0",
        ]
        if payment.actor_name:
            parts.append(f"кто: {payment.actor_name}")
        if payment.cashbox_name:
            parts.append(f"касса: {payment.cashbox_name}")
        note = normalize_text(payment.note, default="", limit=240)
        if note:
            parts.append(note)
        lines.append(f"{index}. {' | '.join(parts)}")
    return lines or ["-"]
