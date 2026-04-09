from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from logging import Logger
from typing import Any

from ..config import get_attachments_dir
from ..demo_seed import build_demo_board
from ..models import (
    ARCHIVE_PREVIEW_LIMIT,
    CARD_DESCRIPTION_LIMIT,
    CARD_TITLE_LIMIT,
    CARD_VEHICLE_LIMIT,
    MAX_ATTACHMENT_SIZE_BYTES,
    REPAIR_ORDER_FILE_RETENTION_LIMIT,
    TAG_LIMIT,
    VALID_INDICATORS,
    VALID_STATUSES,
    WARNING_THRESHOLD_RATIO,
    Attachment,
    AuditEvent,
    Card,
    CardTag,
    CashBox,
    CashTransaction,
    Column,
    StickyNote,
    format_money_minor,
    normalize_actor_name,
    normalize_bool,
    normalize_cash_direction,
    normalize_file_name,
    normalize_int,
    normalize_money_minor,
    normalize_source,
    normalize_tag_label,
    normalize_text,
    parse_datetime,
    normalize_tags,
    short_entity_id,
    utc_now,
    utc_now_iso,
)
from ..repair_order import (
    REPAIR_ORDER_STATUS_CLOSED,
    REPAIR_ORDER_STATUS_OPEN,
    RepairOrder,
    RepairOrderPayment,
    RepairOrderRow,
    normalize_repair_order_payment_method,
    repair_order_payment_method_from_cashbox_name,
    repair_order_payment_method_from_payments,
    normalize_repair_order_payments,
    repair_order_payment_method_label,
    normalize_repair_order_rows,
    normalize_repair_order_tags,
    normalize_repair_order_status,
)
from ..vehicle_profile import (
    VEHICLE_COMPACT_FIELDS,
    VEHICLE_PRIMARY_FIELDS,
    VehicleProfile,
    normalize_vehicle_field_names,
)
from ..agent.openai_client import AgentModelError, OpenAIJsonAgentClient
from ..printing.service import PrintModuleError, PrintModuleService
from .column_service import ColumnService
from .snapshot_service import SnapshotService
from .vehicle_profile_service import VehicleProfileService
from ..storage.json_store import JsonStore, default_columns


_SEARCH_SEPARATOR_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
_LICENSE_PLATE_PATTERN = re.compile(r"\b[А-ЯA-Z]\d{3}[А-ЯA-Z]{2}\d{2,3}\b", re.IGNORECASE)
_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}")
_CUSTOMER_NAME_PATTERN = re.compile(
    r"(?:клиент|владелец|контакт(?:ное лицо)?)\s*[:\-]?\s*([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z.\-]+(?:\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z.\-]+){0,2})",
    re.IGNORECASE,
)
_MILEAGE_PATTERN = re.compile(r"(?:пробег|mileage|одометр)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE)
_REPAIR_ITEM_SPLIT_PATTERN = re.compile(r"\s*(?:,|;|\n|•|\u2022)\s*")
_REPAIR_REASON_PREFIX_PATTERN = re.compile(
    r"^(?:жалоба|жалобы|причина обращения|причина|со слов клиента|симптом|неисправность)\s*[:\-]?\s*",
    re.IGNORECASE,
)
_REPAIR_FINDING_PREFIX_PATTERN = re.compile(
    r"^(?:обнаружено|обнаружили|выявлено|выявили|диагностика показала|по результатам диагностики|дефект|неисправность)\s*[:\-]?\s*",
    re.IGNORECASE,
)
_REPAIR_RECOMMENDATION_PREFIX_PATTERN = re.compile(
    r"^(?:рекомендовано|рекомендуется|дальше|далее|следующий этап|контроль|нужно в дальнейшем)\s*[:\-]?\s*",
    re.IGNORECASE,
)
_REPAIR_WORK_SECTION_MARKERS = (
    "работы",
    "выполнить",
    "выполнено",
    "сделать",
    "что сделали",
)
_REPAIR_MATERIAL_SECTION_MARKERS = (
    "материалы",
    "запчасти",
    "расходники",
    "что установили",
)
_INSPECTION_SHEET_AUTOFILL_INSTRUCTIONS = """You fill an autoservice inspection sheet from CRM data.
Return exactly one JSON object with these string fields:
- client
- vehicle
- vin_or_plate
- complaint_summary
- findings
- recommendations
- planned_works
- planned_materials
- master_comment
And these optional array fields:
- planned_work_rows
- planned_material_rows
- confidence_notes

Each row inside planned_work_rows / planned_material_rows must be an object with:
- name
- quantity

Also allowed:
- confidence_notes

Rules:
- Preserve important facts from the source data.
- Do not invent facts that are not present in the CRM data.
- Use short structured multiline text with one item per line for findings, recommendations, planned_works, and planned_materials.
- If possible, also return planned_work_rows and planned_material_rows as compact structured rows.
- If a field is unknown, leave it empty instead of guessing.
- vin_or_plate may contain both VIN and license plate in one short string.
"""
_REPAIR_WORK_KEYWORDS = (
    "диагност",
    "провер",
    "осмотр",
    "замен",
    "ремонт",
    "обслуж",
    "то ",
    "т.о",
    "адаптац",
    "калибр",
    "чистк",
    "промыв",
    "снятие",
    "установка",
    "сборка",
    "разборка",
    "сброс",
    "поиск",
    "регулиров",
    "прокач",
)
_REPAIR_MATERIAL_KEYWORDS = (
    "масло",
    "atf",
    "жидк",
    "антифриз",
    "охлажда",
    "фильтр",
    "свеч",
    "катуш",
    "колод",
    "диск",
    "ремень",
    "ролик",
    "проклад",
    "сальник",
    "подшип",
    "стойк",
    "амортиз",
    "рычаг",
    "втулк",
    "сайлент",
    "шрус",
    "смазк",
    "очистител",
)
_REPAIR_FINDING_KEYWORDS = (
    "обнаруж",
    "выяв",
    "ошибк",
    "течь",
    "подтек",
    "запотев",
    "износ",
    "люфт",
    "загряз",
    "пинки",
    "рывк",
    "стук",
    "шум",
    "вибрац",
)
_REPAIR_RECOMMENDATION_KEYWORDS = (
    "рекоменд",
    "желательно",
    "необходимо",
    "следует",
    "контроль",
    "повторн",
    "наблюд",
    "через",
)
_REPAIR_QUANTITY_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:шт|штуки|штук|л|литр(?:а|ов)?|l|компл(?:ект)?|уп(?:ак)?|pcs?)\b",
    re.IGNORECASE,
)
_MOJIBAKE_HINT_CHARS = frozenset("РСЃЌљњўќџ °±²ієїґ†‡‰‹›€")
GPT_WALL_TEXT_LINE_LIMIT = 3000
REPAIR_ORDER_SORT_FIELDS = {"number", "opened_at", "closed_at"}
REPAIR_ORDER_SORT_DIRECTIONS = {"asc", "desc"}


class ServiceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CardService:
    def __init__(
        self,
        store: JsonStore,
        logger: Logger,
        attachments_dir: Path | None = None,
        repair_orders_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._logger = logger
        self._lock = threading.RLock()
        self._attachments_dir = attachments_dir or get_attachments_dir()
        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        self._repair_orders_dir = repair_orders_dir or (self._store.base_dir / "repair-orders")
        self._repair_orders_dir.mkdir(parents=True, exist_ok=True)
        self._vehicle_profiles = VehicleProfileService()
        self._print_module = PrintModuleService(self._store.base_dir)
        self._column_service = ColumnService(
            store,
            logger,
            self._lock,
            audit_identity=lambda payload, default_source: self._audit_identity(payload, default_source=default_source),
            append_event=self._append_event,
            save_bundle=self._save_bundle,
            validated_column=self._validated_column,
            fail=self._fail,
        )
        self._snapshot_service = SnapshotService(
            store,
            self._lock,
            validated_optional_bool=self._validated_optional_bool,
            validated_limit=self._validated_limit,
            visible_cards=self._visible_cards,
            archived_cards=self._archived_cards,
            stickies=self._stickies,
            column_labels=self._column_labels,
            serialize_card=self._serialize_card,
            serialize_sticky=self._serialize_sticky,
            build_board_context_payload=self._build_board_context_payload,
            cards_for_wall=self._cards_for_wall,
            wall_events=self._wall_events,
            build_gpt_wall_text=self._build_gpt_wall_text,
            validated_search_query=self._validated_search_query,
            validated_optional_column=self._validated_optional_column,
            validated_optional_tag=self._validated_optional_tag,
            validated_optional_indicator=self._validated_optional_indicator,
            validated_optional_status=self._validated_optional_status,
            search_card_match=self._search_card_match,
            find_card=self._find_card,
            events_for_card=self._events_for_card,
            fail=self._fail,
        )

    def create_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            events = bundle["events"]
            column_labels = self._column_labels(columns)
            actor_name, source = self._audit_identity(payload, default_source="api")
            vehicle = self._validated_vehicle(payload.get("vehicle", ""))
            vehicle_profile = self._validated_vehicle_profile_create(payload.get("vehicle_profile"))
            vehicle = self._resolved_card_vehicle_label(vehicle, vehicle_profile)
            title = self._validated_title(payload.get("title"))
            description = self._validated_description(payload.get("description", ""))
            deadline_total_seconds = self._validated_deadline(payload.get("deadline"))
            tags = self._validated_tags(payload.get("tags", []))
            default_column_id = columns[0].id if columns else "inbox"
            column = self._validated_column(payload.get("column", default_column_id), columns)
            mark_unread = self._validated_optional_bool(payload, "mark_unread", default=source == "mcp")
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
                position=self._next_card_position(cards, column),
                vehicle=vehicle,
                vehicle_profile=vehicle_profile,
                tags=tags,
                is_unread=mark_unread,
            )
            card.mark_seen(actor_name, seen_at=now_iso)
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
                    "tags": card.tag_labels(),
                    "deadline_total_seconds": card.deadline_total_seconds,
                    "vehicle_profile_state": card.vehicle_profile.data_completion_state,
                    "is_unread": card.is_unread,
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

    def mark_card_seen(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            actor_name = normalize_actor_name(payload.get("actor_name"), default="")
            changed = False
            if card.is_unread:
                card.is_unread = False
                changed = True
            if actor_name:
                changed = card.mark_seen(actor_name, seen_at=card.updated_at or card.created_at) or changed
            if changed:
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(columns),
                    include_removed_attachments=True,
                    viewer_username=actor_name or None,
                ),
                "meta": {
                    "changed": changed,
                },
            }

    def get_cards(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.get_cards(payload)

    def get_board_snapshot(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.get_board_snapshot(payload)

    def get_board_context(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.get_board_context(payload)

    def review_board(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.review_board(payload)

    def list_cashboxes(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=200, maximum=1000)
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            return {
                "cashboxes": [self._serialize_cashbox(cashbox, transactions) for cashbox in cashboxes[:limit]],
                "meta": {
                    "total": len(cashboxes),
                    "transactions_total": len(transactions),
                    "limit": limit,
                },
            }

    def get_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            transaction_limit = self._validated_limit(payload.get("transaction_limit"), default=300, maximum=5000)
            bundle = self._store.read_bundle()
            cashbox = self._find_cashbox(bundle["cashboxes"], payload.get("cashbox_id"))
            transactions = self._cashbox_transactions(bundle["cash_transactions"], cashbox.id)
            return {
                "cashbox": self._serialize_cashbox(cashbox, bundle["cash_transactions"]),
                "transactions": [self._serialize_cash_transaction(item) for item in transactions[:transaction_limit]],
                "meta": {
                    "transactions_total": len(transactions),
                    "transaction_limit": transaction_limit,
                },
            }

    def create_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            now_iso = utc_now_iso()
            cashbox = CashBox(
                id=str(uuid.uuid4()),
                name=self._validated_cashbox_name(payload.get("name"), cashboxes),
                created_at=now_iso,
                updated_at=now_iso,
            )
            cashboxes.append(cashbox)
            cashboxes.sort(key=lambda item: (item.name.casefold(), item.id))
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_created",
                message=f"{actor_name} создал кассу",
                card_id=None,
                details={"cashbox_id": cashbox.id, "cashbox_name": cashbox.name},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {"cashbox": self._serialize_cashbox(cashbox, transactions)}

    def delete_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            related_transactions = self._cashbox_transactions(transactions, cashbox.id)
            statistics = self._cashbox_statistics(cashbox, transactions)
            remaining_cashboxes = [item for item in cashboxes if item.id != cashbox.id]
            remaining_transactions = [item for item in transactions if item.cashbox_id != cashbox.id]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_deleted",
                message=f"{actor_name} удалил кассу",
                card_id=None,
                details={
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "transactions_total": len(related_transactions),
                    "balance_minor": statistics["balance_minor"],
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=remaining_cashboxes,
                cash_transactions=remaining_transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "meta": {
                    "deleted": True,
                    "removed_transactions": len(related_transactions),
                },
            }

    def create_cash_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            transaction = CashTransaction(
                id=str(uuid.uuid4()),
                cashbox_id=cashbox.id,
                direction=normalize_cash_direction(payload.get("direction"), default="income"),
                amount_minor=self._validated_cash_amount_minor(payload),
                note=self._validated_cash_transaction_note(payload.get("note")),
                created_at=utc_now_iso(),
                actor_name=actor_name,
                source=source,
            )
            transactions.append(transaction)
            transactions.sort(key=lambda item: (item.created_at, item.id))
            cashbox.updated_at = transaction.created_at
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cash_transaction_created",
                message=f"{actor_name} добавил движение по кассе",
                card_id=None,
                details={
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "direction": transaction.direction,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
                    "note": transaction.note,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "transaction": self._serialize_cash_transaction(transaction),
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
        return self._snapshot_service.get_gpt_wall(payload)

    def list_archived_cards(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.list_archived_cards(payload)

    def search_cards(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.search_cards(payload)

    def list_overdue_cards(self, payload: dict | None = None) -> dict:
        return self._snapshot_service.list_overdue_cards(payload)

    def list_repair_orders(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            status_filter = self._validated_repair_order_status(
                payload.get("status"),
                default=REPAIR_ORDER_STATUS_OPEN,
                allow_all=True,
            )
            limit = self._validated_limit(
                payload.get("limit"),
                default=REPAIR_ORDER_FILE_RETENTION_LIMIT,
                maximum=REPAIR_ORDER_FILE_RETENTION_LIMIT,
            )
            query = normalize_text(payload.get("query"), default="", limit=240)
            sort_by = self._validated_repair_order_sort_by(payload.get("sort_by"))
            sort_dir = self._validated_repair_order_sort_direction(payload.get("sort_dir"))
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            if self._synchronize_repair_order_numbers(cards):
                self._save_bundle(bundle, columns=bundle["columns"], cards=cards, events=bundle["events"])
            self._cleanup_repair_orders_directory(cards)
            ranked_cards = [
                card
                for card in sorted(cards, key=self._repair_order_sort_key, reverse=True)
                if self._card_has_repair_order(card)
            ]
            active_cards = [card for card in ranked_cards if card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED]
            archived_cards = [card for card in ranked_cards if card.repair_order.status == REPAIR_ORDER_STATUS_CLOSED]
            if status_filter == "all":
                ordered_cards = ranked_cards
            elif status_filter == REPAIR_ORDER_STATUS_CLOSED:
                ordered_cards = archived_cards
            else:
                ordered_cards = active_cards
            filtered_cards = self._filter_repair_order_cards(ordered_cards, query=query)
            sorted_cards = sorted(
                filtered_cards,
                key=lambda card: self._repair_order_list_sort_key(card, sort_by=sort_by),
                reverse=(sort_dir == "desc"),
            )
            return {
                "repair_orders": [
                    self._serialize_repair_order_list_item(card)
                    for card in sorted_cards[:limit]
                ],
                "meta": {
                    "limit": limit,
                    "total": len(sorted_cards),
                    "status": status_filter,
                    "query": query,
                    "sort_by": sort_by,
                    "sort_dir": sort_dir,
                    "active_total": len(active_cards),
                    "archived_total": len(archived_cards),
                    "directory": str(self._repair_orders_dir),
                },
            }

    def get_card_context(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            event_limit = self._validated_limit(payload.get("event_limit"), default=20, maximum=200)
            include_repair_order_text = self._validated_optional_bool(payload, "include_repair_order_text", default=True)
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            column_labels = self._column_labels(bundle["columns"])
            board_context_payload = self._build_board_context_payload(
                bundle["columns"],
                bundle["cards"],
                bundle["stickies"],
                bundle["settings"],
            )
            card_events = self._events_for_card(bundle["events"], card.id)
            events = [event.to_dict() for event in card_events[:event_limit]]
            active_attachments = [attachment.to_dict() for attachment in card.active_attachments()]
            removed_attachments = [attachment.to_dict() for attachment in card.attachments if attachment.removed]
            viewer_username = normalize_actor_name(payload.get("actor_name"), default="") or None
            has_repair_order = self._card_has_repair_order(card)
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=column_labels,
                    event_counts={card.id: len(card_events)},
                    include_removed_attachments=True,
                    viewer_username=viewer_username,
                ),
                "events": events,
                "attachments": active_attachments,
                "removed_attachments": removed_attachments,
                "repair_order_text": (
                    self._repair_order_text_payload(card)
                    if include_repair_order_text and has_repair_order
                    else None
                ),
                "board_context": {
                    "context": board_context_payload["context"],
                    "text": board_context_payload["text"],
                },
                "meta": {
                    "event_limit": event_limit,
                    "events_returned": len(events),
                    "attachments_total": len(active_attachments),
                    "removed_attachments_total": len(removed_attachments),
                    "has_repair_order": has_repair_order,
                },
            }

    def get_card(self, payload: dict) -> dict:
        return self._snapshot_service.get_card(payload)

    def get_card_log(self, payload: dict) -> dict:
        return self._snapshot_service.get_card_log(payload)

    def get_repair_order(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            if self._synchronize_repair_order_numbers(bundle["cards"]):
                self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], events=bundle["events"])
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            return {
                "card_id": card.id,
                "heading": card.heading(),
                "repair_order": card.repair_order.to_dict(),
                "meta": {
                    "has_any_data": self._card_has_repair_order(card),
                },
            }

    def update_repair_order(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            patch = self._validated_repair_order_patch(payload.get("repair_order"))
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_payload = self._merged_repair_order_storage(card.repair_order.to_storage_dict(), patch)
            changed = self._update_repair_order(
                card,
                cards,
                next_payload,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=columns,
                    cards=cards,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            return {
                "repair_order": card.repair_order.to_dict(),
                "card": self._serialize_card(card, events, column_labels=self._column_labels(columns)),
                "meta": {
                    "changed": changed or numbering_changed,
                },
            }

    def replace_repair_order_works(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            rows = self._validated_repair_order_rows(payload.get("rows"), field_name="rows")
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_payload = self._merged_repair_order_storage(
                card.repair_order.to_storage_dict(),
                {"works": rows},
            )
            changed = self._update_repair_order(
                card,
                cards,
                next_payload,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=columns,
                    cards=cards,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            return {
                "repair_order": card.repair_order.to_dict(),
                "card": self._serialize_card(card, events, column_labels=self._column_labels(columns)),
                "meta": {
                    "changed": changed or numbering_changed,
                    "rows": len(card.repair_order.works),
                },
            }

    def replace_repair_order_materials(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            rows = self._validated_repair_order_rows(payload.get("rows"), field_name="rows")
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_payload = self._merged_repair_order_storage(
                card.repair_order.to_storage_dict(),
                {"materials": rows},
            )
            changed = self._update_repair_order(
                card,
                cards,
                next_payload,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=columns,
                    cards=cards,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            return {
                "repair_order": card.repair_order.to_dict(),
                "card": self._serialize_card(card, events, column_labels=self._column_labels(columns)),
                "meta": {
                    "changed": changed or numbering_changed,
                    "rows": len(card.repair_order.materials),
                },
            }

    def set_repair_order_status(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            status = self._validated_repair_order_status(payload.get("status"), default=REPAIR_ORDER_STATUS_OPEN)
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_payload = card.repair_order.to_storage_dict()
            next_payload["status"] = status
            next_payload["closed_at"] = self._repair_order_now() if status == REPAIR_ORDER_STATUS_CLOSED else ""
            changed = self._update_repair_order(
                card,
                cards,
                next_payload,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed:
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action=f"repair_order_{status}",
                    message=f"{actor_name} изменил статус заказ-наряда",
                    card_id=card.id,
                    details={"number": card.repair_order.number, "status": status},
                )
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=columns,
                    cards=cards,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            return {
                "repair_order": card.repair_order.to_dict(),
                "card": self._serialize_card(card, events, column_labels=self._column_labels(columns)),
                "meta": {
                    "changed": changed or numbering_changed,
                    "status": card.repair_order.status,
                },
            }

    def get_repair_order_text_download(self, card_id: str) -> tuple[Path, str]:
        with self._lock:
            bundle = self._store.read_bundle()
            if self._synchronize_repair_order_numbers(bundle["cards"]):
                self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], events=bundle["events"])
            card = self._find_card(bundle["cards"], card_id)
            if not self._card_has_repair_order(card):
                self._fail(
                    "not_found",
                    "У карточки нет заказ-наряда для открытия.",
                    status_code=404,
                    details={"card_id": card.id},
                )
            path = self._ensure_repair_order_text_file(card, force=True)
            return path, path.name

    def get_repair_order_text(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            if self._synchronize_repair_order_numbers(bundle["cards"]):
                self._save_bundle(bundle, columns=bundle["columns"], cards=bundle["cards"], events=bundle["events"])
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            if not self._card_has_repair_order(card):
                self._fail(
                    "not_found",
                    "У карточки нет заказ-наряда для чтения.",
                    status_code=404,
                    details={"card_id": card.id},
                )
            repair_order_text = self._repair_order_text_payload(card, force=True)
            return {
                "card_id": card.id,
                "heading": card.heading(),
                "repair_order": card.repair_order.to_dict(),
                "file_name": repair_order_text["file_name"],
                "file_path": repair_order_text["file_path"],
                "text": repair_order_text["text"],
                "meta": {
                    "updated_at": card.updated_at or card.created_at,
                },
            }

    def get_repair_order_print_workspace(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                return self._print_module.workspace(preview_card, repair_order=preview_card.repair_order)
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def get_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                return self._print_module.get_inspection_sheet_form(preview_card, repair_order=preview_card.repair_order)
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def save_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            session = payload.get("_operator_session") if isinstance(payload.get("_operator_session"), dict) else {}
            actor_name = normalize_actor_name((session.get("username") if session else "") or payload.get("actor_name"))
            try:
                return self._print_module.save_inspection_sheet_form(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    form_data=payload.get("form_data") if isinstance(payload.get("form_data"), dict) else {},
                    filled_by=actor_name,
                    source=str(payload.get("form_source", "manual") or "manual"),
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def autofill_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        session = payload.get("_operator_session") if isinstance(payload.get("_operator_session"), dict) else {}
        actor_name = normalize_actor_name((session.get("username") if session else "") or payload.get("actor_name"))
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            autofill_payload = self._print_module.build_inspection_sheet_autofill_payload(
                preview_card,
                repair_order=preview_card.repair_order,
            )
        try:
            model_client = OpenAIJsonAgentClient()
            result = model_client.complete_json(
                instructions=_INSPECTION_SHEET_AUTOFILL_INSTRUCTIONS,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(autofill_payload, ensure_ascii=False, indent=2),
                    }
                ],
                temperature=0.1,
            )
        except AgentModelError as exc:
            self._fail("agent_unavailable", str(exc), status_code=503)
        form_data = {
            "client": self._inspection_sheet_text(result.get("client")),
            "vehicle": self._inspection_sheet_text(result.get("vehicle")),
            "vin_or_plate": self._inspection_sheet_text(result.get("vin_or_plate")),
            "complaint_summary": self._inspection_sheet_text(result.get("complaint_summary"), multiline=True),
            "findings": self._inspection_sheet_text(result.get("findings"), multiline=True),
            "recommendations": self._inspection_sheet_text(result.get("recommendations"), multiline=True),
            "planned_works": self._inspection_sheet_text(result.get("planned_works"), multiline=True),
            "planned_materials": self._inspection_sheet_text(result.get("planned_materials"), multiline=True),
            "planned_work_rows": self._inspection_sheet_table_rows(
                result.get("planned_work_rows"),
                fallback_text=result.get("planned_works"),
            ),
            "planned_material_rows": self._inspection_sheet_table_rows(
                result.get("planned_material_rows"),
                fallback_text=result.get("planned_materials"),
            ),
            "master_comment": self._inspection_sheet_text(result.get("master_comment"), multiline=True),
        }
        confidence_notes = self._inspection_sheet_lines(result.get("confidence_notes"))
        with self._lock:
            try:
                saved = self._print_module.save_inspection_sheet_form(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    form_data=form_data,
                    filled_by=actor_name,
                    source="ai",
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)
        return {
            **saved,
            "autofill": {
                "model": model_client.model,
                "confidence_notes": confidence_notes,
            },
        }

    def preview_repair_order_print_documents(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                return self._print_module.preview_documents(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    selected_document_ids=payload.get("selected_document_ids"),
                    active_document_id=str(payload.get("active_document_id", "") or ""),
                    selected_template_ids=payload.get("selected_template_ids") if isinstance(payload.get("selected_template_ids"), dict) else {},
                    template_overrides=payload.get("template_overrides") if isinstance(payload.get("template_overrides"), dict) else {},
                    print_settings=payload.get("print_settings") if isinstance(payload.get("print_settings"), dict) else {},
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def export_repair_order_print_pdf(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                pdf_bytes, file_name, meta = self._print_module.export_documents_pdf(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    selected_document_ids=payload.get("selected_document_ids"),
                    selected_template_ids=payload.get("selected_template_ids") if isinstance(payload.get("selected_template_ids"), dict) else {},
                    template_overrides=payload.get("template_overrides") if isinstance(payload.get("template_overrides"), dict) else {},
                    print_settings=payload.get("print_settings") if isinstance(payload.get("print_settings"), dict) else {},
                )
                return {
                    "file_name": file_name,
                    "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                    "size_bytes": len(pdf_bytes),
                    "meta": meta,
                }
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def print_repair_order_documents(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                result = self._print_module.print_documents(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    selected_document_ids=payload.get("selected_document_ids"),
                    selected_template_ids=payload.get("selected_template_ids") if isinstance(payload.get("selected_template_ids"), dict) else {},
                    template_overrides=payload.get("template_overrides") if isinstance(payload.get("template_overrides"), dict) else {},
                    print_settings=payload.get("print_settings") if isinstance(payload.get("print_settings"), dict) else {},
                    printer_name=str(payload.get("printer_name", "") or ""),
                )
                return {
                    **result,
                    "card_id": preview_card.id,
                }
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def save_print_template(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            try:
                return self._print_module.save_template(
                    document_type=str(payload.get("document_type", "") or ""),
                    name=str(payload.get("name", "") or ""),
                    content=str(payload.get("content", "") or ""),
                    template_id=str(payload.get("template_id", "") or ""),
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def duplicate_print_template(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            try:
                return self._print_module.duplicate_template(
                    template_id=str(payload.get("template_id", "") or ""),
                    name=str(payload.get("name", "") or ""),
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def delete_print_template(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            try:
                return self._print_module.delete_template(template_id=str(payload.get("template_id", "") or ""))
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def set_default_print_template(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            try:
                return self._print_module.set_default_template(
                    document_type=str(payload.get("document_type", "") or ""),
                    template_id=str(payload.get("template_id", "") or ""),
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def save_print_module_settings(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            try:
                settings_payload = payload.get("print_settings") if isinstance(payload.get("print_settings"), dict) else payload
                return self._print_module.save_settings(settings_payload if isinstance(settings_payload, dict) else {})
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def _print_module_card(self, card: Card, payload: dict | None = None) -> Card:
        payload = payload or {}
        repair_order_payload = payload.get("repair_order")
        if not isinstance(repair_order_payload, dict):
            return Card.from_dict(card.to_storage_dict())
        base_storage = card.repair_order.to_storage_dict()
        override_storage = RepairOrder.from_dict(repair_order_payload).to_storage_dict()
        merged_storage = {**base_storage, **override_storage}
        cloned_card = Card.from_dict(
            {
                **card.to_storage_dict(),
                "repair_order": merged_storage,
            }
        )
        return cloned_card

    def _inspection_sheet_text(self, value: Any, *, multiline: bool = False) -> str:
        if isinstance(value, list):
            lines = self._inspection_sheet_lines(value)
            return "\n".join(lines) if multiline else ("; ".join(lines[:3]) if lines else "")
        if multiline:
            return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalize_text(value, default="", limit=2000)

    def _inspection_sheet_lines(self, value: Any) -> list[str]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        else:
            items = [value]
        lines: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = normalize_text(item, default="", limit=2000)
            if not text or text in seen:
                continue
            seen.add(text)
            lines.append(text)
            if len(lines) >= 20:
                break
        return lines

    def _inspection_sheet_table_rows(self, value: Any, *, fallback_text: Any = "") -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = normalize_text(item.get("name"), default="", limit=240)
                    quantity = normalize_text(item.get("quantity"), default="", limit=40)
                else:
                    name = normalize_text(item, default="", limit=240)
                    quantity = ""
                if not name and not quantity:
                    continue
                rows.append({"name": name, "quantity": quantity})
                if len(rows) >= 50:
                    break
        if rows:
            return rows
        return [{"name": line, "quantity": ""} for line in self._inspection_sheet_lines(fallback_text)]

    def autofill_vehicle_data(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            vehicle_profile_payload = payload.get("vehicle_profile")
            if vehicle_profile_payload is None:
                vehicle_profile_payload = payload.get("existing_profile")
            vehicle_label = normalize_text(payload.get("vehicle"), default="", limit=CARD_VEHICLE_LIMIT)
            if not vehicle_label:
                vehicle_label = normalize_text(payload.get("explicit_vehicle"), default="", limit=CARD_VEHICLE_LIMIT)
            explicit_title = normalize_text(payload.get("title"), default="", limit=CARD_TITLE_LIMIT)
            if not explicit_title:
                explicit_title = normalize_text(payload.get("explicit_title"), default="", limit=CARD_TITLE_LIMIT)
            explicit_description = normalize_text(payload.get("description"), default="", limit=CARD_DESCRIPTION_LIMIT)
            if not explicit_description:
                explicit_description = normalize_text(payload.get("explicit_description"), default="", limit=CARD_DESCRIPTION_LIMIT)
            raw_text = normalize_text(payload.get("raw_text"), default="", limit=6000)
            analysis_parts: list[str] = []
            for part in (vehicle_label, explicit_title, explicit_description, raw_text):
                cleaned = normalize_text(part, default="", limit=6000)
                if cleaned and cleaned not in analysis_parts:
                    analysis_parts.append(cleaned)
            result = self._vehicle_profiles.autofill_preview(
                raw_text="\n\n".join(analysis_parts),
                image_base64=normalize_text(payload.get("image_base64"), default="", limit=16_000_000) or None,
                image_filename=normalize_text(payload.get("image_filename"), default="", limit=240),
                image_mime_type=normalize_text(payload.get("image_mime_type"), default="", limit=120),
                existing_profile=vehicle_profile_payload,
                explicit_vehicle=vehicle_label,
                explicit_title=explicit_title,
                explicit_description=explicit_description,
            )
            return result.to_dict()

    def update_card(self, payload: dict) -> dict:
        with self._lock:
            updated_fields = {"vehicle", "title", "description", "deadline", "tags", "vehicle_profile", "repair_order"} & set(payload.keys())
            if not updated_fields:
                self._fail(
                    "validation_error",
                    "Для обновления карточки нужно передать хотя бы одно поле: vehicle, title, description, deadline, tags, vehicle_profile или repair_order.",
                    details={"fields": ["vehicle", "title", "description", "deadline", "tags", "vehicle_profile", "repair_order"]},
                )
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")

            changed = False
            changed_fields: list[str] = []
            if "vehicle" in payload:
                vehicle_changed = self._update_vehicle(card, payload.get("vehicle", ""), events, actor_name, source)
                changed = vehicle_changed or changed
                if vehicle_changed:
                    changed_fields.append("vehicle")
            if "vehicle_profile" in payload:
                profile_changed = self._update_vehicle_profile(card, payload.get("vehicle_profile"), events, actor_name, source)
                changed = profile_changed or changed
                if profile_changed:
                    changed_fields.append("vehicle_profile")
            if "title" in payload:
                title_changed = self._update_title(card, payload.get("title"), events, actor_name, source)
                changed = title_changed or changed
                if title_changed:
                    changed_fields.append("title")
            if "description" in payload:
                description_changed = self._update_description(card, payload.get("description", ""), events, actor_name, source)
                changed = description_changed or changed
                if description_changed:
                    changed_fields.append("description")
            if "deadline" in payload:
                deadline_changed = self._update_deadline(card, payload.get("deadline"), events, actor_name, source)
                changed = deadline_changed or changed
                if deadline_changed:
                    changed_fields.append("deadline")
            if "tags" in payload:
                tags_changed = self._update_tags(card, payload.get("tags"), events, actor_name, source)
                changed = tags_changed or changed
                if tags_changed:
                    changed_fields.append("tags")
            if "repair_order" in payload:
                repair_order_changed = self._update_repair_order(
                    card,
                    cards,
                    payload.get("repair_order"),
                    events,
                    actor_name,
                    source,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                )
                changed = repair_order_changed or changed
                if repair_order_changed:
                    changed_fields.append("repair_order")

            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            self._logger.info(
                "update_card id=%s changed=%s actor=%s source=%s",
                card.id,
                changed or numbering_changed,
                actor_name,
                source,
            )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "meta": {
                    "changed": changed or numbering_changed,
                    "changed_fields": changed_fields,
                },
            }

    def autofill_repair_order(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            overwrite = self._validated_optional_bool(payload, "overwrite", default=False)
            next_order, autofill_report = self._autofill_repair_order(card, cards, overwrite=overwrite)
            changed = card.repair_order.to_storage_dict() != next_order.to_storage_dict()
            if changed:
                card.repair_order = next_order
                self._touch_card(card, actor_name)
                self._ensure_repair_order_text_file(card, force=True)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="repair_order_autofilled",
                    message=f"{actor_name} автозаполнил заказ-наряд",
                    card_id=card.id,
                    details={
                        "number": next_order.number,
                        "overwrite": overwrite,
                        "works": len(next_order.works),
                        "materials": len(next_order.materials),
                    },
                )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {
                "repair_order": next_order.to_dict(),
                "card": self._serialize_card(card, events, column_labels=self._column_labels(columns)),
                "meta": {
                    "changed": changed or numbering_changed,
                    "overwrite": overwrite,
                    "autofill_report": autofill_report,
                },
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
            self._touch_card(card, actor_name)
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
            next_column = self._validated_column(payload.get("column", card.column), columns)
            before_card_id = normalize_text(payload.get("before_card_id"), default="", limit=128) or None
            column_labels = self._column_labels(columns)
            move_meta = self._reposition_card(cards, card, target_column=next_column, before_card_id=before_card_id)
            changed = (
                move_meta["before_column"] != move_meta["after_column"]
                or move_meta["before_position"] != move_meta["after_position"]
            )
            if changed:
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_moved",
                    message=f"{actor_name} переместил карточку",
                    card_id=card.id,
                    details=move_meta,
                )
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            self._logger.info(
                "move_card id=%s column=%s position=%s actor=%s source=%s",
                card.id,
                next_column,
                card.position,
                actor_name,
                source,
            )
            affected_column_ids = list(
                dict.fromkeys(
                    [
                        str(move_meta["before_column"] or "").strip(),
                        str(move_meta["after_column"] or "").strip(),
                    ]
                )
            )
            affected_column_ids = [column_id for column_id in affected_column_ids if column_id]
            return {
                "card": self._serialize_card(card, events, column_labels=column_labels),
                "affected_column_ids": affected_column_ids,
                "affected_cards": self._serialize_compact_cards_for_columns(
                    cards,
                    events,
                    affected_column_ids,
                    column_labels=column_labels,
                ),
                "meta": {
                    "changed": changed,
                },
            }

    def archive_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            self._ensure_card_can_be_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            card.archived = True
            self._touch_card(card, actor_name)
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
                        previous_position = card.position
                        self._reposition_card(cards, card, target_column=next_column)
                        self._touch_card(card, actor_name)
                        self._append_event(
                            events,
                            actor_name=actor_name,
                            source=source,
                            action="card_moved",
                            message=f"{actor_name} РїРµСЂРµРјРµСЃС‚РёР» РєР°СЂС‚РѕС‡РєСѓ",
                            card_id=card.id,
                            details={
                                "before_column": previous_column,
                                "after_column": next_column,
                                "before_position": previous_position,
                                "after_position": card.position,
                                "before_card_id": None,
                            },
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
            card.position = self._next_card_position(cards, target_column, exclude_card_id=card.id)
            self._touch_card(card, actor_name)
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
        return self._column_service.list_columns(payload)

    def create_column(self, payload: dict) -> dict:
        return self._column_service.create_column(payload)

    def rename_column(self, payload: dict) -> dict:
        return self._column_service.rename_column(payload)

    def delete_column(self, payload: dict) -> dict:
        return self._column_service.delete_column(payload)

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
            self._write_attachment_file(card.id, stored_name, file_bytes)
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
            self._touch_card(card, actor_name)
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
            self._delete_attachment_file(card.id, attachment.stored_name)
            self._touch_card(card, actor_name)
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
            attachment_path = self._require_attachment_file(card.id, attachment)
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
        event_counts: dict[str, int] | None = None,
        include_removed_attachments: bool = False,
        viewer_username: str | None = None,
        compact: bool = False,
    ) -> dict:
        if event_counts is None:
            events_count = sum(1 for event in events if event.card_id == card.id)
        else:
            events_count = event_counts.get(card.id, 0)
        payload = card.to_dict(
            events_count=events_count,
            include_removed_attachments=include_removed_attachments,
            viewer_username=viewer_username,
            compact=compact,
        )
        payload["column_label"] = (column_labels or {}).get(card.column, card.column)
        return payload

    def _serialize_sticky(self, sticky: StickyNote) -> dict:
        return sticky.to_dict()

    def _serialize_cash_transaction(self, transaction: CashTransaction) -> dict[str, object]:
        return transaction.to_dict()

    def _find_cash_transaction(
        self,
        transactions: list[CashTransaction],
        transaction_id: str | None,
    ) -> CashTransaction | None:
        requested_id = normalize_text(transaction_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for transaction in transactions:
            if transaction.id == requested_id or short_entity_id(transaction.id, prefix="CT").upper() == requested_short_id:
                return transaction
        return None

    def _cashbox_transactions(
        self,
        transactions: list[CashTransaction],
        cashbox_id: str,
    ) -> list[CashTransaction]:
        matched = [item for item in transactions if item.cashbox_id == cashbox_id]
        matched.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return matched

    def _cashbox_statistics(
        self,
        cashbox: CashBox,
        transactions: list[CashTransaction],
    ) -> dict[str, object]:
        related = self._cashbox_transactions(transactions, cashbox.id)
        income_minor = sum(item.amount_minor for item in related if item.direction == "income")
        expense_minor = sum(item.amount_minor for item in related if item.direction == "expense")
        balance_minor = income_minor - expense_minor
        return {
            "transactions_total": len(related),
            "income_total_minor": income_minor,
            "income_total_display": format_money_minor(income_minor),
            "expense_total_minor": expense_minor,
            "expense_total_display": format_money_minor(expense_minor),
            "balance_minor": balance_minor,
            "balance_display": format_money_minor(balance_minor),
            "balance_sign": "negative" if balance_minor < 0 else "positive",
            "last_transaction_at": related[0].created_at if related else None,
        }

    def _serialize_cashbox(
        self,
        cashbox: CashBox,
        transactions: list[CashTransaction],
    ) -> dict[str, object]:
        payload = cashbox.to_dict()
        payload["statistics"] = self._cashbox_statistics(cashbox, transactions)
        return payload

    def _column_labels(self, columns: list[Column]) -> dict[str, str]:
        return {column.id: column.label for column in columns}

    def _serialize_compact_cards_for_columns(
        self,
        cards: list[Card],
        events: list[AuditEvent],
        column_ids: list[str],
        *,
        column_labels: dict[str, str] | None = None,
    ) -> list[dict]:
        normalized_column_ids = [str(column_id or "").strip() for column_id in column_ids if str(column_id or "").strip()]
        if not normalized_column_ids:
            return []
        target_ids = set(normalized_column_ids)
        selected_cards = [
            card
            for card in cards
            if not card.archived and card.column in target_ids
        ]
        selected_cards.sort(key=lambda item: (item.column, item.position, item.created_at, item.updated_at, item.id))
        return [
            self._serialize_card(card, events, column_labels=column_labels, compact=True)
            for card in selected_cards
        ]

    def _ordered_cards_in_column(
        self,
        cards: list[Card],
        column_id: str,
        *,
        exclude_card_id: str | None = None,
    ) -> list[Card]:
        ordered = [
            card
            for card in cards
            if card.column == column_id and (exclude_card_id is None or card.id != exclude_card_id)
        ]
        ordered.sort(key=lambda item: (item.position, item.created_at, item.updated_at, item.id))
        return ordered

    def _next_card_position(
        self,
        cards: list[Card],
        column_id: str,
        *,
        exclude_card_id: str | None = None,
    ) -> int:
        return len(self._ordered_cards_in_column(cards, column_id, exclude_card_id=exclude_card_id))

    def _reposition_card(
        self,
        cards: list[Card],
        card: Card,
        *,
        target_column: str,
        before_card_id: str | None = None,
    ) -> dict[str, object]:
        previous_column = card.column
        previous_position = card.position
        before_card = None
        if before_card_id:
            before_card = self._find_card(cards, before_card_id)
            self._ensure_not_archived(before_card)
            if before_card.column != target_column:
                self._fail(
                    "validation_error",
                    "Карточка before_card_id должна находиться в целевом столбце.",
                    details={"field": "before_card_id", "column": target_column},
                )
            if before_card.id == card.id:
                before_card = None

        source_cards = self._ordered_cards_in_column(cards, previous_column, exclude_card_id=card.id)
        if previous_column == target_column:
            target_cards = list(source_cards)
        else:
            target_cards = self._ordered_cards_in_column(cards, target_column, exclude_card_id=card.id)

        insert_index = len(target_cards)
        if before_card is not None:
            insert_index = next(
                (index for index, item in enumerate(target_cards) if item.id == before_card.id),
                len(target_cards),
            )

        target_cards.insert(insert_index, card)
        card.column = target_column

        if previous_column != target_column:
            for position, item in enumerate(source_cards):
                item.position = position
        for position, item in enumerate(target_cards):
            item.position = position

        return {
            "before_column": previous_column,
            "after_column": target_column,
            "before_position": previous_position,
            "after_position": card.position,
            "before_card_id": before_card.id if before_card is not None else None,
        }

    def _cards_for_wall(self, cards: list[Card], columns: list[Column], *, include_archived: bool) -> list[Card]:
        position_map = {column.id: column.position for column in columns}
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived] if include_archived else []
        active_cards.sort(key=lambda item: (position_map.get(item.column, 999), item.position, item.created_at, item.updated_at, item.id))
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
            payload["actor_name"] = self._repair_mojibake_text(payload.get("actor_name"))
            payload["message"] = self._repair_mojibake_text(payload.get("message"))
            card = cards_by_id.get(event.card_id or "")
            if card is not None:
                payload["card_short_id"] = short_entity_id(card.id, prefix="C")
                payload["card_heading"] = card.heading()
                payload["card_column"] = card.column
                payload["card_column_label"] = column_labels.get(card.column, card.column)
            payload["details_text"] = self._repair_mojibake_text(self._describe_wall_event_details(event, column_labels))
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
        return self._repair_mojibake_text(" ".join(str(value).split()))

    def _repair_mojibake_text(self, value) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            return ""
        if not self._looks_like_mojibake(text):
            return text
        try:
            repaired = text.encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        repaired = " ".join(repaired.split())
        return repaired if self._text_quality_score(repaired) > self._text_quality_score(text) else text

    def _looks_like_mojibake(self, text: str) -> bool:
        suspicious = self._mojibake_hint_score(text)
        lowercase_cyrillic = self._lowercase_cyrillic_score(text)
        return suspicious >= 3 and lowercase_cyrillic * 2 < suspicious

    def _mojibake_hint_score(self, text: str) -> int:
        return sum(1 for char in text if char in _MOJIBAKE_HINT_CHARS)

    def _lowercase_cyrillic_score(self, text: str) -> int:
        return sum(1 for char in text if ("а" <= char <= "я") or char == "ё")

    def _text_quality_score(self, text: str) -> int:
        lowercase_cyrillic = self._lowercase_cyrillic_score(text)
        uppercase_cyrillic = sum(1 for char in text if ("А" <= char <= "Я") or char == "Ё")
        ascii_letters = sum(1 for char in text if char.isascii() and char.isalpha())
        suspicious = self._mojibake_hint_score(text)
        return lowercase_cyrillic * 4 + uppercase_cyrillic * 2 + ascii_letters - suspicious * 3

    def _build_board_context_payload(
        self,
        columns: list[Column],
        cards: list[Card],
        stickies: list[StickyNote],
        settings: dict,
    ) -> dict:
        active_cards_total = 0
        archived_cards_total = 0
        active_counts_by_column: dict[str, int] = {}
        archived_counts_by_column: dict[str, int] = {}
        for card in cards:
            if card.archived:
                archived_cards_total += 1
                archived_counts_by_column[card.column] = archived_counts_by_column.get(card.column, 0) + 1
            else:
                active_cards_total += 1
                active_counts_by_column[card.column] = active_counts_by_column.get(card.column, 0) + 1
        column_summary: list[dict[str, object]] = []
        for column in columns:
            column_summary.append(
                {
                    "id": column.id,
                    "label": column.label,
                    "position": column.position,
                    "active_cards": active_counts_by_column.get(column.id, 0),
                    "archived_cards": archived_counts_by_column.get(column.id, 0),
                }
            )

        context = {
            "product_name": "AutoStop CRM",
            "board_name": "Current AutoStop CRM Board",
            "board_key": "minimal-kanban/current-local-board",
            "board_scope": "single_local_board_instance",
            "scope_rule": (
                "This connector may operate only on the current AutoStop CRM board served by this exact MCP/API "
                "instance. Do not use it for Trello, YouGile, or any other kanban system."
            ),
            "storage_backend": "local_json_store",
            "columns_total": len(columns),
            "active_cards_total": active_cards_total,
            "archived_cards_total": archived_cards_total,
            "stickies_total": len(stickies),
            "board_scale": float(settings.get("board_scale", 1.0) or 1.0),
            "vehicle_profile_compact_fields": list(VEHICLE_COMPACT_FIELDS),
            "vehicle_profile_autofill_mode": "card_content_first",
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
            "vehicle_profile_compact_fields: " + ", ".join(context["vehicle_profile_compact_fields"]),
            "vehicle_profile_autofill_mode: card_content_first (vehicle/title/description first, safe merge after)",
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
                self._append_vehicle_profile_wall_lines(lines, card, indent="    ")
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
                self._append_vehicle_profile_wall_lines(lines, card, indent="    ")
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
                self._append_vehicle_profile_wall_lines(lines, card, indent="    ")
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
                self._append_vehicle_profile_wall_lines(lines, card, indent="    ")
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

    def _append_vehicle_profile_wall_lines(self, lines: list[str], card: dict, *, indent: str = "    ") -> None:
        profile = card.get("vehicle_profile_compact")
        full_profile = card.get("vehicle_profile")
        if not isinstance(profile, dict):
            profile = card.get("vehicle_profile")
        if not isinstance(full_profile, dict):
            full_profile = {}
        if not isinstance(profile, dict):
            return
        if not any(profile.get(field_name) for field_name in VEHICLE_COMPACT_FIELDS):
            return

        lines.append(f"{indent}техкарта:")
        identity_line = " | ".join(
            part
            for part in (
                str(profile.get("make_display") or "").strip(),
                str(profile.get("model_display") or "").strip(),
                str(profile.get("production_year") or "").strip(),
            )
            if part
        )
        if identity_line:
            lines.append(f"{indent}  идентификация: {identity_line}")
        customer_phone = str(full_profile.get("customer_phone") or "").strip()
        customer_name = str(full_profile.get("customer_name") or "").strip()
        if customer_phone:
            lines.append(f"{indent}  клиент / телефон: {customer_phone}")
        if customer_name:
            lines.append(f"{indent}  клиент / ФИО: {customer_name}")
        if profile.get("vin"):
            lines.append(f"{indent}  vin: {profile.get('vin')}")

        engine_line = " | ".join(
            part
            for part in (
                str(profile.get("engine_model") or "").strip(),
            )
            if part
        )
        if engine_line:
            lines.append(f"{indent}  двигатель: {engine_line}")

        transmission_line = " | ".join(
            part
            for part in (
                str(profile.get("gearbox_model") or "").strip(),
                str(profile.get("drivetrain") or "").strip(),
            )
            if part
        )
        if transmission_line:
            lines.append(f"{indent}  трансмиссия: {transmission_line}")

        if profile.get("oem_notes"):
            lines.append(f"{indent}  Р·Р°РјРµС‚РєР°: {profile.get('oem_notes')}")

        fluids_line = " | ".join(
            part
            for part in (
                f"engine_oil={profile.get('oil_engine_capacity_l')}L" if profile.get("oil_engine_capacity_l") else "",
                f"gearbox_oil={profile.get('oil_gearbox_capacity_l')}L" if profile.get("oil_gearbox_capacity_l") else "",
                f"coolant={profile.get('coolant_capacity_l')}L" if profile.get("coolant_capacity_l") else "",
            )
            if part
        )
        if fluids_line:
            lines.append(f"{indent}  жидкости: {fluids_line}")

        misc_line = " | ".join(
            part
            for part in (
                str(profile.get("fuel_type") or "").strip(),
                str(profile.get("wheel_bolt_pattern") or "").strip(),
                str(profile.get("generation_or_platform") or "").strip(),
            )
            if part
        )
        if misc_line:
            lines.append(f"{indent}  прочее: {misc_line}")

        source_line = " | ".join(
            part
            for part in (
                str(profile.get("data_completion_state") or "").strip(),
                f"confidence={profile.get('source_confidence')}" if profile.get("source_confidence") not in (None, "") else "",
                str(profile.get("source_summary") or "").strip(),
            )
            if part
        )
        if source_line:
            lines.append(f"{indent}  источники: {source_line}")

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

        profile = card.vehicle_profile
        repair_order = card.repair_order
        searchable_fields = {
            "id": card.id,
            "short_id": short_entity_id(card.id, prefix="C"),
            "heading": card.heading(),
            "vehicle": card.vehicle,
            "title": card.title,
            "description": card.description,
            "tags": " ".join(card.tag_labels()),
            "make_display": profile.make_display,
            "model_display": profile.model_display,
            "generation_or_platform": profile.generation_or_platform,
            "production_year": str(profile.production_year or ""),
            "vin": profile.vin,
            "customer_name": profile.customer_name,
            "customer_phone": profile.customer_phone,
            "engine_code": profile.engine_code,
            "engine_model": profile.engine_model,
            "gearbox_type": profile.gearbox_type,
            "gearbox_model": profile.gearbox_model,
            "drivetrain": profile.drivetrain,
            "fuel_type": profile.fuel_type,
            "wheel_bolt_pattern": profile.wheel_bolt_pattern,
            "oem_notes": profile.oem_notes,
            "repair_order_number": repair_order.number,
            "repair_order_status": repair_order.status,
            "repair_order_date": repair_order.date,
            "repair_order_opened_at": repair_order.opened_at,
            "repair_order_closed_at": repair_order.closed_at,
            "repair_order_client": repair_order.client,
            "repair_order_phone": repair_order.phone,
            "repair_order_vehicle": repair_order.vehicle,
            "repair_order_license_plate": repair_order.license_plate,
            "repair_order_vin": repair_order.vin,
            "repair_order_mileage": repair_order.mileage,
            "repair_order_payment_method": repair_order.payment_method,
            "repair_order_prepayment": repair_order.prepayment,
            "repair_order_reason": repair_order.reason,
            "repair_order_comment": repair_order.comment,
            "repair_order_note": repair_order.note,
            "repair_order_tags": " ".join(tag.label for tag in repair_order.tags),
            "repair_order_works": " ".join(row.name for row in repair_order.works),
            "repair_order_materials": " ".join(row.name for row in repair_order.materials),
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
            ("make_display", 6),
            ("model_display", 6),
            ("vin", 9),
            ("customer_name", 7),
            ("customer_phone", 8),
            ("engine_code", 7),
            ("engine_model", 5),
            ("gearbox_type", 4),
            ("gearbox_model", 5),
            ("drivetrain", 4),
            ("fuel_type", 3),
            ("wheel_bolt_pattern", 4),
            ("generation_or_platform", 4),
            ("production_year", 3),
            ("repair_order_number", 9),
            ("repair_order_status", 3),
            ("repair_order_client", 8),
            ("repair_order_phone", 8),
            ("repair_order_license_plate", 8),
            ("repair_order_vehicle", 6),
            ("repair_order_vin", 9),
            ("repair_order_mileage", 4),
            ("repair_order_payment_method", 3),
            ("repair_order_prepayment", 3),
            ("repair_order_reason", 5),
            ("repair_order_date", 3),
            ("repair_order_opened_at", 4),
            ("repair_order_closed_at", 4),
            ("repair_order_tags", 4),
            ("repair_order_works", 5),
            ("repair_order_materials", 5),
            ("repair_order_comment", 3),
            ("repair_order_note", 3),
            ("oem_notes", 2),
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
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
        events: list[AuditEvent],
    ) -> None:
        written_bundle = self._store.write_bundle(
            columns=columns,
            cards=cards,
            stickies=bundle["stickies"] if stickies is None else stickies,
            cashboxes=bundle["cashboxes"] if cashboxes is None else cashboxes,
            cash_transactions=bundle["cash_transactions"] if cash_transactions is None else cash_transactions,
            events=events,
            settings=bundle["settings"],
        )
        written_cards = written_bundle["cards"]
        self._cleanup_repair_orders_directory(written_cards)
        self._cleanup_attachment_directories(written_cards)

    def _touch_card(self, card: Card, actor_name: str | None = None) -> str:
        updated_at = utc_now_iso()
        card.updated_at = updated_at
        card.mark_seen(actor_name, seen_at=updated_at)
        return updated_at

    def _should_seed_demo(self, bundle: dict) -> bool:
        cards = bundle["cards"]
        stickies = bundle["stickies"]
        events = bundle["events"]
        columns = bundle["columns"]
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived]
        default_signature = [(column.id, column.label) for column in default_columns()]
        current_signature = [(column.id, column.label) for column in columns]
        if not cards and not stickies and not events and current_signature == default_signature:
            return True
        default_pairs = set(default_signature)
        setup_only_events = {
            "column_created",
            "column_deleted",
            "column_renamed",
            "board_scale_changed",
        }
        empty_generic_board = (
            not cards
            and not stickies
            and 1 <= len(columns) <= len(default_signature)
            and all((column.id, column.label) in default_pairs for column in columns)
            and len(events) <= 8
            and all(not event.card_id and event.action in setup_only_events for event in events)
        )
        if empty_generic_board:
            return True
        prototype_like_board = len(columns) > len(default_signature) or bool(archived_cards)
        if (
            prototype_like_board
            and len(stickies) <= 1
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
        requested_id = str(sticky_id).strip()
        requested_short_id = requested_id.upper()
        for sticky in stickies:
            if sticky.id == requested_id or short_entity_id(sticky.id, prefix="S").upper() == requested_short_id:
                return sticky
        self._fail("not_found", "Стикер не найден.", status_code=404, details={"sticky_id": sticky_id})

    def _find_cashbox(self, cashboxes: list[CashBox], cashbox_id: str | None) -> CashBox:
        if not cashbox_id:
            self._fail("validation_error", "Нужно передать cashbox_id.", details={"field": "cashbox_id"})
        requested_id = str(cashbox_id).strip()
        requested_short_id = requested_id.upper()
        for cashbox in cashboxes:
            if cashbox.id == requested_id or short_entity_id(cashbox.id, prefix="CB").upper() == requested_short_id:
                return cashbox
        self._fail("not_found", "Касса не найдена.", status_code=404, details={"cashbox_id": cashbox_id})

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

    def _update_vehicle_profile(
        self,
        card: Card,
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> bool:
        profile, changed_fields = self._merge_vehicle_profile_patch(card.vehicle_profile, value)
        if not changed_fields and profile.to_storage_dict() == card.vehicle_profile.to_storage_dict():
            return False
        previous_profile = card.vehicle_profile
        previous_vehicle = card.vehicle
        card.vehicle_profile = profile
        card.vehicle = self._sync_vehicle_label_with_profile(previous_vehicle, previous_profile, profile)
        details: dict[str, Any] = {
            "changed_fields": changed_fields,
            "completion_state": profile.data_completion_state,
            "confidence": profile.source_confidence,
        }
        if profile.source_summary:
            details["source_summary"] = profile.source_summary
        if profile.warnings:
            details["warnings"] = list(profile.warnings)
        if previous_vehicle != card.vehicle:
            details["vehicle_label_before"] = previous_vehicle
            details["vehicle_label_after"] = card.vehicle
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="vehicle_profile_updated",
            message=f"{actor_name} обновил техкарту автомобиля",
            card_id=card.id,
            details=details,
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
        previous_by_label = {tag.label: tag for tag in previous_tags}
        next_by_label = {tag.label: tag for tag in tags}
        card.tags = tags
        for removed_label in [label for label in previous_by_label if label not in next_by_label]:
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="tag_removed",
                message=f"{actor_name} снял метку",
                card_id=card.id,
                details={"tag": removed_label},
            )
        for added_label in [label for label in next_by_label if label not in previous_by_label]:
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="tag_added",
                message=f"{actor_name} добавил метку",
                card_id=card.id,
                details={"tag": added_label},
            )
        for shared_label in [label for label in next_by_label if label in previous_by_label]:
            before_tag = previous_by_label[shared_label]
            after_tag = next_by_label[shared_label]
            if before_tag.color == after_tag.color:
                continue
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="tag_color_changed",
                message=f"{actor_name} изменил цвет метки",
                card_id=card.id,
                details={"tag": shared_label, "before_color": before_tag.color, "after_color": after_tag.color},
            )
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="tags_changed",
            message=f"{actor_name} обновил набор меток",
            card_id=card.id,
            details={"before": [tag.to_dict() for tag in previous_tags], "after": [tag.to_dict() for tag in tags]},
        )
        return True

    def _update_repair_order(
        self,
        card: Card,
        cards: list[Card],
        value,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
        *,
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
    ) -> bool:
        previous_order = RepairOrder.from_dict(card.repair_order.to_storage_dict())
        order = self._prepared_repair_order(
            self._validated_repair_order(value),
            cards,
            card=card,
            exclude_card_id=card.id,
        )
        if cashboxes is not None and cash_transactions is not None:
            order = self._sync_repair_order_payment_transactions(
                card,
                previous_order,
                order,
                cashboxes,
                cash_transactions,
                events,
                actor_name,
                source,
            )
        if order.to_storage_dict() == previous_order.to_storage_dict():
            return False
        card.repair_order = order
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="repair_order_updated",
            message=f"{actor_name} обновил заказ-наряд",
            card_id=card.id,
            details={
                "number": order.number,
                "status": order.status,
                "works": len(order.works),
                "materials": len(order.materials),
                "payments": len(order.payments),
                "paid_total": order.prepayment_amount(),
                "payment_status": order.payment_status(),
            },
        )
        return True

    def _repair_order_payment_financial_signature(self, payment: RepairOrderPayment) -> tuple[str, str, str]:
        return (
            payment.amount or "",
            payment.note or "",
            payment.cashbox_id or "",
        )

    def _sync_repair_order_payment_transactions(
        self,
        card: Card,
        previous_order: RepairOrder,
        next_order: RepairOrder,
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        events: list[AuditEvent],
        actor_name: str,
        source: str,
    ) -> RepairOrder:
        next_payments = [RepairOrderPayment.from_dict(payment.to_storage_dict()) for payment in next_order.payments]
        next_by_id = {payment.id: payment for payment in next_payments if payment.id}

        for previous_payment in previous_order.payments:
            existing_transaction = self._find_cash_transaction(cash_transactions, previous_payment.cash_transaction_id)
            next_payment = next_by_id.get(previous_payment.id)
            keep_transaction = (
                existing_transaction is not None
                and next_payment is not None
                and self._repair_order_payment_financial_signature(previous_payment)
                == self._repair_order_payment_financial_signature(next_payment)
            )
            if keep_transaction:
                next_payment.cash_transaction_id = existing_transaction.id
                next_payment.actor_name = next_payment.actor_name or previous_payment.actor_name or existing_transaction.actor_name
                next_payment.cashbox_id = next_payment.cashbox_id or previous_payment.cashbox_id or existing_transaction.cashbox_id
                next_payment.cashbox_name = next_payment.cashbox_name or previous_payment.cashbox_name
                continue
            if existing_transaction is None:
                continue
            cash_transactions[:] = [item for item in cash_transactions if item.id != existing_transaction.id]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cash_transaction_deleted",
                message=f"{actor_name} удалил движение по кассе",
                card_id=card.id,
                details={
                    "cash_transaction_id": existing_transaction.id,
                    "cashbox_id": existing_transaction.cashbox_id,
                    "repair_order_number": next_order.number or previous_order.number,
                    "amount_minor": existing_transaction.amount_minor,
                },
            )

        for payment in next_payments:
            if not payment.id:
                payment.id = f"payment-{uuid.uuid4().hex[:10]}"
            payment.actor_name = payment.actor_name or actor_name
            payment.payment_method = normalize_repair_order_payment_method(payment.payment_method or next_order.payment_method)
            if not payment.cashbox_id:
                payment.cashbox_name = ""
                payment.cash_transaction_id = ""
                continue
            cashbox = self._find_cashbox(cashboxes, payment.cashbox_id)
            payment.cashbox_id = cashbox.id
            payment.cashbox_name = cashbox.name
            payment.payment_method = repair_order_payment_method_from_cashbox_name(
                payment.cashbox_name,
                default=payment.payment_method or next_order.payment_method,
            )
            if self._find_cash_transaction(cash_transactions, payment.cash_transaction_id) is not None:
                continue
            transaction = CashTransaction(
                id=str(uuid.uuid4()),
                cashbox_id=cashbox.id,
                direction="income",
                amount_minor=normalize_money_minor(payment.amount, minimum=1),
                note=self._validated_cash_transaction_note(payment.note or f"Заказ-наряд №{next_order.number or '-'}"),
                created_at=payment.paid_at or utc_now_iso(),
                actor_name=payment.actor_name or actor_name,
                source=source,
            )
            cash_transactions.append(transaction)
            cash_transactions.sort(key=lambda item: (item.created_at, item.id))
            cashbox.updated_at = transaction.created_at
            payment.cash_transaction_id = transaction.id
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cash_transaction_created",
                message=f"{actor_name} добавил движение по кассе",
                card_id=card.id,
                details={
                    "cash_transaction_id": transaction.id,
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "repair_order_number": next_order.number,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
                },
            )

        next_order.payments = next_payments
        next_order.payment_method = repair_order_payment_method_from_payments(
            next_payments,
            default=next_order.payment_method,
        )
        next_order.prepayment = next_order.prepayment_amount()
        return next_order

    def _autofill_repair_order(self, card: Card, cards: list[Card], *, overwrite: bool) -> tuple[RepairOrder, dict[str, object]]:
        order = self._prepared_repair_order(
            RepairOrder.from_dict(card.repair_order.to_storage_dict()),
            cards,
            card=card,
            exclude_card_id=card.id,
        )
        profile = card.vehicle_profile
        related_cards = self._related_cards(card, cards)
        suggested_client = profile.customer_name or self._extract_customer_name(card)
        if not suggested_client:
            suggested_client = self._first_related_value(
                related_cards,
                lambda related: related.repair_order.client,
                lambda related: related.vehicle_profile.customer_name,
            )
        if overwrite or not order.client:
            order.client = suggested_client or order.client
        suggested_phone = profile.customer_phone or self._extract_phone(card)
        if not suggested_phone:
            suggested_phone = self._first_related_value(
                related_cards,
                lambda related: related.repair_order.phone,
                lambda related: related.vehicle_profile.customer_phone,
            )
        if overwrite or not order.phone:
            order.phone = suggested_phone or order.phone
        if overwrite or not order.vehicle:
            order.vehicle = card.vehicle_display() or order.vehicle
        if overwrite or not order.opened_at:
            order.opened_at = self._repair_order_card_datetime(card.created_at) or order.opened_at
        if overwrite or not order.vin:
            order.vin = profile.vin or self._extract_vin(card, fallback=order.vin)
        if overwrite or not order.mileage:
            order.mileage = (str(profile.mileage) if profile.mileage else "") or self._extract_mileage(card, fallback=order.mileage)
        if overwrite or not order.reason:
            order.reason = self._build_repair_order_reason(card) or order.reason
        if overwrite or not order.license_plate:
            order.license_plate = self._extract_license_plate(card, fallback=order.license_plate)
        suggested_works = self._suggest_repair_order_rows(card, profile=profile, cards=cards, related_cards=related_cards, section="works")
        suggested_materials = self._suggest_repair_order_rows(card, profile=profile, cards=cards, related_cards=related_cards, section="materials")
        price_hits: list[dict[str, str]] = []
        suggested_works, work_price_hits = self._apply_history_prices_to_rows(
            suggested_works,
            section="works",
            cards=cards,
            related_cards=related_cards,
        )
        suggested_materials, material_price_hits = self._apply_history_prices_to_rows(
            suggested_materials,
            section="materials",
            cards=cards,
            related_cards=related_cards,
        )
        price_hits.extend(work_price_hits)
        price_hits.extend(material_price_hits)

        if overwrite:
            order.works = suggested_works or order.works
            order.materials = suggested_materials or order.materials
        else:
            order.works = self._merge_repair_order_rows(order.works, suggested_works)
            order.materials = self._merge_repair_order_rows(order.materials, suggested_materials)

        if overwrite or not order.comment:
            order.comment = self._build_client_description(card, order) or order.comment
        if overwrite or not order.note:
            order.note = self._build_internal_repair_note(card, order, price_hits=price_hits) or order.note

        review_items: list[str] = []
        if suggested_works or suggested_materials:
            review_items.append("Проверьте перечень работ и материалов перед сохранением.")
        if price_hits:
            review_items.append("Цены подтянуты из истории доски: проверьте актуальность.")
        autofill_report: dict[str, object] = {
            "matched_history_cards": len(related_cards),
            "works_suggested": len(suggested_works),
            "materials_suggested": len(suggested_materials),
            "prices_applied": price_hits,
            "review_items": review_items,
        }
        return order, autofill_report

    def _related_cards(self, card: Card, cards: list[Card]) -> list[Card]:
        current_vin = self._extract_vin(card, fallback="")
        current_license = self._extract_license_plate(card, fallback="")
        current_phone = self._extract_phone(card)
        current_vehicle = self._normalize_search_text(card.vehicle_display() or card.repair_order.vehicle)
        current_customer = self._normalize_search_text(card.vehicle_profile.customer_name or card.repair_order.client)
        ranked: list[tuple[int, str, Card]] = []
        for candidate in cards:
            if candidate.id == card.id:
                continue
            score = 0
            if current_vin and current_vin == self._extract_vin(candidate, fallback=""):
                score += 12
            if current_license and current_license == self._extract_license_plate(candidate, fallback=""):
                score += 10
            if current_phone and current_phone == self._extract_phone(candidate):
                score += 8
            candidate_vehicle = self._normalize_search_text(candidate.vehicle_display() or candidate.repair_order.vehicle)
            if current_vehicle and current_vehicle == candidate_vehicle:
                score += 2
            candidate_customer = self._normalize_search_text(candidate.vehicle_profile.customer_name or candidate.repair_order.client)
            if current_customer and current_customer == candidate_customer:
                score += 1
            if score > 0:
                ranked.append((score, str(candidate.created_at or ""), candidate))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [candidate for _, _, candidate in ranked]

    def _first_related_value(self, related_cards: list[Card], *getters) -> str:
        for related in related_cards:
            for getter in getters:
                value = normalize_text(getter(related), default="", limit=160)
                if value:
                    return value
        return ""

    def _extract_phone(self, card: Card) -> str:
        if card.vehicle_profile.customer_phone:
            return normalize_text(card.vehicle_profile.customer_phone, default="", limit=32)
        haystack = self._repair_analysis_text(card)
        match = _PHONE_PATTERN.search(haystack)
        if not match:
            return ""
        return self._format_phone(match.group(0))

    def _format_phone(self, value: str) -> str:
        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) == 11 and digits.startswith("7"):
            return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
        return normalize_text(value, default="", limit=32)

    def _extract_customer_name(self, card: Card) -> str:
        if card.vehicle_profile.customer_name:
            return normalize_text(card.vehicle_profile.customer_name, default="", limit=80)
        match = _CUSTOMER_NAME_PATTERN.search(self._repair_analysis_text(card))
        if not match:
            return ""
        blocked_tokens = {"ТЕЛЕФОН", "PHONE", "VIN", "ГОСНОМЕР", "ПРОБЕГ", "MILEAGE"}
        parts: list[str] = []
        for part in str(match.group(1) or "").strip().split():
            normalized = str(part or "").strip()
            if not normalized:
                continue
            if normalized.upper().strip(":.,-") in blocked_tokens:
                break
            parts.append(normalized)
        if not parts:
            return ""
        return " ".join(part[:1].upper() + part[1:].lower() for part in parts)[:80]

    def _repair_analysis_text(self, card: Card) -> str:
        return "\n".join(
            part
            for part in (
                card.vehicle,
                card.title,
                card.description,
                card.vehicle_profile.oem_notes,
            )
            if part
        )

    def _repair_text_lines(self, *parts: str) -> list[str]:
        lines: list[str] = []
        for part in parts:
            raw_text = str(part or "").replace("\r", "\n")
            for chunk in raw_text.split("\n"):
                cleaned = self._clean_repair_text_fragment(chunk, limit=320)
                if cleaned and cleaned not in lines:
                    lines.append(cleaned)
                if len(cleaned) > 80 and ":" not in cleaned:
                    for sentence in re.split(r"(?<=[.!?;])\s+", cleaned):
                        normalized = self._clean_repair_text_fragment(sentence, limit=220)
                        if normalized and normalized not in lines:
                            lines.append(normalized)
        return lines

    def _clean_repair_text_fragment(self, value: str, *, limit: int = 240) -> str:
        text = normalize_text(value, default="", limit=limit)
        text = re.sub(r"^[\-\*\u2022\d\.\)\s]+", "", text)
        text = text.strip(" ,.;:-")
        return text

    def _build_repair_order_reason(self, card: Card) -> str:
        complaint_lines: list[str] = []
        description_lines = self._repair_text_lines(card.description)
        for line in description_lines:
            lowered = line.casefold()
            cleaned = _REPAIR_REASON_PREFIX_PATTERN.sub("", line).strip(" .,:;-")
            if not cleaned:
                continue
            if _REPAIR_REASON_PREFIX_PATTERN.match(line) or (
                any(token in lowered for token in ("жалоб", "пинки", "рывк", "стук", "шум", "вибрац", "течь", "не завод", "горит"))
                and not self._looks_like_work_item(cleaned)
            ):
                if cleaned not in complaint_lines:
                    complaint_lines.append(cleaned)
        if complaint_lines:
            return normalize_text(" ".join(complaint_lines[:2]), default="", limit=400)
        first_description = next((line for line in description_lines if not self._looks_like_work_item(line)), "")
        if first_description:
            return normalize_text(first_description, default="", limit=400)
        return normalize_text(card.title, default="", limit=400)

    def _extract_repair_findings(self, card: Card) -> list[str]:
        findings: list[str] = []
        for line in self._repair_text_lines(card.description, card.vehicle_profile.oem_notes):
            lowered = line.casefold()
            cleaned = _REPAIR_FINDING_PREFIX_PATTERN.sub("", line).strip(" .,:;-")
            if not cleaned:
                continue
            if _REPAIR_FINDING_PREFIX_PATTERN.match(line) or any(keyword in lowered for keyword in _REPAIR_FINDING_KEYWORDS):
                if cleaned not in findings:
                    findings.append(cleaned)
        return findings[:3]

    def _extract_repair_recommendations(self, card: Card) -> list[str]:
        recommendations: list[str] = []
        for line in self._repair_text_lines(card.description, card.vehicle_profile.oem_notes):
            lowered = line.casefold()
            cleaned = _REPAIR_RECOMMENDATION_PREFIX_PATTERN.sub("", line).strip(" .,:;-")
            if not cleaned:
                continue
            if _REPAIR_RECOMMENDATION_PREFIX_PATTERN.match(line) or any(keyword in lowered for keyword in _REPAIR_RECOMMENDATION_KEYWORDS):
                if cleaned not in recommendations:
                    recommendations.append(cleaned)
        return recommendations[:3]

    def _suggest_repair_order_rows(
        self,
        card: Card,
        *,
        profile: VehicleProfile,
        cards: list[Card],
        related_cards: list[Card],
        section: str,
    ) -> list[RepairOrderRow]:
        _ = cards
        _ = related_cards
        suggested: list[RepairOrderRow] = []
        title = normalize_text(card.title, default="", limit=240)
        if section == "works" and title and self._looks_like_work_item(title):
            self._append_repair_row(
                suggested,
                RepairOrderRow(name=self._normalize_work_name(title), quantity="1"),
            )
        for line in self._repair_text_lines(card.description, profile.oem_notes):
            if self._line_declares_repair_section(line, section=section):
                for item in self._split_repair_items(self._repair_section_payload(line)):
                    row = self._repair_row_from_item(item, section=section, profile=profile, force_section=True)
                    if row is not None:
                        self._append_repair_row(suggested, row)
                continue
            for item in self._split_repair_items(line):
                row = self._repair_row_from_item(item, section=section, profile=profile, force_section=False)
                if row is not None:
                    self._append_repair_row(suggested, row)
        for row in self._template_repair_rows(card, profile=profile, section=section):
            self._append_repair_row(suggested, row)
        return suggested

    def _line_declares_repair_section(self, line: str, *, section: str) -> bool:
        lowered = line.casefold()
        markers = _REPAIR_WORK_SECTION_MARKERS if section == "works" else _REPAIR_MATERIAL_SECTION_MARKERS
        return any(lowered.startswith(f"{marker}:") or lowered.startswith(f"{marker} -") for marker in markers)

    def _repair_section_payload(self, line: str) -> str:
        if ":" in line:
            return line.split(":", 1)[1]
        if " - " in line:
            return line.split(" - ", 1)[1]
        return line

    def _split_repair_items(self, text: str) -> list[str]:
        items: list[str] = []
        for raw_item in _REPAIR_ITEM_SPLIT_PATTERN.split(str(text or "")):
            item = self._clean_repair_text_fragment(raw_item, limit=240)
            if item and item not in items:
                items.append(item)
        return items

    def _repair_row_from_item(
        self,
        item: str,
        *,
        section: str,
        profile: VehicleProfile,
        force_section: bool,
    ) -> RepairOrderRow | None:
        cleaned = self._clean_repair_text_fragment(item, limit=240)
        if not cleaned:
            return None
        if section == "works":
            normalized_name = self._normalize_work_name(cleaned)
            if not normalized_name:
                return None
            if not force_section and not self._looks_like_work_item(normalized_name):
                return None
            quantity = self._extract_row_quantity(cleaned, default="1")
            return RepairOrderRow(name=normalized_name, quantity=quantity or "1")
        normalized_name = self._normalize_material_name(cleaned)
        if not normalized_name:
            return None
        if not force_section and not self._looks_like_material_item(cleaned):
            return None
        quantity = self._extract_material_quantity(cleaned, profile=profile)
        return RepairOrderRow(name=normalized_name, quantity=quantity)

    def _extract_row_quantity(self, text: str, *, default: str = "") -> str:
        quantity_match = _REPAIR_QUANTITY_PATTERN.search(text)
        if quantity_match:
            return quantity_match.group(1).replace(",", ".")
        multiplier_match = re.search(r"[xх*]\s*(\d+(?:[.,]\d+)?)\b", text, re.IGNORECASE)
        if multiplier_match:
            return multiplier_match.group(1).replace(",", ".")
        return default

    def _looks_like_work_item(self, text: str) -> bool:
        lowered = text.casefold()
        if any(keyword in lowered for keyword in _REPAIR_WORK_KEYWORDS):
            return True
        return bool(re.search(r"\bто\b", lowered))

    def _looks_like_material_item(self, text: str) -> bool:
        lowered = text.casefold()
        if any(keyword in lowered for keyword in _REPAIR_FINDING_KEYWORDS):
            return False
        if any(keyword in lowered for keyword in ("уровень", "давление", "ошибка", "диагност")):
            return False
        if any(keyword in lowered for keyword in _REPAIR_WORK_KEYWORDS) and not any(
            keyword in lowered for keyword in ("фильтр", "atf", "антифриз", "масло")
        ):
            return False
        if any(keyword in lowered for keyword in _REPAIR_MATERIAL_KEYWORDS):
            return True
        return bool(_REPAIR_QUANTITY_PATTERN.search(text) and len(text.split()) <= 6)

    def _normalize_work_name(self, text: str) -> str:
        cleaned = self._clean_repair_text_fragment(text, limit=240)
        cleaned = re.sub(
            r"^(?:нужно|требуется|необходимо|выполнить|выполнено|выполнили|сделать|сделали|провести|провели|произвести|проверить|проверили|заменить|заменили)\s*[:\-]?\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b\d+(?:[.,]\d+)?\s*(?:шт|штуки|штук|л|литр(?:а|ов)?|l|компл(?:ект)?|up|pcs?)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = cleaned.strip(" ,.;:-")
        if not cleaned:
            return ""
        return cleaned[:1].upper() + cleaned[1:]

    def _normalize_material_name(self, text: str) -> str:
        lowered = text.casefold()
        cleaned = re.sub(
            r"\b\d+(?:[.,]\d+)?\s*(?:шт|штуки|штук|л|литр(?:а|ов)?|l|компл(?:ект)?|pcs?)\b",
            "",
            text,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[xх*]\s*\d+(?:[.,]\d+)?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = self._clean_repair_text_fragment(cleaned, limit=240)
        if not cleaned:
            return ""
        if "atf" in lowered or ("масло" in lowered and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб"))):
            return "ATF"
        if "антифриз" in lowered or "охлажда" in lowered:
            return "Антифриз"
        if "масля" in lowered and "фильтр" in lowered:
            return "Масляный фильтр"
        if "фильтр" in lowered and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб")):
            return "Фильтр АКПП"
        if "масло" in lowered and any(token in lowered for token in ("двиг", "мотор", "5w", "0w", "10w", "15w")):
            return "Моторное масло"
        return cleaned[:1].upper() + cleaned[1:]

    def _extract_material_quantity(self, text: str, *, profile: VehicleProfile) -> str:
        explicit_quantity = self._extract_row_quantity(text, default="")
        if explicit_quantity:
            return explicit_quantity
        lowered = text.casefold()
        if "atf" in lowered or ("масло" in lowered and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб"))):
            return self._format_quantity_value(profile.oil_gearbox_capacity_l)
        if "антифриз" in lowered or "охлажда" in lowered:
            return self._format_quantity_value(profile.coolant_capacity_l)
        if "масло" in lowered and any(token in lowered for token in ("двиг", "мотор", "5w", "0w", "10w", "15w")):
            return self._format_quantity_value(profile.oil_engine_capacity_l)
        if "фильтр" in lowered or "проклад" in lowered:
            return "1"
        return ""

    def _template_repair_rows(self, card: Card, *, profile: VehicleProfile, section: str) -> list[RepairOrderRow]:
        combined_text = self._repair_analysis_text(card)
        lowered = combined_text.casefold()
        rows: list[RepairOrderRow] = []
        has_engine_oil_service = self._is_engine_oil_service(lowered)
        has_transmission_service = self._is_transmission_service(lowered)
        has_coolant_service = self._is_coolant_service(lowered)
        if section == "works":
            if "диагност" in lowered:
                diagnostic_name = "Диагностика DSG/АКПП" if has_transmission_service else "Диагностика"
                self._append_repair_row(rows, RepairOrderRow(name=diagnostic_name, quantity="1"))
            if has_engine_oil_service:
                self._append_repair_row(rows, RepairOrderRow(name="Замена масла двигателя", quantity="1"))
            if has_transmission_service:
                transmission_name = "ТО DSG/АКПП" if any(token in lowered for token in ("dsg", "акпп")) else "Обслуживание трансмиссии"
                self._append_repair_row(rows, RepairOrderRow(name=transmission_name, quantity="1"))
            if has_coolant_service:
                self._append_repair_row(rows, RepairOrderRow(name="Замена охлаждающей жидкости", quantity="1"))
            return rows
        if has_engine_oil_service:
            self._append_repair_row(
                rows,
                RepairOrderRow(
                    name="Моторное масло",
                    quantity=self._extract_specific_quantity(combined_text, "масло", "двиг", "мотор")
                    or self._format_quantity_value(profile.oil_engine_capacity_l),
                ),
            )
            if "фильтр" in lowered or re.search(r"\bто\b", lowered):
                self._append_repair_row(rows, RepairOrderRow(name="Масляный фильтр", quantity="1"))
        if has_transmission_service:
            self._append_repair_row(
                rows,
                RepairOrderRow(
                    name="ATF",
                    quantity=self._extract_specific_quantity(combined_text, "atf", "акпп", "dsg", "вариатор", "cvt", "короб")
                    or self._format_quantity_value(profile.oil_gearbox_capacity_l),
                ),
            )
            if "фильтр" in lowered and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб")):
                self._append_repair_row(rows, RepairOrderRow(name="Фильтр АКПП", quantity="1"))
            if "проклад" in lowered and "поддон" in lowered:
                self._append_repair_row(rows, RepairOrderRow(name="Прокладка поддона", quantity="1"))
        if has_coolant_service:
            self._append_repair_row(
                rows,
                RepairOrderRow(
                    name="Антифриз",
                    quantity=self._extract_specific_quantity(combined_text, "антифриз", "охлажда")
                    or self._format_quantity_value(profile.coolant_capacity_l),
                ),
            )
        return rows

    def _is_engine_oil_service(self, lowered_text: str) -> bool:
        has_oil = "масло" in lowered_text and any(token in lowered_text for token in ("двиг", "мотор", "5w", "0w", "10w", "15w"))
        has_service = any(token in lowered_text for token in ("замен", "обслуж")) or bool(re.search(r"\bто\b", lowered_text))
        return has_oil and has_service and not self._is_transmission_service(lowered_text)

    def _is_transmission_service(self, lowered_text: str) -> bool:
        has_transmission = any(token in lowered_text for token in ("акпп", "dsg", "вариатор", "cvt", "короб", "трансмис"))
        has_service = any(token in lowered_text for token in ("замен", "обслуж", "atf")) or bool(re.search(r"\bто\b", lowered_text))
        return has_transmission and has_service

    def _is_coolant_service(self, lowered_text: str) -> bool:
        return any(token in lowered_text for token in ("антифриз", "охлажда")) and any(
            token in lowered_text for token in ("замен", "долив", "обслуж")
        )

    def _extract_specific_quantity(self, text: str, *keywords: str) -> str:
        for line in self._repair_text_lines(text):
            lowered = line.casefold()
            if not any(keyword in lowered for keyword in keywords):
                continue
            quantity = self._extract_row_quantity(line, default="")
            if quantity:
                return quantity
        return ""

    def _format_quantity_value(self, value: float | int | None) -> str:
        if value in (None, ""):
            return ""
        raw = str(value)
        return raw.rstrip("0").rstrip(".") if "." in raw else raw

    def _append_repair_row(self, rows: list[RepairOrderRow], row: RepairOrderRow) -> None:
        row_key = self._repair_row_key(row.name)
        if not row_key:
            return
        for index, existing in enumerate(rows):
            if self._repair_row_key(existing.name) != row_key:
                continue
            rows[index] = RepairOrderRow(
                name=existing.name or row.name,
                quantity=existing.quantity or row.quantity,
                price=existing.price or row.price,
                total=existing.total or row.total,
            )
            return
        rows.append(row)

    def _repair_row_key(self, name: str) -> str:
        normalized = self._normalize_search_text(name)
        return normalized

    def _apply_history_prices_to_rows(
        self,
        rows: list[RepairOrderRow],
        *,
        section: str,
        cards: list[Card],
        related_cards: list[Card],
    ) -> tuple[list[RepairOrderRow], list[dict[str, str]]]:
        updated_rows: list[RepairOrderRow] = []
        hits: list[dict[str, str]] = []
        for row in rows:
            if row.price:
                updated_rows.append(row)
                continue
            price, source = self._history_price_for_row(
                row.name,
                section=section,
                cards=cards,
                related_cards=related_cards,
            )
            if price:
                updated_rows.append(RepairOrderRow(name=row.name, quantity=row.quantity, price=price, total=""))
                hits.append({"section": section, "name": row.name, "price": price, "source": source})
                continue
            updated_rows.append(row)
        return updated_rows, hits

    def _history_price_for_row(
        self,
        row_name: str,
        *,
        section: str,
        cards: list[Card],
        related_cards: list[Card],
    ) -> tuple[str, str]:
        row_key = self._repair_row_key(row_name)
        if not row_key:
            return "", ""

        def collect_prices(candidates: list[Card]) -> list[str]:
            prices: list[str] = []
            for candidate in candidates:
                rows = candidate.repair_order.works if section == "works" else candidate.repair_order.materials
                for item in rows:
                    if self._repair_row_key(item.name) != row_key:
                        continue
                    price = normalize_text(item.price, default="", limit=40)
                    if price:
                        prices.append(price)
            return prices

        related_prices = collect_prices(related_cards)
        if related_prices and len(set(related_prices)) == 1:
            return related_prices[0], "related_history"

        global_candidates = [candidate for candidate in cards if self._card_has_repair_order(candidate)]
        global_prices = collect_prices(global_candidates)
        if len(global_prices) >= 2 and len(set(global_prices)) == 1:
            return global_prices[0], "board_history"
        return "", ""

    def _merge_repair_order_rows(self, existing: list[RepairOrderRow], suggested: list[RepairOrderRow]) -> list[RepairOrderRow]:
        if not suggested:
            return list(existing)
        merged = [RepairOrderRow.from_dict(row.to_dict()) for row in existing]
        for row in suggested:
            self._append_repair_row(merged, row)
        return normalize_repair_order_rows([row.to_dict() for row in merged])

    def _build_client_description(self, card: Card, order: RepairOrder) -> str:
        parts: list[str] = []
        reason = normalize_text(order.reason or self._build_repair_order_reason(card), default="", limit=320)
        findings = self._extract_repair_findings(card)
        recommendations = self._extract_repair_recommendations(card)
        work_names = [row.name for row in order.works if row.name][:4]
        material_names = [self._format_client_material(row) for row in order.materials if row.name][:5]
        if reason:
            parts.append(f"Клиент обратился с запросом: {reason.rstrip('.')} .".replace(" .", "."))
        if findings:
            parts.append(f"В ходе проверки выявлено: {'; '.join(findings[:2]).rstrip('.')} .".replace(" .", "."))
        if work_names:
            parts.append(f"Выполнены работы: {', '.join(work_names)}.")
        if material_names:
            parts.append(f"Использованы материалы и запчасти: {', '.join(material_names)}.")
        if recommendations:
            parts.append(f"Рекомендовано далее: {'; '.join(recommendations[:2]).rstrip('.')} .".replace(" .", "."))
        if not parts:
            fallback = self._clean_repair_text_fragment(card.description, limit=320)
            if fallback:
                parts.append(fallback)
        return normalize_text(" ".join(parts), default="", limit=1200)

    def _format_client_material(self, row: RepairOrderRow) -> str:
        quantity = normalize_text(row.quantity, default="", limit=40)
        if quantity:
            return f"{row.name} x {quantity}"
        return row.name

    def _build_internal_repair_note(
        self,
        card: Card,
        order: RepairOrder,
        *,
        price_hits: list[dict[str, str]],
    ) -> str:
        parts: list[str] = []
        findings = self._extract_repair_findings(card)
        recommendations = self._extract_repair_recommendations(card)
        if findings:
            parts.append(f"Технические замечания: {'; '.join(findings[:3])}.")
        if price_hits:
            labels = ", ".join(f"{item['name']} = {item['price']}" for item in price_hits[:3])
            parts.append(f"Цены подтянуты из истории: {labels}.")
        if recommendations:
            parts.append(f"Контроль / следующий шаг: {'; '.join(recommendations[:2])}.")
        return normalize_text(" ".join(parts), default="", limit=1200)

    def _validated_repair_order(self, value) -> RepairOrder:
        if value is None:
            return RepairOrder()
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Поле repair_order должно быть объектом.",
                details={"field": "repair_order"},
            )
        return RepairOrder.from_dict(value)

    def _validated_repair_order_patch(self, value) -> dict[str, Any]:
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Поле repair_order должно быть объектом.",
                details={"field": "repair_order"},
            )
        allowed_fields = {
            "number",
            "date",
            "status",
            "opened_at",
            "openedAt",
            "closed_at",
            "closedAt",
            "client",
            "phone",
            "vehicle",
            "license_plate",
            "licensePlate",
            "vin",
            "mileage",
            "odometer",
            "payment_method",
            "paymentMethod",
            "prepayment",
            "advance_payment",
            "advancePayment",
            "payments",
            "payment_history",
            "reason",
            "comment",
            "client_information",
            "clientInformation",
            "note",
            "master_comment",
            "masterComment",
            "internal_comment",
            "internalComment",
            "tags",
            "works",
            "materials",
        }
        patch = {key: value[key] for key in value if key in allowed_fields}
        if not patch:
            self._fail(
                "validation_error",
                "Для обновления заказ-наряда нужно передать хотя бы одно поле.",
                details={"fields": sorted(allowed_fields)},
            )
        if "works" in patch:
            patch["works"] = self._validated_repair_order_rows(patch["works"], field_name="repair_order.works")
        if "materials" in patch:
            patch["materials"] = self._validated_repair_order_rows(
                patch["materials"],
                field_name="repair_order.materials",
            )
        if "tags" in patch:
            patch["tags"] = self._validated_repair_order_tags(patch["tags"], field_name="repair_order.tags")
        if "payments" in patch:
            patch["payments"] = self._validated_repair_order_payments(
                patch["payments"],
                field_name="repair_order.payments",
            )
        if "payment_history" in patch:
            patch["payment_history"] = self._validated_repair_order_payments(
                patch["payment_history"],
                field_name="repair_order.payment_history",
            )
        return patch

    def _validated_repair_order_rows(self, value, *, field_name: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            self._fail(
                "validation_error",
                f"Поле {field_name} должно быть массивом строк заказ-наряда.",
                details={"field": field_name},
            )
        return [row.to_dict() for row in normalize_repair_order_rows(value)]

    def _validated_repair_order_tags(self, value, *, field_name: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            self._fail(
                "validation_error",
                f"Поле {field_name} должно быть массивом меток заказ-наряда.",
                details={"field": field_name},
            )
        return [tag.to_dict() for tag in normalize_repair_order_tags(value)]

    def _validated_repair_order_payments(self, value, *, field_name: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            self._fail(
                "validation_error",
                f"Поле {field_name} должно быть массивом оплат заказ-наряда.",
                details={"field": field_name},
            )
        return [payment.to_storage_dict() for payment in normalize_repair_order_payments(value)]

    def _merged_repair_order_storage(
        self,
        current: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        alias_map = {
            "openedAt": "opened_at",
            "closedAt": "closed_at",
            "licensePlate": "license_plate",
            "odometer": "mileage",
            "client_information": "comment",
            "clientInformation": "comment",
            "payment_history": "payments",
            "master_comment": "note",
            "masterComment": "note",
            "internal_comment": "note",
            "internalComment": "note",
        }
        for key, value in patch.items():
            merged[alias_map.get(key, key)] = value
        return merged

    def _prepared_repair_order(
        self,
        order: RepairOrder,
        cards: list[Card],
        *,
        card: Card | None = None,
        exclude_card_id: str | None = None,
    ) -> RepairOrder:
        prepared = RepairOrder.from_dict(order.to_storage_dict())
        if not prepared.number:
            prepared.number = self._next_repair_order_number(cards, exclude_card_id=exclude_card_id)
        if not prepared.date:
            prepared.date = self._repair_order_card_datetime(card.created_at if card is not None else "") or self._repair_order_now()
        if not prepared.opened_at:
            prepared.opened_at = (
                self._repair_order_card_datetime(card.created_at if card is not None else "")
                or prepared.date
                or self._repair_order_now()
            )
        prepared.status = normalize_repair_order_status(prepared.status, default=REPAIR_ORDER_STATUS_OPEN)
        if prepared.status == REPAIR_ORDER_STATUS_CLOSED and not prepared.closed_at:
            prepared.closed_at = self._repair_order_now()
        if prepared.status != REPAIR_ORDER_STATUS_CLOSED:
            prepared.closed_at = ""
        return prepared

    def _next_repair_order_number(self, cards: list[Card], *, exclude_card_id: str | None = None) -> str:
        current_max = 0
        for item in cards:
            if exclude_card_id is not None and item.id == exclude_card_id:
                continue
            raw_number = str(item.repair_order.number or "").strip()
            if not raw_number.isdigit():
                continue
            current_max = max(current_max, int(raw_number))
        return str(current_max + 1)

    def _repair_order_now(self) -> str:
        return datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")

    def _card_has_repair_order(self, card: Card) -> bool:
        return not card.repair_order.is_empty()

    def _card_has_open_repair_order(self, card: Card) -> bool:
        return self._card_has_repair_order(card) and card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED

    def _ensure_card_can_be_archived(self, card: Card) -> None:
        if not self._card_has_open_repair_order(card):
            return
        repair_order_number = str(card.repair_order.number or "").strip()
        number_suffix = f" №{repair_order_number}" if repair_order_number else ""
        self._fail(
            "repair_order_open_archive_blocked",
            f"Нельзя отправить карточку в архив: по ней открыт заказ-наряд{number_suffix}. Сначала закройте заказ-наряд или снимите его с карточки.",
            status_code=409,
            details={"card_id": card.id, "repair_order_number": repair_order_number},
        )

    def _validated_repair_order_status(
        self,
        value,
        *,
        default: str,
        allow_all: bool = False,
    ) -> str:
        raw = normalize_text(value, default="", limit=16).strip().lower()
        if allow_all and raw == "all":
            return "all"
        return normalize_repair_order_status(raw, default=default)

    def _validated_repair_order_sort_by(self, value) -> str:
        normalized = normalize_text(value, default="opened_at", limit=24).strip().lower()
        if normalized not in REPAIR_ORDER_SORT_FIELDS:
            return "opened_at"
        return normalized

    def _validated_repair_order_sort_direction(self, value) -> str:
        normalized = normalize_text(value, default="desc", limit=8).strip().lower()
        if normalized not in REPAIR_ORDER_SORT_DIRECTIONS:
            return "desc"
        return normalized

    def _repair_order_status_label(self, status: str) -> str:
        return "Закрыт" if status == REPAIR_ORDER_STATUS_CLOSED else "Открыт"

    def _repair_order_card_datetime(self, value: str | None) -> str:
        parsed = parse_datetime(value)
        if parsed is None:
            return ""
        return parsed.astimezone().strftime("%d.%m.%Y %H:%M")

    def _repair_order_sort_key(self, card: Card) -> tuple[str, int]:
        raw_number = str(card.repair_order.number or "").strip()
        numeric_number = int(raw_number) if raw_number.isdigit() else 0
        return str(card.created_at or ""), numeric_number

    def _repair_order_sortable_datetime(self, value: str | None) -> str:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.astimezone().strftime("%Y%m%d%H%M%S")
        raw_value = normalize_text(value, default="", limit=32)
        if not raw_value:
            return ""
        try:
            return datetime.strptime(raw_value, "%d.%m.%Y %H:%M").strftime("%Y%m%d%H%M%S")
        except ValueError:
            return raw_value

    def _repair_order_opened_sort_value(self, card: Card) -> str:
        order = card.repair_order
        return self._repair_order_sortable_datetime(order.opened_at or card.created_at or order.date)

    def _repair_order_closed_sort_value(self, card: Card) -> str:
        return self._repair_order_sortable_datetime(card.repair_order.closed_at)

    def _repair_order_number_sort_value(self, card: Card) -> int:
        raw_number = str(card.repair_order.number or "").strip()
        return int(raw_number) if raw_number.isdigit() else 0

    def _repair_order_list_sort_key(self, card: Card, *, sort_by: str) -> tuple[object, ...]:
        opened_value = self._repair_order_opened_sort_value(card)
        closed_value = self._repair_order_closed_sort_value(card)
        number_value = self._repair_order_number_sort_value(card)
        if sort_by == "number":
            return (number_value, opened_value, str(card.id or ""))
        if sort_by == "closed_at":
            return (closed_value, opened_value, number_value, str(card.id or ""))
        return (opened_value, number_value, str(card.id or ""))

    def _synchronize_repair_order_numbers(self, cards: list[Card]) -> bool:
        ordered_cards = sorted(
            (card for card in cards if self._card_has_repair_order(card)),
            key=lambda item: (str(item.created_at or ""), str(item.id or "")),
        )
        changed = False
        for index, card in enumerate(ordered_cards, start=1):
            expected = str(index)
            if card.repair_order.number == expected:
                continue
            card.repair_order = RepairOrder.from_dict(
                {
                    **card.repair_order.to_storage_dict(),
                    "number": expected,
                }
            )
            changed = True
        return changed

    def _repair_order_list_summary(self, card: Card) -> str:
        for value in (card.description, card.title, card.vehicle_profile.oem_notes, card.heading()):
            summary = normalize_text(value, default="", limit=220)
            if summary:
                return summary
        return ""

    def _repair_order_search_values(self, card: Card) -> list[str]:
        order = card.repair_order
        payment_text = " ".join(
            " ".join(
                filter(
                    None,
                    (
                        payment.amount,
                        payment.paid_at,
                        payment.note,
                        payment.payment_method,
                    ),
                )
            )
            for payment in order.payments
        )
        return [
            order.number,
            order.date,
            order.opened_at or self._repair_order_card_datetime(card.created_at),
            order.closed_at,
            self._repair_order_status_label(order.status),
            order.status,
            order.client,
            order.phone,
            order.vehicle or card.vehicle,
            order.license_plate,
            order.vin,
            order.mileage,
            order.payment_method,
            order.prepayment,
            payment_text,
            order.reason,
            order.comment,
            order.note,
            card.heading(),
            self._repair_order_list_summary(card),
            " ".join(tag.label for tag in order.tags),
        ]

    def _filter_repair_order_cards(self, cards: list[Card], *, query: str) -> list[Card]:
        normalized_query = self._normalize_search_text(query)
        if not normalized_query:
            return list(cards)
        tokens = [token for token in normalized_query.split() if token]
        if not tokens:
            return list(cards)
        filtered: list[Card] = []
        for card in cards:
            haystack = " ".join(
                normalized
                for normalized in (
                    self._normalize_search_text(value)
                    for value in self._repair_order_search_values(card)
                )
                if normalized
            )
            if not haystack:
                continue
            if all(token in haystack for token in tokens):
                filtered.append(card)
        return filtered

    def _serialize_repair_order_list_item(self, card: Card) -> dict[str, object]:
        path = self._ensure_repair_order_text_file(card)
        order = card.repair_order
        paid_total = order.prepayment_amount()
        return {
            "card_id": card.id,
            "number": order.number,
            "date": order.date,
            "created_at": card.created_at,
            "opened_at": order.opened_at or self._repair_order_card_datetime(card.created_at),
            "closed_at": order.closed_at,
            "status": order.status,
            "status_label": self._repair_order_status_label(order.status),
            "client": order.client,
            "phone": order.phone,
            "vehicle": order.vehicle or card.vehicle,
            "license_plate": order.license_plate,
            "vin": order.vin,
            "mileage": order.mileage,
            "payment_method": order.payment_method,
            "payment_method_label": order.to_dict()["payment_method_label"],
            "prepayment": order.prepayment_amount() if order.payments else order.prepayment,
            "prepayment_display": order.prepayment_amount(),
            "paid_total": paid_total,
            "paid_total_display": paid_total,
            "is_paid": order.is_paid(),
            "payment_status": order.payment_status(),
            "payment_status_label": order.payment_status_label(),
            "payments": [payment.to_dict() for payment in order.payments],
            "reason": order.reason,
            "heading": card.heading(),
            "summary": self._repair_order_list_summary(card),
            "tags": [tag.to_dict() for tag in order.tags],
            "subtotal_total": order.subtotal_amount(),
            "taxes_total": order.taxes_amount(),
            "works_total": order.works_total_amount(),
            "grand_total": order.grand_total_amount(),
            "due_total": order.due_total_amount(),
            "updated_at": card.updated_at,
            "file_name": path.name,
            "file_path": str(path),
        }

    def _ensure_repair_order_text_file(self, card: Card, *, force: bool = False) -> Path:
        path = self._repair_order_text_path(card)
        if not force and path.exists():
            return path
        self._repair_orders_dir.mkdir(parents=True, exist_ok=True)
        content = self._render_repair_order_text(card)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
        self._cleanup_repair_order_text_files(card, keep_path=path)
        return path

    def _repair_order_text_path(self, card: Card) -> Path:
        return self._repair_orders_dir / self._repair_order_file_name(card)

    def _repair_order_file_name(self, card: Card) -> str:
        number = normalize_file_name(card.repair_order.number or f"card-{card.id}") or f"card-{card.id}"
        title = normalize_file_name(card.heading()) or "repair-order"
        short_id = short_entity_id(card.id, prefix="C")
        return f"{number}__{title}__{short_id}.txt"

    def _repair_order_text_payload(self, card: Card, *, force: bool = False) -> dict[str, str]:
        path = self._ensure_repair_order_text_file(card, force=force)
        return {
            "file_name": path.name,
            "file_path": str(path),
            "text": path.read_text(encoding="utf-8"),
        }

    def _cleanup_repair_order_text_files(self, card: Card, *, keep_path: Path) -> None:
        short_id = short_entity_id(card.id, prefix="C")
        for candidate in self._repair_orders_dir.glob(f"*__{short_id}.txt"):
            if candidate == keep_path:
                continue
            try:
                candidate.unlink()
            except OSError:
                continue

    def _cleanup_repair_orders_directory(self, cards: list[Card]) -> None:
        ranked_cards = [
            card
            for card in sorted(cards, key=self._repair_order_sort_key, reverse=True)
            if self._card_has_repair_order(card)
        ]
        keep_short_ids = {
            short_entity_id(card.id, prefix="C")
            for card in ranked_cards[:REPAIR_ORDER_FILE_RETENTION_LIMIT]
        }
        for candidate in self._repair_orders_dir.glob("*.txt"):
            if not candidate.is_file():
                continue
            if not keep_short_ids:
                try:
                    candidate.unlink()
                except OSError:
                    continue
                continue
            name = candidate.name
            if "__" not in name:
                continue
            short_id = name.rsplit("__", 1)[-1].removesuffix(".txt")
            if short_id in keep_short_ids:
                continue
            try:
                candidate.unlink()
            except OSError:
                continue

    def _cleanup_attachment_directories(self, cards: list[Card]) -> None:
        keep_card_ids = {card.id for card in cards}
        for candidate in self._attachments_dir.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.name in keep_card_ids:
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                continue

    def _render_repair_order_text(self, card: Card) -> str:
        order = card.repair_order
        lines = [
            "ЗАКАЗ-НАРЯД",
            "",
            f"Номер: {order.number or '-'}",
            f"Дата: {order.date or '-'}",
            f"Карточка: {card.heading()}",
            f"Card ID: {card.id}",
            "",
            f"Status: {self._repair_order_status_label(order.status)}",
            f"Opened at: {order.opened_at or self._repair_order_card_datetime(card.created_at) or '-'}",
            f"Closed at: {order.closed_at or '-'}",
            f"Форма оплаты: {order.to_dict()['payment_method_label']}",
            f"Предоплата: {order.prepayment_amount()}",
            "",
            "Оплаты:",
        ]
        if order.payments:
            lines.extend(self._render_repair_order_payments(order.payments))
        else:
            lines.append("-")
        lines.extend(
            [
                "",
                f"Клиент: {order.client or '-'}",
                f"Телефон: {order.phone or '-'}",
                f"Автомобиль: {order.vehicle or '-'}",
                f"Госномер: {order.license_plate or '-'}",
                "",
                "Информация для клиента:",
                order.comment or "-",
                "",
                "Master note:",
                order.note or "-",
                "",
                "Работы:",
            ]
        )
        lines.extend(self._render_repair_order_rows(order.works))
        lines.append(f"Итого работы: {order.works_total_amount()}")
        lines.extend(["", "Материалы:"])
        lines.extend(self._render_repair_order_rows(order.materials))
        lines.append(f"Итого материалы: {order.materials_total_amount()}")
        lines.extend(
            [
                "",
                f"Стоимость заказ-наряда: {order.subtotal_amount()}",
                f"Налоги и сборы: {order.taxes_amount()}",
                f"Итого по заказ-наряду: {order.grand_total_amount()}",
                f"К доплате: {order.due_total_amount()}",
                "",
                "JSON:",
                json.dumps(order.to_storage_dict(), ensure_ascii=False, indent=2),
                "",
                f"Обновлено: {card.updated_at or card.created_at or '-'}",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _render_repair_order_rows(self, rows: list[RepairOrderRow]) -> list[str]:
        if not rows:
            return ["-"]
        lines: list[str] = []
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. {row.name or '-'} | кол-во: {row.quantity or '-'} | цена: {row.price or '-'} | сумма: {row.total or '-'}"
            )
        return lines

    def _render_repair_order_payments(self, payments: list[RepairOrderPayment]) -> list[str]:
        lines: list[str] = []
        for index, payment in enumerate(payments, start=1):
            parts = [
                payment.paid_at or "-",
                repair_order_payment_method_label(payment.payment_method),
                payment.amount or "0",
            ]
            if payment.actor_name:
                parts.append(f"кто: {payment.actor_name}")
            if payment.cashbox_name:
                parts.append(f"касса: {payment.cashbox_name}")
            note = normalize_text(payment.note, default="", limit=240)
            if note:
                parts.append(note)
            lines.append(f"{index}. {' | '.join(parts)}")
        return lines or ["-"]

    def _extract_license_plate(self, card: Card, *, fallback: str = "") -> str:
        haystack = "\n".join(
            part
            for part in (
                card.vehicle,
                card.title,
                card.description,
                card.vehicle_profile.oem_notes,
            )
            if part
        )
        match = _LICENSE_PLATE_PATTERN.search(haystack.upper())
        if match:
            return match.group(0)
        return fallback

    def _extract_vin(self, card: Card, *, fallback: str = "") -> str:
        if card.vehicle_profile.vin:
            return card.vehicle_profile.vin
        haystack = "\n".join(
            part
            for part in (
                card.vehicle,
                card.title,
                card.description,
                card.vehicle_profile.oem_notes,
            )
            if part
        ).upper()
        match = _VIN_PATTERN.search(haystack)
        if match:
            return match.group(0)
        return fallback

    def _extract_mileage(self, card: Card, *, fallback: str = "") -> str:
        if card.vehicle_profile.mileage:
            return str(card.vehicle_profile.mileage)
        haystack = "\n".join(
            part
            for part in (
                card.title,
                card.description,
                card.vehicle_profile.oem_notes,
            )
            if part
        )
        match = _MILEAGE_PATTERN.search(haystack)
        if not match:
            return fallback
        return " ".join(match.group(1).split())

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

    def _validated_vehicle_profile_create(self, value) -> VehicleProfile:
        profile, _, _ = self._vehicle_profiles.normalize_profile_payload(
            value,
            assume_manual_for_explicit_fields=False,
        )
        return self._vehicle_profiles.finalize_profile_metadata(profile)

    def _merge_vehicle_profile_patch(self, existing: VehicleProfile, value) -> tuple[VehicleProfile, list[str]]:
        incoming, present_primary, present_meta = self._vehicle_profiles.normalize_profile_payload(
            value,
            assume_manual_for_explicit_fields=False,
        )
        merged, changed_fields = self._vehicle_profiles.merge_profile_patch(
            existing,
            incoming,
            present_primary=present_primary,
            present_meta=present_meta,
        )
        return self._vehicle_profiles.finalize_profile_metadata(merged), changed_fields

    def _resolved_card_vehicle_label(self, vehicle: str, profile: VehicleProfile) -> str:
        if vehicle:
            return vehicle
        profile_display = profile.display_name()
        if not profile_display:
            return ""
        return self._validated_vehicle(profile_display)

    def _sync_vehicle_label_with_profile(
        self,
        current_vehicle: str,
        previous_profile: VehicleProfile,
        next_profile: VehicleProfile,
    ) -> str:
        previous_display = previous_profile.display_name()
        next_display = next_profile.display_name()
        if not current_vehicle.strip():
            return self._resolved_card_vehicle_label("", next_profile)
        if previous_display and current_vehicle.strip() == self._validated_vehicle(previous_display):
            return self._resolved_card_vehicle_label("", next_profile)
        return self._validated_vehicle(current_vehicle)

    def _attachment_path(self, card_id: str, stored_name: str) -> Path:
        return self._attachments_dir / card_id / stored_name

    def _write_attachment_file(self, card_id: str, stored_name: str, content: bytes) -> Path:
        attachment_path = self._attachment_path(card_id, stored_name)
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        attachment_path.write_bytes(content)
        return attachment_path

    def _delete_attachment_file(self, card_id: str, stored_name: str) -> None:
        attachment_path = self._attachment_path(card_id, stored_name)
        if attachment_path.exists():
            attachment_path.unlink()
        self._cleanup_empty_attachment_directory(card_id)

    def _require_attachment_file(self, card_id: str, attachment: Attachment) -> Path:
        attachment_path = self._attachment_path(card_id, attachment.stored_name)
        for _ in range(20):
            if attachment_path.exists():
                return attachment_path
            time.sleep(0.05)
        if not attachment_path.exists():
            self._fail(
                "not_found",
                "Файл не найден на диске.",
                status_code=404,
                details={"attachment_id": attachment.id},
            )
        return attachment_path

    def _cleanup_empty_attachment_directory(self, card_id: str) -> None:
        attachment_dir = self._attachments_dir / card_id
        if not attachment_dir.exists() or not attachment_dir.is_dir():
            return
        try:
            next(attachment_dir.iterdir())
            return
        except StopIteration:
            pass
        try:
            attachment_dir.rmdir()
        except OSError:
            return

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
                "Поле deadline для стикера должно быть JSON-объектом с числами days, hours, minutes, seconds или total_seconds.",
                details={"field": "deadline"},
            )
        total_seconds_direct = self._validated_deadline_part(value, "total_seconds", maximum=31_536_000)
        days = self._validated_deadline_part(value, "days", maximum=365)
        hours = self._validated_deadline_part(value, "hours", maximum=23)
        minutes = self._validated_deadline_part(value, "minutes", maximum=59)
        seconds = self._validated_deadline_part(value, "seconds", maximum=59)
        total_seconds = total_seconds_direct + days * 24 * 3600 + hours * 3600 + minutes * 60 + seconds
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

    def _validated_column_label(
        self,
        value,
        columns: list[Column],
        *,
        exclude_column_id: str | None = None,
    ) -> str:
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
        existing_labels = {
            column.label.casefold()
            for column in columns
            if exclude_column_id is None or column.id != exclude_column_id
        }
        if label.casefold() in existing_labels:
            self._fail(
                "validation_error",
                "Столбец с таким названием уже существует.",
                details={"field": "label"},
            )
        return label

    def _validated_tags(self, value) -> list[CardTag]:
        if value is None:
            return []
        if not isinstance(value, list):
            self._fail(
                "validation_error",
                "Поле tags должно быть массивом строк.",
                details={"field": "tags"},
            )
        unique_labels: set[str] = set()
        for item in value:
            tag = CardTag.from_value(item)
            if tag is None:
                continue
            unique_labels.add(tag.label)
            if len(unique_labels) > TAG_LIMIT:
                self._fail(
                    "validation_error",
                    f"Количество меток не должно превышать {TAG_LIMIT}.",
                    details={"field": "tags"},
                )
        tags = normalize_tags(value)
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

    def _validated_cashbox_name(
        self,
        value,
        cashboxes: list[CashBox],
        *,
        exclude_cashbox_id: str | None = None,
    ) -> str:
        name = normalize_text(value, default="", limit=80)
        if not name:
            self._fail("validation_error", "Нужно передать название кассы.", details={"field": "name"})
        existing_names = {
            item.name.casefold()
            for item in cashboxes
            if exclude_cashbox_id is None or item.id != exclude_cashbox_id
        }
        if name.casefold() in existing_names:
            self._fail("validation_error", "Касса с таким названием уже существует.", details={"field": "name"})
        return name

    def _validated_cash_transaction_note(self, value) -> str:
        return normalize_text(value, default="", limit=240)

    def _validated_cash_amount_minor(self, payload: dict) -> int:
        raw_value = payload.get("amount_minor")
        if raw_value in (None, ""):
            raw_value = payload.get("amount")
        if raw_value in (None, ""):
            self._fail("validation_error", "Нужно передать сумму операции.", details={"field": "amount"})
        amount_minor = normalize_money_minor(raw_value, minimum=1)
        if amount_minor < 1:
            self._fail("validation_error", "Сумма операции должна быть больше нуля.", details={"field": "amount"})
        return amount_minor

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
