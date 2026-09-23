from __future__ import annotations

import re

from ..models import Card, normalize_text
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED

SEARCH_SEPARATOR_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
_REPAIR_ORDER_SEARCH_FIELDS = frozenset(
    {"number", "date", "client", "phone", "vehicle", "summary", "license_plate"}
)


class CardServiceRepairOrderSearchMixin:
    def _validated_repair_order_search_field(self, value) -> str:
        normalized = normalize_text(value, default="all", limit=24).strip().lower()
        if normalized not in _REPAIR_ORDER_SEARCH_FIELDS | {"all"}:
            self._fail(
                "validation_error",
                "Поле search_field содержит неподдерживаемое поле поиска заказ-наряда.",
                details={"field": "search_field"},
            )
        return normalized

    def _filter_repair_order_cards(
        self,
        cards: list[Card],
        *,
        query: str,
        search_field: str = "all",
        status_filter: str = "all",
    ) -> list[Card]:
        normalized_query = self._normalize_search_text(query)
        if not normalized_query:
            return list(cards)
        tokens = normalized_query.split()
        if search_field == "all":
            filtered: list[Card] = []
            for card in cards:
                haystack = " ".join(
                    filter(
                        None,
                        map(self._normalize_search_text, self._repair_order_search_values(card)),
                    )
                )
                if haystack and all(token in haystack for token in tokens):
                    filtered.append(card)
            return filtered

        normalized_query = normalized_query.replace("ё", "е")
        tokens = normalized_query.split()
        compact_query = SEARCH_SEPARATOR_PATTERN.sub("", normalized_query)
        if search_field == "number":
            if not compact_query:
                return list(cards)
            return [
                card
                for card in cards
                if SEARCH_SEPARATOR_PATTERN.sub(
                    "",
                    self._normalize_search_text(card.repair_order.number).replace("ё", "е"),
                )
                == compact_query
            ]

        filtered: list[Card] = []
        for card in cards:
            order = card.repair_order
            if search_field == "date":
                opened_at = order.opened_at or self._repair_order_card_datetime(card.created_at)
                opened_at = opened_at or card.created_at or order.date or card.updated_at
                closed_at = order.closed_at or opened_at
                date_value = closed_at if status_filter == REPAIR_ORDER_STATUS_CLOSED else opened_at
                display_date = self._repair_order_card_datetime(date_value) or str(date_value or "")
                date_part = display_date.split(" ", 1)[0]
                date_parts = date_part.split(".")
                if len(date_parts) == 3 and len(date_parts[2]) == 4:
                    date_part = ".".join((*date_parts[:2], date_parts[2][-2:]))
                values = [date_part]
            elif search_field == "client":
                values = [order.client]
            elif search_field == "phone":
                values = [order.phone]
            elif search_field == "vehicle":
                values = [order.vehicle or card.vehicle]
            elif search_field == "license_plate":
                values = [
                    order.license_plate,
                    self._repair_order_list_summary(card),
                    order.reason,
                    card.heading(),
                ]
            else:  # summary uses the same fields as the browser search.
                values = [
                    self._repair_order_list_summary(card),
                    order.reason,
                    card.heading(),
                    order.vehicle or card.vehicle,
                    order.client,
                ]
            haystack = " ".join(
                normalized
                for normalized in (
                    self._normalize_search_text(value).replace("ё", "е") for value in values
                )
                if normalized
            )
            if not haystack:
                continue

            if search_field == "summary":
                haystack_tokens = haystack.split()
                matched = all(
                    any(token in word or word in token for word in haystack_tokens)
                    for token in tokens
                )
            else:
                compact_haystack = SEARCH_SEPARATOR_PATTERN.sub("", haystack)
                matched = normalized_query in haystack or (
                    bool(compact_query) and compact_query in compact_haystack
                )
            if matched:
                filtered.append(card)
        return filtered
