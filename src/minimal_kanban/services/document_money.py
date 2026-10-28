from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

_CENT = Decimal("0.01")
_ZERO = Decimal("0")


class DocumentMoneyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalDocumentRow:
    gross: Decimal
    subtotal: Decimal
    vat: Decimal
    vat_adjustment: Decimal
    invoice_unit_price: Decimal
    invoice_unit_precision: int
    regulated_unit_price: Decimal
    regulated_unit_precision: int


@dataclass(frozen=True, slots=True)
class CanonicalDocumentMoney:
    rows: tuple[CanonicalDocumentRow, ...]
    gross_total: Decimal
    subtotal_total: Decimal
    vat_total: Decimal


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _included_vat(gross: Decimal, rate: Decimal) -> Decimal:
    gross = _money(gross)
    if rate <= _ZERO:
        return Decimal("0.00")
    return _money(gross * rate / (Decimal("1") + rate))


def _cent_count(value: Decimal) -> int:
    cents = value / _CENT
    integral = cents.to_integral_value()
    if cents != integral:
        raise DocumentMoneyError("Money allocation is not a whole number of cents.")
    return int(integral)


def _unit_price(total: Decimal, quantity: Decimal | None) -> tuple[Decimal, int]:
    if quantity is None or quantity <= _ZERO:
        return total, 2
    if quantity == Decimal("1"):
        return total, 2
    exact_price = total / quantity
    for precision in range(2, 12):
        quant = Decimal(1).scaleb(-precision)
        price = exact_price.quantize(quant, rounding=ROUND_HALF_UP)
        if _money(price * quantity) == total:
            if price > NUMBER_ABS_MAX:
                raise DocumentMoneyError("Derived unit price exceeds the supported magnitude.")
            return price, precision
    raise DocumentMoneyError("Unit price cannot reproduce the line total with 2-11 decimals.")


def _rank_by_gross_remainder(
    indexes: list[int], exact: list[Decimal], floors: list[Decimal]
) -> list[int]:
    return sorted(
        indexes,
        key=lambda index: (exact[index] - floors[index], -index),
        reverse=True,
    )


def _gross_values(
    base_totals: list[Decimal],
    *,
    target_total: Decimal,
    net_rate: Decimal,
    tax_rate: Decimal,
) -> list[Decimal]:
    # Preserve the existing allocation of agreed cashless row amounts.
    # Actual VAT is calculated independently from those rows afterwards.
    exact = [value / net_rate if value > _ZERO else _ZERO for value in base_totals]
    floors = [value.quantize(_CENT, rounding=ROUND_FLOOR) for value in exact]
    remaining_count = _cent_count(target_total - sum(floors, _ZERO))
    eligible = [index for index, value in enumerate(exact) if value > floors[index]]
    if remaining_count < 0 or remaining_count > len(eligible):
        raise DocumentMoneyError("Gross cents cannot be allocated across document rows.")

    floor_vat = [_included_vat(value, tax_rate) for value in floors]
    target_vat = _included_vat(target_total, tax_rate)
    needed_vat_steps = _cent_count(target_vat - sum(floor_vat, _ZERO))
    vat_step: dict[int, int] = {}
    for index in eligible:
        step = _cent_count(_included_vat(floors[index] + _CENT, tax_rate) - floor_vat[index])
        if step not in {0, 1}:
            raise DocumentMoneyError("Unexpected VAT step while allocating gross cents.")
        vat_step[index] = step

    increases_vat = [index for index in eligible if vat_step[index] == 1]
    keeps_vat = [index for index in eligible if vat_step[index] == 0]
    min_increases = max(0, remaining_count - len(keeps_vat))
    max_increases = min(remaining_count, len(increases_vat))
    selected_increases = min(max(needed_vat_steps, min_increases), max_increases)
    selected = _rank_by_gross_remainder(increases_vat, exact, floors)[:selected_increases]
    selected += _rank_by_gross_remainder(keeps_vat, exact, floors)[
        : remaining_count - selected_increases
    ]
    gross = list(floors)
    for index in selected:
        gross[index] += _CENT
    return gross


def _build_canonical_document_money(
    base_totals: list[Decimal],
    quantities: list[Decimal | None],
    *,
    target_total: Decimal,
    net_rate: Decimal,
    tax_rate: Decimal,
) -> CanonicalDocumentMoney:
    if len(base_totals) != len(quantities):
        raise DocumentMoneyError("Money rows and quantities have different lengths.")
    if any(not value.is_finite() for value in [*base_totals, target_total, net_rate, tax_rate]):
        raise DocumentMoneyError("Document money inputs must be finite.")
    if any(value is not None and (not value.is_finite() or value < 0) for value in quantities):
        raise DocumentMoneyError("Document quantities must be finite and non-negative.")
    if target_total > NUMBER_ABS_MAX or any(value > NUMBER_ABS_MAX for value in base_totals):
        raise DocumentMoneyError("Document total exceeds the supported magnitude.")
    if net_rate <= _ZERO or tax_rate < _ZERO or any(value < _ZERO for value in base_totals):
        raise DocumentMoneyError("Document money inputs must be non-negative.")
    target_total = _money(target_total)
    if _money(sum(base_totals, _ZERO) / net_rate) != target_total:
        raise DocumentMoneyError("Document total does not match source rows.")
    if not base_totals:
        if target_total != Decimal("0.00"):
            raise DocumentMoneyError("A non-zero document total requires rows.")
        return CanonicalDocumentMoney((), target_total, target_total, Decimal("0.00"))

    gross = _gross_values(
        base_totals,
        target_total=target_total,
        net_rate=net_rate,
        tax_rate=tax_rate,
    )
    nominal_vat = [_included_vat(value, tax_rate) for value in gross]
    vat = nominal_vat
    target_vat = sum(vat, _ZERO)
    rows: list[CanonicalDocumentRow] = []
    for gross_value, nominal, vat_value, quantity in zip(
        gross, nominal_vat, vat, quantities, strict=True
    ):
        subtotal = _money(gross_value - vat_value)
        invoice_price, invoice_precision = _unit_price(gross_value, quantity)
        regulated_price, regulated_precision = _unit_price(subtotal, quantity)
        rows.append(
            CanonicalDocumentRow(
                gross=gross_value,
                subtotal=subtotal,
                vat=vat_value,
                vat_adjustment=_money(vat_value - nominal),
                invoice_unit_price=invoice_price,
                invoice_unit_precision=invoice_precision,
                regulated_unit_price=regulated_price,
                regulated_unit_precision=regulated_precision,
            )
        )
    subtotal_total = _money(sum((row.subtotal for row in rows), _ZERO))
    if (
        sum((row.gross for row in rows), _ZERO) != target_total
        or sum((row.vat for row in rows), _ZERO) != target_vat
        or subtotal_total + target_vat != target_total
        or any(row.subtotal + row.vat != row.gross for row in rows)
    ):
        raise DocumentMoneyError("Canonical document totals are inconsistent.")
    return CanonicalDocumentMoney(tuple(rows), target_total, subtotal_total, target_vat)


MONEY_POLICY_VERSION = 2
MAX_DECIMAL_PLACES = 11
NUMBER_ABS_MAX = Decimal("999999999999999.99")


def build_canonical_document_money(
    base_totals: list[Decimal],
    quantities: list[Decimal | None],
    *,
    target_total: Decimal,
    net_rate: Decimal,
    tax_rate: Decimal,
) -> CanonicalDocumentMoney:
    with localcontext() as context:
        context.prec = 64
        return _build_canonical_document_money(
            base_totals, quantities, target_total=target_total, net_rate=net_rate, tax_rate=tax_rate
        )


def net_line_money(price: Decimal, quantity: Decimal, tax_rate: Decimal = _ZERO) -> tuple:
    """Round the selected net basis before adding the tax, independently for each row."""
    with localcontext() as context:
        context.prec = 64
        subtotal = _money(price * quantity)
        vat = _money(subtotal * tax_rate)
        return subtotal, vat, subtotal + vat


def decimal_places(value: Decimal) -> int:
    if value.is_zero():
        return 0
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):
        raise InvalidOperation
    trailing = 0
    for digit in reversed(parts.digits):
        if digit != 0:
            break
        trailing += 1
    return max(0, -parts.exponent - trailing)


def document_number(
    value: Any, *, field: str = "", maximum: Decimal = NUMBER_ABS_MAX
) -> Decimal | None:
    text = (
        str(value if value is not None else "")
        .strip()
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace(",", ".")
    )
    if not text:
        return None
    try:
        number = Decimal(text)
        if not number.is_finite() or number < 0 or number > maximum:
            raise InvalidOperation
        if decimal_places(number) > MAX_DECIMAL_PLACES:
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise DocumentMoneyError(
            f"{field}: число должно быть неотрицательным, в допустимом диапазоне и иметь не более 11 дробных разрядов."
        ) from error


def money(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 64
        return _money(value)


def included_vat(gross: Decimal, rate: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 64
        return _included_vat(gross, rate)


def minimum_unit_price(total: Decimal, quantity: Decimal | None) -> tuple[Decimal, int]:
    with localcontext() as context:
        context.prec = 64
        return _unit_price(total, quantity)


def resolve_document_tax(order_label: str, profile_label: str = "") -> dict[str, Any]:
    label = str(order_label or profile_label or "").strip()
    source = (
        "repair_order"
        if str(order_label or "").strip()
        else "service_profile"
        if label
        else "missing"
    )
    normalized = label.casefold().replace("ё", "е")
    without_vat = normalized == "без ндс"
    match = re.fullmatch(r"(?:ндс\s*\(?\s*)?(\d{1,2}(?:[,.]\d{1,2})?)\s*%\s*\)?", normalized)
    known = without_vat or match is not None
    rate_text = match.group(1).replace(",", ".") if match else "0"
    if match and not normalized.startswith("ндс"):
        label = f"НДС ({rate_text.replace('.', ',')}%)"
    return {
        "label": "Без НДС" if without_vat else label,
        "rate_display": rate_text.replace(".", ",") + "%",
        "rate": Decimal(rate_text) / 100,
        "has_vat": bool(match),
        "known": known,
        "source": source,
    }
