from __future__ import annotations

from ..models import Card


def ordered_active_card_ids_by_column(
    cards: list[Card],
    column_ids: list[str],
) -> dict[str, list[str]]:
    grouped: dict[str, list[Card]] = {column_id: [] for column_id in column_ids}
    for card in cards:
        if card.archived:
            continue
        bucket = grouped.get(card.column)
        if bucket is not None:
            bucket.append(card)
    result: dict[str, list[str]] = {}
    for column_id, bucket in grouped.items():
        bucket.sort(key=lambda item: (item.position, item.created_at, item.updated_at, item.id))
        result[column_id] = [item.id for item in bucket]
    return result
