"""Shared source selection and display fields for vehicle acceptance acts."""

from __future__ import annotations

from typing import Any

from ..models import Card
from ..repair_order import RepairOrder
from .formatting import _line_breaks_html, _money_display


def acceptance_act_context(
    card: Card, order: RepairOrder, estimated_cost: Any, terms_html: str
) -> dict[str, str]:
    requested_repair = order.reason if card.id == "manual-document" else card.title
    return {
        "requested_repair_html": _line_breaks_html(requested_repair),
        "photo_fixation_yes": "ДА",
        "photo_fixation_no": "НЕТ",
        "estimated_cost_display": _money_display(estimated_cost),
        "terms_html": terms_html,
    }
