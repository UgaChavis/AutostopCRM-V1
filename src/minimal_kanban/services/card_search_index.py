"""Cache derived search text while retaining live filters and serialization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Card

SearchFields = dict[str, list[str]]
PreparedQuery = tuple[list[str], list[list[str]]]
SearchMatch = tuple[int, list[str]]


class CardSearchIndex:
    def __init__(
        self,
        prepare_fields: Callable[[Card], SearchFields],
        prepare_query: Callable[[str], PreparedQuery],
        match_fields: Callable[[SearchFields, PreparedQuery], SearchMatch],
    ) -> None:
        self._prepare_fields = prepare_fields
        self._prepare_query = prepare_query
        self._match_fields = match_fields
        self._key: tuple[tuple[int, int, int, int] | None, int] | None = None
        self._fields: dict[str, SearchFields] = {}

    def matcher(
        self, bundle: dict[str, Any], signature: tuple[int, int, int, int] | None, query: str
    ) -> Callable[[Card], SearchMatch]:
        # File identity changes on local saves and external reloads, including
        # edits that preserve updated_at. Bundle identity also detects fresh
        # normalization with unchanged stat. The caller owns the service lock.
        key = (signature, id(bundle))
        if signature is None or key != self._key:
            self._fields = {}
            self._key = key
        prepared = self._prepare_query(query) if query else None
        fields = self._fields

        def match(card: Card) -> SearchMatch:
            if prepared is None or not prepared[1]:
                return 0, []
            if card.id not in fields:
                fields[card.id] = self._prepare_fields(card)
            return self._match_fields(fields[card.id], prepared)

        return match
