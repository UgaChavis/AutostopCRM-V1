from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from threading import RLock
from typing import Any

from ..json_safety import json_safe_api_value as _json_safe_value
from ..models import (
    ARCHIVE_PREVIEW_LIMIT,
    AuditEvent,
    Card,
    Column,
    StickyNote,
    normalize_text,
    parse_datetime,
    short_entity_id,
    utc_now,
    utc_now_iso,
)
from ..storage.json_store import JsonStore
from .card_log_projection import CARD_JOURNAL_COMPACT_DEFAULT_LIMIT, CardLogProjection
from .card_search_index import CardSearchIndex
from .operator_visibility import (
    operator_can_access_employees_cashboxes,
    project_operator_result,
    public_snapshot_settings,
    visible_audit_events,
)
from .snapshot_cache import (
    COMPACT_SNAPSHOT_CACHE_TTL_SECONDS,
    PreparedSnapshotData,
    SnapshotResponseCache,
    build_prepared_snapshot_data,
    build_snapshot_meta,
    build_snapshot_revision,
)
from .snapshot_event_page import (
    encode_event_page_cursor,
    event_page_key,
    redacted_event_page_item,
)

REVIEW_BOARD_STALE_HOURS_DEFAULT = 48
REVIEW_BOARD_OVERLOAD_THRESHOLD_DEFAULT = 5
REVIEW_BOARD_PRIORITY_LIMIT_DEFAULT = 5
REVIEW_BOARD_EVENT_LIMIT_DEFAULT = 10
GPT_WALL_MARKDOWN_LINE_LIMIT = 3000
GPT_WALL_AGENT_EVENT_LIMIT = 20


def _json_dumps(
    payload: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
    safe_depth: int = 8,
) -> str:
    return json.dumps(
        _json_safe_value(payload, depth=safe_depth),
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        separators=separators,
        allow_nan=False,
    )


def _event_counts(events: list[AuditEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if event.card_id:
            counts[event.card_id] = counts.get(event.card_id, 0) + 1
    return counts


class SnapshotService:
    _event_counts = staticmethod(_event_counts)

    def __init__(
        self,
        store: JsonStore,
        lock: RLock,
        *,
        validated_optional_bool: Callable[..., bool],
        validated_limit: Callable[..., int],
        visible_cards: Callable[..., list[Card]],
        archived_cards: Callable[..., list[Card]],
        stickies: Callable[[list[StickyNote]], list[StickyNote]],
        column_labels: Callable[[list[Column]], dict[str, str]],
        serialize_card: Callable[..., dict],
        serialize_sticky: Callable[[StickyNote], dict],
        build_board_context_payload: Callable[..., dict[str, Any]],
        cards_for_wall: Callable[..., list[Card]],
        wall_events: Callable[..., list[dict]],
        validated_search_query: Callable[[Any], str],
        validated_optional_column: Callable[[Any, list[Column]], str | None],
        validated_optional_tag: Callable[[Any], str | None],
        validated_optional_indicator: Callable[[Any], str | None],
        validated_optional_status: Callable[[Any], str | None],
        card_search_index: CardSearchIndex,
        find_card: Callable[[list[Card], str | None], Card],
        events_for_card: Callable[[list[AuditEvent], str], list[AuditEvent]],
        fail: Callable[..., None],
        hydrate_event_details: Callable[[AuditEvent], AuditEvent] | None = None,
    ) -> None:
        self._store = store
        self._lock = lock
        self._validated_optional_bool = validated_optional_bool
        self._validated_limit = validated_limit
        self._visible_cards = visible_cards
        self._archived_cards = archived_cards
        self._stickies = stickies
        self._column_labels = column_labels
        self._serialize_card = serialize_card
        self._serialize_sticky = serialize_sticky
        self._build_board_context_payload = build_board_context_payload
        self._cards_for_wall = cards_for_wall
        self._wall_events = wall_events
        self._validated_search_query = validated_search_query
        self._validated_optional_column = validated_optional_column
        self._validated_optional_tag = validated_optional_tag
        self._validated_optional_indicator = validated_optional_indicator
        self._validated_optional_status = validated_optional_status
        self._card_search_index = card_search_index
        self._find_card = find_card
        self._events_for_card = events_for_card
        self._hydrate_event_details = hydrate_event_details
        self._fail = fail
        self._snapshot_cache = SnapshotResponseCache()
        self._card_log_projection = CardLogProjection(json_dumps=_json_dumps)

    def _viewer_username(self, payload: dict | None) -> str | None:
        raw_value = (payload or {}).get("actor_name")
        normalized = str(raw_value or "").strip()
        return normalized or None

    def _serialize_cards_payload(
        self,
        cards: list[Card],
        *,
        events: list[AuditEvent],
        column_labels: dict[str, str],
        event_counts: dict[str, int],
        viewer_username: str | None,
        compact: bool = False,
    ) -> list[dict]:
        return [
            self._serialize_card(
                card,
                events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact,
            )
            for card in cards
        ]

    def _card_serialization_context(
        self,
        cards: list[Card],
        *,
        columns: list[Column],
        events: list[AuditEvent],
    ) -> tuple[dict[str, str], dict[str, int]]:
        if not cards:
            return {}, {}
        return self._column_labels(columns), _event_counts(events)

    def _snapshot_revision(
        self,
        *,
        payload: dict[str, Any],
        columns: list[Column],
        cards: list[Card],
        archive: list[Card],
        stickies: list[StickyNote],
        events: list[AuditEvent],
        settings: dict[str, Any],
        viewer_username: str | None,
        compact_cards: bool,
        include_archive: bool,
        archive_limit: int,
    ) -> str:
        event_counts = _event_counts(events) if cards or archive else {}
        public_settings = public_snapshot_settings(
            settings,
            include_employees_cashboxes=operator_can_access_employees_cashboxes(payload),
        )
        return build_snapshot_revision(
            columns=columns,
            cards=cards,
            archive=archive,
            stickies=stickies,
            settings=public_settings,
            event_counts=event_counts,
            viewer_username=viewer_username,
            compact_cards=compact_cards,
            include_archive=include_archive,
            archive_limit=archive_limit,
            json_dumps=_json_dumps,
            card_projection=lambda card: project_operator_result(payload, card),
        )

    def _markdown_value(self, value: Any) -> str:
        if value is None or value == "":
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return _json_dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value).replace("\r", " ").replace("\n", " / ").strip() or "null"

    def _append_markdown_block(self, lines: list[str], key: str, value: Any) -> None:
        text = str(value or "").replace("\r", "").strip()
        if not text:
            lines.append(f"{key}: null")
            return
        lines.append(f"{key}: |")
        for raw_line in text.splitlines():
            lines.append(f"  {raw_line.rstrip()}")

    def _limit_markdown_wall_text(self, text: str) -> str:
        lines = text.splitlines()
        if len(lines) <= GPT_WALL_MARKDOWN_LINE_LIMIT:
            return text
        kept = max(GPT_WALL_MARKDOWN_LINE_LIMIT - 3, 0)
        return "\n".join(
            [
                *lines[:kept],
                "",
                f"> [WALL TRUNCATED] Reached {GPT_WALL_MARKDOWN_LINE_LIMIT} lines. Use get_board_content or get_board_events for section-specific reads.",
            ]
        )

    def _append_markdown_card(self, lines: list[str], card: dict, *, index: int) -> None:
        short_id = card.get("short_id") or short_entity_id(str(card.get("id") or ""), prefix="C")
        title = card.get("heading") or card.get("title") or card.get("vehicle") or "Без названия"
        lines.append(f"#### Card {index}: {self._markdown_value(short_id)}")
        for key in (
            "id",
            "short_id",
            "column",
            "column_label",
            "archived",
            "position",
            "vehicle",
            "title",
            "heading",
            "status",
            "indicator",
            "remaining_display",
            "deadline_timestamp",
            "created_at",
            "updated_at",
            "attachment_count",
            "events_count",
        ):
            output_key = "card_id" if key == "id" else key
            lines.append(f"{output_key}: {self._markdown_value(card.get(key))}")
        lines.append(f"display_title: {self._markdown_value(title)}")
        lines.append(f"tags: {self._markdown_value(card.get('tags') or [])}")
        if isinstance(card.get("vehicle_profile"), dict):
            lines.append(
                "vehicle_profile: " + self._markdown_value(card.get("vehicle_profile") or {})
            )
        lines.append(
            "vehicle_profile_compact: "
            + self._markdown_value(card.get("vehicle_profile_compact") or {})
        )
        self._append_markdown_block(lines, "description", card.get("description"))
        lines.append("")

    def _board_content_markdown_column_lines(
        self, column: Column, active_by_column: dict[str, list[dict]]
    ) -> list[str]:
        return [
            f"### Column: {self._markdown_value(column.label)}",
            f"column_id: {self._markdown_value(column.id)}",
            f"position: {self._markdown_value(column.position)}",
            f"active_cards: {len(active_by_column.get(column.id, []))}",
            "",
        ]

    def _board_content_markdown_sticky_lines(self, sticky: dict, *, index: int) -> list[str]:
        lines = [
            f"### Sticky {index}: {self._markdown_value(sticky.get('short_id') or sticky.get('id'))}",
            f"sticky_id: {self._markdown_value(sticky.get('id'))}",
            f"short_id: {self._markdown_value(sticky.get('short_id'))}",
            f"x: {self._markdown_value(sticky.get('x'))}",
            f"y: {self._markdown_value(sticky.get('y'))}",
            f"remaining_seconds: {self._markdown_value(sticky.get('remaining_seconds'))}",
        ]
        self._append_markdown_block(lines, "text", sticky.get("text"))
        lines.append("")
        return lines

    def _append_board_content_markdown_cards(
        self, lines: list[str], columns: list[Column], active_by_column: dict[str, list[dict]]
    ) -> None:
        lines.append("## Cards By Column")
        if not any(active_by_column.values()):
            lines.append("cards: []")
            lines.append("")
        for column in columns:
            column_cards = active_by_column.get(column.id, [])
            lines.append(f"### Column: {self._markdown_value(column.label)}")
            lines.append(f"column_id: {self._markdown_value(column.id)}")
            if not column_cards:
                lines.append("cards: []")
                lines.append("")
                continue
            for index, card in enumerate(column_cards, start=1):
                self._append_markdown_card(lines, card, index=index)

    def _append_board_content_markdown_stickies(
        self, lines: list[str], stickies: list[dict]
    ) -> None:
        lines.append("## Stickies")
        if not stickies:
            lines.append("stickies: []")
            return
        for index, sticky in enumerate(stickies, start=1):
            lines.extend(self._board_content_markdown_sticky_lines(sticky, index=index))

    def _build_board_content_markdown(
        self,
        columns: list[Column],
        cards: list[dict],
        stickies: list[dict],
        meta: dict[str, Any],
    ) -> str:
        active_cards = [card for card in cards if not card.get("archived")]
        archived_cards = [card for card in cards if card.get("archived")]
        active_by_column: dict[str, list[dict]] = {column.id: [] for column in columns}
        for card in active_cards:
            active_by_column.setdefault(str(card.get("column") or ""), []).append(card)

        lines = [
            "# AutoStop CRM Board Content",
            "",
            "## Metadata",
            f"generated_at: {self._markdown_value(meta.get('generated_at'))}",
            "text_format: markdown",
            "section_kind: board_content",
            f"include_archived: {self._markdown_value(meta.get('include_archived'))}",
            f"columns_total: {self._markdown_value(meta.get('columns'))}",
            f"active_cards_total: {self._markdown_value(meta.get('active_cards'))}",
            f"archived_cards_total: {self._markdown_value(meta.get('archived_cards'))}",
            f"cards_returned: {self._markdown_value(meta.get('cards_returned'))}",
            f"stickies_returned: {self._markdown_value(meta.get('stickies_returned'))}",
            "",
            "## Columns",
        ]
        if not columns:
            lines.append("columns: []")
        for column in columns:
            lines.extend(self._board_content_markdown_column_lines(column, active_by_column))

        self._append_board_content_markdown_cards(lines, columns, active_by_column)

        lines.append("## Archived Cards")
        if not archived_cards:
            lines.append("cards: []")
            lines.append("")
        for index, card in enumerate(archived_cards, start=1):
            self._append_markdown_card(lines, card, index=index)

        self._append_board_content_markdown_stickies(lines, stickies)
        return "\n".join(lines).rstrip()

    def _build_structured_event_log_text(self, events: list[dict], meta: dict[str, Any]) -> str:
        lines = [
            "# AutoStop CRM Event Log",
            "",
            "## Metadata",
            f"generated_at: {self._markdown_value(meta.get('generated_at'))}",
            "text_format: markdown",
            "section_kind: event_log",
            "event_order: newest_first",
            f"include_archived: {self._markdown_value(meta.get('include_archived'))}",
            f"events_returned: {self._markdown_value(meta.get('events_returned') or len(events))}",
            f"events_total: {self._markdown_value(meta.get('events_total') or len(events))}",
            f"event_limit: {self._markdown_value(meta.get('event_limit') or len(events))}",
            "",
            "## Events",
        ]
        if not events:
            lines.append("events: none")
            return "\n".join(lines)

        for index, event in enumerate(events, start=1):
            card_ref = str(event.get("card_short_id") or event.get("card_id") or "").strip()
            heading = str(event.get("card_heading") or "").strip()
            details = (
                str(event.get("details_text") or "").strip().replace("\r", "").replace("\n", " / ")
            )
            lines.extend(
                [
                    f"### Event {index}",
                    f"event_id: {self._markdown_value(event.get('id'))}",
                    f"time: {self._markdown_value(event.get('timestamp'))}",
                    f"actor: {self._markdown_value(event.get('actor_name'))}",
                    f"source: {self._markdown_value(event.get('source'))}",
                    f"action: {self._markdown_value(event.get('action'))}",
                    f"message: {self._markdown_value(event.get('message'))}",
                ]
            )
            if card_ref:
                lines.append(f"card: {card_ref}")
            if heading:
                lines.append(f"heading: {self._markdown_value(heading)}")
            if details:
                lines.append(f"details: {self._markdown_value(details)}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def get_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(
                payload, "include_archived", default=False
            )
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            bundle = self._store.read_bundle()
            events = visible_audit_events(payload, bundle["events"])
            cards = self._visible_cards(bundle["cards"], include_archived=include_archived)
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                cards,
                columns=bundle["columns"],
                events=events,
            )
            serialized_cards = self._serialize_cards_payload(
                cards,
                events=events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            return {
                "cards": serialized_cards,
                "meta": {
                    "include_archived": include_archived,
                    "compact": compact_cards,
                    "total": len(cards),
                    "returned": len(serialized_cards),
                    "has_more": False,
                },
            }

    def get_board_snapshot(self, payload: dict | None = None) -> dict:
        result = self._get_board_snapshot(payload, prepared=False)
        if isinstance(result, PreparedSnapshotData):  # pragma: no cover - internal invariant
            raise RuntimeError("Prepared snapshot escaped the HTTP-only path.")
        return project_operator_result(payload, result)

    def get_board_snapshot_for_http(
        self, payload: dict | None = None
    ) -> dict | PreparedSnapshotData:
        if operator_can_access_employees_cashboxes(payload):
            return self._get_board_snapshot(payload, prepared=True)
        result = self._get_board_snapshot(payload, prepared=False)
        if isinstance(result, PreparedSnapshotData):  # pragma: no cover - internal invariant
            raise RuntimeError("Prepared snapshot escaped the restricted HTTP path.")
        return project_operator_result(payload, result)

    def _get_board_snapshot(
        self,
        payload: dict | None,
        *,
        prepared: bool,
    ) -> dict | PreparedSnapshotData:
        with self._lock:
            payload = payload or {}
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            include_archive = self._validated_optional_bool(
                payload, "include_archive", default=True
            )
            archive_limit = (
                self._validated_limit(
                    payload.get("archive_limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=50
                )
                if include_archive
                else 0
            )
            bundle, signature = self._store.read_bundle_with_signature()
            viewer_username = self._viewer_username(payload)
            employees_cashboxes_access = operator_can_access_employees_cashboxes(payload)
            cache_key = self._snapshot_cache.key(
                viewer_username=viewer_username,
                employees_cashboxes_access=employees_cashboxes_access,
                compact_cards=compact_cards,
                include_archive=include_archive,
                archive_limit=archive_limit,
            )
            cache_entry = self._snapshot_cache.get(signature, cache_key)
            if compact_cards and cache_entry is not None:
                cached_snapshot = cache_entry.get("snapshot")
                cached_at = float(cache_entry.get("snapshot_cached_at") or 0.0)
                if (
                    isinstance(cached_snapshot, dict)
                    and time.monotonic() - cached_at <= COMPACT_SNAPSHOT_CACHE_TTL_SECONDS
                ):
                    if prepared:
                        cached_prepared = cache_entry.get("prepared_snapshot")
                        if isinstance(cached_prepared, PreparedSnapshotData):
                            return cached_prepared
                    result = deepcopy(cached_snapshot)
                    result["meta"]["generated_at"] = utc_now_iso()
                    return result
            cards = self._visible_cards(bundle["cards"], include_archived=False)
            archived_cards_total = sum(1 for card in bundle["cards"] if card.archived)
            archive = (
                self._archived_cards(bundle["cards"], limit=archive_limit)
                if include_archive
                else []
            )
            stickies = self._stickies(bundle["stickies"])
            events = visible_audit_events(payload, bundle["events"])
            column_labels, event_counts = self._card_serialization_context(
                cards + archive,
                columns=bundle["columns"],
                events=events,
            )
            revision = str((cache_entry or {}).get("revision") or "")
            if not revision:
                revision = self._snapshot_revision(
                    payload=payload,
                    columns=bundle["columns"],
                    cards=cards,
                    archive=archive,
                    stickies=stickies,
                    events=events,
                    settings=bundle["settings"],
                    viewer_username=viewer_username,
                    compact_cards=compact_cards,
                    include_archive=include_archive,
                    archive_limit=archive_limit,
                )
            serialized_columns = [column.to_dict() for column in bundle["columns"]]
            serialized_cards = self._serialize_cards_payload(
                cards,
                events=events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            serialized_archive = self._serialize_cards_payload(
                archive,
                events=events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            serialized_stickies = [self._serialize_sticky(sticky) for sticky in stickies]
            serialized_settings = public_snapshot_settings(
                bundle["settings"],
                include_employees_cashboxes=employees_cashboxes_access,
            )
            meta = build_snapshot_meta(
                archive_limit=archive_limit,
                compact_cards=compact_cards,
                include_archive=include_archive,
                archived_cards_total=archived_cards_total,
                cards_returned=len(serialized_cards),
                archive_returned=len(serialized_archive),
                stickies_returned=len(serialized_stickies),
                revision=revision,
            )
            meta["generated_at"] = utc_now_iso()
            result = {
                "columns": serialized_columns,
                "cards": serialized_cards,
                "archive": serialized_archive,
                "stickies": serialized_stickies,
                "settings": serialized_settings,
                "meta": meta,
            }
            entry = cache_entry or {}
            entry.update(
                {
                    "revision": revision,
                    "counts": {
                        "columns": len(bundle["columns"]),
                        "cards": len(cards),
                        "archive": len(archive),
                        "archived_cards_total": archived_cards_total,
                        "stickies": len(stickies),
                    },
                    "meta": {key: value for key, value in meta.items() if key != "generated_at"},
                }
            )
            if compact_cards:
                entry["snapshot"] = deepcopy(result)
                entry["prepared_snapshot"] = build_prepared_snapshot_data(
                    result,
                    json_dumps=_json_dumps,
                )
                entry["snapshot_cached_at"] = time.monotonic()
            else:
                entry.pop("snapshot", None)
                entry.pop("prepared_snapshot", None)
                entry.pop("snapshot_cached_at", None)
            self._snapshot_cache.put(signature, cache_key, entry)
            if prepared and compact_cards:
                return entry["prepared_snapshot"]
            return result

    def get_board_revision(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            include_archive = self._validated_optional_bool(
                payload, "include_archive", default=True
            )
            archive_limit = (
                self._validated_limit(
                    payload.get("archive_limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=50
                )
                if include_archive
                else 0
            )
            bundle, signature = self._store.read_bundle_with_signature()
            viewer_username = self._viewer_username(payload)
            employees_cashboxes_access = operator_can_access_employees_cashboxes(payload)
            cache_key = self._snapshot_cache.key(
                viewer_username=viewer_username,
                employees_cashboxes_access=employees_cashboxes_access,
                compact_cards=compact_cards,
                include_archive=include_archive,
                archive_limit=archive_limit,
            )
            cache_entry = self._snapshot_cache.get(signature, cache_key)
            if cache_entry is None or not cache_entry.get("revision"):
                cards = self._visible_cards(bundle["cards"], include_archived=False)
                archived_cards_total = sum(1 for card in bundle["cards"] if card.archived)
                archive = (
                    self._archived_cards(bundle["cards"], limit=archive_limit)
                    if include_archive
                    else []
                )
                stickies = self._stickies(bundle["stickies"])
                events = visible_audit_events(payload, bundle["events"])
                revision = self._snapshot_revision(
                    payload=payload,
                    columns=bundle["columns"],
                    cards=cards,
                    archive=archive,
                    stickies=stickies,
                    events=events,
                    settings=public_snapshot_settings(
                        bundle["settings"],
                        include_employees_cashboxes=employees_cashboxes_access,
                    ),
                    viewer_username=viewer_username,
                    compact_cards=compact_cards,
                    include_archive=include_archive,
                    archive_limit=archive_limit,
                )
                counts = {
                    "columns": len(bundle["columns"]),
                    "cards": len(cards),
                    "archive": len(archive),
                    "archived_cards_total": archived_cards_total,
                    "stickies": len(stickies),
                }
                meta = build_snapshot_meta(
                    archive_limit=archive_limit,
                    compact_cards=compact_cards,
                    include_archive=include_archive,
                    archived_cards_total=archived_cards_total,
                    cards_returned=len(cards),
                    archive_returned=len(archive),
                    stickies_returned=len(stickies),
                    revision=revision,
                )
                cache_entry = {
                    "revision": revision,
                    "counts": counts,
                    "meta": meta,
                }
                self._snapshot_cache.put(signature, cache_key, cache_entry)
            revision = str(cache_entry["revision"])
            counts = deepcopy(cache_entry["counts"])
            meta = deepcopy(cache_entry["meta"])
            meta["generated_at"] = utc_now_iso()
            return {
                "revision": revision,
                "counts": counts,
                "meta": meta,
            }

    def get_board_context(self, payload: dict | None = None) -> dict:
        with self._lock:
            _ = payload or {}
            bundle = self._store.read_bundle()
            context_payload = self._build_board_context_payload(
                bundle["columns"],
                bundle["cards"],
                bundle["stickies"],
                bundle["settings"],
            )
            return {
                "context": context_payload["context"],
                "text": context_payload["text"],
                "meta": {
                    "generated_at": utc_now_iso(),
                    "view_mode": "summary",
                    "columns": context_payload["context"]["columns_total"],
                    "active_cards": context_payload["context"]["active_cards_total"],
                    "archived_cards": context_payload["context"]["archived_cards_total"],
                    "stickies": context_payload["context"]["stickies_total"],
                },
            }

    def review_board(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            stale_hours = self._validated_limit(
                payload.get("stale_hours"),
                default=REVIEW_BOARD_STALE_HOURS_DEFAULT,
                maximum=24 * 30,
            )
            overload_threshold = self._validated_limit(
                payload.get("overload_threshold"),
                default=REVIEW_BOARD_OVERLOAD_THRESHOLD_DEFAULT,
                maximum=100,
            )
            priority_limit = self._validated_limit(
                payload.get("priority_limit"),
                default=REVIEW_BOARD_PRIORITY_LIMIT_DEFAULT,
                maximum=20,
            )
            recent_event_limit = self._validated_limit(
                payload.get("recent_event_limit"),
                default=REVIEW_BOARD_EVENT_LIMIT_DEFAULT,
                maximum=50,
            )
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            events = visible_audit_events(payload, bundle["events"])
            now = utc_now()
            column_labels = self._column_labels(columns)
            cards_by_id = {card.id: card for card in cards}
            latest_event_by_card = self._latest_event_by_card(events)
            active_cards = [card for card in cards if not card.archived]
            archived_cards_total = sum(1 for card in cards if card.archived)
            card_states = [
                self._review_card_state(
                    card,
                    now=now,
                    stale_hours=stale_hours,
                    column_labels=column_labels,
                    latest_event=latest_event_by_card.get(card.id),
                )
                for card in active_cards
            ]
            summary = {
                "active_cards": len(card_states),
                "archived_cards": archived_cards_total,
                "overdue_cards": sum(1 for item in card_states if item["overdue"]),
                "critical_cards": sum(1 for item in card_states if item["critical"]),
                "stale_cards": sum(1 for item in card_states if item["stale"]),
            }
            by_column: list[dict[str, Any]] = []
            for column in columns:
                column_cards = [item for item in card_states if item["column_id"] == column.id]
                by_column.append(
                    {
                        "column_id": column.id,
                        "label": column.label,
                        "count": len(column_cards),
                        "stale_count": sum(1 for item in column_cards if item["stale"]),
                        "overdue_count": sum(1 for item in column_cards if item["overdue"]),
                        "critical_count": sum(1 for item in column_cards if item["critical"]),
                    }
                )

            alerts: list[str] = []
            if summary["overdue_cards"]:
                alerts.append(f"{summary['overdue_cards']} просроченных карточек")
            for item in by_column:
                if item["count"] >= overload_threshold:
                    alerts.append(f"Колонка {item['label']} перегружена")
            if summary["stale_cards"]:
                alerts.append(
                    f"{summary['stale_cards']} карточек без движения более {stale_hours} ч"
                )
            critical_stale_cards = [
                item for item in card_states if item["critical"] and item["stale"]
            ]
            if critical_stale_cards:
                alerts.append(f"{len(critical_stale_cards)} критичных карточек без обновлений")

            priority_cards = [
                {
                    "card_id": item["card_id"],
                    "short_id": item["short_id"],
                    "title": item["title"],
                    "vehicle": item["vehicle"],
                    "column": item["column_id"],
                    "column_label": item["column_label"],
                    "indicator": item["indicator"],
                    "short_reason": item["short_reason"],
                }
                for item in sorted(
                    (item for item in card_states if item["priority_score"] > 0),
                    key=lambda item: (
                        -item["priority_score"],
                        -item["stale_hours"],
                        item["deadline_sort"],
                        item["title"],
                    ),
                )[:priority_limit]
            ]
            recent_events = self._review_recent_events(
                events,
                cards_by_id=cards_by_id,
                column_labels=column_labels,
                limit=recent_event_limit,
            )
            return {
                "summary": summary,
                "by_column": by_column,
                "alerts": alerts,
                "priority_cards": priority_cards,
                "recent_events": recent_events,
                "meta": {
                    "generated_at": utc_now_iso(),
                    "stale_hours": stale_hours,
                    "overload_threshold": overload_threshold,
                    "priority_limit": priority_limit,
                    "recent_event_limit": recent_event_limit,
                },
                "text": self._build_review_board_text(
                    summary=summary,
                    by_column=by_column,
                    alerts=alerts,
                    priority_cards=priority_cards,
                    recent_events=recent_events,
                ),
            }

    def get_gpt_wall(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(
                payload, "include_archived", default=True
            )
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            event_limit = self._validated_limit(
                payload.get("event_limit"), default=100, maximum=5000
            )
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            stickies = bundle["stickies"]
            events = visible_audit_events(payload, bundle["events"])
            column_labels = self._column_labels(columns)
            cards_by_id = {card.id: card for card in cards}
            ordered_cards = self._cards_for_wall(cards, columns, include_archived=include_archived)
            event_counts = _event_counts(events)
            viewer_username = self._viewer_username(payload)
            wall_cards = self._serialize_cards_payload(
                ordered_cards,
                events=events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            wall_stickies = [self._serialize_sticky(sticky) for sticky in self._stickies(stickies)]
            wall_events = self._wall_events(events, cards_by_id, column_labels, limit=event_limit)
            board_context = self._build_board_context_payload(
                columns, cards, stickies, bundle["settings"]
            )
            board_context_counts = board_context["context"]
            meta = {
                "generated_at": utc_now_iso(),
                "text_format": "markdown",
                "section_kind": "gpt_wall",
                "event_order": "newest_first",
                "columns": len(columns),
                "active_cards": board_context_counts["active_cards_total"],
                "archived_cards": board_context_counts["archived_cards_total"],
                "stickies": len(wall_stickies),
                "cards_returned": len(wall_cards),
                "stickies_returned": len(wall_stickies),
                "cards_compact": compact_cards,
                "events_total": len(events),
                "events_returned": len(wall_events),
                "has_more_events": len(events) > len(wall_events),
                "event_limit": event_limit,
                "include_archived": include_archived,
            }
            board_content_meta = {
                "generated_at": meta["generated_at"],
                "text_format": "markdown",
                "section_kind": "board_content",
                "columns": meta["columns"],
                "active_cards": meta["active_cards"],
                "archived_cards": meta["archived_cards"],
                "stickies": meta["stickies"],
                "cards_returned": meta["cards_returned"],
                "stickies_returned": meta["stickies_returned"],
                "cards_compact": meta["cards_compact"],
                "include_archived": meta["include_archived"],
            }
            event_log_meta = {
                "generated_at": meta["generated_at"],
                "text_format": "markdown",
                "section_kind": "event_log",
                "event_order": "newest_first",
                "include_archived": meta["include_archived"],
                "events_total": meta["events_total"],
                "events_returned": meta["events_returned"],
                "event_limit": meta["event_limit"],
            }
            board_content_text = self._build_board_content_markdown(
                columns, wall_cards, wall_stickies, meta
            )
            event_log_text = self._build_structured_event_log_text(wall_events, meta)
            wall_text = self._limit_markdown_wall_text(f"{board_content_text}\n\n{event_log_text}")
            return {
                "meta": meta,
                "columns": [column.to_dict() for column in columns],
                "cards": wall_cards,
                "stickies": wall_stickies,
                "events": wall_events,
                "board_context": board_context,
                "sections": {
                    "board_content": {
                        "meta": board_content_meta,
                        "text": board_content_text,
                        "cards": wall_cards,
                        "stickies": wall_stickies,
                        "board_context": board_context,
                    },
                    "event_log": {
                        "meta": event_log_meta,
                        "text": event_log_text,
                        "events": wall_events,
                    },
                },
                "text": wall_text,
            }

    def get_board_content(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        view_mode = normalize_text(payload.get("view_mode"), default="agent", limit=20).lower()
        if view_mode not in {"agent", "full"}:
            view_mode = "agent"
        include_archived = self._validated_optional_bool(payload, "include_archived", default=True)
        wall = self.get_gpt_wall(
            {
                "include_archived": include_archived,
                "event_limit": GPT_WALL_AGENT_EVENT_LIMIT if view_mode == "agent" else 100,
                "compact": view_mode == "agent",
                "actor_name": payload.get("actor_name"),
                "_operator_session": payload.get("_operator_session"),
            }
        )
        sections = wall.get("sections") if isinstance(wall.get("sections"), dict) else {}
        section = dict(sections.get("board_content") or {})
        meta = dict(section.get("meta") or {})
        meta.update(
            {
                "response_mode": "agent_context" if view_mode == "agent" else "export",
                "view_mode": view_mode,
                "include_archived": include_archived,
            }
        )
        section["meta"] = meta
        return section

    def get_board_events(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        event_limit = self._validated_limit(payload.get("event_limit"), default=100, maximum=5000)
        include_archived = self._validated_optional_bool(payload, "include_archived", default=True)
        view_mode = normalize_text(payload.get("view_mode"), default="audit", limit=20).lower()
        if view_mode not in {"audit", "full"}:
            view_mode = "audit"
        wall = self.get_gpt_wall(
            {
                "include_archived": include_archived,
                "event_limit": event_limit,
                "actor_name": payload.get("actor_name"),
                "_operator_session": payload.get("_operator_session"),
            }
        )
        sections = wall.get("sections") if isinstance(wall.get("sections"), dict) else {}
        section = dict(sections.get("event_log") or {})
        meta = dict(section.get("meta") or {})
        meta.update(
            {
                "response_mode": "audit",
                "view_mode": view_mode,
                "event_limit": event_limit,
                "include_archived": include_archived,
                "event_order": "newest_first",
            }
        )
        section["meta"] = meta
        return section

    def get_board_event_page(self, payload: dict | None = None) -> dict:
        """Return a stable, deliberately minimal audit-event page for Manager."""

        payload = payload or {}
        limit = self._validated_limit(payload.get("limit"), default=200, maximum=500)
        include_archived = self._validated_optional_bool(payload, "include_archived", default=True)
        cursor_key = self._decode_event_page_cursor(payload.get("cursor"))
        with self._lock:
            bundle = self._store.read_bundle()
            archived_by_card_id = {card.id: card.archived for card in bundle["cards"]}
            visible_events = visible_audit_events(payload, bundle["events"])
            events = [
                event
                for event in visible_events
                if include_archived or not archived_by_card_id.get(event.card_id or "", False)
            ]
            ordered = sorted(events, key=event_page_key)
            if cursor_key is not None:
                ordered = [event for event in ordered if event_page_key(event) > cursor_key]
            page_events = ordered[:limit]
            has_more = len(ordered) > len(page_events)
            next_cursor = (
                encode_event_page_cursor(event_page_key(page_events[-1]))
                if has_more and page_events
                else None
            )
            return {
                "events": [redacted_event_page_item(event) for event in page_events],
                "next_cursor": next_cursor,
                "has_more": has_more,
                "total_count": len(events),
                "schema_version": "board_event_page.v1",
            }

    def _decode_event_page_cursor(self, value: object) -> tuple[str, str] | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str) or len(value) > 512:
            self._fail("validation_error", "Некорректный cursor.", details={"field": "cursor"})
        try:
            padding = "=" * (-len(value) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8"))
            if (
                not isinstance(decoded, dict)
                or decoded.get("v") != 1
                or not isinstance(decoded.get("at"), str)
                or not isinstance(decoded.get("id"), str)
                or parse_datetime(decoded["at"]) is None
                or not decoded["id"]
            ):
                raise ValueError("invalid cursor")
            return decoded["at"], decoded["id"]
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._fail("validation_error", "Некорректный cursor.", details={"field": "cursor"})
        return None

    def list_archived_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(
                payload.get("limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=100
            )
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            bundle = self._store.read_bundle()
            events = visible_audit_events(payload, bundle["events"])
            archived_total = sum(1 for card in bundle["cards"] if card.archived)
            archived = self._archived_cards(bundle["cards"], limit=limit)
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                archived,
                columns=bundle["columns"],
                events=events,
            )
            return {
                "cards": self._serialize_cards_payload(
                    archived,
                    events=events,
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                    compact=compact_cards,
                ),
                "meta": {
                    "limit": limit,
                    "compact": compact_cards,
                    "total": archived_total,
                    "returned": len(archived),
                    "has_more": archived_total > len(archived),
                },
            }

    def search_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(
                payload, "include_archived", default=False
            )
            limit = self._validated_limit(payload.get("limit"), default=20, maximum=100)
            bundle, signature = self._store.read_bundle_with_signature()
            columns = bundle["columns"]
            events = visible_audit_events(payload, bundle["events"])
            viewer_username = self._viewer_username(payload)
            query = self._validated_search_query(payload.get("query"))
            column = self._validated_optional_column(payload.get("column"), columns)
            tag = self._validated_optional_tag(payload.get("tag"))
            indicator = self._validated_optional_indicator(payload.get("indicator"))
            status = self._validated_optional_status(payload.get("status"))

            if not any([query, column, tag, indicator, status]):
                self._fail(
                    "validation_error",
                    "Для поиска нужно передать query или хотя бы один фильтр: column, tag, indicator, status.",
                    details={"fields": ["query", "column", "tag", "indicator", "status"]},
                )

            match_card = self._card_search_index.matcher(bundle, signature, query)
            matches: list[tuple[int, Card, list[str]]] = []
            for card in bundle["cards"]:
                if card.archived and not include_archived:
                    continue
                if column and card.column != column:
                    continue
                if tag and tag not in card.tag_labels():
                    continue
                if status and card.status() != status:
                    continue
                if indicator and card.indicator() != indicator:
                    continue
                score, fields = match_card(card)
                if query and score <= 0:
                    continue
                matches.append((score, card, fields))

            matches.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
            limited_matches = matches[:limit]
            cards_payload: list[dict] = []
            limited_cards = [card for _, card, _ in limited_matches]
            column_labels, event_counts = self._card_serialization_context(
                limited_cards,
                columns=columns,
                events=events,
            )
            for score, card, fields in limited_matches:
                serialized = self._serialize_card(
                    card,
                    events,
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                )
                serialized["match"] = {
                    "score": score,
                    "fields": fields,
                    "query": query,
                    "tag": tag,
                }
                cards_payload.append(serialized)

            return {
                "cards": cards_payload,
                "meta": {
                    "query": query,
                    "limit": limit,
                    "total_matches": len(matches),
                    "returned": len(cards_payload),
                    "has_more": len(matches) > len(cards_payload),
                    "include_archived": include_archived,
                    "filters": {
                        "column": column,
                        "tag": tag,
                        "indicator": indicator,
                        "status": status,
                    },
                },
            }

    def list_overdue_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(
                payload, "include_archived", default=False
            )
            bundle = self._store.read_bundle()
            events = visible_audit_events(payload, bundle["events"])
            viewer_username = self._viewer_username(payload)
            overdue_cards = [
                card
                for card in bundle["cards"]
                if card.status() == "expired" and (include_archived or not card.archived)
            ]
            overdue_cards.sort(key=lambda item: item.deadline_timestamp)
            column_labels, event_counts = self._card_serialization_context(
                overdue_cards,
                columns=bundle["columns"],
                events=events,
            )
            return {
                "cards": self._serialize_cards_payload(
                    overdue_cards,
                    events=events,
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                )
            }

    def get_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            events = visible_audit_events(payload, bundle["events"])
            column_labels = self._column_labels(bundle["columns"])
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=column_labels,
                    include_removed_attachments=True,
                    include_attachment_status=self._validated_optional_bool(
                        payload, "include_attachment_status", default=True
                    ),
                    viewer_username=self._viewer_username(payload),
                )
            }

    def get_card_log(self, payload: dict) -> dict:
        with self._lock:
            payload = payload or {}
            compact = self._validated_optional_bool(payload, "compact", default=False)
            include_full_details = self._validated_optional_bool(
                payload, "include_full_details", default=False
            )
            limit_raw = payload.get("limit")
            limit = (
                self._validated_limit(limit_raw, default=100, maximum=1000)
                if limit_raw is not None
                else CARD_JOURNAL_COMPACT_DEFAULT_LIMIT
                if compact
                else None
            )
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            card_events = visible_audit_events(
                payload,
                self._events_for_card(bundle["events"], card.id),
            )
            full_details_included = bool(
                include_full_details and operator_can_access_employees_cashboxes(payload)
            )
            if full_details_included and self._hydrate_event_details is not None:
                card_events = [self._hydrate_event_details(event) for event in card_events]
            events = [
                event.to_dict()
                for event in (card_events[:limit] if limit is not None else card_events)
            ]
            entries = self._card_log_projection._card_log_entries(events, card=card)
            if compact:
                entries = self._card_log_projection._compact_card_log_entries(entries)
            days = self._card_log_projection._card_log_group_entries(
                entries, key="day_key", kind="day"
            )
            weeks = self._card_log_projection._card_log_group_entries(
                entries, key="week_key", kind="week"
            )
            months = self._card_log_projection._card_log_group_entries(
                entries, key="month_key", kind="month"
            )
            if compact:
                days = self._card_log_projection._compact_card_log_groups(days)
                weeks = self._card_log_projection._compact_card_log_groups(weeks)
                months = self._card_log_projection._compact_card_log_groups(months)
            totals = self._card_log_projection._card_log_totals(entries)
            newest_timestamp = entries[0]["timestamp"] if entries else ""
            oldest_timestamp = entries[-1]["timestamp"] if entries else ""
            meta = {
                "schema_version": "card_journal.v2",
                "card_id": card.id,
                "card_short_id": short_entity_id(card.id, prefix="C"),
                "card_heading": card.heading(),
                "limit": limit,
                "events_total": len(card_events),
                "events_returned": len(events),
                "has_more": len(card_events) > len(events),
                "first_timestamp": newest_timestamp,
                "last_timestamp": oldest_timestamp,
                "newest_timestamp": newest_timestamp,
                "oldest_timestamp": oldest_timestamp,
                "format": "json_compact" if compact else "markdown+json",
                "text_alias": "" if compact else "markdown",
                "event_order": "newest_first",
                "compact": compact,
                "include_full_details": full_details_included,
            }
            if compact:
                return {
                    "entries": entries,
                    "timeline": entries,
                    "days": days,
                    "weeks": weeks,
                    "months": months,
                    "totals": totals,
                    "meta": {
                        **meta,
                    },
                }
            markdown = self._card_log_projection._card_log_markdown(
                card=card,
                entries=entries,
                days=days,
                weeks=weeks,
                months=months,
                totals=totals,
                meta=meta,
            )
            return {
                "events": events,
                "entries": entries,
                "timeline": entries,
                "days": days,
                "weeks": weeks,
                "months": months,
                "totals": totals,
                "markdown": markdown,
                "text": markdown,
                "meta": {
                    **meta,
                },
            }

    def _latest_event_by_card(self, events: list[AuditEvent]) -> dict[str, AuditEvent]:
        latest: dict[str, AuditEvent] = {}
        for event in events:
            if not event.card_id:
                continue
            current = latest.get(event.card_id)
            if current is None or str(event.timestamp) > str(current.timestamp):
                latest[event.card_id] = event
        return latest

    def _review_card_state(
        self,
        card: Card,
        *,
        now: datetime,
        stale_hours: int,
        column_labels: dict[str, str],
        latest_event: AuditEvent | None,
    ) -> dict[str, Any]:
        updated_at = parse_datetime(card.updated_at) or parse_datetime(card.created_at) or now
        latest_event_at = (
            parse_datetime(latest_event.timestamp) if latest_event is not None else None
        )
        last_activity = updated_at
        if latest_event_at is not None and latest_event_at > last_activity:
            last_activity = latest_event_at
        stale_age_seconds = max(0.0, (now - last_activity).total_seconds())
        stale_age_hours = int(stale_age_seconds // 3600)
        status = card.status(now)
        indicator = card.indicator(now)
        overdue = status == "expired"
        critical = indicator == "red" or status in {"critical", "expired"}
        stale = stale_age_seconds >= stale_hours * 3600
        priority_score = 0
        if overdue:
            priority_score += 4
        if critical:
            priority_score += 3
        if stale:
            priority_score += 2
        if status == "warning" or indicator == "yellow":
            priority_score += 1
        return {
            "card_id": card.id,
            "short_id": short_entity_id(card.id, prefix="C"),
            "title": card.title,
            "vehicle": card.vehicle_display(),
            "column_id": card.column,
            "column_label": column_labels.get(card.column, card.column),
            "indicator": indicator,
            "overdue": overdue,
            "critical": critical,
            "stale": stale,
            "stale_hours": stale_age_hours,
            "deadline_sort": card.deadline_timestamp or "",
            "short_reason": self._review_short_reason(
                overdue=overdue,
                critical=critical,
                stale=stale,
                stale_hours=stale_age_hours,
                status=status,
            ),
            "priority_score": priority_score,
        }

    def _review_short_reason(
        self,
        *,
        overdue: bool,
        critical: bool,
        stale: bool,
        stale_hours: int,
        status: str,
    ) -> str:
        if overdue and stale:
            return f"Просрочена и без движения {stale_hours} ч"
        if overdue:
            return "Просрочена"
        if critical and stale:
            return f"Критичная и без движения {stale_hours} ч"
        if critical:
            return "Критичный сигнал"
        if stale:
            return f"Без движения {stale_hours} ч"
        if status == "warning":
            return "Срок подходит"
        return "Требует внимания"

    def _review_recent_events(
        self,
        events: list[AuditEvent],
        *,
        cards_by_id: dict[str, Card],
        column_labels: dict[str, str],
        limit: int,
    ) -> list[dict[str, Any]]:
        reviewable = self._wall_events(
            events, cards_by_id, column_labels, limit=max(limit * 4, limit)
        )
        result: list[dict[str, Any]] = []
        for event in reviewable:
            if not self._is_review_relevant_event(event):
                continue
            result.append(
                {
                    "type": event.get("action"),
                    "timestamp": event.get("timestamp"),
                    "actor_name": event.get("actor_name"),
                    "card_id": event.get("card_id"),
                    "card_short_id": event.get("card_short_id"),
                    "text": event.get("message"),
                    "related_to": event.get("card_heading") or event.get("details_text") or "",
                }
            )
            if len(result) >= limit:
                break
        return result

    def _is_review_relevant_event(self, event: dict[str, Any]) -> bool:
        action = str(event.get("action") or "").strip().lower()
        if action in {
            "card_created",
            "card_moved",
            "card_archived",
            "card_restored",
            "repair_order_updated",
            "repair_order_open",
            "repair_order_closed",
            "repair_order_autofilled",
            "description_changed",
            "title_changed",
            "vehicle_changed",
            "vehicle_profile_updated",
            "signal_changed",
            "signal_indicator_changed",
            "timer_started",
            "timer_restarted",
            "timer_stopped",
            "tags_changed",
        }:
            return True
        return "repair_order_" in action

    def _build_review_board_text(
        self,
        *,
        summary: dict[str, Any],
        by_column: list[dict[str, Any]],
        alerts: list[str],
        priority_cards: list[dict[str, Any]],
        recent_events: list[dict[str, Any]],
    ) -> str:
        lines = [
            "[BOARD REVIEW]",
            f"active_cards: {summary.get('active_cards', 0)}",
            f"archived_cards: {summary.get('archived_cards', 0)}",
            f"overdue_cards: {summary.get('overdue_cards', 0)}",
            f"critical_cards: {summary.get('critical_cards', 0)}",
            f"stale_cards: {summary.get('stale_cards', 0)}",
            "",
            "[ALERTS]",
        ]
        if alerts:
            lines.extend(f"- {item}" for item in alerts)
        else:
            lines.append("- no critical alerts")
        lines.extend(["", "[BY COLUMN]"])
        for item in by_column:
            lines.append(
                f"- {item.get('label') or item.get('column_id')}: count={item.get('count', 0)}, stale={item.get('stale_count', 0)}, overdue={item.get('overdue_count', 0)}, critical={item.get('critical_count', 0)}"
            )
        lines.extend(["", "[PRIORITY CARDS]"])
        if priority_cards:
            for item in priority_cards:
                lines.append(
                    f"- {item.get('short_id') or item.get('card_id')}: {item.get('vehicle') or '-'} / {item.get('title') or '-'} | {item.get('column_label') or item.get('column') or '-'} | {item.get('indicator') or '-'} | {item.get('short_reason') or '-'}"
                )
        else:
            lines.append("- no priority cards")
        lines.extend(["", "[RECENT EVENTS]"])
        if recent_events:
            for item in recent_events:
                lines.append(
                    f"- {item.get('timestamp') or '-'} | {item.get('actor_name') or '-'} | {item.get('type') or '-'} | {item.get('card_short_id') or item.get('card_id') or '-'} | {item.get('text') or '-'}"
                )
        else:
            lines.append("- no recent events")
        return "\n".join(lines) + "\n"
