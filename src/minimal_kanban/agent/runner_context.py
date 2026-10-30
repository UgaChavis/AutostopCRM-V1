from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..mcp.client import BoardApiClient


def read_fallback_card_context(
    board_api: BoardApiClient,
    card_id: str,
    response_data: Callable[[object], dict[str, Any]],
) -> dict[str, Any]:
    card_payload = board_api.get_card(card_id)
    card_data = response_data(card_payload)
    card = card_data.get("card") if isinstance(card_data.get("card"), dict) else card_data
    context: dict[str, Any] = {
        "card": dict(card) if isinstance(card, dict) else {"id": card_id},
        "events": [],
    }
    if hasattr(board_api, "get_repair_order"):
        try:
            order_payload = board_api.get_repair_order(card_id, create_if_missing=False)
            order_data = response_data(order_payload)
            order = (
                order_data.get("repair_order")
                if isinstance(order_data.get("repair_order"), dict)
                else order_data
            )
            if isinstance(order, dict):
                context["card"]["repair_order"] = order
        except Exception:
            pass
    if hasattr(board_api, "get_repair_order_text"):
        try:
            text_payload = board_api.get_repair_order_text(card_id, read_only=True)
            text_data = response_data(text_payload)
            if isinstance(text_data, dict):
                context["repair_order_text"] = text_data
        except Exception:
            pass
    return context
