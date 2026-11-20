"""Cache derived search text while retaining live filters and serialization."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from weakref import ReferenceType, ref

from ..models import Card
from ..storage.readonly_context_snapshot import ReadonlyContextSnapshot

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
        self._lock = Lock()
        self._snapshot: ReferenceType[ReadonlyContextSnapshot] | None = None
        self._fields: dict[int, SearchFields] = {}

    def matcher(
        self, snapshot: ReadonlyContextSnapshot, query: str
    ) -> Callable[[Card], SearchMatch]:
        # Each search retains the fields for its own detached state generation.
        with self._lock:
            if self._snapshot is None or self._snapshot() is not snapshot:
                self._fields = {}
                self._snapshot = ref(snapshot)
            fields = self._fields
        prepared = self._prepare_query(query) if query else None

        def match(card: Card) -> SearchMatch:
            if prepared is None or not prepared[1]:
                return 0, []
            key = id(card)
            if key not in fields:
                fields[key] = self._prepare_fields(card)
            return self._match_fields(fields[key], prepared)

        return match
