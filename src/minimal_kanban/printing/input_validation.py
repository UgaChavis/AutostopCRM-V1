"""Reject unsafe print inputs before legacy model normalization performs arithmetic."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

from ..services.document_money import DocumentMoneyError, document_number, money
from .errors import PrintModuleError


def _invalid_input(field: str) -> PrintModuleError:
    return PrintModuleError(
        "validation_error",
        "Проверьте числовое поле документа: требуется конечное число в допустимом диапазоне.",
        details={"field": field},
    )


def _number_text(value: Any) -> str:
    # Both order adapters collapse whitespace before their Decimal parser runs,
    # including NBSP and narrow NBSP from copied, grouped monetary values.
    return "".join(str(value if value is not None else "").split()).replace(",", ".")


def _input_number(value: Any, *, field: str, rounded: bool = False) -> Decimal | None:
    text = _number_text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
        # This is a safety boundary, not a new payment or sign policy. In
        # particular, signed payment corrections retain their existing meaning.
        checked = money(number.copy_abs()) if rounded else number.copy_abs()
        document_number(checked)
        return number
    except (InvalidOperation, ValueError, OverflowError, DocumentMoneyError):
        raise _invalid_input(field) from None


def _validate_salary_input(value: Any, *, field: str, percent: bool) -> None:
    # These non-print fields have their own legacy normalization: free text is
    # blank, negatives clamp to zero, percentages clamp to 100 and values round
    # to cents. Do not apply the printed unit-price precision policy to them.
    try:
        number = Decimal(_number_text(value))
    except InvalidOperation:
        return
    if not number.is_finite():
        raise _invalid_input(field)
    normalized = max(number, Decimal("0"))
    if percent:
        normalized = min(normalized, Decimal("100"))
    try:
        normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise _invalid_input(field) from None


def _selected_value(payload: dict, *keys: str, skip_none: bool = False) -> Any:
    for key in keys:
        if key in payload and (not skip_none or payload[key] is not None):
            return payload[key]
    return None


def validate_print_order_input(payload: dict, *, manual: bool = False) -> None:
    """Validate new print-only input; never normalize or rewrite saved orders.

    Manual rows have a separate alias contract from RepairOrder.from_dict.
    Validate before either adapter truncates text or repairs stale row totals,
    so a malformed explicit value cannot become a different valid amount.
    Missing values still reach the existing document money diagnostics.
    """
    for section in ("works", "materials"):
        rows = payload.get(section)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            prefix = f"{section}[{index}]"
            quantity = _input_number(
                _selected_value(row, "quantity", "qty", skip_none=manual),
                field=f"{prefix}.quantity",
            )
            price = _input_number(
                _selected_value(
                    row, "price", *(("unit_price",) if manual else ()), skip_none=manual
                ),
                field=f"{prefix}.price",
            )
            _input_number(
                _selected_value(row, "total", "amount" if manual else "sum", skip_none=manual),
                field=f"{prefix}.total",
                rounded=True,
            )
            if quantity is not None and price is not None:
                # Individually valid operands can overflow the model's Decimal
                # context when multiplied, before normal money guards run.
                with localcontext() as context:
                    context.prec = 64
                    _input_number(money(quantity * price), field=f"{prefix}.total")
            if not manual:
                for field in (
                    "work_salary_guarantee",
                    "work_salary_percent_override",
                    "work_salary_cost_price",
                ):
                    _validate_salary_input(
                        row.get(field),
                        field=f"{prefix}.{field}",
                        percent=field == "work_salary_percent_override",
                    )
    _input_number(
        _selected_value(payload, "prepayment", "advance_payment", "advancePayment"),
        field="prepayment",
        rounded=True,
    )
    payments = _selected_value(payload, "payments", "payment_history")
    if isinstance(payments, list):
        for index, payment in enumerate(payments):
            if isinstance(payment, dict):
                _input_number(
                    _selected_value(payment, "amount", "value"),
                    field=f"payments[{index}].amount",
                    rounded=True,
                )
    if not manual:
        for field, aliases in (
            ("cycles", ("cycles", "repair_order_cycles")),
            ("payroll_postings", ("payroll_postings",)),
        ):
            if any(alias in payload for alias in aliases) and not isinstance(
                _selected_value(payload, *aliases), list
            ):
                raise PrintModuleError(
                    "validation_error",
                    "Поле документа должно содержать список записей.",
                    details={"field": field},
                )
