from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def invoice_guard(document_id: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    if document_id != "invoice":
        return None
    invoice = context["invoice"]
    totals = context["totals"]
    repair_order = context["repair_order"]
    financial_mismatch = invoice["amount_due"] != totals["noncash_due"]
    tax_mismatch = (
        str(invoice["tax_label"]).strip() != str(repair_order.get("tax_label") or "").strip()
    )
    mismatch = financial_mismatch or tax_mismatch
    return {
        "money_basis": "cashless",
        "rendered_total": format(invoice["amount_due"], "f"),
        "repair_order_total": format(totals["noncash_due"], "f"),
        "tax_status": invoice["tax_label"],
        "financial_mismatch": financial_mismatch,
        "tax_mismatch": tax_mismatch,
        "financial_or_tax_mismatch": mismatch,
        "mismatch_with_current_repair_order": mismatch,
    }


def export_document_meta(payload: Mapping[str, Any]) -> dict[str, Any]:
    document = payload["document"]
    template = payload["template"]
    guard = payload.get("document_guard")
    return {
        "id": document.id,
        "label": document.label,
        "template_id": template.id,
        "template_name": template.name,
        **({"document_guard": guard} if guard else {}),
    }
