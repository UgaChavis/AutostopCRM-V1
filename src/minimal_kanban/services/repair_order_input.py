"""Validate incoming order data before tolerant legacy normalizers can lose it."""

from __future__ import annotations

from decimal import Decimal, DecimalException
from typing import Any

from ..repair_order import (
    REPAIR_ORDER_PAYMENTS_LIMIT,
    REPAIR_ORDER_ROW_VALUE_LIMIT,
    REPAIR_ORDER_ROWS_LIMIT,
)
from .errors import ServiceError


def invalid_order_input(field: str) -> ServiceError:
    return ServiceError(
        "validation_error",
        "Проверьте данные заказ-наряда: числовые поля должны содержать конечные числа "
        "в допустимом диапазоне, без сокращения введённого значения.",
        details={"field": field},
    )


def _selected(payload: dict, *keys: str) -> Any:
    return next((payload[key] for key in keys if key in payload), None)


def _number(value: Any, *, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise invalid_order_input(field)
    # Match the model's grouping whitespace and comma normalization, without
    # its destructive 40-character truncation. Precision and signs are unchanged.
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) > REPAIR_ORDER_ROW_VALUE_LIMIT:
        raise invalid_order_input(field)
    try:
        number = Decimal(text.replace(" ", "").replace(",", "."))
        if not number.is_finite():
            raise invalid_order_input(field)
        # Scientific notation must not bypass the existing field-size bound.
        # Compute its expanded size without allocating exponent-sized strings.
        parts = number.as_tuple()
        if not number.is_zero():
            integer_digits = max(len(parts.digits) + parts.exponent, 1)
            fractional_digits = max(-parts.exponent, 0)
            expanded_size = (
                parts.sign + integer_digits + fractional_digits + bool(fractional_digits)
            )
            if expanded_size > REPAIR_ORDER_ROW_VALUE_LIMIT:
                raise invalid_order_input(field)
    except DecimalException:
        raise invalid_order_input(field) from None
    return number


def _collection(value: Any, *, field: str, limit: int) -> list[dict]:
    if not isinstance(value, list):
        raise ServiceError(
            "validation_error",
            f"Поле {field} должно быть массивом записей.",
            details={"field": field},
        )
    if len(value) > limit:
        raise ServiceError(
            "validation_error",
            f"Поле {field} превышает предел {limit} записей.",
            details={"field": field, "limit": limit},
        )
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ServiceError(
                "validation_error",
                f"Строка {field} должна быть объектом.",
                details={"field": f"{field}[{index}]"},
            )
    return value


def validate_order_rows(value: Any, *, field: str) -> None:
    for index, row in enumerate(_collection(value, field=field, limit=REPAIR_ORDER_ROWS_LIMIT)):
        prefix = f"{field}[{index}]"
        quantity = _number(_selected(row, "quantity", "qty"), field=f"{prefix}.quantity")
        price = _number(row.get("price"), field=f"{prefix}.price")
        cost = _number(row.get("cost_price"), field=f"{prefix}.cost_price")
        _number(_selected(row, "total", "sum"), field=f"{prefix}.total")
        for name in (
            "work_salary_guarantee",
            "work_salary_percent_override",
            "work_salary_cost_price",
        ):
            # Salary metadata retains its own blank/text/clamp/rounding policy,
            # but infinity must never silently become a configured salary rule.
            try:
                number = Decimal("".join(str(row.get(name) or "").split()).replace(",", "."))
            except DecimalException:
                continue
            if not number.is_finite():
                raise invalid_order_input(f"{prefix}.{name}")
        for value, name in ((price, "total"), (cost, "cost_price")):
            if quantity is not None and value is not None:
                try:
                    (quantity * value).quantize(Decimal("0.01"))
                except DecimalException:
                    raise invalid_order_input(f"{prefix}.{name}") from None


def validate_order_payments(value: Any, *, field: str) -> None:
    for index, payment in enumerate(
        _collection(value, field=field, limit=REPAIR_ORDER_PAYMENTS_LIMIT)
    ):
        _number(_selected(payment, "amount", "value"), field=f"{field}[{index}].amount")


def validate_order_input(payload: dict, *, field: str = "repair_order") -> None:
    for name in ("works", "materials"):
        if name in payload:
            validate_order_rows(payload[name], field=f"{field}.{name}")
    if "payments" in payload or "payment_history" in payload:
        validate_order_payments(
            _selected(payload, "payments", "payment_history"), field=f"{field}.payments"
        )
    _number(
        _selected(payload, "prepayment", "advance_payment", "advancePayment"),
        field=f"{field}.prepayment",
    )
