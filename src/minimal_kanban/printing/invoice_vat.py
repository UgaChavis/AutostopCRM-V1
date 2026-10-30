"""Project included invoice VAT without reallocating agreed gross row amounts."""

from __future__ import annotations

from typing import Any

from ..services.document_money import (
    DocumentMoneyError,
    document_number,
    included_vat,
    minimum_unit_price,
    money,
    resolve_document_tax,
)
from .formatting import _number_display


def invoice_tax_payload() -> dict[str, Any]:
    return {**resolve_document_tax("НДС 5%"), "source": "invoice_policy"}


def invoice_vat_for_document(
    document_id: str, rows: list[dict[str, Any]], source_tax: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if document_id not in {"invoice", "invoice_factura", "upd", "completion_act"}:
        return rows, source_tax
    return invoice_vat_rows(rows), invoice_tax_payload()


def invoice_vat_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep existing prices and gross cents; only split out the included VAT.

    Upstream canonical rows retain responsibility for gross allocation and
    input validation. Invalid rows stay invalid and never become zero-money
    rows merely because this projection cannot calculate their VAT.
    """
    rate = invoice_tax_payload()["rate"]
    result = []
    for row in rows:
        projected = dict(row)
        if row.get("money_error"):
            result.append(projected)
            continue
        try:
            gross = document_number(row.get("total"), field="total")
            quantity = document_number(row.get("quantity"), field="quantity")
            if gross is None or gross != money(gross):
                raise DocumentMoneyError("Invoice VAT requires canonical gross cents.")
            vat = included_vat(gross, rate)
            subtotal = money(gross - vat)
        except DocumentMoneyError:
            projected["money_error"] = True
            result.append(projected)
            continue
        projected.update(vat=vat, subtotal=subtotal)
        try:
            unit_price, precision = minimum_unit_price(subtotal, quantity)
            projected.update(
                regulated_price=unit_price,
                regulated_price_display=_number_display(unit_price, minimum_places=precision),
            )
        except DocumentMoneyError:
            # Gross invoice prices can remain exact even when the net price
            # would need more than the supported 11 fractional digits.
            projected.update(
                regulated_money_error=True,
                regulated_price=None,
                regulated_price_display="—",
            )
        result.append(projected)
    return result
