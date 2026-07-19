from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Any

from ..models import Card, Column, StickyNote, normalize_actor_name

SNAPSHOT_CACHE_MAX_ENTRIES = 32
COMPACT_SNAPSHOT_CACHE_TTL_SECONDS = 1.5


class SnapshotResponseCache:
    """Bounded cache invalidated by the atomically observed state signature."""

    def __init__(self, *, max_entries: int = SNAPSHOT_CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._signature: tuple[int, int, int, int] | None = None
        self._entries: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self._entries)

    @staticmethod
    def key(
        *,
        viewer_username: str | None,
        compact_cards: bool,
        include_archive: bool,
        archive_limit: int,
    ) -> tuple[Any, ...]:
        normalized_viewer = normalize_actor_name(viewer_username, default="").casefold()
        return normalized_viewer, compact_cards, include_archive, archive_limit

    def get(
        self,
        signature: tuple[int, int, int, int] | None,
        key: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        if signature is None:
            self._signature = None
            self._entries.clear()
            return None
        if signature != self._signature:
            self._signature = signature
            self._entries.clear()
            return None
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(
        self,
        signature: tuple[int, int, int, int] | None,
        key: tuple[Any, ...],
        entry: dict[str, Any],
    ) -> None:
        if signature is None:
            return
        if signature != self._signature:
            self._signature = signature
            self._entries.clear()
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


def build_snapshot_meta(
    *,
    archive_limit: int,
    compact_cards: bool,
    include_archive: bool,
    archived_cards_total: int,
    cards_returned: int,
    archive_returned: int,
    stickies_returned: int,
    revision: str,
) -> dict[str, Any]:
    return {
        "archive_limit": archive_limit,
        "compact_cards": compact_cards,
        "include_archive": include_archive,
        "archived_cards_total": archived_cards_total,
        "cards_returned": cards_returned,
        "archive_returned": archive_returned,
        "has_more_archive": include_archive and archived_cards_total > archive_returned,
        "stickies_returned": stickies_returned,
        "stickies_total": stickies_returned,
        "revision": revision,
    }


def build_snapshot_revision(
    *,
    columns: list[Column],
    cards: list[Card],
    archive: list[Card],
    stickies: list[StickyNote],
    settings: dict[str, Any],
    event_counts: dict[str, int],
    viewer_username: str | None,
    compact_cards: bool,
    include_archive: bool,
    archive_limit: int,
    json_dumps: Callable[..., str],
) -> str:
    def card_signature(card: Card) -> dict[str, Any]:
        return {
            "card": card.to_storage_dict(),
            "events_count": event_counts.get(card.id, 0),
            "viewer_seen_at": str(card.seen_by_users.get(str(viewer_username or "").strip()) or ""),
            "has_unseen_update": card.has_unseen_update_for(viewer_username),
        }

    revision_payload = {
        "columns": [column.to_dict() for column in columns],
        "cards": [card_signature(card) for card in cards],
        "archive": [card_signature(card) for card in archive],
        "stickies": [sticky.to_storage_dict() for sticky in stickies],
        "settings": dict(settings),
        "viewer_username": str(viewer_username or ""),
        "compact_cards": compact_cards,
        "include_archive": include_archive,
        "archive_limit": archive_limit,
    }
    serialized = json_dumps(revision_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
