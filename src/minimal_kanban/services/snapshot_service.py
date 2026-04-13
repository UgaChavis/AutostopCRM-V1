from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Any

from ..models import ARCHIVE_PREVIEW_LIMIT, AuditEvent, Card, Column, StickyNote, parse_datetime, short_entity_id, utc_now, utc_now_iso
from ..storage.json_store import JsonStore

REVIEW_BOARD_STALE_HOURS_DEFAULT = 48
REVIEW_BOARD_OVERLOAD_THRESHOLD_DEFAULT = 5
REVIEW_BOARD_PRIORITY_LIMIT_DEFAULT = 5
REVIEW_BOARD_EVENT_LIMIT_DEFAULT = 10

GPT_WALL_EVENTS_HEADER = "[ЛЕНТА СОБЫТИЙ]"


class SnapshotService:
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
        build_gpt_wall_text: Callable[..., str],
        validated_search_query: Callable[[Any], str],
        validated_optional_column: Callable[[Any, list[Column]], str | None],
        validated_optional_tag: Callable[[Any], str | None],
        validated_optional_indicator: Callable[[Any], str | None],
        validated_optional_status: Callable[[Any], str | None],
        search_card_match: Callable[[Card, str], tuple[int, list[str]]],
        find_card: Callable[[list[Card], str | None], Card],
        events_for_card: Callable[[list[AuditEvent], str], list[AuditEvent]],
        fail: Callable[..., None],
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
        self._build_gpt_wall_text = build_gpt_wall_text
        self._validated_search_query = validated_search_query
        self._validated_optional_column = validated_optional_column
        self._validated_optional_tag = validated_optional_tag
        self._validated_optional_indicator = validated_optional_indicator
        self._validated_optional_status = validated_optional_status
        self._search_card_match = search_card_match
        self._find_card = find_card
        self._events_for_card = events_for_card
        self._fail = fail

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
        return self._column_labels(columns), self._event_counts(events)

    def _snapshot_card_signature(
        self,
        *,
        card: Card,
        events_count: int,
        viewer_username: str | None,
    ) -> dict[str, Any]:
        return {
            "card": card.to_storage_dict(),
            "events_count": events_count,
            "viewer_seen_at": str(card.seen_by_users.get(str(viewer_username or "").strip()) or ""),
            "has_unseen_update": card.has_unseen_update_for(viewer_username),
        }

    def _snapshot_revision(
        self,
        *,
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
        event_counts = self._event_counts(events) if cards or archive else {}
        revision_payload = {
            "columns": [column.to_dict() for column in columns],
            "cards": [
                self._snapshot_card_signature(
                    card=card,
                    events_count=event_counts.get(card.id, 0),
                    viewer_username=viewer_username,
                )
                for card in cards
            ],
            "archive": [
                self._snapshot_card_signature(
                    card=card,
                    events_count=event_counts.get(card.id, 0),
                    viewer_username=viewer_username,
                )
                for card in archive
            ],
            "stickies": [sticky.to_storage_dict() for sticky in stickies],
            "settings": dict(settings),
            "viewer_username": str(viewer_username or ""),
            "compact_cards": compact_cards,
            "include_archive": include_archive,
            "archive_limit": archive_limit,
        }
        serialized = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _extract_board_content_text(self, full_text: str) -> str:
        normalized = str(full_text or "").strip()
        marker = f"\n{GPT_WALL_EVENTS_HEADER}\n"
        if marker in normalized:
            return normalized.split(marker, 1)[0].rstrip()
        return normalized

    def _build_event_log_text(self, events: list[dict], meta: dict[str, Any]) -> str:
        lines = [
            "[ЖУРНАЛ СОБЫТИЙ ДОСКИ]",
            (
                "собрано: {generated_at} | показано: {events_returned} | "
                "всего: {events_total} | лимит: {event_limit}"
            ).format(
                generated_at=meta.get("generated_at") or "—",
                events_returned=meta.get("events_returned") or len(events),
                events_total=meta.get("events_total") or len(events),
                event_limit=meta.get("event_limit") or len(events),
            ),
            "",
        ]
        if not events:
            lines.append("СОБЫТИЙ НЕТ.")
            return "\n".join(lines)

        for event in events:
            parts = [
                str(event.get("timestamp") or "—"),
                str(event.get("actor_name") or "—"),
                str(event.get("message") or "—"),
            ]
            related_parts: list[str] = []
            if event.get("card_short_id"):
                related_parts.append(str(event["card_short_id"]))
            elif event.get("card_id"):
                related_parts.append(str(event["card_id"]))
            if event.get("card_heading"):
                related_parts.append(str(event["card_heading"]))
            if event.get("details_text"):
                related_parts.append(str(event["details_text"]))
            if related_parts:
                parts.append(" | ".join(related_parts))
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def _build_structured_event_log_text(self, events: list[dict], meta: dict[str, Any]) -> str:
        lines = [
            "[Р–РЈР РќРђР› РЎРћР‘Р«РўРР™ Р”РћРЎРљР]",
            f"generated_at: {meta.get('generated_at') or 'вЂ”'}",
            f"shown: {meta.get('events_returned') or len(events)}",
            f"total: {meta.get('events_total') or len(events)}",
            f"limit: {meta.get('event_limit') or len(events)}",
            "",
        ]
        if not events:
            lines.append("events: none")
            return "\n".join(lines)

        for index, event in enumerate(events, start=1):
            card_ref = str(event.get("card_short_id") or event.get("card_id") or "").strip()
            heading = str(event.get("card_heading") or "").strip()
            details = str(event.get("details_text") or "").strip().replace("\r", "").replace("\n", " / ")
            lines.extend(
                [
                    f"[event {index}]",
                    f"time: {event.get('timestamp') or 'вЂ”'}",
                    f"actor: {event.get('actor_name') or 'вЂ”'}",
                    f"source: {event.get('source') or 'вЂ”'}",
                    f"action: {event.get('action') or 'вЂ”'}",
                    f"message: {event.get('message') or 'вЂ”'}",
                ]
            )
            if card_ref:
                lines.append(f"card: {card_ref}")
            if heading:
                lines.append(f"heading: {heading}")
            if details:
                lines.append(f"details: {details}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def get_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(payload, "include_archived", default=False)
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            bundle = self._store.read_bundle()
            cards = self._visible_cards(bundle["cards"], include_archived=include_archived)
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                cards,
                columns=bundle["columns"],
                events=bundle["events"],
            )
            return {
                "cards": self._serialize_cards_payload(
                    cards,
                    events=bundle["events"],
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                    compact=compact_cards,
                )
            }

    def get_board_snapshot(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            archive_limit = self._validated_limit(payload.get("archive_limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=50)
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            include_archive = self._validated_optional_bool(payload, "include_archive", default=True)
            bundle = self._store.read_bundle()
            cards = self._visible_cards(bundle["cards"], include_archived=False)
            archived_cards_total = sum(1 for card in bundle["cards"] if card.archived)
            archive = self._archived_cards(bundle["cards"], limit=archive_limit) if include_archive else []
            stickies = self._stickies(bundle["stickies"])
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                cards + archive,
                columns=bundle["columns"],
                events=bundle["events"],
            )
            revision = self._snapshot_revision(
                columns=bundle["columns"],
                cards=cards,
                archive=archive,
                stickies=stickies,
                events=bundle["events"],
                settings=bundle["settings"],
                viewer_username=viewer_username,
                compact_cards=compact_cards,
                include_archive=include_archive,
                archive_limit=archive_limit,
            )
            serialized_columns = [column.to_dict() for column in bundle["columns"]]
            serialized_cards = self._serialize_cards_payload(
                cards,
                events=bundle["events"],
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            serialized_archive = self._serialize_cards_payload(
                archive,
                events=bundle["events"],
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
                compact=compact_cards,
            )
            serialized_stickies = [self._serialize_sticky(sticky) for sticky in stickies]
            serialized_settings = dict(bundle["settings"])
            return {
                "columns": serialized_columns,
                "cards": serialized_cards,
                "archive": serialized_archive,
                "stickies": serialized_stickies,
                "settings": serialized_settings,
                "meta": {
                    "generated_at": utc_now_iso(),
                    "archive_limit": archive_limit,
                    "compact_cards": compact_cards,
                    "include_archive": include_archive,
                    "archived_cards_total": archived_cards_total,
                    "stickies_total": len(stickies),
                    "revision": revision,
                },
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
            events = bundle["events"]
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
                alerts.append(f"{summary['stale_cards']} карточек без движения более {stale_hours} ч")
            critical_stale_cards = [item for item in card_states if item["critical"] and item["stale"]]
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
            include_archived = self._validated_optional_bool(payload, "include_archived", default=True)
            event_limit = self._validated_limit(payload.get("event_limit"), default=100, maximum=5000)
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            stickies = bundle["stickies"]
            events = bundle["events"]
            column_labels = self._column_labels(columns)
            cards_by_id = {card.id: card for card in cards}
            ordered_cards = self._cards_for_wall(cards, columns, include_archived=include_archived)
            event_counts = self._event_counts(events)
            viewer_username = self._viewer_username(payload)
            wall_cards = self._serialize_cards_payload(
                ordered_cards,
                events=events,
                column_labels=column_labels,
                event_counts=event_counts,
                viewer_username=viewer_username,
            )
            wall_stickies = [self._serialize_sticky(sticky) for sticky in self._stickies(stickies)]
            wall_events = self._wall_events(events, cards_by_id, column_labels, limit=event_limit)
            board_context = self._build_board_context_payload(columns, cards, stickies, bundle["settings"])
            board_context_counts = board_context["context"]
            meta = {
                "generated_at": utc_now_iso(),
                "columns": len(columns),
                "active_cards": board_context_counts["active_cards_total"],
                "archived_cards": board_context_counts["archived_cards_total"],
                "stickies": len(wall_stickies),
                "events_total": len(events),
                "events_returned": len(wall_events),
                "event_limit": event_limit,
                "include_archived": include_archived,
            }
            wall_text = self._build_gpt_wall_text(columns, wall_cards, wall_stickies, wall_events, meta)
            board_content_meta = {
                "generated_at": meta["generated_at"],
                "columns": meta["columns"],
                "active_cards": meta["active_cards"],
                "archived_cards": meta["archived_cards"],
                "stickies": meta["stickies"],
                "include_archived": meta["include_archived"],
            }
            event_log_meta = {
                "generated_at": meta["generated_at"],
                "events_total": meta["events_total"],
                "events_returned": meta["events_returned"],
                "event_limit": meta["event_limit"],
            }
            board_content_text = self._extract_board_content_text(wall_text)
            event_log_text = self._build_structured_event_log_text(wall_events, meta)
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

    def list_archived_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=100)
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            bundle = self._store.read_bundle()
            archived = self._archived_cards(bundle["cards"], limit=limit)
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                archived,
                columns=bundle["columns"],
                events=bundle["events"],
            )
            return {
                "cards": self._serialize_cards_payload(
                    archived,
                    events=bundle["events"],
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                    compact=compact_cards,
                )
            }

    def search_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(payload, "include_archived", default=False)
            limit = self._validated_limit(payload.get("limit"), default=20, maximum=100)
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            events = bundle["events"]
            viewer_username = self._viewer_username(payload)
            query = self._validated_search_query(payload.get("query"))
            column = self._validated_optional_column(payload.get("column"), columns)
            tag = self._validated_optional_tag(payload.get("tag"))
            indicator = self._validated_optional_indicator(payload.get("indicator"))
            status = self._validated_optional_status(payload.get("status"))

            if not any([query, column, tag, indicator, status]):
                self._fail(
                    "validation_error",
                    "Р”Р»СЏ РїРѕРёСЃРєР° РЅСѓР¶РЅРѕ РїРµСЂРµРґР°С‚СЊ query РёР»Рё С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ С„РёР»СЊС‚СЂ: column, tag, indicator, status.",
                    details={"fields": ["query", "column", "tag", "indicator", "status"]},
                )

            matches: list[tuple[int, Card, list[str]]] = []
            for card in bundle["cards"]:
                if card.archived and not include_archived:
                    continue
                if column and card.column != column:
                    continue
                if tag and tag not in card.tag_labels():
                    continue
                card_status = card.status()
                if status and card_status != status:
                    continue
                card_indicator = card.indicator()
                if indicator and card_indicator != indicator:
                    continue
                score, fields = self._search_card_match(card, query)
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
            include_archived = self._validated_optional_bool(payload, "include_archived", default=False)
            bundle = self._store.read_bundle()
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
                events=bundle["events"],
            )
            return {
                "cards": self._serialize_cards_payload(
                    overdue_cards,
                    events=bundle["events"],
                    column_labels=column_labels,
                    event_counts=event_counts,
                    viewer_username=viewer_username,
                )
            }

    def get_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            column_labels = self._column_labels(bundle["columns"])
            viewer_username = self._viewer_username(payload)
            event_counts = {card.id: self._event_count_for_card(bundle["events"], card.id)}
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=column_labels,
                    event_counts=event_counts,
                    include_removed_attachments=True,
                    viewer_username=viewer_username,
                )
            }

    def get_card_log(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            _ = card
            events = [event.to_dict() for event in self._events_for_card(bundle["events"], card.id)]
            return {"events": events}

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
        latest_event_at = parse_datetime(latest_event.timestamp) if latest_event is not None else None
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
        reviewable = self._wall_events(events, cards_by_id, column_labels, limit=max(limit * 4, limit))
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

    def _event_counts(self, events: list[AuditEvent]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events:
            if not event.card_id:
                continue
            counts[event.card_id] = counts.get(event.card_id, 0) + 1
        return counts

    def _event_count_for_card(self, events: list[AuditEvent], card_id: str) -> int:
        return sum(1 for event in events if event.card_id == card_id)
