from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Any

from ..models import (
    ARCHIVE_PREVIEW_LIMIT,
    AuditEvent,
    Card,
    Column,
    StickyNote,
    normalize_actor_name,
    normalize_text,
    parse_datetime,
    short_entity_id,
    utc_now,
    utc_now_iso,
)
from ..storage.json_store import JsonStore

REVIEW_BOARD_STALE_HOURS_DEFAULT = 48
REVIEW_BOARD_OVERLOAD_THRESHOLD_DEFAULT = 5
REVIEW_BOARD_PRIORITY_LIMIT_DEFAULT = 5
REVIEW_BOARD_EVENT_LIMIT_DEFAULT = 10
GPT_WALL_MARKDOWN_LINE_LIMIT = 3000
GPT_WALL_AGENT_EVENT_LIMIT = 20

CARD_JOURNAL_ACTION_LABELS = {
    "card_created": "Создана карточка",
    "card_moved": "Перемещена карточка",
    "card_archived": "Карточка отправлена в архив",
    "card_restored": "Карточка восстановлена",
    "title_changed": "Изменён заголовок",
    "vehicle_changed": "Изменён автомобиль",
    "description_changed": "Изменено описание",
    "board_summary_changed": "Обновлена краткая суть для доски",
    "signal_changed": "Изменён срок/сигнал",
    "signal_indicator_changed": "Изменён индикатор",
    "tag_added": "Добавлена метка",
    "tag_removed": "Удалена метка",
    "tag_color_changed": "Изменён цвет метки",
    "tags_changed": "Изменены метки",
    "attachment_added": "Добавлен файл",
    "attachment_removed": "Удалён файл",
    "vehicle_profile_updated": "Обновлена техкарта",
    "repair_order_updated": "Обновлён заказ-наряд",
    "repair_order_autofilled": "Автозаполнен заказ-наряд",
    "repair_order_vehicle_fields_synced": "Синхронизированы данные заказ-наряда",
    "ready_column_synchronized": "Синхронизирован столбец готовности",
    "cash_transaction_deleted": "Удалено движение кассы",
    "cash_transaction_created": "Добавлено движение кассы",
    "card_client_linked": "Привязан клиент",
    "card_client_unlinked": "Клиент отвязан",
    "card_client_vehicle_synced": "Синхронизирован автомобиль клиента",
    "card_client_vehicle_unlinked": "Автомобиль клиента отвязан",
    "card_cleanup_applied": "Выполнена очистка карточки",
    "card_auto_cleanup_blocked": "Автоочистка карточки заблокирована",
    "card_full_enrichment_requested": "Запрошено полное обогащение карточки",
}
CARD_JOURNAL_ACTION_ICONS = {
    "card_created": "✨",
    "card_moved": "📍",
    "card_archived": "📦",
    "card_restored": "↩️",
    "title_changed": "✏️",
    "vehicle_changed": "🚗",
    "description_changed": "📝",
    "board_summary_changed": "🧭",
    "signal_changed": "⏰",
    "signal_indicator_changed": "🚦",
    "tag_added": "🏷️",
    "tag_removed": "🏷️",
    "tag_color_changed": "🎨",
    "tags_changed": "🏷️",
    "attachment_added": "📎",
    "attachment_removed": "🗑️",
    "vehicle_profile_updated": "🧾",
    "repair_order_updated": "🛠️",
    "repair_order_autofilled": "🛠️",
    "repair_order_vehicle_fields_synced": "🛠️",
    "ready_column_synchronized": "✅",
    "cash_transaction_deleted": "💸",
    "cash_transaction_created": "💸",
    "card_client_linked": "👤",
    "card_client_unlinked": "👤",
    "card_client_vehicle_synced": "🚗",
    "card_client_vehicle_unlinked": "🚗",
    "card_cleanup_applied": "🧹",
    "card_auto_cleanup_blocked": "⚠️",
    "card_full_enrichment_requested": "🤖",
}
CARD_JOURNAL_FIELD_LABELS = {
    "vehicle": "Автомобиль",
    "title": "Заголовок",
    "description": "Описание",
    "board_summary": "Краткая суть для доски",
    "column": "Столбец",
    "deadline": "Срок/сигнал",
    "indicator": "Индикатор",
    "tags": "Метки",
    "tag": "Метка",
    "tag_color": "Цвет метки",
    "attachment": "Файл",
    "vehicle_profile": "Техкарта автомобиля",
    "repair_order": "Заказ-наряд",
    "cash_transaction": "Движение кассы",
    "client": "Клиент",
    "client_vehicle": "Автомобиль клиента",
    "ready_state": "Готовность",
}
CARD_JOURNAL_SOURCE_LABELS = {
    "ui": "интерфейс",
    "api": "API",
    "mcp": "MCP/GPT",
    "system": "система",
}


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
        serialized = json.dumps(
            revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _markdown_value(self, value: Any) -> str:
        if value is None or value == "":
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
            lines.extend(
                [
                    f"### Column: {self._markdown_value(column.label)}",
                    f"column_id: {self._markdown_value(column.id)}",
                    f"position: {self._markdown_value(column.position)}",
                    f"active_cards: {len(active_by_column.get(column.id, []))}",
                    "",
                ]
            )

        lines.append("## Cards By Column")
        if not active_cards:
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

        lines.append("## Archived Cards")
        if not archived_cards:
            lines.append("cards: []")
            lines.append("")
        for index, card in enumerate(archived_cards, start=1):
            self._append_markdown_card(lines, card, index=index)

        lines.append("## Stickies")
        if not stickies:
            lines.append("stickies: []")
        for index, sticky in enumerate(stickies, start=1):
            lines.extend(
                [
                    f"### Sticky {index}: {self._markdown_value(sticky.get('short_id') or sticky.get('id'))}",
                    f"sticky_id: {self._markdown_value(sticky.get('id'))}",
                    f"short_id: {self._markdown_value(sticky.get('short_id'))}",
                    f"x: {self._markdown_value(sticky.get('x'))}",
                    f"y: {self._markdown_value(sticky.get('y'))}",
                    f"remaining_seconds: {self._markdown_value(sticky.get('remaining_seconds'))}",
                ]
            )
            self._append_markdown_block(lines, "text", sticky.get("text"))
            lines.append("")
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
            cards = self._visible_cards(bundle["cards"], include_archived=include_archived)
            viewer_username = self._viewer_username(payload)
            column_labels, event_counts = self._card_serialization_context(
                cards,
                columns=bundle["columns"],
                events=bundle["events"],
            )
            serialized_cards = self._serialize_cards_payload(
                cards,
                events=bundle["events"],
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
            bundle = self._store.read_bundle()
            cards = self._visible_cards(bundle["cards"], include_archived=False)
            archived_cards_total = sum(1 for card in bundle["cards"] if card.archived)
            archive = (
                self._archived_cards(bundle["cards"], limit=archive_limit)
                if include_archive
                else []
            )
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
                    "cards_returned": len(serialized_cards),
                    "archive_returned": len(serialized_archive),
                    "has_more_archive": include_archive
                    and archived_cards_total > len(serialized_archive),
                    "stickies_returned": len(serialized_stickies),
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

    def list_archived_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(
                payload.get("limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=100
            )
            compact_cards = self._validated_optional_bool(payload, "compact", default=False)
            bundle = self._store.read_bundle()
            archived_total = sum(1 for card in bundle["cards"] if card.archived)
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
                    "Для поиска нужно передать query или хотя бы один фильтр: column, tag, indicator, status.",
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
            payload = payload or {}
            limit_raw = payload.get("limit")
            limit = (
                self._validated_limit(limit_raw, default=100, maximum=1000)
                if limit_raw is not None
                else None
            )
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            card_events = self._events_for_card(bundle["events"], card.id)
            events = [
                event.to_dict()
                for event in (card_events[:limit] if limit is not None else card_events)
            ]
            entries = self._card_log_entries(events, card=card)
            days = self._card_log_group_entries(entries, key="day_key", kind="day")
            weeks = self._card_log_group_entries(entries, key="week_key", kind="week")
            months = self._card_log_group_entries(entries, key="month_key", kind="month")
            totals = self._card_log_totals(entries)
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
                "format": "markdown+json",
                "text_alias": "markdown",
                "event_order": "newest_first",
            }
            markdown = self._card_log_markdown(
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

    def _card_log_value_text(self, value: Any) -> str:
        if value is None or value == "":
            return "—"
        if isinstance(value, bool):
            return "да" if value else "нет"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return normalize_text(value, default="—", limit=240) or "—"

    def _card_log_full_value_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "да" if value else "нет"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        return str(value)

    def _card_log_compact_change_value(self, value: Any) -> str:
        text = self._card_log_full_value_text(value).replace("\r", " ").replace("\n", " / ")
        text = " ".join(text.split())
        if not text:
            return "—"
        return text[:237] + "..." if len(text) > 240 else text

    def _card_log_is_empty_value(self, value: Any) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, (list, tuple, set, dict)) and not value:
            return True
        return False

    def _card_log_change_kind(self, before: Any, after: Any) -> str:
        before_empty = self._card_log_is_empty_value(before)
        after_empty = self._card_log_is_empty_value(after)
        if before_empty and not after_empty:
            return "added"
        if not before_empty and after_empty:
            return "removed"
        return "changed"

    def _card_log_change(
        self,
        field: str,
        *,
        before: Any = "",
        after: Any = "",
        label: str | None = None,
    ) -> dict[str, Any]:
        kind = self._card_log_change_kind(before, after)
        return {
            "schema_version": "card_journal.change.v2",
            "field": field,
            "label": label or CARD_JOURNAL_FIELD_LABELS.get(field, field.replace("_", " ")),
            "kind": kind,
            "before": self._card_log_full_value_text(before),
            "after": self._card_log_full_value_text(after),
            "before_summary": self._card_log_compact_change_value(before),
            "after_summary": self._card_log_compact_change_value(after),
        }

    def _card_log_action_label(self, action: str) -> str:
        return CARD_JOURNAL_ACTION_LABELS.get(action, action.replace("_", " ").strip() or "Событие")

    def _card_log_action_icon(self, action: str) -> str:
        return CARD_JOURNAL_ACTION_ICONS.get(action, "•")

    def _card_log_source_label(self, source: Any) -> str:
        normalized = str(source or "").strip().lower()
        return CARD_JOURNAL_SOURCE_LABELS.get(normalized, normalized or "система")

    def _card_log_changes(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        details = event.get("details")
        if not isinstance(details, dict) or not details:
            return []

        action = str(event.get("action") or "").strip()
        changes: list[dict[str, Any]] = []

        def add(field: str, before: Any = "", after: Any = "", label: str | None = None) -> None:
            if self._card_log_is_empty_value(before) and self._card_log_is_empty_value(after):
                return
            changes.append(self._card_log_change(field, before=before, after=after, label=label))

        if action == "card_created":
            add("vehicle", after=details.get("vehicle"))
            add("title", after=details.get("title"))
            add("column", after=details.get("column"))
            add("tags", after=details.get("tags"))
            add("deadline", after=details.get("deadline_total_seconds"))
        elif action == "card_moved":
            add("column", before=details.get("before_column"), after=details.get("after_column"))
        elif action in {"card_archived", "card_restored"}:
            add("column", after=details.get("column"))
        elif action == "vehicle_changed":
            add("vehicle", before=details.get("before"), after=details.get("after"))
        elif action == "title_changed":
            add("title", before=details.get("before"), after=details.get("after"))
        elif action == "description_changed":
            add("description", before=details.get("before"), after=details.get("after"))
        elif action == "board_summary_changed":
            add("board_summary", before=details.get("before"), after=details.get("after"))
        elif action == "signal_changed":
            add(
                "deadline",
                before=details.get("before_total_seconds"),
                after=details.get("after_total_seconds"),
            )
        elif action == "signal_indicator_changed":
            add(
                "indicator",
                before=details.get("before_indicator"),
                after=details.get("after_indicator"),
            )
            if "deadline_total_seconds" in details:
                add("deadline", after=details.get("deadline_total_seconds"))
        elif action == "attachment_added":
            add("attachment", after=details.get("file_name"))
        elif action == "attachment_removed":
            add("attachment", before=details.get("file_name"))
        elif action == "tag_added":
            add("tag", after=details.get("tag"))
        elif action == "tag_removed":
            add("tag", before=details.get("tag"))
        elif action == "tag_color_changed":
            add(
                "tag_color",
                before=details.get("before_color"),
                after=details.get("after_color"),
                label=f"Цвет метки {details.get('tag') or ''}".strip(),
            )
        elif action == "tags_changed":
            add("tags", before=details.get("before"), after=details.get("after"))
        elif action == "vehicle_profile_updated":
            if "before" in details or "after" in details:
                add("vehicle_profile", before=details.get("before"), after=details.get("after"))
            elif details.get("changed_fields"):
                add("vehicle_profile", after={"changed_fields": details.get("changed_fields")})
        elif action == "repair_order_updated":
            if "before" in details or "after" in details:
                add("repair_order", before=details.get("before"), after=details.get("after"))
        elif action == "cash_transaction_deleted":
            add("cash_transaction", before=details)

        return changes

    def _card_log_detail_text(
        self, event: dict[str, Any], changes: list[dict[str, Any]] | None = None
    ) -> str:
        if changes is None:
            changes = self._card_log_changes(event)
        if changes:
            return " | ".join(
                f"{item['label']}: {item['before_summary']} → {item['after_summary']}"
                for item in changes
            )

        details = event.get("details")
        if not isinstance(details, dict) or not details:
            return ""
        parts: list[str] = []
        for key in sorted(details.keys()):
            text = self._card_log_value_text(details.get(key))
            if text != "—":
                parts.append(f"{key.replace('_', ' ')}: {text}")
        return " | ".join(parts)

    def _card_log_value_lines(self, value: Any) -> list[str]:
        text = self._card_log_full_value_text(value)
        if not text:
            return ["—"]
        return [line.rstrip() or "—" for line in text.splitlines()]

    def _card_log_change_detail_lines(self, change: dict[str, Any]) -> list[str]:
        label = str(change.get("label") or change.get("field") or "Изменение")
        removed = change.get("kind") == "removed"
        marker = "⚠️ Очищено поле" if removed else "Изменено поле"
        lines = [f"{marker} {label}", "до:"]
        lines.extend(f"  {line}" for line in self._card_log_value_lines(change.get("before")))
        lines.append("после:")
        lines.extend(f"  {line}" for line in self._card_log_value_lines(change.get("after")))
        return lines

    def _card_log_entry_detail_lines(
        self,
        event: dict[str, Any],
        changes: list[dict[str, Any]],
        *,
        action_label: str,
        message: str,
    ) -> list[str]:
        lines: list[str] = []
        if message and message != action_label:
            lines.append(f"Сообщение: {message}")
        if changes:
            for change in changes:
                lines.extend(self._card_log_change_detail_lines(change))
            return lines

        details = event.get("details")
        if not isinstance(details, dict) or not details:
            return lines
        for key in sorted(details.keys()):
            value = details.get(key)
            if self._card_log_is_empty_value(value):
                continue
            label = CARD_JOURNAL_FIELD_LABELS.get(key, key.replace("_", " "))
            compact = self._card_log_compact_change_value(value)
            lines.append(f"{label}: {compact}")
        return lines

    def _card_log_entries(
        self,
        events: list[dict[str, Any]],
        *,
        card: Card,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        card_short_id = short_entity_id(card.id, prefix="C")
        card_heading = card.heading()
        for event in events:
            timestamp = parse_datetime(event.get("timestamp")) or utc_now()
            day_key = timestamp.date().isoformat()
            iso_year, iso_week, _ = timestamp.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            month_key = timestamp.strftime("%Y-%m")
            time_short = timestamp.strftime("%H:%M")
            details = event.get("details")
            action = normalize_text(event.get("action"), default="unknown", limit=80)
            source = event.get("source") or "system"
            changes = self._card_log_changes(event)
            details_copy = dict(details) if isinstance(details, dict) else {}
            has_deletion = any(item.get("kind") == "removed" for item in changes)
            actor_name = normalize_actor_name(event.get("actor_name"))
            action_label = self._card_log_action_label(action)
            source_label = self._card_log_source_label(source)
            icon = self._card_log_action_icon(action)
            message = normalize_text(event.get("message"), default="Событие", limit=300)
            detail_lines = self._card_log_entry_detail_lines(
                event,
                changes,
                action_label=action_label,
                message=message,
            )
            display_line = f"{time_short} | {icon} {action_label} | {actor_name} | {source_label}"
            entry = {
                "schema_version": "card_journal.entry.v2",
                "id": event.get("id") or "",
                "timestamp": timestamp.isoformat(),
                "date": day_key,
                "time": timestamp.strftime("%H:%M:%S"),
                "time_short": time_short,
                "day_key": day_key,
                "week_key": week_key,
                "month_key": month_key,
                "actor_name": actor_name,
                "source": source,
                "source_label": source_label,
                "action": action,
                "action_label": action_label,
                "icon": icon,
                "message": message,
                "card_id": card.id,
                "card_short_id": card_short_id,
                "card_heading": card_heading,
                "details": details_copy,
                "details_text": "\n".join(detail_lines),
                "detail_lines": detail_lines,
                "changes": changes,
                "change_count": len(changes),
                "has_deletion": has_deletion,
                "display_line": display_line,
            }
            entry["summary"] = " · ".join(
                value
                for value in (
                    entry["time_short"],
                    entry["actor_name"],
                    str(entry["action_label"] or entry["message"] or "").strip(),
                )
                if value
            )
            entries.append(entry)
        return entries

    def _card_log_group_entries(
        self, entries: list[dict[str, Any]], *, key: str, kind: str
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in entries:
            grouped.setdefault(str(item.get(key) or "unknown"), []).append(item)
        result: list[dict[str, Any]] = []
        for group_key in sorted(grouped.keys(), reverse=True):
            group_entries = grouped[group_key]
            totals = self._card_log_totals(group_entries)
            payload: dict[str, Any] = {
                "key": group_key,
                "entries": group_entries,
                **totals,
            }
            if kind == "day":
                payload["day_key"] = group_key
                payload["label"] = self._card_log_day_label(group_key)
                payload["first_timestamp"] = group_entries[0]["timestamp"]
                payload["last_timestamp"] = group_entries[-1]["timestamp"]
            elif kind == "week":
                payload["week_key"] = group_key
                payload["label"] = self._card_log_week_label(group_key)
            else:
                payload["month_key"] = group_key
                payload["label"] = self._card_log_month_label(group_key)
            result.append(payload)
        return result

    def _card_log_day_label(self, date_key: str) -> str:
        try:
            value = datetime.strptime(date_key, "%Y-%m-%d")
        except ValueError:
            return date_key
        weekdays = [
            "понедельник",
            "вторник",
            "среда",
            "четверг",
            "пятница",
            "суббота",
            "воскресенье",
        ]
        return f"{value.strftime('%d.%m.%Y')}, {weekdays[value.weekday()]}"

    def _card_log_week_label(self, week_key: str) -> str:
        try:
            year_text, week_text = week_key.split("-W", 1)
            start = datetime.fromisocalendar(int(year_text), int(week_text), 1)
            end = datetime.fromisocalendar(int(year_text), int(week_text), 7)
        except (ValueError, TypeError):
            return week_key
        return f"{week_text} неделя: {start.strftime('%d.%m')} - {end.strftime('%d.%m.%Y')}"

    def _card_log_month_label(self, month_key: str) -> str:
        try:
            value = datetime.strptime(month_key, "%Y-%m")
        except ValueError:
            return month_key
        month_names = [
            "Январь",
            "Февраль",
            "Март",
            "Апрель",
            "Май",
            "Июнь",
            "Июль",
            "Август",
            "Сентябрь",
            "Октябрь",
            "Ноябрь",
            "Декабрь",
        ]
        return f"{month_names[value.month - 1]} {value.year}"

    def _card_log_totals(self, entries: list[dict[str, Any]]) -> dict[str, object]:
        return {
            "count": len(entries),
            "actors": len(
                {str(item.get("actor_name") or "") for item in entries if item.get("actor_name")}
            ),
            "sources": len(
                {str(item.get("source") or "") for item in entries if item.get("source")}
            ),
            "actions": len(
                {str(item.get("action") or "") for item in entries if item.get("action")}
            ),
            "changes": sum(int(item.get("change_count") or 0) for item in entries),
            "deletions": sum(1 for item in entries if item.get("has_deletion")),
        }

    def _card_log_markdown(
        self,
        *,
        card: Card,
        entries: list[dict[str, Any]],
        days: list[dict[str, Any]],
        weeks: list[dict[str, Any]],
        months: list[dict[str, Any]],
        totals: dict[str, object],
        meta: dict[str, object],
    ) -> str:
        lines = [
            "# 🧾 Журнал карточки",
            "",
            "## 📊 Итоги карточки",
            f"- Карточка: {card.heading()}",
            f"- ID: {meta['card_short_id']}",
            f"- Показано событий: {totals['count']} из {meta['events_total']}",
            f"- Участников: {totals['actors']}",
            f"- Источников: {totals['sources']}",
            f"- Типов действий: {totals['actions']}",
            f"- Изменений полей: {totals['changes']}",
            f"- Очищений/удалений: {totals['deletions']}",
        ]
        if meta.get("has_more"):
            lines.append("- Показана только часть журнала по лимиту выгрузки.")
        if meta.get("oldest_timestamp") and meta.get("newest_timestamp"):
            lines.append(f"- Диапазон: {meta['oldest_timestamp']} → {meta['newest_timestamp']}")
        lines.append("")
        if not entries:
            lines.extend(["## 🧾 События", "Журнал пуст."])
            return "\n".join(lines).strip()

        lines.extend(["## 🗓️ По месяцам"])
        for item in months:
            lines.append(
                f"- **{item['label']}**: {item['count']} событ. | "
                + f"{item['changes']} изм. | {item['deletions']} очищ. | "
                + f"{item['actors']} участн."
            )
        lines.extend(["", "## 📅 По неделям"])
        for item in weeks:
            lines.append(
                f"- **{item['label']}**: {item['count']} событ. | "
                + f"{item['changes']} изм. | {item['deletions']} очищ. | "
                + f"{item['actors']} участн."
            )
        lines.extend(["", "## 🧾 События по дням"])
        for day in days:
            lines.extend(
                [
                    "",
                    f"### 📆 {day['label']}",
                    f"Итого: {day['count']} событ. | {day['changes']} изм. | "
                    + f"{day['deletions']} очищ. | {day['actors']} участн.",
                    "",
                ]
            )
            for item in day["entries"]:
                lines.append(f"- {item['display_line']}")
                self._append_card_log_detail_lines(lines, item.get("detail_lines"))
        return "\n".join(lines).strip()

    def _append_card_log_detail_lines(self, lines: list[str], detail_lines: Any) -> None:
        if not isinstance(detail_lines, list):
            return
        for raw_line in detail_lines:
            line = str(raw_line)
            if line.startswith("  ") or line in {"до:", "после:"}:
                lines.append(f"    {line}")
            else:
                lines.append(f"  - {line}")

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
