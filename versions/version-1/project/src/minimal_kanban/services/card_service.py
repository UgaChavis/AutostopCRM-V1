from __future__ import annotations

import base64
import binascii
from datetime import timedelta
from pathlib import Path
import re
import threading
import uuid
from logging import Logger

from ..config import get_attachments_dir
from ..demo_seed import build_demo_board
from ..models import (
    ARCHIVE_PREVIEW_LIMIT,
    CARD_DESCRIPTION_LIMIT,
    CARD_TITLE_LIMIT,
    CARD_VEHICLE_LIMIT,
    COLUMN_LABEL_LIMIT,
    MAX_ATTACHMENT_SIZE_BYTES,
    TAG_LIMIT,
    VALID_INDICATORS,
    VALID_STATUSES,
    WARNING_THRESHOLD_RATIO,
    Attachment,
    AuditEvent,
    Card,
    Column,
    StickyNote,
    normalize_actor_name,
    normalize_file_name,
    normalize_int,
    normalize_source,
    normalize_tag_label,
    normalize_text,
    normalize_tags,
    short_entity_id,
    utc_now,
    utc_now_iso,
)
from ..storage.json_store import JsonStore, default_columns


_SEARCH_SEPARATOR_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
GPT_WALL_TEXT_LINE_LIMIT = 3000


class ServiceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CardService:
    def __init__(self, store: JsonStore, logger: Logger, attachments_dir: Path | None = None) -> None:
        self._store = store
        self._logger = logger
        self._lock = threading.RLock()
        self._attachments_dir = attachments_dir or get_attachments_dir()
        self._attachments_dir.mkdir(parents=True, exist_ok=True)

    def create_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            events = bundle["events"]
            column_labels = self._column_labels(columns)
            actor_name, source = self._audit_identity(payload, default_source="api")
            vehicle = self._validated_vehicle(payload.get("vehicle", ""))
            title = self._validated_title(payload.get("title"))
            description = self._validated_description(payload.get("description", ""))
            deadline_total_seconds = self._validated_deadline(payload.get("deadline"))
            tags = self._validated_tags(payload.get("tags", []))
            default_column_id = columns[0].id if columns else "inbox"
            column = self._validated_column(payload.get("column", default_column_id), columns)
            now = utc_now()
            now_iso = now.isoformat()
            card = Card(
                id=str(uuid.uuid4()),
                title=title,
                description=description,
                column=column,
                archived=False,
                created_at=now_iso,
                updated_at=now_iso,
                deadline_timestamp=(now + timedelta(seconds=deadline_total_seconds)).isoformat(),
                deadline_total_seconds=deadline_total_seconds,
                vehicle=vehicle,
                tags=tags,
            )
            cards.append(card)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="card_created",
                message=f"{actor_name} создал карточку",
                card_id=card.id,
                details={
                    "vehicle": card.vehicle,
                    "title": card.title,
                    "column": card.column,
                    "tags": list(card.tags),
                    "deadline_total_seconds": card.deadline_total_seconds,
                },
            )
            self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            self._logger.info(
                "create_card id=%s title=%s column=%s actor=%s source=%s",
                card.id,
                card.title,
                card.column,
                actor_name,
                source,
            )
            return {"card": self._serialize_card(card, events, column_labels=column_labels)}

    def get_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(payload, "include_archived", default=False)
            bundle = self._store.read_bundle()
            cards = self._visible_cards(bundle["cards"], include_archived=include_archived)
            column_labels = self._column_labels(bundle["columns"])
            return {"cards": [self._serialize_card(card, bundle["events"], column_labels=column_labels) for card in cards]}

    def get_board_snapshot(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            archive_limit = self._validated_limit(payload.get("archive_limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=50)
            bundle = self._store.read_bundle()
            cards = self._visible_cards(bundle["cards"], include_archived=False)
            archive = self._archived_cards(bundle["cards"], limit=archive_limit)
            stickies = self._stickies(bundle["stickies"])
            column_labels = self._column_labels(bundle["columns"])
            return {
                "columns": [column.to_dict() for column in bundle["columns"]],
                "cards": [self._serialize_card(card, bundle["events"], column_labels=column_labels) for card in cards],
                "archive": [self._serialize_card(card, bundle["events"], column_labels=column_labels) for card in archive],
                "stickies": [self._serialize_sticky(sticky) for sticky in stickies],
                "settings": dict(bundle["settings"]),
                "meta": {
                    "generated_at": utc_now_iso(),
                    "archive_limit": archive_limit,
                    "stickies_total": len(stickies),
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

    def update_board_settings(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="ui")
            board_scale = self._validated_board_scale(payload.get("board_scale"))
            bundle = self._store.read_bundle()
            previous_scale = float(bundle["settings"].get("board_scale", 1.0))
            settings = dict(bundle["settings"])
            settings["board_scale"] = board_scale
            if previous_scale != board_scale:
                events = bundle["events"]
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="board_scale_changed",
                    message=f"{actor_name} изменил масштаб доски",
                    card_id=None,
                    details={"before": previous_scale, "after": board_scale},
                )
                self._store.write_bundle(
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=events,
                    settings=settings,
                )
            else:
                self._store.write_bundle(
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                    settings=settings,
                )
            return {
                "settings": settings,
                "meta": {
                    "board_scale": board_scale,
                    "previous_board_scale": previous_scale,
                    "changed": previous_scale != board_scale,
                },
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
            wall_cards = [self._serialize_card(card, events, column_labels=column_labels) for card in ordered_cards]
            wall_stickies = [self._serialize_sticky(sticky) for sticky in self._stickies(stickies)]
            wall_events = self._wall_events(events, cards_by_id, column_labels, limit=event_limit)
            board_context = self._build_board_context_payload(columns, cards, stickies, bundle["settings"])
            meta = {
                "generated_at": utc_now_iso(),
                "columns": len(columns),
                "active_cards": sum(1 for card in cards if not card.archived),
                "archived_cards": sum(1 for card in cards if card.archived),
                "stickies": len(wall_stickies),
                "events_total": len(events),
                "events_returned": len(wall_events),
                "event_limit": event_limit,
                "include_archived": include_archived,
            }
            return {
                "meta": meta,
                "columns": [column.to_dict() for column in columns],
                "cards": wall_cards,
                "stickies": wall_stickies,
                "events": wall_events,
                "board_context": board_context,
                "text": self._build_gpt_wall_text(columns, wall_cards, wall_stickies, wall_events, meta),
            }

    def list_archived_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=ARCHIVE_PREVIEW_LIMIT, maximum=100)
            bundle = self._store.read_bundle()
            archived = self._archived_cards(bundle["cards"], limit=limit)
            column_labels = self._column_labels(bundle["columns"])
            return {"cards": [self._serialize_card(card, bundle["events"], column_labels=column_labels) for card in archived]}

    def search_cards(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            include_archived = self._validated_optional_bool(payload, "include_archived", default=False)
            limit = self._validated_limit(payload.get("limit"), default=20, maximum=100)
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            events = bundle["events"]
            column_labels = self._column_labels(columns)
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
                if tag and tag not in card.tags:
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
            for score, card, fields in limited_matches:
                serialized = self._serialize_card(card, events, column_labels=column_labels)
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
            column_labels = self._column_labels(bundle["columns"])
            overdue_cards = [
                card
                for card in bundle["cards"]
                if card.status() == "expired" and (include_archived or not card.archived)
            ]
            overdue_cards.sort(key=lambda item: item.deadline_timestamp)
            return {
                "cards": [
                    self._serialize_card(card, bundle["events"], column_labels=column_labels)
                    for card in overdue_cards
                ]
            }

    def get_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            column_labels = self._column_labels(bundle["columns"])
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=column_labels,
                    include_removed_attachments=True,
                )
            }

    def get_card_log(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            _ = card
            events = [event.to_dict() for event in self._events_for_card(bundle["events"], card.id)]
            return {"events": events}

    def update_card(self, payload: dict) -> dict:
        with self._lock:
            updated_fields = {"vehicle", "title", "description", "deadline", "tags"} & set(payload.keys())
            if not updated_fields:
                self._fail(
                    "validation_error",
                    "Для обновления карточки нужно передать хотя бы одно поле: vehicle, title, description, deadline или tags.",
                    details={"fields": ["vehicle", "title", "description", "deadline", "tags"]},
                )
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")

            changed = False
            if "vehicle" in payload:
                changed = self._update_vehicle(card, payload.get("vehicle", ""), events, actor_name, source) or changed
            if "title" in payload:
                changed = self._update_title(card, payload.get("title"), events, actor_name, source) or changed
            if "description" in payload:
                changed = self._update_description(card, payload.get("description", ""), events, actor_name, source) or changed
            if "deadline" in payload:
                changed = self._update_deadline(card, payload.get("deadline"), events, actor_name, source) or changed
            if "tags" in payload:
                changed = self._update_tags(card, payload.get("tags"), events, actor_name, source) or changed

            if changed:
                card.updated_at = utc_now_iso()
                self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=events)
            self._logger.info("update_card id=%s changed=%s actor=%s source=%s", card.id, changed, actor_name, source)
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                )
            }

    def set_card_deadline(self, payload: dict) -> dict:
        with self._lock:
            payload = dict(payload)
            payload["deadline"] = payload.get("deadline")
            return self.update_card(payload)

    def set_card_indicator(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            indicator = self._validated_indicator(payload.get("indicator"))
            previous_indicator = card.indicator()
            self._apply_indicator(card, indicator)
            card.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="signal_indicator_changed",
                message=f"{actor_name} изменил сигнал карточки",
                card_id=card.id,
                details={
                    "before_indicator": previous_indicator,
                    "after_indicator": indicator,
                    "deadline_total_seconds": card.deadline_total_seconds,
                    "deadline_timestamp": card.deadline_timestamp,
                },
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=events)
            self._logger.info("set_card_indicator id=%s indicator=%s actor=%s source=%s", card.id, indicator, actor_name, source)
            return {"card": self._serialize_card(card, events, column_labels=self._column_labels(bundle["columns"]))}

    def move_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_column = self._validated_column(payload.get("column"), columns)
            if card.column != next_column:
                previous_column = card.column
                card.column = next_column
                card.updated_at = utc_now_iso()
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_moved",
                    message=f"{actor_name} переместил карточку",
                    card_id=card.id,
                    details={"before_column": previous_column, "after_column": next_column},
                )
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            self._logger.info("move_card id=%s column=%s actor=%s source=%s", card.id, next_column, actor_name, source)
            return {"card": self._serialize_card(card, events, column_labels=self._column_labels(columns))}

    def archive_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            card.archived = True
            card.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="card_archived",
                message=f"{actor_name} отправил карточку в архив",
                card_id=card.id,
                details={"column": card.column},
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=events)
            self._logger.info("archive_card id=%s actor=%s source=%s", card.id, actor_name, source)
            return {"card": self._serialize_card(card, events, column_labels=self._column_labels(bundle["columns"]))}

    def bulk_move_cards(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_column = self._validated_column(payload.get("column"), columns)
            raw_card_ids = payload.get("card_ids")
            if not isinstance(raw_card_ids, list) or not raw_card_ids:
                self._fail(
                    "validation_error",
                    "Нужно передать непустой список card_ids для пакетного переноса.",
                    details={"field": "card_ids"},
                )

            seen_ids: set[str] = set()
            normalized_card_ids: list[str] = []
            for raw_id in raw_card_ids:
                card_id = normalize_text(raw_id, default="", limit=128)
                if not card_id:
                    self._fail(
                        "validation_error",
                        "Список card_ids содержит пустое значение.",
                        details={"field": "card_ids"},
                    )
                if card_id in seen_ids:
                    continue
                seen_ids.add(card_id)
                normalized_card_ids.append(card_id)

            moved_cards: list[dict] = []
            unchanged_cards: list[dict] = []
            errors: list[dict] = []
            changed_any = False
            column_labels = self._column_labels(columns)

            for card_id in normalized_card_ids:
                try:
                    card = self._find_card(cards, card_id)
                    self._ensure_not_archived(card)
                    previous_column = card.column
                    changed = False
                    if card.column != next_column:
                        card.column = next_column
                        card.updated_at = utc_now_iso()
                        self._append_event(
                            events,
                            actor_name=actor_name,
                            source=source,
                            action="card_moved",
                            message=f"{actor_name} РїРµСЂРµРјРµСЃС‚РёР» РєР°СЂС‚РѕС‡РєСѓ",
                            card_id=card.id,
                            details={"before_column": previous_column, "after_column": next_column},
                        )
                        changed = True
                        changed_any = True
                    serialized = self._serialize_card(card, events, column_labels=column_labels)
                    serialized["bulk_move"] = {
                        "before_column": previous_column,
                        "after_column": next_column,
                        "changed": changed,
                    }
                    if changed:
                        moved_cards.append(serialized)
                    else:
                        unchanged_cards.append(serialized)
                except ServiceError as exc:
                    errors.append(
                        {
                            "card_id": card_id,
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        }
                    )

            if changed_any:
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)

            self._logger.info(
                "bulk_move_cards count=%s moved=%s unchanged=%s errors=%s column=%s actor=%s source=%s",
                len(normalized_card_ids),
                len(moved_cards),
                len(unchanged_cards),
                len(errors),
                next_column,
                actor_name,
                source,
            )
            return {
                "column": next_column,
                "moved_cards": moved_cards,
                "unchanged_cards": unchanged_cards,
                "errors": errors,
                "meta": {
                    "requested": len(normalized_card_ids),
                    "moved": len(moved_cards),
                    "unchanged": len(unchanged_cards),
                    "errors": len(errors),
                    "partial_failure": bool(errors),
                },
            }

    def restore_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            if not card.archived:
                self._fail("validation_error", "Карточка уже находится на доске.", details={"card_id": card.id})
            actor_name, source = self._audit_identity(payload, default_source="api")
            target_column = self._validated_column(
                payload.get("column", columns[0].id if columns else "inbox"),
                columns,
            )
            card.archived = False
            card.column = target_column
            card.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="card_restored",
                message=f"{actor_name} вернул карточку из архива",
                card_id=card.id,
                details={"column": target_column},
            )
            self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            self._logger.info("restore_card id=%s actor=%s source=%s", card.id, actor_name, source)
            return {"card": self._serialize_card(card, events, column_labels=self._column_labels(columns))}

    def list_columns(self, payload: dict | None = None) -> dict:
        with self._lock:
            _ = payload
            bundle = self._store.read_bundle()
            return {"columns": [column.to_dict() for column in bundle["columns"]]}

    def create_column(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            label = self._validated_column_label(payload.get("label"), columns)
            column = Column(id=self._next_column_id(columns, label), label=label, position=len(columns))
            columns.append(column)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="column_created",
                message=f"{actor_name} создал столбец",
                card_id=None,
                details={"column_id": column.id, "label": column.label},
            )
            self._save_bundle(bundle, columns=columns, cards=bundle["cards"], events=events)
            self._logger.info("create_column id=%s label=%s actor=%s source=%s", column.id, column.label, actor_name, source)
            return {
                "column": column.to_dict(),
                "columns": [item.to_dict() for item in columns],
            }

    def create_sticky(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            stickies = bundle["stickies"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            text = self._validated_sticky_text(payload.get("text"))
            x = self._validated_sticky_position(payload.get("x"), field="x")
            y = self._validated_sticky_position(payload.get("y"), field="y")
            deadline_total_seconds = self._validated_sticky_deadline(payload.get("deadline"))
            now = utc_now()
            sticky = StickyNote(
                id=str(uuid.uuid4()),
                text=text,
                x=x,
                y=y,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                deadline_timestamp=(now + timedelta(seconds=deadline_total_seconds)).isoformat(),
                deadline_total_seconds=deadline_total_seconds,
            )
            stickies.append(sticky)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="sticky_created",
                message=f"{actor_name} создал стикер",
                card_id=None,
                details={
                    "sticky_id": sticky.id,
                    "text": sticky.text,
                    "x": sticky.x,
                    "y": sticky.y,
                    "deadline_total_seconds": sticky.deadline_total_seconds,
                },
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], stickies=stickies, events=events)
            self._logger.info("create_sticky id=%s actor=%s source=%s", sticky.id, actor_name, source)
            return {"sticky": self._serialize_sticky(sticky), "stickies": [self._serialize_sticky(item) for item in stickies]}

    def update_sticky(self, payload: dict) -> dict:
        with self._lock:
            updated_fields = {"text", "deadline"} & set(payload.keys())
            if not updated_fields:
                self._fail(
                    "validation_error",
                    "Для обновления стикера нужно передать хотя бы одно поле: text или deadline.",
                    details={"fields": ["text", "deadline"]},
                )
            bundle = self._store.read_bundle()
            stickies = bundle["stickies"]
            events = bundle["events"]
            sticky = self._find_sticky(stickies, payload.get("sticky_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")

            changed = False
            if "text" in payload:
                next_text = self._validated_sticky_text(payload.get("text"))
                if next_text != sticky.text:
                    before = sticky.text
                    sticky.text = next_text
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="sticky_text_changed",
                        message=f"{actor_name} изменил текст стикера",
                        card_id=None,
                        details={"sticky_id": sticky.id, "before": before, "after": sticky.text},
                    )
                    changed = True
            if "deadline" in payload:
                next_deadline_seconds = self._validated_sticky_deadline(payload.get("deadline"))
                if next_deadline_seconds != sticky.deadline_total_seconds:
                    before_seconds = sticky.deadline_total_seconds
                    sticky.deadline_total_seconds = next_deadline_seconds
                    sticky.deadline_timestamp = (utc_now() + timedelta(seconds=next_deadline_seconds)).isoformat()
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="sticky_deadline_changed",
                        message=f"{actor_name} изменил срок стикера",
                        card_id=None,
                        details={
                            "sticky_id": sticky.id,
                            "before_total_seconds": before_seconds,
                            "after_total_seconds": next_deadline_seconds,
                            "deadline_timestamp": sticky.deadline_timestamp,
                        },
                    )
                    changed = True
            if changed:
                sticky.updated_at = utc_now_iso()
                self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], stickies=stickies, events=events)
            self._logger.info("update_sticky id=%s changed=%s actor=%s source=%s", sticky.id, changed, actor_name, source)
            return {"sticky": self._serialize_sticky(sticky), "stickies": [self._serialize_sticky(item) for item in stickies]}

    def move_sticky(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            stickies = bundle["stickies"]
            events = bundle["events"]
            sticky = self._find_sticky(stickies, payload.get("sticky_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_x = self._validated_sticky_position(payload.get("x"), field="x")
            next_y = self._validated_sticky_position(payload.get("y"), field="y")
            before = {"x": sticky.x, "y": sticky.y}
            if sticky.x != next_x or sticky.y != next_y:
                sticky.x = next_x
                sticky.y = next_y
                sticky.updated_at = utc_now_iso()
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="sticky_moved",
                    message=f"{actor_name} переместил стикер",
                    card_id=None,
                    details={"sticky_id": sticky.id, "before": before, "after": {"x": sticky.x, "y": sticky.y}},
                )
                self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], stickies=stickies, events=events)
            self._logger.info("move_sticky id=%s x=%s y=%s actor=%s source=%s", sticky.id, sticky.x, sticky.y, actor_name, source)
            return {"sticky": self._serialize_sticky(sticky), "stickies": [self._serialize_sticky(item) for item in stickies]}

    def delete_sticky(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            stickies = bundle["stickies"]
            events = bundle["events"]
            sticky = self._find_sticky(stickies, payload.get("sticky_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            stickies = [item for item in stickies if item.id != sticky.id]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="sticky_deleted",
                message=f"{actor_name} удалил стикер",
                card_id=None,
                details={"sticky_id": sticky.id, "text": sticky.text, "x": sticky.x, "y": sticky.y},
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], stickies=stickies, events=events)
            self._logger.info("delete_sticky id=%s actor=%s source=%s", sticky.id, actor_name, source)
            return {"deleted": True, "sticky_id": sticky.id, "stickies": [self._serialize_sticky(item) for item in stickies]}

    def add_card_attachment(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            file_name = self._validated_attachment_name(payload.get("file_name"))
            mime_type = normalize_text(payload.get("mime_type"), default="application/octet-stream", limit=120)
            file_bytes = self._validated_attachment_content(payload.get("content_base64"))
            attachment_id = str(uuid.uuid4())
            suffix = Path(file_name).suffix[:16]
            stored_name = f"{attachment_id}{suffix}"
            attachment_path = self._attachment_path(card.id, stored_name)
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(file_bytes)
            attachment = Attachment(
                id=attachment_id,
                file_name=file_name,
                stored_name=stored_name,
                mime_type=mime_type,
                size_bytes=len(file_bytes),
                created_at=utc_now_iso(),
                created_by=actor_name,
            )
            card.attachments.append(attachment)
            card.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="attachment_added",
                message=f"{actor_name} добавил файл",
                card_id=card.id,
                details={
                    "attachment_id": attachment.id,
                    "file_name": attachment.file_name,
                    "size_bytes": attachment.size_bytes,
                },
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=events)
            self._logger.info("add_attachment card_id=%s attachment_id=%s actor=%s", card.id, attachment.id, actor_name)
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "attachment": attachment.to_dict(),
            }

    def remove_card_attachment(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            attachment = self._find_attachment(card, payload.get("attachment_id"))
            if attachment.removed:
                self._fail(
                    "validation_error",
                    "Файл уже удалён из карточки.",
                    details={"attachment_id": attachment.id},
                )
            attachment.removed = True
            attachment.removed_at = utc_now_iso()
            attachment.removed_by = actor_name
            attachment_path = self._attachment_path(card.id, attachment.stored_name)
            if attachment_path.exists():
                attachment_path.unlink()
            card.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="attachment_removed",
                message=f"{actor_name} удалил файл",
                card_id=card.id,
                details={"attachment_id": attachment.id, "file_name": attachment.file_name},
            )
            self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=events)
            self._logger.info("remove_attachment card_id=%s attachment_id=%s actor=%s", card.id, attachment.id, actor_name)
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                )
            }

    def get_attachment_download(self, card_id: str, attachment_id: str) -> tuple[Path, Attachment]:
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], card_id)
            attachment = self._find_attachment(card, attachment_id)
            if attachment.removed:
                self._fail(
                    "not_found",
                    "Файл был удалён из карточки.",
                    status_code=404,
                    details={"attachment_id": attachment.id},
                )
            attachment_path = self._attachment_path(card.id, attachment.stored_name)
            if not attachment_path.exists():
                self._fail(
                    "not_found",
                    "Файл не найден на диске.",
                    status_code=404,
                    details={"attachment_id": attachment.id},
                )
            return attachment_path, attachment

    def get_onboarding_seen(self) -> bool:
        with self._lock:
            return bool(self._store.get_setting("has_seen_onboarding", False))

    def set_onboarding_seen(self, value: bool) -> None:
        with self._lock:
            self._store.set_setting("has_seen_onboarding", bool(value))

    def ensure_demo_board(self) -> bool:
        with self._lock:
            bundle = self._store.read_bundle()
            settings = dict(bundle["settings"])
            if settings.get("demo_seeded"):
                return False
            if self._should_seed_demo(bundle):
                seeded = build_demo_board(settings)
                self._store.write_bundle(
                    columns=seeded["columns"],
                    cards=seeded["cards"],
                    stickies=seeded.get("stickies", []),
                    events=seeded["events"],
                    settings=seeded["settings"],
                )
                self._logger.info("demo_board_seeded cards=%s columns=%s", len(seeded["cards"]), len(seeded["columns"]))
                return True
            settings["demo_seeded"] = True
            self._store.write_bundle(
                columns=bundle["columns"],
                cards=bundle["cards"],
                stickies=bundle["stickies"],
                events=bundle["events"],
                settings=settings,
            )
            return False

    def _serialize_card(
        self,
        card: Card,
        events: list[AuditEvent],
        *,
        column_labels: dict[str, str] | None = None,
        include_removed_attachments: bool = False,
    ) -> dict:
        events_count = sum(1 for event in events if event.card_id == card.id)
        payload = card.to_dict(
            events_count=events_count,
            include_removed_attachments=include_removed_attachments,
        )
        payload["column_label"] = (column_labels or {}).get(card.column, card.column)
        return payload

    def _serialize_sticky(self, sticky: StickyNote) -> dict:
        return sticky.to_dict()

    def _column_labels(self, columns: list[Column]) -> dict[str, str]:
        return {column.id: column.label for column in columns}

    def _cards_for_wall(self, cards: list[Card], columns: list[Column], *, include_archived: bool) -> list[Card]:
        position_map = {column.id: column.position for column in columns}
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived] if include_archived else []
        active_cards.sort(key=lambda item: item.updated_at, reverse=True)
        active_cards.sort(key=lambda item: position_map.get(item.column, 999))
        archived_cards.sort(key=lambda item: item.updated_at, reverse=True)
        return active_cards + archived_cards

    def _stickies(self, stickies: list[StickyNote]) -> list[StickyNote]:
        ordered = [sticky for sticky in stickies]
        ordered.sort(key=lambda item: (item.y, item.x, item.updated_at, item.id))
        return ordered

    def _wall_events(
        self,
        events: list[AuditEvent],
        cards_by_id: dict[str, Card],
        column_labels: dict[str, str],
        *,
        limit: int,
    ) -> list[dict]:
        ordered_events = sorted(events, key=lambda item: item.timestamp, reverse=True)[:limit]
        payloads: list[dict] = []
        for event in ordered_events:
            payload = event.to_dict()
            card = cards_by_id.get(event.card_id or "")
            if card is not None:
                payload["card_short_id"] = short_entity_id(card.id, prefix="C")
                payload["card_heading"] = card.heading()
                payload["card_column"] = card.column
                payload["card_column_label"] = column_labels.get(card.column, card.column)
            payload["details_text"] = self._describe_wall_event_details(event, column_labels)
            payloads.append(payload)
        return payloads

    def _describe_wall_event_details(self, event: AuditEvent, column_labels: dict[str, str]) -> str:
        details = event.details or {}
        parts: list[str] = []
        for key, value in details.items():
            parts.append(f"{self._wall_label(key)}={self._wall_value(value, key=key, column_labels=column_labels)}")
        return " | ".join(parts)

    def _wall_label(self, key: str) -> str:
        return str(key).replace("_", " ").strip()

    def _wall_value(self, value, *, key: str, column_labels: dict[str, str]) -> str:
        if isinstance(value, list):
            return ", ".join(self._wall_value(item, key=key, column_labels=column_labels) for item in value) or "—"
        if value in (None, ""):
            return "—"
        key_lower = key.lower()
        if "column" in key_lower:
            return column_labels.get(str(value), str(value))
        if "indicator" in key_lower:
            return str(value).lower()
        if "timestamp" in key_lower:
            return str(value)
        if "total_seconds" in key_lower:
            return str(value)
        return " ".join(str(value).split())

    def _build_board_context_payload(
        self,
        columns: list[Column],
        cards: list[Card],
        stickies: list[StickyNote],
        settings: dict,
    ) -> dict:
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived]
        column_summary: list[dict[str, object]] = []
        for column in columns:
            active_count = sum(1 for card in active_cards if card.column == column.id)
            archived_count = sum(1 for card in archived_cards if card.column == column.id)
            column_summary.append(
                {
                    "id": column.id,
                    "label": column.label,
                    "position": column.position,
                    "active_cards": active_count,
                    "archived_cards": archived_count,
                }
            )

        context = {
            "product_name": "Minimal Kanban",
            "board_name": "Current Minimal Kanban Board",
            "board_key": "minimal-kanban/current-local-board",
            "board_scope": "single_local_board_instance",
            "scope_rule": (
                "This connector may operate only on the current Minimal Kanban board served by this exact MCP/API "
                "instance. Do not use it for Trello, YouGile, or any other kanban system."
            ),
            "storage_backend": "local_json_store",
            "columns_total": len(columns),
            "active_cards_total": len(active_cards),
            "archived_cards_total": len(archived_cards),
            "stickies_total": len(stickies),
            "board_scale": float(settings.get("board_scale", 1.0) or 1.0),
            "columns": column_summary,
        }
        return {
            "context": context,
            "text": self._build_board_context_text(context),
        }

    def _build_board_context_text(self, context: dict[str, object]) -> str:
        lines = [
            "[BOARD CONTEXT]",
            f"product_name: {context['product_name']}",
            f"board_name: {context['board_name']}",
            f"board_key: {context['board_key']}",
            f"board_scope: {context['board_scope']}",
            f"scope_rule: {context['scope_rule']}",
            f"storage_backend: {context['storage_backend']}",
            (
                "counts: columns={columns_total} | active_cards={active_cards_total} | "
                "archived_cards={archived_cards_total} | stickies={stickies_total}"
            ).format(**context),
            f"board_scale: {context['board_scale']}",
            "allowed_columns:",
        ]
        for column in context["columns"]:
            lines.append(
                "- id={id} | label={label} | position={position} | active_cards={active_cards} | archived_cards={archived_cards}".format(
                    **column
                )
            )
        return "\n".join(lines)

    def _build_gpt_wall_text(
        self,
        columns: list[Column],
        cards: list[dict],
        stickies: list[dict],
        events: list[dict],
        meta: dict[str, object],
    ) -> str:
        return self._build_gpt_wall_text_compact(columns, cards, stickies, events, meta)

    def _build_gpt_wall_text_v2(
        self,
        columns: list[Column],
        cards: list[dict],
        stickies: list[dict],
        events: list[dict],
        meta: dict[str, object],
    ) -> str:
        lines: list[str] = []
        lines.append("СТЕНА GPT")
        lines.append(
            "СОБРАНО: {generated_at} | СТОЛБЦОВ: {columns} | АКТИВНЫХ: {active_cards} | АРХИВ: {archived_cards} | СТИКЕРОВ: {stickies} | СОБЫТИЙ: {events_total}".format(
                **meta
            )
        )
        lines.append("")
        lines.append("[ТЕКУЩЕЕ СОСТОЯНИЕ ДОСКИ]")
        lines.append("НИЖЕ ИДЁТ ПОЛНЫЙ СРЕЗ ПО ВСЕМ КАРТОЧКАМ: ГДЕ ОНИ ЛЕЖАТ, ЧТО В НИХ НАПИСАНО И В КАКОМ ОНИ СОСТОЯНИИ.")
        lines.append("")

        active_cards = [card for card in cards if not card.get("archived")]
        archived_cards = [card for card in cards if card.get("archived")]

        for column in columns:
            column_cards = [card for card in active_cards if card.get("column") == column.id]
            lines.append(f"СТОЛБЕЦ: {column.label} | id={column.id} | карточек={len(column_cards)}")
            if not column_cards:
                lines.append("  ПУСТО.")
                lines.append("")
                continue
            for index, card in enumerate(column_cards, start=1):
                lines.append(f"  КАРТОЧКА {index}")
                lines.append(f"    card_id: {card['id']}")
                lines.append(f"    short_id: {card.get('short_id') or short_entity_id(card['id'], prefix='C')}")
                lines.append(f"    местоположение: {card.get('column_label', card['column'])}")
                lines.append(f"    марка: {card.get('vehicle') or '—'}")
                lines.append(f"    заголовок: {card.get('title') or card.get('heading') or '—'}")
                lines.append(f"    полный_заголовок: {card.get('heading') or '—'}")
                lines.append(f"    сигнал: {card.get('indicator') or 'none'}")
                lines.append(f"    остаток: {card.get('remaining_display') or '—'}")
                lines.append(f"    статус: {card.get('status') or 'active'}")
                lines.append(f"    метки: {', '.join(card.get('tags') or []) or '—'}")
                lines.append(
                    f"    файлы: {card.get('attachment_count', 0)} | записей_журнала: {card.get('events_count', 0)} | обновлено: {card.get('updated_at') or '—'}"
                )
                lines.append("    описание:")
                description = str(card.get("description") or "Описание не указано").strip()
                for line in description.splitlines() or ["Описание не указано"]:
                    lines.append(f"      {line}")
                lines.append("")

        lines.append("[СТИКЕРЫ]")
        if stickies:
            for index, sticky in enumerate(stickies, start=1):
                lines.append(f"  СТИКЕР {index}")
                lines.append(f"    sticky_id: {sticky['id']}")
                lines.append(f"    short_id: {sticky.get('short_id') or short_entity_id(sticky['id'], prefix='S')}")
                lines.append(f"    позиция: x={sticky.get('x', 0)} | y={sticky.get('y', 0)}")
                lines.append(f"    текст: {sticky.get('text') or '—'}")
                lines.append(f"    остаток: {sticky.get('remaining_seconds', 0)} | opacity: {sticky.get('opacity', 0.5)}")
                lines.append("")
        else:
            lines.append("  СТИКЕРОВ НЕТ.")
            lines.append("")

        lines.append("[АРХИВНЫЕ КАРТОЧКИ]")
        if archived_cards:
            for index, card in enumerate(archived_cards, start=1):
                lines.append(f"  АРХИВ {index}")
                lines.append(f"    card_id: {card['id']}")
                lines.append(f"    short_id: {card.get('short_id') or short_entity_id(card['id'], prefix='C')}")
                lines.append(f"    последнее_место: {card.get('column_label', card['column'])}")
                lines.append(f"    марка: {card.get('vehicle') or '—'}")
                lines.append(f"    заголовок: {card.get('title') or card.get('heading') or '—'}")
                lines.append(f"    полный_заголовок: {card.get('heading') or '—'}")
                lines.append(f"    метки: {', '.join(card.get('tags') or []) or '—'}")
                lines.append(
                    f"    файлы: {card.get('attachment_count', 0)} | записей_журнала: {card.get('events_count', 0)} | обновлено: {card.get('updated_at') or '—'}"
                )
                lines.append("    описание:")
                description = str(card.get("description") or "Описание не указано").strip()
                for line in description.splitlines() or ["Описание не указано"]:
                    lines.append(f"      {line}")
                lines.append("")
        else:
            lines.append("  АРХИВ ПУСТ.")
            lines.append("")

        lines.append("[ЛЕНТА СОБЫТИЙ]")
        lines.append(
            "НИЖЕ ИДУТ ПОСЛЕДНИЕ {events_total} СОБЫТИЙ ПО ДОСКЕ В ХРОНОЛОГИЧЕСКОМ ПОРЯДКЕ: ВРЕМЯ | ПОЛЬЗОВАТЕЛЬ | ИСТОЧНИК | ДЕЙСТВИЕ | ДЕТАЛИ.".format(
                **meta
            )
        )
        if events:
            for event in events:
                line = f"{event.get('timestamp')} | {event.get('actor_name')} | {event.get('source')} | {event.get('message')}"
                if event.get("card_id"):
                    line += f" | card_id={event.get('card_id')}"
                if event.get("card_heading"):
                    line += f" | карта={event.get('card_heading')}"
                if event.get("details_text"):
                    line += f" | {event.get('details_text')}"
                lines.append(line)
        else:
            lines.append("СОБЫТИЙ НЕТ.")
        return "\n".join(lines)

    def _build_gpt_wall_text_compact(
        self,
        columns: list[Column],
        cards: list[dict],
        stickies: list[dict],
        events: list[dict],
        meta: dict[str, object],
    ) -> str:
        lines: list[str] = []
        lines.append("СТЕНА GPT")
        lines.append(
            "СОБРАНО: {generated_at} | СТОЛБЦОВ: {columns} | АКТИВНЫХ: {active_cards} | АРХИВ: {archived_cards} | СТИКЕРОВ: {stickies} | СОБЫТИЙ: {events_total}".format(
                **meta
            )
        )
        lines.append("")
        lines.append("[ТЕКУЩЕЕ СОСТОЯНИЕ ДОСКИ]")
        lines.append("НИЖЕ ИДЁТ ПОЛНЫЙ СРЕЗ ПО ВСЕМ КАРТОЧКАМ: ГДЕ ОНИ ЛЕЖАТ, ЧТО В НИХ НАПИСАНО И В КАКОМ ОНИ СОСТОЯНИИ.")
        lines.append("")

        active_cards = [card for card in cards if not card.get("archived")]
        archived_cards = [card for card in cards if card.get("archived")]

        for column in columns:
            column_cards = [card for card in active_cards if card.get("column") == column.id]
            lines.append(f"СТОЛБЕЦ: {column.label} | id={column.id} | карточек={len(column_cards)}")
            if not column_cards:
                lines.append("  ПУСТО.")
                lines.append("")
                continue
            for index, card in enumerate(column_cards, start=1):
                lines.append(f"  КАРТОЧКА {index}")
                lines.append(f"    card_id: {card['id']}")
                lines.append(f"    short_id: {card.get('short_id') or short_entity_id(card['id'], prefix='C')}")
                lines.append(f"    местоположение: {card.get('column_label', card['column'])}")
                lines.append(f"    марка: {card.get('vehicle') or '—'}")
                lines.append(f"    заголовок: {card.get('title') or card.get('heading') or '—'}")
                lines.append(f"    полный_заголовок: {card.get('heading') or '—'}")
                lines.append(f"    сигнал: {card.get('indicator') or 'none'}")
                lines.append(f"    остаток: {card.get('remaining_display') or '—'}")
                lines.append(f"    статус: {card.get('status') or 'active'}")
                lines.append(f"    метки: {', '.join(card.get('tags') or []) or '—'}")
                lines.append(
                    f"    файлы: {card.get('attachment_count', 0)} | записей_журнала: {card.get('events_count', 0)} | обновлено: {card.get('updated_at') or '—'}"
                )
                lines.append("    описание:")
                description = str(card.get("description") or "Описание не указано").strip()
                for line in description.splitlines() or ["Описание не указано"]:
                    lines.append(f"      {line}")
                lines.append("")

        lines.append("[СТИКЕРЫ]")
        if stickies:
            for index, sticky in enumerate(stickies, start=1):
                lines.append(f"  СТИКЕР {index}")
                lines.append(f"    sticky_id: {sticky['id']}")
                lines.append(f"    short_id: {sticky.get('short_id') or short_entity_id(sticky['id'], prefix='S')}")
                lines.append(f"    позиция: x={sticky.get('x', 0)} | y={sticky.get('y', 0)}")
                lines.append(f"    текст: {sticky.get('text') or '—'}")
                lines.append(f"    остаток: {sticky.get('remaining_seconds', 0)} | opacity: {sticky.get('opacity', 0.5)}")
                lines.append("")
        else:
            lines.append("  СТИКЕРОВ НЕТ.")
            lines.append("")

        lines.append("[АРХИВНЫЕ КАРТОЧКИ]")
        if archived_cards:
            for index, card in enumerate(archived_cards, start=1):
                lines.append(f"  АРХИВ {index}")
                lines.append(f"    card_id: {card['id']}")
                lines.append(f"    short_id: {card.get('short_id') or short_entity_id(card['id'], prefix='C')}")
                lines.append(f"    последнее_место: {card.get('column_label', card['column'])}")
                lines.append(f"    марка: {card.get('vehicle') or '—'}")
                lines.append(f"    заголовок: {card.get('title') or card.get('heading') or '—'}")
                lines.append(f"    полный_заголовок: {card.get('heading') or '—'}")
                lines.append(f"    метки: {', '.join(card.get('tags') or []) or '—'}")
                lines.append(
                    f"    файлы: {card.get('attachment_count', 0)} | записей_журнала: {card.get('events_count', 0)} | обновлено: {card.get('updated_at') or '—'}"
                )
                lines.append("    описание:")
                description = str(card.get("description") or "Описание не указано").strip()
                for line in description.splitlines() or ["Описание не указано"]:
                    lines.append(f"      {line}")
                lines.append("")
        else:
            lines.append("  АРХИВ ПУСТ.")
            lines.append("")

        event_lines: list[str] = []
        event_lines.append("[ЛЕНТА СОБЫТИЙ]")
        event_lines.append(
            "НИЖЕ ИДУТ ПОСЛЕДНИЕ {events_total} СОБЫТИЙ ПО ДОСКЕ. ПОКАЗЫВАЮТСЯ ОТ НОВЫХ К БОЛЕЕ СТАРЫМ, С ПУСТОЙ СТРОКОЙ МЕЖДУ ЗАПИСЯМИ ДЛЯ ЛУЧШЕГО ЧТЕНИЯ.".format(
                **meta
            )
        )
        if events:
            event_lines.append("")
            for index, event in enumerate(events, start=1):
                event_lines.extend(self._build_gpt_wall_event_block(index, event))
        else:
            event_lines.append("СОБЫТИЙ НЕТ.")

        return "\n".join(self._truncate_gpt_wall_lines(lines, event_lines))

    def _build_gpt_wall_event_block(self, index: int, event: dict) -> list[str]:
        lines = [f"СОБЫТИЕ {index}"]
        lines.append(f"  время: {event.get('timestamp') or '—'}")
        lines.append(f"  пользователь: {event.get('actor_name') or '—'}")
        lines.append(f"  источник: {event.get('source') or '—'}")
        lines.append(f"  действие: {event.get('message') or event.get('action') or '—'}")
        if event.get("card_heading"):
            lines.append(f"  карточка: {event.get('card_heading')}")
        if event.get("card_short_id"):
            lines.append(f"  short_id: {event.get('card_short_id')}")
        if event.get("card_id"):
            lines.append(f"  card_id: {event.get('card_id')}")
        if event.get("card_column_label"):
            lines.append(f"  столбец: {event.get('card_column_label')}")
        if event.get("details_text"):
            lines.append("  детали:")
            for detail_line in str(event.get("details_text")).split(" | "):
                lines.append(f"    - {detail_line}")
        lines.append("")
        return lines

    def _truncate_gpt_wall_lines(self, base_lines: list[str], event_lines: list[str]) -> list[str]:
        combined = [*base_lines, *event_lines]
        if len(combined) <= GPT_WALL_TEXT_LINE_LIMIT:
            return combined

        if len(base_lines) >= GPT_WALL_TEXT_LINE_LIMIT - 2:
            return [
                *base_lines[: GPT_WALL_TEXT_LINE_LIMIT - 2],
                "",
                f"[СТЕНА УСЕЧЕНА] Достигнут лимит {GPT_WALL_TEXT_LINE_LIMIT} строк. Хвост стены скрыт.",
            ]

        remaining_for_events = GPT_WALL_TEXT_LINE_LIMIT - len(base_lines) - 2
        return [
            *base_lines,
            *event_lines[: max(0, remaining_for_events)],
            "",
            f"[СТЕНА УСЕЧЕНА] Показана только свежая часть ленты. Общий лимит: {GPT_WALL_TEXT_LINE_LIMIT} строк.",
        ]

    def _visible_cards(self, cards: list[Card], *, include_archived: bool) -> list[Card]:
        visible_cards = [card for card in cards if include_archived or not card.archived]
        visible_cards.sort(key=lambda item: item.updated_at, reverse=True)
        return visible_cards

    def _archived_cards(self, cards: list[Card], *, limit: int) -> list[Card]:
        archived = [card for card in cards if card.archived]
        archived.sort(key=lambda item: item.updated_at, reverse=True)
        return archived[:limit]

    def _events_for_card(self, events: list[AuditEvent], card_id: str) -> list[AuditEvent]:
        filtered = [event for event in events if event.card_id == card_id]
        filtered.sort(key=lambda item: item.timestamp, reverse=True)
        return filtered

    def _search_card_match(self, card: Card, query: str) -> tuple[int, list[str]]:
        if not query:
            return 0, []
        normalized_query = self._normalize_search_text(query)
        tokens = [token for token in normalized_query.split() if token.strip()]
        if not tokens:
            return 0, []

        searchable_fields = {
            "id": card.id,
            "short_id": short_entity_id(card.id, prefix="C"),
            "heading": card.heading(),
            "vehicle": card.vehicle,
            "title": card.title,
            "description": card.description,
            "tags": " ".join(card.tags),
        }
        normalized_fields = {
            name: self._normalize_search_text(value)
            for name, value in searchable_fields.items()
            if value
        }
        if not all(any(token in value for value in normalized_fields.values()) for token in tokens):
            return 0, []

        score = 0
        fields: list[str] = []
        weighted_fields = (
            ("short_id", 9),
            ("id", 8),
            ("heading", 7),
            ("vehicle", 6),
            ("title", 6),
            ("tags", 5),
            ("description", 2),
        )
        for field_name, weight in weighted_fields:
            field_value = normalized_fields.get(field_name, "")
            if field_value and any(token in field_value for token in tokens):
                fields.append(field_name)
                score += weight
                if normalized_query in field_value:
                    score += 2
        return score, fields

    def _normalize_search_text(self, value) -> str:
        text = normalize_text(value, default="", limit=500).casefold()
        if not text:
            return ""
        text = _SEARCH_SEPARATOR_PATTERN.sub(" ", text)
        return " ".join(text.split())

    def _save_bundle(
        self,
        bundle: dict,
        *,
        columns: list[Column],
        cards: list[Card],
        stickies: list[StickyNote] | None = None,
        events: list[AuditEvent],
    ) -> None:
        self._store.write_bundle(
            columns=columns,
            cards=cards,
            stickies=bundle["stickies"] if stickies is None else stickies,
            events=events,
            settings=bundle["settings"],
        )

    def _should_seed_demo(self, bundle: dict) -> bool:
        cards = bundle["cards"]
        events = bundle["events"]
        columns = bundle["columns"]
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived]
        default_signature = [(column.id, column.label) for column in default_columns()]
        current_signature = [(column.id, column.label) for column in columns]
        if not cards and not events and current_signature == default_signature:
            return True
        prototype_like_board = len(columns) > len(default_signature) or bool(archived_cards)
        if (
            prototype_like_board
            and len(active_cards) <= 1
            and len(cards) <= 3
            and len(events) <= 8
        ):
            return True
        return False

    def _audit_identity(self, payload: dict, *, default_source: str) -> tuple[str, str]:
        source = normalize_source(payload.get("source"), default=default_source)
        default_actor = {
            "ui": "ОПЕРАТОР",
            "api": "API",
            "mcp": "GPT",
            "system": "СИСТЕМА",
        }[source]
        actor_name = normalize_actor_name(payload.get("actor_name"), default=default_actor)
        return actor_name, source

    def _append_event(
        self,
        events: list[AuditEvent],
        *,
        actor_name: str,
        source: str,
        action: str,
        message: str,
        card_id: str | None,
        details: dict | None = None,
    ) -> None:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            timestamp=utc_now_iso(),
            actor_name=actor_name,
            source=source,  # type: ignore[arg-type]
            action=action,
            message=message,
            details=details or {},
            card_id=card_id,
        )
        events.append(event)

    def _find_card(self, cards: list[Card], card_id: str | None) -> Card:
        if not card_id:
            self._fail("validation_error", "Нужно передать card_id.", details={"field": "card_id"})
        for card in cards:
            if card.id == str(card_id):
                return card
        self._fail("not_found", "Карточка не найдена.", status_code=404, details={"card_id": card_id})

    def _find_sticky(self, stickies: list[StickyNote], sticky_id: str | None) -> StickyNote:
        if not sticky_id:
            self._fail("validation_error", "Нужно передать sticky_id.", details={"field": "sticky_id"})
        for sticky in stickies:
            if sticky.id == str(sticky_id):
                return sticky
        self._fail("not_found", "Стикер не найден.", status_code=404, details={"sticky_id": sticky_id})

    def _find_attachment(self, card: Card, attachment_id: str | None) -> Attachment:
        if not attachment_id:
            self._fail("validation_error", "Нужно передать attachment_id.", details={"field": "attachment_id"})
        for attachment in card.attachments:
            if attachment.id == str(attachment_id):
                return attachment
        self._fail(
            "not_found",
            "Файл в карточке не найден.",
            status_code=404,
            details={"attachment_id": attachment_id},
        )

    def _update_title(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        title = self._validated_title(value)
        if title == card.title:
            return False
        previous = card.title
        card.title = title
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="title_changed",
            message=f"{actor_name} изменил заголовок",
            card_id=card.id,
            details={"before": previous, "after": title},
        )
        return True

    def _update_vehicle(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        vehicle = self._validated_vehicle(value)
        if vehicle == card.vehicle:
            return False
        previous = card.vehicle
        card.vehicle = vehicle
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="vehicle_changed",
            message=f"{actor_name} изменил машину карточки",
            card_id=card.id,
            details={"before": previous, "after": vehicle},
        )
        return True

    def _update_description(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        description = self._validated_description(value)
        if description == card.description:
            return False
        previous = card.description
        card.description = description
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="description_changed",
            message=f"{actor_name} изменил описание",
            card_id=card.id,
            details={"before": previous, "after": description},
        )
        return True

    def _update_deadline(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        deadline_total_seconds = self._validated_deadline(value)
        previous_timestamp = card.deadline_timestamp
        previous_total_seconds = card.deadline_total_seconds
        next_timestamp = (utc_now() + timedelta(seconds=deadline_total_seconds)).isoformat()
        if previous_total_seconds == deadline_total_seconds and previous_timestamp == next_timestamp:
            return False
        card.deadline_total_seconds = deadline_total_seconds
        card.deadline_timestamp = next_timestamp
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="signal_changed",
            message=f"{actor_name} изменил сигнал карточки",
            card_id=card.id,
            details={
                "before_deadline_timestamp": previous_timestamp,
                "after_deadline_timestamp": next_timestamp,
                "before_total_seconds": previous_total_seconds,
                "after_total_seconds": deadline_total_seconds,
            },
        )
        return True

    def _update_tags(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        tags = self._validated_tags(value)
        previous_tags = list(card.tags)
        if tags == previous_tags:
            return False
        previous_set = set(previous_tags)
        next_set = set(tags)
        card.tags = tags
        for removed_tag in [tag for tag in previous_tags if tag not in next_set]:
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="tag_removed",
                message=f"{actor_name} снял метку",
                card_id=card.id,
                details={"tag": removed_tag},
            )
        for added_tag in [tag for tag in tags if tag not in previous_set]:
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="tag_added",
                message=f"{actor_name} добавил метку",
                card_id=card.id,
                details={"tag": added_tag},
            )
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="tags_changed",
            message=f"{actor_name} обновил набор меток",
            card_id=card.id,
            details={"before": previous_tags, "after": tags},
        )
        return True

    def _apply_indicator(self, card: Card, indicator: str) -> None:
        total_seconds = max(1, int(card.deadline_total_seconds))
        if indicator == "green":
            total_seconds = max(total_seconds, 10)
            remaining_seconds = total_seconds
        elif indicator == "yellow":
            total_seconds = max(total_seconds, 10)
            remaining_seconds = max(1, int(total_seconds * WARNING_THRESHOLD_RATIO))
        else:
            remaining_seconds = 0
        card.deadline_total_seconds = total_seconds
        card.deadline_timestamp = (utc_now() + timedelta(seconds=remaining_seconds)).isoformat()

    def _attachment_path(self, card_id: str, stored_name: str) -> Path:
        return self._attachments_dir / card_id / stored_name

    def _validated_sticky_text(self, value) -> str:
        text = " ".join(str(value or "").strip().split())
        if not text:
            self._fail(
                "validation_error",
                "Нужно передать непустой текст стикера.",
                details={"field": "text"},
            )
        if len(text) > 1000:
            self._fail(
                "validation_error",
                "Текст стикера не должен превышать 1000 символов.",
                details={"field": "text"},
            )
        return text

    def _validated_sticky_position(self, value, *, field: str) -> int:
        if isinstance(value, bool) or value is None:
            return 0
        try:
            coordinate = int(value)
        except (TypeError, ValueError):
            self._fail(
                "validation_error",
                f"Поле {field} должно быть целым числом.",
                details={"field": field},
            )
        if coordinate < 0:
            coordinate = 0
        if coordinate > 200000:
            self._fail(
                "validation_error",
                f"Поле {field} слишком велико.",
                details={"field": field},
            )
        return coordinate

    def _validated_sticky_deadline(self, value) -> int:
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Поле deadline для стикера должно быть JSON-объектом с числами days и hours.",
                details={"field": "deadline"},
            )
        days = self._validated_deadline_part(value, "days", maximum=365)
        hours = self._validated_deadline_part(value, "hours", maximum=23)
        total_seconds = days * 24 * 3600 + hours * 3600
        if total_seconds <= 0:
            self._fail(
                "validation_error",
                "Срок стикера должен быть больше нуля.",
                details={"field": "deadline"},
            )
        return total_seconds

    def _validated_vehicle(self, value) -> str:
        vehicle = " ".join(str(value or "").strip().split())
        if len(vehicle) > CARD_VEHICLE_LIMIT:
            self._fail(
                "validation_error",
                f"Поле vehicle не должно превышать {CARD_VEHICLE_LIMIT} символов.",
                details={"field": "vehicle"},
            )
        return vehicle

    def _validated_title(self, value) -> str:
        title = str(value or "").strip()
        if not title:
            self._fail("validation_error", "Нужно передать непустой title.", details={"field": "title"})
        if len(title) > CARD_TITLE_LIMIT:
            self._fail(
                "validation_error",
                f"Поле title не должно превышать {CARD_TITLE_LIMIT} символов.",
                details={"field": "title"},
            )
        return title

    def _validated_description(self, value) -> str:
        description = str(value or "").strip()
        if len(description) > CARD_DESCRIPTION_LIMIT:
            self._fail(
                "validation_error",
                f"Поле description не должно превышать {CARD_DESCRIPTION_LIMIT} символов.",
                details={"field": "description"},
            )
        return description

    def _validated_deadline(self, value) -> int:
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Поле deadline должно быть JSON-объектом с числами days, hours, minutes и seconds.",
                details={"field": "deadline"},
            )
        days = self._validated_deadline_part(value, "days", maximum=365)
        hours = self._validated_deadline_part(value, "hours", maximum=23)
        minutes = self._validated_deadline_part(value, "minutes", maximum=59)
        seconds = self._validated_deadline_part(value, "seconds", maximum=59)
        total_seconds = days * 24 * 3600 + hours * 3600 + minutes * 60 + seconds
        if total_seconds <= 0:
            self._fail(
                "validation_error",
                "Срок карточки должен быть больше нуля.",
                details={"field": "deadline"},
            )
        return total_seconds

    def _validated_deadline_part(self, deadline: dict, field: str, *, maximum: int) -> int:
        value = deadline.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            self._fail(
                "validation_error",
                f"Поле deadline.{field} должно быть целым числом.",
                details={"field": f"deadline.{field}"},
            )
        if value < 0 or value > maximum:
            self._fail(
                "validation_error",
                f"Поле deadline.{field} должно быть в диапазоне от 0 до {maximum}.",
                details={"field": f"deadline.{field}"},
            )
        return value

    def _validated_indicator(self, value) -> str:
        indicator = str(value or "").strip().lower()
        if indicator not in VALID_INDICATORS:
            self._fail(
                "validation_error",
                "Поле indicator должно быть одним из значений: green, yellow, red.",
                details={"field": "indicator", "allowed_values": list(VALID_INDICATORS)},
            )
        return indicator

    def _validated_optional_indicator(self, value) -> str | None:
        if value in (None, ""):
            return None
        return self._validated_indicator(value)

    def _validated_optional_status(self, value) -> str | None:
        if value in (None, ""):
            return None
        status = str(value or "").strip().lower()
        if status not in VALID_STATUSES:
            self._fail(
                "validation_error",
                "Поле status должно быть одним из значений: ok, warning, critical, expired.",
                details={"field": "status", "allowed_values": list(VALID_STATUSES)},
            )
        return status

    def _validated_column(self, value, columns: list[Column]) -> str:
        column = str(value or "").strip().lower()
        valid_column_ids = [item.id for item in columns]
        if column not in valid_column_ids:
            self._fail(
                "validation_error",
                "Поле column должно ссылаться на существующий столбец доски.",
                details={"field": "column", "available_columns": valid_column_ids},
            )
        return column

    def _validated_optional_column(self, value, columns: list[Column]) -> str | None:
        if value in (None, ""):
            return None
        return self._validated_column(value, columns)

    def _validated_column_label(self, value, columns: list[Column]) -> str:
        label = str(value or "").strip()
        if not label:
            self._fail(
                "validation_error",
                "Нужно передать непустой label для нового столбца.",
                details={"field": "label"},
            )
        if len(label) > COLUMN_LABEL_LIMIT:
            self._fail(
                "validation_error",
                f"Поле label не должно превышать {COLUMN_LABEL_LIMIT} символов.",
                details={"field": "label"},
            )
        existing_labels = {column.label.casefold() for column in columns}
        if label.casefold() in existing_labels:
            self._fail(
                "validation_error",
                "Столбец с таким названием уже существует.",
                details={"field": "label"},
            )
        return label

    def _validated_tags(self, value) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            self._fail(
                "validation_error",
                "Поле tags должно быть массивом строк.",
                details={"field": "tags"},
            )
        tags = normalize_tags(value)
        if len(tags) > TAG_LIMIT:
            self._fail(
                "validation_error",
                f"Количество меток не должно превышать {TAG_LIMIT}.",
                details={"field": "tags"},
            )
        return tags

    def _validated_optional_tag(self, value) -> str | None:
        if value in (None, ""):
            return None
        tag = normalize_tag_label(value)
        if not tag:
            self._fail(
                "validation_error",
                "Поле tag должно быть непустой строкой.",
                details={"field": "tag"},
            )
        return tag

    def _validated_search_query(self, value) -> str:
        if value in (None, ""):
            return ""
        query = " ".join(str(value).strip().split())
        if len(query) > 160:
            self._fail(
                "validation_error",
                "Поле query не должно превышать 160 символов.",
                details={"field": "query"},
            )
        return query

    def _validated_attachment_name(self, value) -> str:
        file_name = normalize_file_name(value)
        if not file_name:
            self._fail(
                "validation_error",
                "Нужно передать file_name для файла.",
                details={"field": "file_name"},
            )
        return file_name

    def _validated_attachment_content(self, value) -> bytes:
        raw_value = normalize_text(value, default="")
        if not raw_value:
            self._fail(
                "validation_error",
                "Нужно передать content_base64 для файла.",
                details={"field": "content_base64"},
            )
        try:
            content = base64.b64decode(raw_value.encode("utf-8"), validate=True)
        except (binascii.Error, ValueError):
            self._fail(
                "validation_error",
                "Поле content_base64 содержит некорректные данные.",
                details={"field": "content_base64"},
            )
        if not content:
            self._fail(
                "validation_error",
                "Нельзя загрузить пустой файл.",
                details={"field": "content_base64"},
            )
        if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
            self._fail(
                "validation_error",
                "Файл слишком большой.",
                details={"field": "content_base64", "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES},
            )
        return content

    def _validated_limit(self, value, *, default: int, maximum: int) -> int:
        if value in (None, ""):
            return default
        try:
            limit = int(value)
        except (TypeError, ValueError):
            self._fail(
                "validation_error",
                "Параметр limit должен быть целым числом.",
                details={"field": "limit"},
            )
        if limit < 1 or limit > maximum:
            self._fail(
                "validation_error",
                f"Параметр limit должен быть в диапазоне от 1 до {maximum}.",
                details={"field": "limit"},
            )
        return limit

    def _validated_board_scale(self, value) -> float:
        if value in (None, ""):
            self._fail(
                "validation_error",
                "Нужно передать board_scale числом от 0.5 до 1.5.",
                details={"field": "board_scale"},
            )
        try:
            scale = float(value)
        except (TypeError, ValueError):
            self._fail(
                "validation_error",
                "Параметр board_scale должен быть числом от 0.5 до 1.5.",
                details={"field": "board_scale"},
            )
        if scale < 0.5 or scale > 1.5:
            self._fail(
                "validation_error",
                "board_scale должен быть в диапазоне от 0.5 до 1.5.",
                details={"field": "board_scale", "minimum": 0.5, "maximum": 1.5},
            )
        return round(scale, 2)

    def _next_column_id(self, columns: list[Column], label: str) -> str:
        existing_ids = {column.id for column in columns}
        _ = label
        index = 1
        while True:
            column_id = f"column_{index}"
            if column_id not in existing_ids:
                return column_id
            index += 1

    def _ensure_not_archived(self, card: Card) -> None:
        if card.archived:
            self._fail("archived_card", "Архивную карточку нельзя изменить.", status_code=409)

    def _validated_optional_bool(self, payload: dict, field: str, *, default: bool) -> bool:
        if field not in payload:
            return default
        value = payload.get(field)
        if isinstance(value, bool):
            return value
        self._fail(
            "validation_error",
            f"Поле {field} должно иметь тип boolean.",
            details={"field": field},
        )

    def _fail(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict | None = None,
    ) -> None:
        self._logger.warning(
            "service_error code=%s status=%s details=%s message=%s",
            code,
            status_code,
            details or {},
            message,
        )
        raise ServiceError(code, message, status_code=status_code, details=details)
