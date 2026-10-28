"""Validate persisted v2 snapshots before they enter preview or release paths."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..services.document_money import (
    document_number,
    included_vat,
    money,
    net_line_money,
    resolve_document_tax,
)


def validate_completion_act_snapshot(snapshot: dict, mode: str) -> None:
    from .formatting import _money_display, _money_words_display, _number_display

    form, calculation = snapshot.get("form"), snapshot.get("calculation")
    if not isinstance(form, dict) or not isinstance(calculation, dict):
        raise ValueError("Incomplete completion act money snapshot")
    for payload in (form, calculation):
        if payload.get("money_policy_version") != 2 or payload.get("calculation_mode") != mode:
            raise ValueError("Completion act snapshot policy mismatch")
    rows, totals, tax = (
        calculation.get("items"),
        calculation.get("totals"),
        calculation.get("tax_context"),
    )
    if (
        not isinstance(rows, list)
        or not isinstance(form.get("items"), list)
        or len(rows) != len(form["items"])
        or not isinstance(totals, dict)
        or not isinstance(tax, dict)
        or type(tax.get("known")) is not bool
    ):
        raise ValueError("Invalid completion act snapshot structure")
    rate = document_number(tax.get("rate"), field="tax.rate")
    if rate is None:
        raise ValueError("Missing completion act snapshot tax rate")
    resolved_tax = resolve_document_tax(tax.get("label", ""))
    if (
        any(tax.get(key) != resolved_tax[key] for key in ("known", "has_vat"))
        or rate != resolved_tax["rate"]
    ):
        raise ValueError("Completion act snapshot tax status mismatch")
    subtotal = vat = Decimal(0)
    for row, source in zip(rows, form["items"], strict=True):
        if not isinstance(row, dict) or not isinstance(source, dict):
            raise ValueError("Invalid completion act snapshot row")
        price, quantity = (
            document_number(source.get("price")),
            document_number(source.get("quantity")),
        )
        displayed_quantity = (
            document_number(row.get("quantity_display"))
            if row.get("quantity_display") != "—"
            else None
        )
        if (
            row.get("id") != source.get("id")
            or price != document_number(row.get("price_without_vat"))
            or quantity != displayed_quantity
        ):
            raise ValueError("Completion act snapshot row source mismatch")
        row_subtotal = document_number(row.get("sum_without_vat"))
        if (
            row.get("quantity_display") != _number_display(quantity)
            or row.get("price_without_vat_display") != _number_display(price, minimum_places=2)
            or row.get("sum_without_vat_display") != _money_display(row_subtotal)
        ):
            raise ValueError("Completion act snapshot monetary display mismatch")
        row_vat = document_number(row.get("vat"))
        if row_vat is None:
            raise ValueError("Missing completion act snapshot row VAT")
        if (
            price is not None
            and quantity is not None
            and net_line_money(price, quantity)[0] != row_subtotal
        ):
            raise ValueError("Completion act snapshot row amount mismatch")
        if row_subtotal is None and row_vat != 0:
            raise ValueError("Completion act snapshot incomplete row has VAT")
        if row_subtotal is not None:
            expected_vat = (
                money(row_subtotal * rate)
                if mode == "manual_net"
                else included_vat(row_subtotal + row_vat, rate)
            )
            if row_vat != expected_vat:
                raise ValueError("Completion act snapshot row VAT mismatch")
            subtotal += row_subtotal
        vat += row_vat
    expected: dict[str, Any] = {"base": subtotal, "vat": vat, "gross": subtotal + vat}
    if any(document_number(totals.get(key)) != value for key, value in expected.items()):
        raise ValueError("Completion act snapshot totals mismatch")
    if any(
        totals.get(f"{key}_display") != _money_display(value)
        or totals.get(f"{key}_words_display") != _money_words_display(value)
        for key, value in expected.items()
    ):
        raise ValueError("Completion act snapshot total display mismatch")
    if (
        totals.get("has_vat") != resolved_tax["has_vat"]
        or totals.get("tax_label") != (resolved_tax["label"] or "НДС не определён")
        or totals.get("vat_rate_display") != resolved_tax["rate_display"]
    ):
        raise ValueError("Completion act snapshot tax display mismatch")
