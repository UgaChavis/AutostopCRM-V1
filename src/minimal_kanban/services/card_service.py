from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from logging import Logger
from pathlib import Path, PurePath
from typing import Any

from ..agent.knowledge import build_ai_chat_knowledge_packet
from ..agent.openai_client import AgentModelError, OpenAIJsonAgentClient
from ..config import get_attachments_dir
from ..demo_seed import build_demo_board
from ..models import (
    CARD_DESCRIPTION_LIMIT,
    CARD_MANUAL_TAG_LIMIT,
    CARD_TITLE_LIMIT,
    CARD_VEHICLE_LIMIT,
    COLUMN_LABEL_LIMIT,
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
    ClientProfile,
    ClientVehicle,
    Column,
    StickyNote,
    format_money_minor,
    normalize_actor_name,
    normalize_bool,
    normalize_cash_direction,
    normalize_file_name,
    normalize_money_minor,
    normalize_source,
    normalize_tag_label,
    normalize_tags,
    normalize_text,
    parse_datetime,
    short_entity_id,
    utc_now,
    utc_now_iso,
)
from ..printing.service import PrintModuleError, PrintModuleService
from ..repair_order import (
    REPAIR_ORDER_PAYMENT_METHOD_CARD,
    REPAIR_ORDER_PAYMENT_METHOD_CASHLESS,
    REPAIR_ORDER_STATUS_CLOSED,
    REPAIR_ORDER_STATUS_OPEN,
    REPAIR_ORDER_STATUS_READY,
    RepairOrder,
    RepairOrderPayment,
    RepairOrderRow,
    normalize_repair_order_payment_method,
    normalize_repair_order_payments,
    normalize_repair_order_rows,
    normalize_repair_order_status,
    normalize_repair_order_tags,
    repair_order_payment_method_from_cashbox_name,
    repair_order_payment_method_from_payments,
    repair_order_payment_method_label,
)
from ..storage.json_store import JsonStore, default_columns
from ..vehicle_profile import (
    VEHICLE_COMPACT_FIELDS,
    VehicleProfile,
    normalize_license_plate,
)
from .column_service import ColumnService
from .ready_column import READY_CARD_TAG_COLOR, READY_CARD_TAG_LABEL, ensure_ready_column
from .snapshot_service import SnapshotService
from .vehicle_profile_service import VehicleProfileService

_CARD_AI_LOG_LIMIT = 24
_CARD_AI_LEVELS = {"INFO", "RUN", "WAIT", "DONE", "WARN"}
_CARD_AI_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_CARD_AI_DTC_PATTERN = re.compile(r"\b[PBCU][0-9]{4}\b", re.IGNORECASE)
_READY_CARD_TAG_NORMALIZED = normalize_tag_label(READY_CARD_TAG_LABEL)


_SEARCH_SEPARATOR_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
_SEARCH_LATIN_TO_CYRILLIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("shch", "щ"),
    ("sch", "щ"),
    ("yo", "ё"),
    ("yu", "ю"),
    ("ya", "я"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("ts", "ц"),
    ("ae", "ае"),
    ("ie", "ие"),
    ("ai", "ай"),
    ("oi", "ой"),
    ("ei", "ей"),
    ("ui", "уй"),
    ("uy", "уй"),
    ("iu", "ию"),
    ("ia", "ия"),
)
_SEARCH_LATIN_TO_CYRILLIC_SINGLE = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "й",
    "z": "з",
}
_SEARCH_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ы": "y",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ь": "",
    "ъ": "",
}
_LICENSE_PLATE_PATTERN = re.compile(r"\b[А-ЯA-Z]\d{3}[А-ЯA-Z]{2}\d{2,3}\b", re.IGNORECASE)
_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(
    r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}"
)
_CUSTOMER_NAME_PATTERN = re.compile(
    r"(?:клиент|владелец|контакт(?:ное лицо)?)\s*[:\-]?\s*([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z.\-]+(?:\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z.\-]+){0,2})",
    re.IGNORECASE,
)
_MILEAGE_PATTERN = re.compile(
    r"(?:пробег|mileage|одометр)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE
)
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
EMPLOYEES_SETTING_KEY = "employees"
EMPLOYEES_MAX_COUNT = 15
PAYROLL_MODE_SALARY_ONLY = "salary_only"
PAYROLL_MODE_PERCENT_ONLY = "percent_only"
PAYROLL_MODE_SALARY_PLUS_PERCENT = "salary_plus_percent"
PAYROLL_ALLOWED_MODES = {
    PAYROLL_MODE_SALARY_ONLY,
    PAYROLL_MODE_PERCENT_ONLY,
    PAYROLL_MODE_SALARY_PLUS_PERCENT,
}
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
_ALLOWED_ATTACHMENT_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".txt",
    ".pdf",
)
_ALLOWED_ATTACHMENT_TYPES_LABEL = "PNG, JPG, JPEG, WEBP, GIF, DOC, DOCX, XLS, XLSX, TXT, PDF"
_ATTACHMENT_GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream"})
_ATTACHMENT_DANGEROUS_INTERMEDIATE_EXTENSIONS = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".exe",
        ".js",
        ".jse",
        ".msi",
        ".ps1",
        ".scr",
        ".sh",
        ".vbs",
    }
)
_ATTACHMENT_TYPE_SPECS: dict[str, dict[str, Any]] = {
    "png": {
        "extensions": {".png"},
        "canonical_extension": ".png",
        "canonical_mime": "image/png",
        "mime_types": {"image/png"},
    },
    "jpeg": {
        "extensions": {".jpg", ".jpeg"},
        "canonical_extension": ".jpg",
        "canonical_mime": "image/jpeg",
        "mime_types": {"image/jpeg", "image/jpg", "image/pjpeg"},
    },
    "webp": {
        "extensions": {".webp"},
        "canonical_extension": ".webp",
        "canonical_mime": "image/webp",
        "mime_types": {"image/webp"},
    },
    "gif": {
        "extensions": {".gif"},
        "canonical_extension": ".gif",
        "canonical_mime": "image/gif",
        "mime_types": {"image/gif"},
    },
    "doc": {
        "extensions": {".doc"},
        "canonical_extension": ".doc",
        "canonical_mime": "application/msword",
        "mime_types": {
            "application/doc",
            "application/msword",
            "application/vnd.ms-word",
            "application/x-ole-storage",
        },
    },
    "docx": {
        "extensions": {".docx"},
        "canonical_extension": ".docx",
        "canonical_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "mime_types": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        },
    },
    "xls": {
        "extensions": {".xls"},
        "canonical_extension": ".xls",
        "canonical_mime": "application/vnd.ms-excel",
        "mime_types": {
            "application/msexcel",
            "application/vnd.ms-excel",
            "application/x-msexcel",
            "application/x-ole-storage",
        },
    },
    "xlsx": {
        "extensions": {".xlsx"},
        "canonical_extension": ".xlsx",
        "canonical_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "mime_types": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        },
    },
    "txt": {
        "extensions": {".txt"},
        "canonical_extension": ".txt",
        "canonical_mime": "text/plain",
        "mime_types": {"text/plain"},
    },
    "pdf": {
        "extensions": {".pdf"},
        "canonical_extension": ".pdf",
        "canonical_mime": "application/pdf",
        "mime_types": {"application/pdf", "application/x-pdf"},
    },
}
_ATTACHMENT_EXTENSION_TO_TYPE = {
    extension: type_name
    for type_name, spec in _ATTACHMENT_TYPE_SPECS.items()
    for extension in spec["extensions"]
}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ATTACHMENT_READ_DEFAULT_CHARS = 12_000
_ATTACHMENT_READ_MAX_CHARS = 50_000
_ATTACHMENT_BASE64_DEFAULT_BYTES = 1_048_576
_ATTACHMENT_BASE64_MAX_BYTES = 4_194_304
_ATTACHMENT_XML_READ_MAX_BYTES = 5_000_000


class ServiceError(Exception):
    def __init__(
        self, code: str, message: str, *, status_code: int = 400, details: dict | None = None
    ) -> None:
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
        self._agent_control: Any | None = None
        self._client_search_index_signature: tuple[Any, ...] | None = None
        self._client_search_index: dict[str, dict[str, Any]] = {}
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
            audit_identity=lambda payload, default_source: self._audit_identity(
                payload, default_source=default_source
            ),
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

    def attach_agent_control(self, agent_control: Any | None) -> None:
        self._agent_control = agent_control

    def agent_status(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._agent_control.agent_status(payload)
        return {
            "agent": {
                "name": "AUTOSTOP SERVER AGENT",
                "enabled": False,
                "available": False,
                "ready": False,
                "availability_reason": "disabled",
                "configured": False,
                "model": "",
                "board_api_url": "",
            },
            "ai_remodel": {},
            "board_control": {},
            "worker": {
                "embedded": False,
                "running": False,
                "heartbeat_fresh": False,
            },
            "scheduler": {
                "last_run_at": "",
                "last_success_at": "",
                "last_error": "",
            },
            "status": {
                "running": False,
                "current_task_id": None,
                "current_run_id": None,
                "last_heartbeat": "",
                "last_run_started_at": "",
                "last_run_finished_at": "",
                "last_error": "",
                "last_scheduler_run_at": "",
                "last_scheduler_success_at": "",
                "last_scheduler_error": "",
                "board_control": {},
            },
            "queue": {
                "pending_total": 0,
                "running_total": 0,
            },
            "scheduled": {
                "total": 0,
                "active_total": 0,
                "paused_total": 0,
            },
            "recent_runs": [],
        }

    def agent_tasks(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._agent_control.agent_tasks(payload)
        limit = self._normalize_limit(
            payload.get("limit") if isinstance(payload, dict) else None,
            default=50,
            minimum=1,
            maximum=200,
        )
        return {"tasks": [], "meta": {"limit": limit, "statuses": []}}

    def agent_actions(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._agent_control.agent_actions(payload)
        limit = self._normalize_limit(
            payload.get("limit") if isinstance(payload, dict) else None,
            default=100,
            minimum=1,
            maximum=500,
        )
        return {"actions": [], "meta": {"limit": limit, "run_id": None, "task_id": None}}

    def agent_scheduled_tasks(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._agent_control.agent_scheduled_tasks(payload)
        return {"tasks": [], "meta": {"total": 0}}

    def save_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.save_agent_scheduled_task(payload)

    def delete_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.delete_agent_scheduled_task(payload)

    def pause_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.pause_agent_scheduled_task(payload)

    def resume_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.resume_agent_scheduled_task(payload)

    def run_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.run_agent_scheduled_task(payload)

    def agent_enqueue_task(self, payload: dict | None = None) -> dict:
        if self._agent_control is None:
            self._fail("agent_runtime_unavailable", "AI агент не подключён.", status_code=503)
        return self._agent_control.agent_enqueue_task(payload)

    def create_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            clients = bundle["clients"]
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
            mark_unread = self._validated_optional_bool(
                payload, "mark_unread", default=source == "mcp"
            )
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
            client_id = normalize_text(payload.get("client_id"), default="", limit=128)
            if client_id:
                client = self._find_client(clients, client_id)
                card.client_id = client.id
                client_vehicle_id = normalize_text(
                    payload.get("client_vehicle_id") or payload.get("vehicle_id"),
                    default="",
                    limit=128,
                )
                create_vehicle_from_card = self._validated_optional_bool(
                    payload, "create_vehicle_from_card", default=False
                )
                sync_vehicle_fields = self._validated_optional_bool(
                    payload, "sync_vehicle_fields", default=True
                )
                if create_vehicle_from_card:
                    vehicle_record = self._client_vehicle_from_card(card)
                    client.vehicles.append(vehicle_record)
                    client.vehicles = self._dedupe_client_vehicles(client.vehicles)
                    client.updated_at = utc_now_iso()
                    card.client_vehicle_id = vehicle_record.id
                elif client_vehicle_id:
                    vehicle_record = self._find_client_vehicle(client, client_vehicle_id)
                    card.client_vehicle_id = vehicle_record.id
                    if sync_vehicle_fields:
                        self._sync_card_vehicle_fields(card, vehicle_record, overwrite=True)
                self._sync_card_client_fields(card, client, overwrite=False)
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
            self._save_bundle(bundle, columns=columns, cards=cards, clients=clients, events=events)
            self._logger.info(
                "create_card id=%s title=%s column=%s actor=%s source=%s",
                card.id,
                card.title,
                card.column,
                actor_name,
                source,
            )
            self._notify_agent_card_created(card)
            return {"card": self._serialize_card(card, events, column_labels=column_labels)}

    def set_card_ai_autofill(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._set_card_ai_autofill_with_agent_control(payload)
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="ui")
            had_legacy_state = any(
                (
                    bool(card.ai_autofill_active),
                    bool(card.ai_autofill_until),
                    bool(card.ai_next_run_at),
                    bool(card.ai_autofill_prompt),
                    bool(card.last_card_fingerprint),
                    int(card.ai_run_count or 0) > 0,
                )
            )
            if had_legacy_state:
                card.ai_autofill_active = False
                card.ai_autofill_until = ""
                card.ai_next_run_at = ""
                card.ai_autofill_prompt = ""
                card.last_card_fingerprint = ""
                card.ai_run_count = 0
                self._touch_card(card, actor_name)
                self._append_card_ai_log(
                    card,
                    level="DONE",
                    message="Старое автосопровождение отключено. Доступна только локальная уборка карточки.",
                )
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_ai_legacy_disabled",
                    message=f"{actor_name} отключил старое автосопровождение карточки",
                    card_id=card.id,
                    details={"cleanup_available": True},
                )
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "enabled": False,
                    "launched": False,
                    "prompt_updated": False,
                    "task_id": "",
                    "server_available": False,
                    "next_check_at": "",
                    "retired": True,
                    "cleanup_available": True,
                    "reason": "legacy_agent_runtime_disabled",
                },
            }

    # Retained only as inert reference during the rollback stabilization pass.
    # The live compatibility shim is defined further below.
    def cleanup_card_content(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="ui")

            changed = False
            changed_fields: list[str] = []
            normalized_description = self._build_card_cleanup_description(card)
            if normalized_description and normalized_description != card.description:
                if self._update_description(
                    card, normalized_description, events, actor_name, source
                ):
                    changed = True
                    changed_fields.append("description")

            cleanup_vehicle = self._build_card_cleanup_vehicle_label(card)
            if cleanup_vehicle and cleanup_vehicle != card.vehicle:
                if self._update_vehicle(card, cleanup_vehicle, events, actor_name, source):
                    changed = True
                    changed_fields.append("vehicle")

            profile_patch = self._build_card_cleanup_vehicle_profile_patch(card)
            if profile_patch:
                if self._update_vehicle_profile(card, profile_patch, events, actor_name, source):
                    changed = True
                    changed_fields.append("vehicle_profile")

            verify = {
                "description_ok": not normalized_description
                or card.description == normalized_description,
                "vehicle_ok": not cleanup_vehicle or card.vehicle == cleanup_vehicle,
                "vehicle_profile_ok": self._cleanup_profile_patch_applied(card, profile_patch),
                "external_calls": False,
            }
            verify_passed = (
                bool(verify["description_ok"])
                and bool(verify["vehicle_ok"])
                and bool(verify["vehicle_profile_ok"])
                and not bool(verify["external_calls"])
            )

            if changed:
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_cleanup_applied",
                    message=f"{actor_name} прибрался в карточке",
                    card_id=card.id,
                    details={
                        "changed_fields": changed_fields,
                        "verify_passed": verify_passed,
                        "cleanup_mode": "local_card_cleanup",
                    },
                )
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)

            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "changed": changed,
                    "changed_fields": changed_fields,
                    "verify": {
                        **verify,
                        "passed": verify_passed,
                    },
                    "cleanup_mode": "local_card_cleanup",
                },
            }

    def run_full_card_enrichment(self, payload: dict | None = None) -> dict:
        if self._agent_control is not None:
            return self._run_full_card_enrichment_with_agent_control(payload)
        result = self.cleanup_card_content(payload)
        result["meta"].update(
            {
                "launched": bool(result["meta"].get("changed")),
                "already_running": False,
                "task_id": "",
                "server_available": False,
                "scenario_id": "card_cleanup",
                "retired": True,
                "legacy_request": "run_full_card_enrichment",
            }
        )
        return result

    def _build_full_card_enrichment_prompt(self, payload: dict[str, object]) -> str:
        scenario_id = str(payload.get("scenario_id", "") or "").strip().lower()
        heading = str(payload.get("card_heading", "") or payload.get("title", "") or "").strip()
        vehicle = str(payload.get("vehicle", "") or "").strip()
        mini_prompt = str(
            payload.get("prompt", "") or payload.get("ai_autofill_prompt", "") or ""
        ).strip()
        lines = [
            "Выполни полное заполнение карточки автосервиса.",
            "Работай только с этой карточкой и заполни все подтверждаемые поля самостоятельно.",
            "Сначала прочитай get_card_context(card_id).",
            "Используй обычные write-команды update_card, update_repair_order, replace_repair_order_works и replace_repair_order_materials.",
            "Не используй autofill helpers и не выдумывай данные.",
            "Если часть данных не подтверждается, оставь поле пустым.",
            "После записи обязательно проверь результат через read-after-write.",
            "Паспортные поля автомобиля можно заполнять только при явной поддержке контекстом карточки.",
        ]
        if heading:
            lines.append(f"Карточка: {heading}.")
        if vehicle:
            lines.append(f"Автомобиль: {vehicle}.")
        if scenario_id in {"full_card_enrichment", "card_enrichment"}:
            lines.append("Сценарий: full_card_enrichment.")
        if mini_prompt:
            lines.append(f"User mini-prompt: {mini_prompt}")
        return "\n".join(lines)

    def _set_card_ai_autofill_with_agent_control(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="ui")
            enabled_requested = "enabled" in payload
            prompt_requested = "prompt" in payload or "ai_autofill_prompt" in payload
            previous_enabled = bool(card.ai_autofill_active)
            enabled = self._validated_optional_bool(payload, "enabled", default=previous_enabled)
            prompt_text = normalize_text(
                payload.get("prompt", payload.get("ai_autofill_prompt", card.ai_autofill_prompt)),
                default=card.ai_autofill_prompt,
                limit=800,
            )
            now = utc_now()
            now_iso = now.isoformat()
            prompt_updated = prompt_text != str(card.ai_autofill_prompt or "").strip()
            card.ai_autofill_prompt = prompt_text
            if enabled_requested:
                card.ai_autofill_active = enabled
                card.ai_autofill_until = (now + timedelta(hours=4)).isoformat() if enabled else ""
                card.ai_next_run_at = now_iso if enabled else ""
            if enabled_requested and not enabled:
                card.last_card_fingerprint = ""
                card.ai_run_count = 0
                self._append_card_ai_log(
                    card, level="DONE", message="Автосопровождение остановлено."
                )
            if prompt_requested and prompt_updated:
                self._append_card_ai_log(
                    card, level="INFO", message="ИИ-подсказка автосопровождения обновлена."
                )
            launched_task_id = ""
            server_available = self._agent_control is not None
            if self._agent_control is not None:
                try:
                    status_payload = self._agent_control.agent_status()
                    server_available = bool(status_payload.get("agent", {}).get("available"))
                except Exception:
                    server_available = True
            if enabled_requested and enabled and not previous_enabled:
                self._append_card_ai_log(
                    card, level="RUN", message="Полное заполнение карточки включено."
                )
                for level, message in self._card_ai_context_messages(card):
                    self._append_card_ai_log(card, level=level, message=message)
                if self._agent_control is not None:
                    task_payload = {
                        "card_id": card.id,
                        "card_heading": card.heading(),
                        "title": card.title,
                        "vehicle": card.vehicle_display(),
                        "requested_by": actor_name,
                        "ai_autofill_prompt": card.ai_autofill_prompt,
                        "ai_log_tail": list(card.ai_autofill_log[-8:]),
                        "scenario_id": "full_card_enrichment",
                    }
                    task_payload["task_text"] = self._build_full_card_enrichment_prompt(
                        task_payload
                    )
                    task = self._agent_control.enqueue_card_autofill_task(
                        task_payload,
                        source="ui_full_card_enrichment",
                        trigger="manual_activate",
                        purpose="full_card_enrichment",
                        mode="full_card_enrichment",
                    )
                    if task is not None:
                        launched_task_id = str(task.get("id", "") or "").strip()
                        card.last_ai_run_at = (
                            str(task.get("created_at", "") or now_iso).strip() or now_iso
                        )
                        card.ai_run_count = max(0, int(card.ai_run_count)) + 1
                        card.ai_next_run_at = (
                            now
                            + timedelta(
                                minutes=self._card_ai_next_interval_minutes(card, changed=True)
                            )
                        ).isoformat()
                        self._append_card_ai_log(
                            card,
                            level="RUN",
                            message="Полное заполнение карточки запущено.",
                            task_id=launched_task_id,
                        )
                    else:
                        self._append_card_ai_log(
                            card,
                            level="WAIT",
                            message="Полное заполнение карточки уже выполняется.",
                        )
                else:
                    self._append_card_ai_log(card, level="WARN", message="Server AI недоступен.")
            self._touch_card(card, actor_name)
            if enabled:
                card.last_card_fingerprint = self._card_ai_fingerprint(card)
            event_action = (
                "card_full_enrichment_enabled" if enabled else "card_full_enrichment_disabled"
            )
            event_message = (
                f"{actor_name} {'включил' if enabled else 'выключил'} полное заполнение карточки"
            )
            if prompt_requested and not enabled_requested:
                event_action = "card_full_enrichment_prompt_updated"
                event_message = f"{actor_name} обновил mini-prompt полного заполнения карточки"
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action=event_action,
                message=event_message,
                card_id=card.id,
                details={
                    "enabled": enabled,
                    "ai_autofill_until": card.ai_autofill_until,
                    "ai_autofill_prompt": card.ai_autofill_prompt,
                    "task_id": launched_task_id,
                },
            )
            self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "enabled": enabled,
                    "launched": bool(launched_task_id),
                    "prompt_updated": prompt_updated,
                    "task_id": launched_task_id,
                    "server_available": server_available,
                    "next_check_at": card.ai_next_run_at,
                },
            }

    def _run_full_card_enrichment_with_agent_control(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="ui")
            server_available = bool(self._agent_control is not None)
            launched_task_id = ""
            already_running = False
            if self._agent_control is not None:
                task_payload = {
                    "card_id": card.id,
                    "card_heading": card.heading(),
                    "title": card.title,
                    "vehicle": card.vehicle_display(),
                    "requested_by": actor_name,
                    "scenario_id": "full_card_enrichment",
                }
                task_payload["task_text"] = self._build_full_card_enrichment_prompt(task_payload)
                task = self._agent_control.enqueue_card_autofill_task(
                    task_payload,
                    source="ui_full_card_enrichment",
                    trigger="manual_enrichment",
                    purpose="full_card_enrichment",
                    mode="full_card_enrichment",
                )
                if task is not None:
                    launched_task_id = str(task.get("id", "") or "").strip()
                    card.last_ai_run_at = (
                        str(task.get("created_at", "") or utc_now_iso()).strip() or utc_now_iso()
                    )
                    card.ai_run_count = max(0, int(card.ai_run_count)) + 1
                    self._append_card_ai_log(
                        card,
                        level="RUN",
                        message="Полное заполнение карточки запущено.",
                        task_id=launched_task_id,
                    )
                    for level, message in self._card_ai_context_messages(card):
                        self._append_card_ai_log(
                            card, level=level, message=message, task_id=launched_task_id
                        )
                else:
                    already_running = True
                    latest_task = self._agent_control.latest_task_for_card(
                        card.id, purpose="full_card_enrichment"
                    )
                    launched_task_id = str((latest_task or {}).get("id", "") or "").strip()
                    self._append_card_ai_log(
                        card,
                        level="WAIT",
                        message="Полное заполнение карточки уже выполняется.",
                        task_id=launched_task_id,
                    )
            else:
                self._append_card_ai_log(card, level="WARN", message="Server AI недоступен.")
            self._touch_card(card, actor_name)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="card_full_enrichment_requested",
                message=f"{actor_name} запустил bounded полное заполнение карточки",
                card_id=card.id,
                details={
                    "task_id": launched_task_id,
                    "server_available": server_available,
                    "already_running": already_running,
                    "scenario_id": "full_card_enrichment",
                },
            )
            self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "launched": bool(launched_task_id) and not already_running,
                    "already_running": already_running,
                    "task_id": launched_task_id,
                    "server_available": server_available,
                    "scenario_id": "full_card_enrichment",
                },
            }

    def trigger_due_ai_followups(self) -> dict:
        with self._lock:
            # Background AI follow-up is intentionally retired.
            return {"launched": [], "failed": []}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            now = utc_now()
            now_iso = now.isoformat()
            launched: list[str] = []
            failed: list[dict[str, str]] = []
            changed_any = False
            for card in cards:
                if card.archived:
                    if card.ai_autofill_active:
                        card.ai_autofill_active = False
                        card.ai_autofill_until = ""
                        card.ai_next_run_at = ""
                        self._append_card_ai_log(
                            card,
                            level="DONE",
                            message="Автосопровождение остановлено: карточка в архиве.",
                        )
                        changed_any = True
                    continue
                if not card.ai_autofill_active:
                    continue
                until_at = parse_datetime(card.ai_autofill_until)
                max_runs = self._card_ai_max_runs(card)
                if until_at is None or until_at <= now or card.ai_run_count >= max_runs:
                    card.ai_autofill_active = False
                    card.ai_autofill_until = ""
                    card.ai_next_run_at = ""
                    if until_at is None or until_at <= now:
                        done_message = (
                            "Автосопровождение завершено: вышло время окна автосопровождения."
                        )
                    else:
                        done_message = (
                            f"Автосопровождение завершено: достигнут лимит проходов ({max_runs})."
                        )
                    self._append_card_ai_log(card, level="DONE", message=done_message)
                    changed_any = True
                    continue
                next_run_at = parse_datetime(card.ai_next_run_at) or now
                if next_run_at > now:
                    continue
                if self._agent_control.has_active_task_for_card(card.id, purpose="card_autofill"):
                    continue
                latest_task = self._agent_control.latest_task_for_card(
                    card.id, purpose="card_autofill"
                )
                retry_after_failure = (
                    str(latest_task.get("status", "") if isinstance(latest_task, dict) else "")
                    .strip()
                    .lower()
                    == "failed"
                )
                fingerprint = self._card_ai_fingerprint(card)
                changed = fingerprint != str(card.last_card_fingerprint or "").strip()
                if not changed and not retry_after_failure:
                    next_interval_minutes = self._card_ai_next_interval_minutes(card, changed=False)
                    card.ai_next_run_at = (
                        now + timedelta(minutes=next_interval_minutes)
                    ).isoformat()
                    self._append_card_ai_log(
                        card,
                        level="WAIT",
                        message=f"Изменений не обнаружено. Повторная проверка отложена на {next_interval_minutes} мин.",
                    )
                    changed_any = True
                    continue
                followup_trigger = (
                    "retry_after_error"
                    if retry_after_failure and not changed
                    else "adaptive_followup"
                )
                try:
                    task = self._agent_control.enqueue_card_autofill_task(
                        {
                            "card_id": card.id,
                            "card_heading": card.heading(),
                            "title": card.title,
                            "vehicle": card.vehicle_display(),
                            "requested_by": "scheduler",
                            "ai_autofill_prompt": card.ai_autofill_prompt,
                            "ai_log_tail": list(card.ai_autofill_log[-8:]),
                        },
                        source="agent_card_followup",
                        trigger=followup_trigger,
                    )
                    if task is None:
                        self._append_card_ai_log(
                            card, level="WAIT", message="Не удалось запустить повторный проход."
                        )
                        changed_any = True
                        continue
                    launched.append(str(task.get("id", "") or "").strip())
                    card.last_ai_run_at = (
                        str(task.get("created_at", "") or now_iso).strip() or now_iso
                    )
                    card.ai_run_count = max(0, int(card.ai_run_count)) + 1
                    card.last_card_fingerprint = fingerprint
                    card.ai_next_run_at = (
                        now
                        + timedelta(minutes=self._card_ai_next_interval_minutes(card, changed=True))
                    ).isoformat()
                    if retry_after_failure and not changed:
                        self._append_card_ai_log(
                            card,
                            level="WARN",
                            message="Предыдущий проход завершился ошибкой. Запущен безопасный повтор.",
                            task_id=launched[-1],
                        )
                    self._append_card_ai_log(
                        card,
                        level="RUN",
                        message="Обнаружены изменения. Запущен повторный проход.",
                        task_id=launched[-1],
                    )
                    changed_any = True
                except Exception as exc:
                    self._append_card_ai_log(
                        card, level="WARN", message="Не удалось запустить повторный проход."
                    )
                    failed.append({"card_id": card.id, "error": str(exc)})
                    changed_any = True
            if changed_any:
                self._save_bundle(bundle, columns=columns, cards=cards, events=events)
            return {"launched": launched, "failed": failed}

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
                changed = (
                    card.mark_seen(actor_name, seen_at=card.updated_at or card.created_at)
                    or changed
                )
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
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            serialized_cashboxes = [
                self._serialize_cashbox(cashbox, transactions) for cashbox in cashboxes[:limit]
            ]
            return {
                "cashboxes": serialized_cashboxes,
                "meta": {
                    "total": len(cashboxes),
                    "transactions_total": len(transactions),
                    "limit": limit,
                    "returned": len(serialized_cashboxes),
                    "has_more": len(cashboxes) > len(serialized_cashboxes),
                },
            }

    def get_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            transaction_limit = self._validated_limit(
                payload.get("transaction_limit"), default=300, maximum=5000
            )
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            transactions = self._cashbox_transactions(bundle["cash_transactions"], cashbox.id)
            return {
                "cashbox": self._serialize_cashbox(cashbox, bundle["cash_transactions"]),
                "transactions": [
                    self._serialize_cash_transaction(item)
                    for item in transactions[:transaction_limit]
                ],
                "meta": {
                    "transactions_total": len(transactions),
                    "transaction_limit": transaction_limit,
                },
            }

    def get_cash_journal(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            months = self._validated_limit(payload.get("months"), default=3, maximum=12)
            limit = self._validated_limit(payload.get("limit"), default=5000, maximum=10000)
            bundle = self._store.read_bundle()
            period_start = utc_now() - timedelta(days=30 * months)
            recent_transactions: list[CashTransaction] = []
            for item in bundle["cash_transactions"]:
                created_at = parse_datetime(item.created_at)
                if created_at is None or created_at < period_start:
                    continue
                recent_transactions.append(item)
            recent_transactions.sort(
                key=lambda item: (
                    self._cash_transaction_sortable_datetime(item.created_at),
                    item.id,
                ),
                reverse=True,
            )
            returned_transactions = recent_transactions[:limit]
            cashboxes_by_id = {cashbox.id: cashbox for cashbox in bundle["cashboxes"]}
            journal = self._build_cash_journal(
                returned_transactions,
                cashboxes_by_id,
                months=months,
                limit=limit,
                total=len(recent_transactions),
                period_start=period_start,
            )
            return {
                "entries": journal["entries"],
                "days": journal["days"],
                "weeks": journal["weeks"],
                "months": journal["months"],
                "totals": journal["totals"],
                "markdown": journal["markdown"],
                "text": journal["markdown"],
                "meta": journal["meta"],
            }

    def create_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            if len(cashboxes) >= 6:
                raise ValueError("Нельзя создать больше 6 касс.")
            now_iso = utc_now_iso()
            cashbox = CashBox(
                id=str(uuid.uuid4()),
                name=self._validated_cashbox_name(payload.get("name"), cashboxes),
                order=len(cashboxes),
                created_at=now_iso,
                updated_at=now_iso,
            )
            cashboxes.append(cashbox)
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

    def reorder_cashboxes(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            before_cashbox_id = (
                payload.get("before_cashbox_id")
                or payload.get("before_id")
                or payload.get("target_cashbox_id")
            )
            if before_cashbox_id and str(before_cashbox_id).strip() == cashbox.id:
                return {
                    "cashboxes": [
                        self._serialize_cashbox(item, transactions) for item in cashboxes
                    ],
                    "cashbox": self._serialize_cashbox(cashbox, transactions),
                    "meta": {
                        "changed": False,
                        "total": len(cashboxes),
                    },
                }
            reordered_cashboxes, changed = self._reposition_cashbox(
                cashboxes,
                cashbox,
                before_cashbox_id=before_cashbox_id,
            )
            if changed:
                before_cashbox = None
                if before_cashbox_id:
                    before_cashbox = self._find_cashbox(reordered_cashboxes, before_cashbox_id)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="cashbox_reordered",
                    message=f"{actor_name} изменил порядок касс",
                    card_id=None,
                    details={
                        "cashbox_id": cashbox.id,
                        "cashbox_name": cashbox.name,
                        "before_cashbox_id": before_cashbox.id if before_cashbox else None,
                        "before_cashbox_name": before_cashbox.name if before_cashbox else None,
                    },
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    cashboxes=reordered_cashboxes,
                    cash_transactions=transactions,
                    events=events,
                )
            return {
                "cashboxes": [
                    self._serialize_cashbox(item, transactions) for item in reordered_cashboxes
                ],
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "meta": {
                    "changed": changed,
                    "total": len(reordered_cashboxes),
                },
            }

    def create_cashbox_transfer(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            source_cashbox = self._find_cashbox(
                cashboxes, payload.get("from_cashbox_id") or payload.get("cashbox_id")
            )
            target_cashbox = self._find_cashbox(
                cashboxes, payload.get("to_cashbox_id") or payload.get("target_cashbox_id")
            )
            if source_cashbox.id == target_cashbox.id:
                self._fail(
                    "validation_error",
                    "Нельзя переместить деньги в ту же кассу.",
                    details={"field": "to_cashbox_id"},
                )
            amount_minor = self._validated_cash_amount_minor(payload)
            base_note = self._validated_cash_transaction_note(payload.get("note"))
            transfer_out_note = f"Перемещение в {target_cashbox.name}"
            transfer_in_note = f"Перемещение из {source_cashbox.name}"
            if base_note:
                transfer_out_note = f"{transfer_out_note}: {base_note}"
                transfer_in_note = f"{transfer_in_note}: {base_note}"
            transfer_created_at = utc_now_iso()
            source_transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=source_cashbox,
                direction="expense",
                amount_minor=amount_minor,
                note=transfer_out_note,
                actor_name=actor_name,
                source=source,
                created_at=transfer_created_at,
            )
            target_transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=target_cashbox,
                direction="income",
                amount_minor=amount_minor,
                note=transfer_in_note,
                actor_name=actor_name,
                source=source,
                created_at=transfer_created_at,
            )
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_transfer_created",
                message=f"{actor_name} переместил деньги между кассами",
                card_id=None,
                details={
                    "from_cashbox_id": source_cashbox.id,
                    "from_cashbox_name": source_cashbox.name,
                    "to_cashbox_id": target_cashbox.id,
                    "to_cashbox_name": target_cashbox.name,
                    "amount_minor": amount_minor,
                    "amount_display": format_money_minor(amount_minor),
                    "note": base_note,
                    "source_transaction_id": source_transaction.id,
                    "target_transaction_id": target_transaction.id,
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
                "from_cashbox": self._serialize_cashbox(source_cashbox, transactions),
                "to_cashbox": self._serialize_cashbox(target_cashbox, transactions),
                "source_transaction": self._serialize_cash_transaction(source_transaction),
                "target_transaction": self._serialize_cash_transaction(target_transaction),
            }

    def delete_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            related_transactions = self._cashbox_transactions(transactions, cashbox.id)
            if related_transactions:
                raise ValueError("Нельзя удалить кассу, пока в ней есть движения.")
            statistics = self._cashbox_statistics(cashbox, transactions)
            remaining_cashboxes = self._ordered_cashboxes(
                [item for item in cashboxes if item.id != cashbox.id]
            )
            self._renumber_cashboxes(remaining_cashboxes)
            remaining_transactions = [
                item for item in transactions if item.cashbox_id != cashbox.id
            ]
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
            transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=cashbox,
                direction=normalize_cash_direction(payload.get("direction"), default="income"),
                amount_minor=self._validated_cash_amount_minor(payload),
                note=self._validated_cash_transaction_note(payload.get("note")),
                actor_name=actor_name,
                source=source,
                employee_id=normalize_text(payload.get("employee_id"), default="", limit=64),
                employee_name=normalize_text(payload.get("employee_name"), default="", limit=80),
                transaction_kind=normalize_text(
                    payload.get("transaction_kind"), default="", limit=32
                ),
            )
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

    def create_employee_salary_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            settings = bundle["settings"]
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            kind = self._normalize_salary_transaction_kind(
                payload.get("transaction_kind") or payload.get("kind")
            )
            amount_minor = self._validated_cash_amount_minor(payload)
            requested_cashbox_id = normalize_text(
                payload.get("cashbox_id") or payload.get("cashboxId"),
                default="",
                limit=128,
            )
            cashbox = (
                self._find_cashbox(cashboxes, requested_cashbox_id)
                if requested_cashbox_id
                else self._salary_cashbox(cashboxes)
            )
            if cashbox is None:
                self._fail(
                    "validation_error",
                    "Для выплат зарплаты нужно выбрать кассу.",
                    details={"field": "cashbox_id"},
                )
            note_prefix = "Выплата зарплаты" if kind == "salary_payout" else "Аванс"
            note = self._validated_cash_transaction_note(
                payload.get("note") or f"{note_prefix}: {employee['name']}",
            )
            transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=cashbox,
                direction="expense",
                amount_minor=amount_minor,
                note=note,
                actor_name=actor_name,
                source=source,
                employee_id=employee["id"],
                employee_name=employee["name"],
                transaction_kind=kind,
            )
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="employee_salary_transaction_created",
                message=f"{actor_name} провёл {'выплату зарплаты' if kind == 'salary_payout' else 'аванс'} сотруднику",
                card_id=None,
                details={
                    "employee_id": employee["id"],
                    "employee_name": employee["name"],
                    "transaction_kind": kind,
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
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
                "employee": employee,
            }

    def cancel_last_cash_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            settings = bundle["settings"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            related_transactions = self._cashbox_transactions(transactions, cashbox.id)
            if not related_transactions:
                self._fail(
                    "validation_error",
                    "В кассе нет движений для отмены.",
                    details={"field": "cashbox_id"},
                )
            latest_transaction = related_transactions[0]
            requested_transaction = (
                self._find_cash_transaction(transactions, payload.get("transaction_id"))
                or latest_transaction
            )
            if requested_transaction.id != latest_transaction.id:
                self._fail(
                    "validation_error",
                    "Можно отменить только последнее движение по выбранной кассе.",
                    details={
                        "field": "transaction_id",
                        "cashbox_id": cashbox.id,
                        "latest_transaction_id": latest_transaction.id,
                    },
                )
            if self._is_cashbox_transfer_transaction(latest_transaction):
                self._fail(
                    "validation_error",
                    "Последнее движение — перемещение между кассами. Автоотмена перемещений не поддерживается.",
                    details={"transaction_id": latest_transaction.id, "cashbox_id": cashbox.id},
                )

            linked_card, linked_payment = self._find_repair_order_payment_by_cash_transaction(
                cards, latest_transaction.id
            )
            response_meta: dict[str, object] = {
                "cancelled": True,
                "transaction_id": latest_transaction.id,
                "cashbox_id": cashbox.id,
                "repair_order_card_id": linked_card.id if linked_card is not None else None,
            }
            if linked_card is not None and linked_payment is not None:
                next_order_payload = linked_card.repair_order.to_storage_dict()
                next_order_payload["payments"] = [
                    payment.to_storage_dict()
                    for payment in linked_card.repair_order.payments
                    if payment.id != linked_payment.id
                ]
                if not next_order_payload["payments"]:
                    next_order_payload["prepayment"] = ""
                changed = self._update_repair_order(
                    linked_card,
                    cards,
                    next_order_payload,
                    events,
                    actor_name,
                    source,
                    cashboxes=cashboxes,
                    cash_transactions=transactions,
                    settings=settings,
                )
                if not changed:
                    self._fail(
                        "validation_error",
                        "Не удалось отменить последнее движение по кассе.",
                        details={"transaction_id": latest_transaction.id, "cashbox_id": cashbox.id},
                    )
                self._touch_card(linked_card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(linked_card, actor_name, source)
                if self._card_has_repair_order(linked_card):
                    self._ensure_repair_order_text_file(linked_card, force=True)
            else:
                transactions[:] = [
                    item for item in transactions if item.id != latest_transaction.id
                ]
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="cash_transaction_deleted",
                    message=f"{actor_name} отменил последнее движение по кассе",
                    card_id=None,
                    details={
                        "cash_transaction_id": latest_transaction.id,
                        "cashbox_id": latest_transaction.cashbox_id,
                        "cashbox_name": cashbox.name,
                        "direction": latest_transaction.direction,
                        "amount_minor": latest_transaction.amount_minor,
                        "amount_display": format_money_minor(latest_transaction.amount_minor),
                        "note": latest_transaction.note,
                    },
                )

            self._refresh_cashbox_updated_at(cashbox, transactions)
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "cancelled_transaction": self._serialize_cash_transaction(latest_transaction),
                "meta": response_meta,
            }

    def _append_cash_transaction(
        self,
        *,
        transactions: list[CashTransaction],
        cashbox: CashBox,
        direction: str,
        amount_minor: int,
        note: str,
        actor_name: str,
        source: str,
        created_at: str | None = None,
        employee_id: str = "",
        employee_name: str = "",
        transaction_kind: str = "",
    ) -> CashTransaction:
        parsed_created_at = parse_datetime(created_at) if created_at else None
        transaction = CashTransaction(
            id=str(uuid.uuid4()),
            cashbox_id=cashbox.id,
            direction=direction,
            amount_minor=amount_minor,
            note=note,
            created_at=(parsed_created_at or utc_now()).isoformat(),
            actor_name=actor_name,
            source=source,
            employee_id=normalize_text(employee_id, default="", limit=64),
            employee_name=normalize_text(employee_name, default="", limit=80),
            transaction_kind=normalize_text(transaction_kind, default="", limit=32),
        )
        transactions.append(transaction)
        transactions.sort(
            key=lambda item: (
                self._cash_transaction_sortable_datetime(item.created_at),
                item.id,
            )
        )
        cashbox.updated_at = transaction.created_at
        return transaction

    def update_board_settings(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="ui")
            bundle = self._store.read_bundle()
            previous_scale = float(bundle["settings"].get("board_scale", 1.0))
            previous_board_control = self._normalized_ai_board_control_settings(
                bundle["settings"].get("ai_board_control")
            )
            board_scale = (
                self._validated_board_scale(payload.get("board_scale"))
                if "board_scale" in payload
                else previous_scale
            )
            board_control_payload = self._extract_ai_board_control_settings_payload(payload)
            board_control_settings = (
                self._validated_ai_board_control_settings(
                    board_control_payload, default=previous_board_control
                )
                if board_control_payload is not None
                else previous_board_control
            )
            settings = dict(bundle["settings"])
            settings["board_scale"] = board_scale
            settings["ai_board_control"] = board_control_settings
            scale_changed = previous_scale != board_scale
            board_control_changed = previous_board_control != board_control_settings
            if scale_changed or board_control_changed:
                events = bundle["events"]
                if scale_changed:
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="board_scale_changed",
                        message=f"{actor_name} изменил масштаб доски",
                        card_id=None,
                        details={"before": previous_scale, "after": board_scale},
                    )
                if board_control_changed:
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="board_ai_control_changed",
                        message=f"{actor_name} РѕР±РЅРѕРІРёР» С‚РёС…РёРµ РЅР°СЃС‚СЂРѕР№РєРё board_control",
                        card_id=None,
                        details={"before": previous_board_control, "after": board_control_settings},
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
                    "ai_board_control": board_control_settings,
                    "previous_ai_board_control": previous_board_control,
                    "changed": scale_changed or board_control_changed,
                    "board_scale_changed": scale_changed,
                    "board_control_changed": board_control_changed,
                },
            }

    def get_ai_board_control_settings(self) -> dict[str, Any]:
        with self._lock:
            bundle = self._store.read_bundle()
            return self._normalized_ai_board_control_settings(
                bundle["settings"].get("ai_board_control")
            )

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
                self._save_bundle(
                    bundle, columns=bundle["columns"], cards=cards, events=bundle["events"]
                )
            self._cleanup_repair_orders_directory(cards)
            ranked_cards = [
                card
                for card in sorted(cards, key=self._repair_order_sort_key, reverse=True)
                if self._card_has_repair_order(card)
            ]
            inconsistent_cards = [
                card
                for card in ranked_cards
                if self._card_has_inconsistent_repair_order_state(card)
            ]
            ranked_cards = [
                card
                for card in ranked_cards
                if not self._card_has_inconsistent_repair_order_state(card)
            ]
            open_cards = [
                card
                for card in ranked_cards
                if card.repair_order.status == REPAIR_ORDER_STATUS_OPEN
            ]
            ready_cards = [
                card
                for card in ranked_cards
                if card.repair_order.status == REPAIR_ORDER_STATUS_READY
            ]
            archived_cards = [
                card
                for card in ranked_cards
                if card.repair_order.status == REPAIR_ORDER_STATUS_CLOSED
            ]
            if status_filter == "all":
                ordered_cards = ranked_cards
            elif status_filter == REPAIR_ORDER_STATUS_READY:
                ordered_cards = ready_cards
            elif status_filter == REPAIR_ORDER_STATUS_CLOSED:
                ordered_cards = archived_cards
            else:
                ordered_cards = open_cards
            filtered_cards = self._filter_repair_order_cards(ordered_cards, query=query)
            sorted_cards = sorted(
                filtered_cards,
                key=lambda card: self._repair_order_list_sort_key(card, sort_by=sort_by),
                reverse=(sort_dir == "desc"),
            )
            return {
                "repair_orders": [
                    self._serialize_repair_order_list_item(card) for card in sorted_cards[:limit]
                ],
                "meta": {
                    "limit": limit,
                    "total": len(sorted_cards),
                    "returned": min(len(sorted_cards), limit),
                    "has_more": len(sorted_cards) > limit,
                    "status": status_filter,
                    "query": query,
                    "sort_by": sort_by,
                    "sort_dir": sort_dir,
                    "active_total": len(open_cards),
                    "open_total": len(open_cards),
                    "ready_total": len(ready_cards),
                    "archived_total": len(archived_cards),
                    "inconsistent_total": len(inconsistent_cards),
                    "directory": str(self._repair_orders_dir),
                },
            }

    def list_clients(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=200, maximum=1000)
            include_stats = self._validated_optional_bool(payload, "include_stats", default=True)
            bundle = self._store.read_bundle()
            clients = self._ordered_clients(bundle["clients"])
            return {
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=include_stats,
                        compact=True,
                        include_vehicle_preview=include_stats,
                    )
                    for client in clients[:limit]
                ],
                "meta": {
                    "total": len(clients),
                    "returned": min(len(clients), limit),
                    "has_more": len(clients) > limit,
                    "include_stats": include_stats,
                },
            }

    def search_clients(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            query = normalize_text(payload.get("query"), default="", limit=240)
            limit = self._validated_limit(payload.get("limit"), default=10, maximum=100)
            bundle = self._store.read_bundle()
            clients = self._ordered_clients(bundle["clients"])
            matches = self._rank_client_matches(clients, query, bundle["cards"])
            selected = [client for _, client in matches[:limit]]
            related_cards_by_client_id = self._client_related_cards_map(
                selected, bundle["cards"]
            )
            return {
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=True,
                        compact=True,
                        query=query,
                        related_cards=related_cards_by_client_id.get(client.id, []),
                    )
                    for client in selected
                ],
                "meta": {
                    "query": query,
                    "total": len(matches),
                    "returned": len(selected),
                    "has_more": len(matches) > len(selected),
                },
            }

    def get_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            order_limit = self._validated_limit(payload.get("order_limit"), default=30, maximum=200)
            bundle = self._store.read_bundle()
            client = self._find_client(bundle["clients"], payload.get("client_id"))
            return self._client_profile_payload(client, bundle["cards"], order_limit=order_limit)

    def get_client_stats(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            client = self._find_client(bundle["clients"], payload.get("client_id"))
            return {
                "client": self._serialize_client(
                    client, bundle["cards"], include_stats=True, compact=True
                ),
                "stats": self._client_stats(client, bundle["cards"]),
            }

    def create_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._validated_client_profile(payload)
            clients.append(client)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_created",
                message=f"{actor_name} создал клиента",
                card_id=None,
                details={"client_id": client.id, "client_name": client.name()},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, bundle["cards"], include_stats=True),
                "meta": {"created": True},
            }

    def update_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id") or payload.get("id"))
            before = client.to_storage_dict()
            merged = {**before, **self._client_patch_payload(payload)}
            merged["id"] = client.id
            merged["created_at"] = client.created_at
            merged["updated_at"] = utc_now_iso()
            next_client = ClientProfile.from_dict(merged)
            changed = before != next_client.to_storage_dict()
            if changed:
                index = clients.index(client)
                clients[index] = next_client
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="client_updated",
                    message=f"{actor_name} обновил клиента",
                    card_id=None,
                    details={"client_id": next_client.id, "client_name": next_client.name()},
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    clients=clients,
                    events=events,
                )
                client = next_client
            return {
                "client": self._serialize_client(client, bundle["cards"], include_stats=True),
                "meta": {"changed": changed},
            }

    def link_card_to_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            clients = bundle["clients"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            client = self._find_client(clients, payload.get("client_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            sync_fields = self._validated_optional_bool(payload, "sync_fields", default=True)
            overwrite = self._validated_optional_bool(
                payload, "overwrite_card_fields", default=False
            )
            sync_vehicle_fields = self._validated_optional_bool(
                payload, "sync_vehicle_fields", default=True
            )
            create_vehicle_from_card = self._validated_optional_bool(
                payload, "create_vehicle_from_card", default=False
            )
            client_vehicle_id = normalize_text(
                payload.get("client_vehicle_id") or payload.get("vehicle_id"),
                default="",
                limit=128,
            )
            vehicle: ClientVehicle | None = None
            clients_changed = False
            if create_vehicle_from_card:
                vehicle = self._client_vehicle_from_card(card)
                client.vehicles.append(vehicle)
                client.vehicles = self._dedupe_client_vehicles(client.vehicles)
                client.updated_at = utc_now_iso()
                client_vehicle_id = vehicle.id
                clients_changed = True
            elif client_vehicle_id:
                vehicle = self._find_client_vehicle(client, client_vehicle_id)

            changed = card.client_id != client.id
            card.client_id = client.id
            if client_vehicle_id and card.client_vehicle_id != client_vehicle_id:
                card.client_vehicle_id = client_vehicle_id
                changed = True
            if sync_fields:
                changed = (
                    self._sync_card_client_fields(card, client, overwrite=overwrite) or changed
                )
            if vehicle is not None and sync_vehicle_fields:
                changed = self._sync_card_vehicle_fields(card, vehicle, overwrite=True) or changed
            if changed:
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_linked",
                    message=f"{actor_name} связал карточку с клиентом",
                    card_id=card.id,
                    details={
                        "client_id": client.id,
                        "client_name": client.name(),
                        "client_vehicle_id": card.client_vehicle_id,
                        "vehicle_created": create_vehicle_from_card,
                    },
                )
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
            if clients_changed and not changed:
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="client_vehicle_created",
                    message=f"{actor_name} добавил автомобиль клиента",
                    card_id=card.id,
                    details={"client_id": client.id, "client_vehicle_id": client_vehicle_id},
                )
            if changed or clients_changed:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=clients,
                    events=events,
                )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "client": self._serialize_client(client, cards, include_stats=True),
                "meta": {
                    "changed": changed or clients_changed,
                    "sync_fields": sync_fields,
                    "sync_vehicle_fields": sync_vehicle_fields,
                    "client_vehicle_id": card.client_vehicle_id,
                    "vehicle_created": create_vehicle_from_card,
                },
            }

    def upsert_client_vehicle(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            cards = bundle["cards"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id"))
            vehicle_id = normalize_text(
                payload.get("client_vehicle_id") or payload.get("vehicle_id"),
                default="",
                limit=128,
            )
            card = self._find_card(cards, payload.get("card_id")) if payload.get("card_id") else None
            if card is not None and not payload.get("vehicle"):
                next_vehicle = self._client_vehicle_from_card(card, vehicle_id=vehicle_id)
            else:
                next_vehicle = self._validated_client_vehicle(payload)
                if vehicle_id:
                    next_vehicle.id = vehicle_id

            existing = self._find_client_vehicle_or_none(client, vehicle_id)
            created = existing is None
            changed = created
            if existing is None:
                client.vehicles.append(next_vehicle)
            else:
                merged = self._merge_client_vehicle(existing, next_vehicle)
                changed = existing.to_dict() != merged.to_dict()
                if changed:
                    self._replace_client_vehicle(client, merged)
                next_vehicle = merged
            client.vehicles = self._dedupe_client_vehicles(client.vehicles)
            next_vehicle_key = self._client_vehicle_identity_key(
                next_vehicle.vehicle,
                next_vehicle.vin,
                next_vehicle.license_plate,
                next_vehicle.year,
            )
            if next_vehicle_key and next_vehicle_key in set(client.deleted_vehicle_keys or []):
                client.deleted_vehicle_keys = [
                    key for key in client.deleted_vehicle_keys if key != next_vehicle_key
                ]
                changed = True
            sync_linked_cards = self._validated_optional_bool(
                payload, "sync_linked_cards", default=True
            )
            synced_card_ids: list[str] = []
            if sync_linked_cards and next_vehicle.id:
                for linked_card in cards:
                    if (
                        linked_card.client_id == client.id
                        and linked_card.client_vehicle_id == next_vehicle.id
                    ):
                        if self._sync_card_vehicle_fields(
                            linked_card, next_vehicle, overwrite=True
                        ):
                            self._touch_card(linked_card, actor_name)
                            synced_card_ids.append(linked_card.id)
                            self._append_event(
                                events,
                                actor_name=actor_name,
                                source=source,
                                action="card_client_vehicle_synced",
                                message=f"{actor_name} обновил паспорт автомобиля из профиля клиента",
                                card_id=linked_card.id,
                                details={
                                    "client_id": client.id,
                                    "client_vehicle_id": next_vehicle.id,
                                },
                            )
            if changed or synced_card_ids:
                client.updated_at = utc_now_iso()
                if changed:
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="client_vehicle_created" if created else "client_vehicle_updated",
                        message=(
                            f"{actor_name} добавил автомобиль клиента"
                            if created
                            else f"{actor_name} обновил автомобиль клиента"
                        ),
                        card_id=card.id if card is not None else None,
                        details={"client_id": client.id, "client_vehicle_id": next_vehicle.id},
                    )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=clients,
                    events=events,
                )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "vehicle": next_vehicle.to_dict(),
                "meta": {
                    "changed": changed or bool(synced_card_ids),
                    "created": created,
                    "synced_card_ids": synced_card_ids,
                },
            }

    def delete_client_vehicle(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            cards = bundle["cards"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id"))
            vehicle = self._find_client_vehicle(
                client, payload.get("client_vehicle_id") or payload.get("vehicle_id")
            )
            unlink_cards = self._validated_optional_bool(
                payload, "unlink_cards", default=True
            )
            linked_cards = [
                card
                for card in cards
                if card.client_id == client.id and card.client_vehicle_id == vehicle.id
            ]
            if linked_cards and not unlink_cards:
                self._fail(
                    "client_vehicle_has_linked_cards",
                    "Нельзя удалить автомобиль клиента, пока к нему привязаны карточки.",
                    status_code=409,
                    details={
                        "client_id": client.id,
                        "client_vehicle_id": vehicle.id,
                        "linked_card_ids": [card.id for card in linked_cards],
                    },
                )
            client.vehicles = [
                candidate for candidate in client.vehicles if candidate.id != vehicle.id
            ]
            deleted_key = self._client_vehicle_identity_key(
                vehicle.vehicle,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
            )
            if deleted_key and deleted_key not in set(client.deleted_vehicle_keys or []):
                client.deleted_vehicle_keys.append(deleted_key)
            unlinked_card_ids: list[str] = []
            for card in linked_cards:
                card.client_vehicle_id = ""
                self._touch_card(card, actor_name)
                unlinked_card_ids.append(card.id)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_vehicle_unlinked",
                    message=f"{actor_name} убрал связь карточки с удалённым автомобилем клиента",
                    card_id=card.id,
                    details={"client_id": client.id, "client_vehicle_id": vehicle.id},
                )
            client.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_vehicle_deleted",
                message=f"{actor_name} удалил автомобиль клиента",
                card_id=None,
                details={
                    "client_id": client.id,
                    "client_vehicle_id": vehicle.id,
                    "unlinked_card_ids": unlinked_card_ids,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "vehicle": vehicle.to_dict(),
                "meta": {
                    "deleted": True,
                    "unlinked_card_ids": unlinked_card_ids,
                    "linked_cards_unlinked": len(unlinked_card_ids),
                },
            }

    def unlink_card_from_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            previous_client_id = card.client_id
            previous_client_vehicle_id = card.client_vehicle_id
            changed = bool(previous_client_id)
            if changed:
                card.client_id = ""
                card.client_vehicle_id = ""
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_unlinked",
                    message=f"{actor_name} убрал связь карточки с клиентом",
                    card_id=card.id,
                    details={
                        "previous_client_id": previous_client_id,
                        "previous_client_vehicle_id": previous_client_vehicle_id,
                    },
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=bundle["clients"],
                    events=events,
                )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "meta": {
                    "changed": changed,
                    "previous_client_id": previous_client_id,
                    "previous_client_vehicle_id": previous_client_vehicle_id,
                },
            }

    def delete_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id") or payload.get("id"))
            allow_linked = self._validated_optional_bool(payload, "allow_linked", default=False)
            linked_cards = [card for card in cards if card.client_id == client.id]
            if linked_cards and not allow_linked:
                self._fail(
                    "client_has_linked_cards",
                    "Нельзя удалить клиента, пока к нему привязаны карточки.",
                    status_code=409,
                    details={
                        "client_id": client.id,
                        "linked_card_ids": [card.id for card in linked_cards],
                    },
                )
            if linked_cards:
                for card in linked_cards:
                    card.client_id = ""
                    card.client_vehicle_id = ""
                    self._touch_card(card, actor_name)
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="card_client_unlinked",
                        message=f"{actor_name} убрал связь карточки с удаляемым клиентом",
                        card_id=card.id,
                        details={"previous_client_id": client.id},
                    )
            clients = [item for item in clients if item.id != client.id]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_deleted",
                message=f"{actor_name} удалил клиента",
                card_id=None,
                details={"client_id": client.id, "client_name": client.name()},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "meta": {
                    "deleted": True,
                    "allow_linked": allow_linked,
                    "unlinked_cards": len(linked_cards),
                },
            }

    def suggest_clients_for_card(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=8, maximum=30)
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            query = normalize_text(payload.get("query"), default="", limit=240)
            if not query:
                query = " ".join(
                    part
                    for part in (
                        card.vehicle_profile.customer_name,
                        card.vehicle_profile.customer_phone,
                        card.repair_order.client,
                        card.repair_order.phone,
                        card.vehicle_profile.vin,
                        card.repair_order.vin,
                        card.repair_order.license_plate,
                        card.vehicle_profile.registration_plate,
                    )
                    if part
                )
            if not query.strip():
                return {
                    "card": self._serialize_card(
                        card,
                        bundle["events"],
                        column_labels=self._column_labels(bundle["columns"]),
                        compact=True,
                    ),
                    "clients": [],
                    "meta": {"query": query, "total": 0, "returned": 0},
                }
            matches = self._rank_client_matches(bundle["clients"], query)
            selected = [client for _, client in matches[:limit]]
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=self._column_labels(bundle["columns"]),
                    compact=True,
                ),
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=True,
                        compact=True,
                        query=query,
                    )
                    for client in selected
                ],
                "meta": {"query": query, "total": len(matches), "returned": len(selected)},
            }

    def get_card_context(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            event_limit = self._validated_limit(payload.get("event_limit"), default=20, maximum=200)
            include_repair_order_text = self._validated_optional_bool(
                payload, "include_repair_order_text", default=True
            )
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
            removed_attachments = [
                attachment.to_dict() for attachment in card.attachments if attachment.removed
            ]
            viewer_username = normalize_actor_name(payload.get("actor_name"), default="") or None
            has_repair_order = self._card_has_repair_order(card)
            linked_client = self._find_client_or_none(bundle["clients"], card.client_id)
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
                "client": (
                    self._serialize_client(linked_client, bundle["cards"], include_stats=True)
                    if linked_client is not None
                    else None
                ),
                "client_suggestions": [
                    self._serialize_client(
                        client, bundle["cards"], include_stats=True, compact=True
                    )
                    for _, client in self._rank_client_matches(
                        bundle["clients"],
                        " ".join(
                            part
                            for part in (
                                card.vehicle_profile.customer_name,
                                card.vehicle_profile.customer_phone,
                                card.repair_order.client,
                                card.repair_order.phone,
                            )
                            if part
                        ),
                    )[:5]
                ]
                if any(
                    part
                    for part in (
                        card.vehicle_profile.customer_name,
                        card.vehicle_profile.customer_phone,
                        card.repair_order.client,
                        card.repair_order.phone,
                    )
                )
                else [],
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
                    "events_total": len(card_events),
                    "events_returned": len(events),
                    "has_more_events": len(card_events) > len(events),
                    "attachments_total": len(active_attachments),
                    "removed_attachments_total": len(removed_attachments),
                    "has_repair_order": has_repair_order,
                },
            }

    def get_ai_chat_knowledge(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        prompt = str(payload.get("prompt") or "").strip()
        context = payload.get("context") if isinstance(payload.get("context"), dict) else None
        prompt_profile = (
            payload.get("prompt_profile")
            if isinstance(payload.get("prompt_profile"), dict)
            else None
        )
        return build_ai_chat_knowledge_packet(
            prompt=prompt,
            context=context,
            prompt_profile=prompt_profile,
        )

    def get_card(self, payload: dict) -> dict:
        return self._snapshot_service.get_card(payload)

    def get_card_log(self, payload: dict) -> dict:
        return self._snapshot_service.get_card_log(payload)

    def get_repair_order(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_repair_order_state_supported(card)
            actor_name, source = self._audit_identity(payload, default_source="ui")
            created = self._ensure_repair_order_exists(
                card,
                cards,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                settings=bundle["settings"],
            )
            synced_fields = self._fill_missing_repair_order_fields_from_card(card)
            if synced_fields:
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="repair_order_vehicle_fields_synced",
                    message=f"{actor_name} дополнил заказ-наряд данными паспорта автомобиля",
                    card_id=card.id,
                    details={"fields": synced_fields},
                )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if created or numbering_changed or synced_fields:
                if synced_fields:
                    self._touch_card(card, actor_name)
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
                "card_id": card.id,
                "heading": card.heading(),
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "repair_order": card.repair_order.to_dict(),
                "meta": {
                    "has_any_data": self._card_has_repair_order(card),
                    "created": created,
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
            next_payload = self._merged_repair_order_storage(
                card.repair_order.to_storage_dict(), patch
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
                settings=bundle["settings"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
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
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
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
                settings=bundle["settings"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
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
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
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
                settings=bundle["settings"],
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
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
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "changed": changed or numbering_changed,
                    "rows": len(card.repair_order.materials),
                },
            }

    def set_repair_order_status(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            status = self._validated_repair_order_status(
                payload.get("status"), default=REPAIR_ORDER_STATUS_OPEN
            )
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_repair_order_state_supported(card)
            self._ensure_not_archived(card)
            self._ensure_repair_order_can_change_status(card, status)
            actor_name, source = self._audit_identity(payload, default_source="api")
            next_payload = card.repair_order.to_storage_dict()
            next_payload["status"] = status
            next_payload["closed_at"] = (
                self._repair_order_now() if status == REPAIR_ORDER_STATUS_CLOSED else ""
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
                settings=bundle["settings"],
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
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
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
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
                "meta": {
                    "changed": changed or numbering_changed,
                    "status": card.repair_order.status,
                },
            }

    def list_employees(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            month = self._validated_payroll_month(payload.get("month"))
            report = self._build_payroll_report(bundle["cards"], employees, month=month)
            cashboxes = bundle["cashboxes"]
            cash_transactions = bundle["cash_transactions"]
            employee_balances = {
                employee["id"]: self._build_employee_salary_ledger(
                    bundle["cards"],
                    cashboxes,
                    cash_transactions,
                    employee,
                    months=6,
                )["balance_total"]
                for employee in employees
            }
            employees = [
                {
                    **employee,
                    "balance_total": employee_balances.get(employee["id"], "0"),
                }
                for employee in employees
            ]
            return {
                "employees": employees,
                "month": month,
                "summary": report["summary"],
                "detail_rows": report["detail_rows"],
            }

    def save_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            create_mode = normalize_bool(payload.get("create_mode"), default=False)
            if create_mode:
                payload = dict(payload)
                payload.pop("employee_id", None)
                payload.pop("id", None)
            employee_id = (
                ""
                if create_mode
                else normalize_text(payload.get("employee_id"), default="", limit=64)
            )
            existing = next((item for item in employees if item["id"] == employee_id), None)
            created = existing is None
            if created and len(employees) >= EMPLOYEES_MAX_COUNT:
                self._fail(
                    "validation_error",
                    f"Можно сохранить не более {EMPLOYEES_MAX_COUNT} сотрудников.",
                    details={"field": EMPLOYEES_SETTING_KEY, "max_count": EMPLOYEES_MAX_COUNT},
                )
            employee = self._validated_employee_payload(payload, existing=existing)
            next_employees = [item for item in employees if item["id"] != employee["id"]]
            next_employees.append(employee)
            next_employees.sort(
                key=lambda item: (not item["is_active"], item["name"].casefold(), item["id"])
            )
            settings[EMPLOYEES_SETTING_KEY] = next_employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_saved",
                message=f"{actor_name} {'добавил' if created else 'обновил'} сотрудника",
                card_id=None,
                details={"employee_id": employee["id"], "name": employee["name"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"employee": employee, "employees": next_employees, "created": created}

    def toggle_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            target = next((item for item in employees if item["id"] == employee_id), None)
            if target is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            target["is_active"] = not bool(target.get("is_active"))
            target["updated_at"] = utc_now_iso()
            settings[EMPLOYEES_SETTING_KEY] = employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_toggled",
                message=f"{actor_name} изменил активность сотрудника",
                card_id=None,
                details={"employee_id": target["id"], "is_active": target["is_active"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"employee": target, "employees": employees}

    def delete_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "РќСѓР¶РЅРѕ РїРµСЂРµРґР°С‚СЊ employee_id.",
                    details={"field": "employee_id"},
                )
            target = next((item for item in employees if item["id"] == employee_id), None)
            if target is None:
                self._fail(
                    "not_found",
                    "РЎРѕС‚СЂСѓРґРЅРёРє РЅРµ РЅР°Р№РґРµРЅ.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            next_employees = [item for item in employees if item["id"] != employee_id]
            settings[EMPLOYEES_SETTING_KEY] = next_employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_deleted",
                message=f"{actor_name} СѓРґР°Р»РёР» СЃРѕС‚СЂСѓРґРЅРёРєР°",
                card_id=None,
                details={"employee_id": employee_id, "name": target["name"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"deleted": True, "employee_id": employee_id, "employees": next_employees}

    def get_payroll_report(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            month = self._validated_payroll_month(payload.get("month"))
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            report = self._build_payroll_report(
                bundle["cards"], employees, month=month, employee_id=employee_id or None
            )
            return {
                "month": month,
                "summary": report["summary"],
                "detail_rows": report["detail_rows"],
            }

    def _build_employee_salary_ledger(
        self,
        cards: list[Card],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        employee: dict[str, Any],
        *,
        months: int = 6,
        period_only_totals: bool = False,
    ) -> dict[str, Any]:
        period_start = utc_now() - timedelta(days=30 * months)
        employee_id = employee["id"]
        cashboxes_by_id = {cashbox.id: cashbox for cashbox in cashboxes}
        journal_rows: list[dict[str, Any]] = []
        accrual_total = Decimal("0")
        payout_total = Decimal("0")
        advance_total = Decimal("0")

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_at = self._parse_repair_order_datetime(order.closed_at)
            if closed_at is None:
                continue
            is_recent = closed_at >= period_start
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if row.executor_id != employee_id:
                    continue
                amount = self._parse_payroll_decimal(row.salary_amount)
                if period_only_totals:
                    if not is_recent:
                        continue
                    accrual_total += amount
                else:
                    accrual_total += amount
                    if not is_recent:
                        continue
                journal_rows.append(
                    {
                        "kind": "accrual",
                        "kind_label": "НАЧИСЛЕНИЕ",
                        "created_at": order.closed_at,
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "work_name": row.name,
                        "amount_display": self._format_payroll_decimal(amount),
                        "source_label": "заказ-наряд",
                    }
                )

        for transaction in cash_transactions:
            if transaction.employee_id != employee_id:
                continue
            kind = normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
            if kind not in {"salary_payout", "salary_advance"}:
                continue
            amount = Decimal(transaction.amount_minor) / Decimal("100")
            created_at = parse_datetime(transaction.created_at)
            if period_only_totals and created_at is not None and created_at < period_start:
                continue
            if kind == "salary_payout":
                payout_total += amount
                kind_label = "ВЫПЛАТА"
            else:
                advance_total += amount
                kind_label = "АВАНС"
            if created_at is not None and created_at < period_start:
                continue
            cashbox_name = (
                cashboxes_by_id.get(transaction.cashbox_id).name
                if cashboxes_by_id.get(transaction.cashbox_id)
                else "касса"
            )
            journal_rows.append(
                {
                    "kind": kind,
                    "kind_label": kind_label,
                    "created_at": transaction.created_at,
                    "closed_at": "",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "work_name": "",
                    "amount_display": format_money_minor(transaction.amount_minor),
                    "source_label": cashbox_name,
                    "cashbox_id": transaction.cashbox_id,
                    "note": transaction.note,
                }
            )

        journal_rows.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item["created_at"]),
                item["kind_label"],
                item.get("repair_order_number") or "",
                item.get("work_name") or "",
            ),
            reverse=True,
        )
        balance_total = accrual_total - payout_total - advance_total
        return {
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "position": employee["position"],
            "period_months": months,
            "period_start": period_start.isoformat(),
            "balance_total": self._format_payroll_decimal(balance_total),
            "balance_display": self._format_payroll_decimal(balance_total),
            "accrued_total": self._format_payroll_decimal(accrual_total),
            "accrued_total_display": self._format_payroll_decimal(accrual_total),
            "payout_total": self._format_payroll_decimal(payout_total),
            "payout_total_display": self._format_payroll_decimal(payout_total),
            "advance_total": self._format_payroll_decimal(advance_total),
            "advance_total_display": self._format_payroll_decimal(advance_total),
            "journal_rows": journal_rows,
            "journal_total": len(journal_rows),
        }

    def get_employee_salary_ledger(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            months = self._validated_limit(payload.get("months"), default=6, maximum=12)
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            ledger = self._build_employee_salary_ledger(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                months=months,
            )
            return ledger

    def _employee_salary_report_text(self, ledger: dict[str, Any]) -> str:
        period_months = ledger.get("period_months", 2)
        lines = [
            "ОТЧЕТ ПО ЗАРПЛАТЕ",
            f"СОТРУДНИК: {ledger.get('employee_name') or 'СОТРУДНИК'}",
            f"ДОЛЖНОСТЬ: {ledger.get('position') or '-'}",
            f"ПЕРИОД: ПОСЛЕДНИЕ {period_months} МЕС.",
            f"НАЧИСЛЕНО: {ledger.get('accrued_total_display') or '0'}",
            f"ВЫПЛАЧЕНО: {ledger.get('payout_total_display') or '0'}",
            f"АВАНСЫ: {ledger.get('advance_total_display') or '0'}",
            f"БАЛАНС: {ledger.get('balance_display') or '0'}",
            f"СТРОК: {ledger.get('journal_total') or 0}",
            "",
        ]
        journal_rows = ledger.get("journal_rows") or []
        if not journal_rows:
            lines.append("ЗА ВЫБРАННЫЙ ПЕРИОД ДВИЖЕНИЙ НЕТ.")
            return "\n".join(lines).strip()

        current_day = ""
        for row in journal_rows:
            created_at = parse_datetime(row.get("created_at"))
            day_label = created_at.strftime("%d.%m.%Y") if created_at is not None else "—"
            if day_label != current_day:
                if current_day:
                    lines.append("")
                lines.append(day_label)
                current_day = day_label
            time_label = created_at.strftime("%H:%M") if created_at is not None else "—"
            kind_label = normalize_text(row.get("kind_label"), default="ДВИЖЕНИЕ", limit=32)
            source_label = normalize_text(row.get("source_label"), default="—", limit=80)
            lines.append(
                f"  {time_label} | {kind_label} | {row.get('repair_order_number') or '—'} | "
                f"{row.get('vehicle') or '—'} | {row.get('work_name') or '—'} | "
                f"{row.get('amount_display') or '0'} | {source_label}"
            )
            note = normalize_text(row.get("note"), default="", limit=240)
            if note:
                lines.append(f"    {note}")
        return "\n".join(lines).strip()

    def get_employee_salary_report(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            months = self._validated_limit(payload.get("months"), default=2, maximum=2)
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            ledger = self._build_employee_salary_ledger(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                months=months,
                period_only_totals=True,
            )
            return {
                "employee_id": employee_id,
                "employee_name": employee["name"],
                "file_name": normalize_file_name(
                    f"employee-salary-report-{employee['name']}-{months}-months.txt"
                ),
                "text": self._employee_salary_report_text(ledger),
                "meta": {
                    "months": months,
                    "period_start": ledger["period_start"],
                    "journal_total": ledger["journal_total"],
                    "accrued_total": ledger["accrued_total"],
                    "payout_total": ledger["payout_total"],
                    "advance_total": ledger["advance_total"],
                    "balance_total": ledger["balance_total"],
                },
            }

    def get_repair_order_text_download(self, card_id: str) -> tuple[Path, str]:
        with self._lock:
            bundle = self._store.read_bundle()
            if self._synchronize_repair_order_numbers(bundle["cards"]):
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            card = self._find_card(bundle["cards"], card_id)
            self._ensure_repair_order_state_supported(card)
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
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            self._ensure_repair_order_state_supported(card)
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
            self._ensure_repair_order_state_supported(card)
            preview_card = self._print_module_card(card, payload)
            try:
                return self._print_module.workspace(
                    preview_card, repair_order=preview_card.repair_order
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def get_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            try:
                return self._print_module.get_inspection_sheet_form(
                    preview_card, repair_order=preview_card.repair_order
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def save_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            session = (
                payload.get("_operator_session")
                if isinstance(payload.get("_operator_session"), dict)
                else {}
            )
            actor_name = normalize_actor_name(
                (session.get("username") if session else "") or payload.get("actor_name")
            )
            try:
                return self._print_module.save_inspection_sheet_form(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    form_data=payload.get("form_data")
                    if isinstance(payload.get("form_data"), dict)
                    else {},
                    filled_by=actor_name,
                    source=str(payload.get("form_source", "manual") or "manual"),
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def autofill_inspection_sheet_form(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        session = (
            payload.get("_operator_session")
            if isinstance(payload.get("_operator_session"), dict)
            else {}
        )
        actor_name = normalize_actor_name(
            (session.get("username") if session else "") or payload.get("actor_name")
        )
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
            "complaint_summary": self._inspection_sheet_text(
                result.get("complaint_summary"), multiline=True
            ),
            "findings": self._inspection_sheet_text(result.get("findings"), multiline=True),
            "recommendations": self._inspection_sheet_text(
                result.get("recommendations"), multiline=True
            ),
            "planned_works": self._inspection_sheet_text(
                result.get("planned_works"), multiline=True
            ),
            "planned_materials": self._inspection_sheet_text(
                result.get("planned_materials"), multiline=True
            ),
            "planned_work_rows": self._inspection_sheet_table_rows(
                result.get("planned_work_rows"),
                fallback_text=result.get("planned_works"),
            ),
            "planned_material_rows": self._inspection_sheet_table_rows(
                result.get("planned_material_rows"),
                fallback_text=result.get("planned_materials"),
            ),
            "master_comment": self._inspection_sheet_text(
                result.get("master_comment"), multiline=True
            ),
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
            linked_client = self._print_module_client(bundle, preview_card)
            try:
                return self._print_module.preview_documents(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    client=linked_client,
                    selected_document_ids=payload.get("selected_document_ids"),
                    active_document_id=str(payload.get("active_document_id", "") or ""),
                    selected_template_ids=payload.get("selected_template_ids")
                    if isinstance(payload.get("selected_template_ids"), dict)
                    else {},
                    template_overrides=payload.get("template_overrides")
                    if isinstance(payload.get("template_overrides"), dict)
                    else {},
                    print_settings=payload.get("print_settings")
                    if isinstance(payload.get("print_settings"), dict)
                    else {},
                )
            except PrintModuleError as exc:
                self._fail(exc.code, exc.message, status_code=exc.status_code, details=exc.details)

    def export_repair_order_print_pdf(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            preview_card = self._print_module_card(card, payload)
            linked_client = self._print_module_client(bundle, preview_card)
            try:
                pdf_bytes, file_name, meta = self._print_module.export_documents_pdf(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    client=linked_client,
                    selected_document_ids=payload.get("selected_document_ids"),
                    selected_template_ids=payload.get("selected_template_ids")
                    if isinstance(payload.get("selected_template_ids"), dict)
                    else {},
                    template_overrides=payload.get("template_overrides")
                    if isinstance(payload.get("template_overrides"), dict)
                    else {},
                    print_settings=payload.get("print_settings")
                    if isinstance(payload.get("print_settings"), dict)
                    else {},
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
            linked_client = self._print_module_client(bundle, preview_card)
            try:
                result = self._print_module.print_documents(
                    preview_card,
                    repair_order=preview_card.repair_order,
                    client=linked_client,
                    selected_document_ids=payload.get("selected_document_ids"),
                    selected_template_ids=payload.get("selected_template_ids")
                    if isinstance(payload.get("selected_template_ids"), dict)
                    else {},
                    template_overrides=payload.get("template_overrides")
                    if isinstance(payload.get("template_overrides"), dict)
                    else {},
                    print_settings=payload.get("print_settings")
                    if isinstance(payload.get("print_settings"), dict)
                    else {},
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
                return self._print_module.delete_template(
                    template_id=str(payload.get("template_id", "") or "")
                )
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
                settings_payload = (
                    payload.get("print_settings")
                    if isinstance(payload.get("print_settings"), dict)
                    else payload
                )
                return self._print_module.save_settings(
                    settings_payload if isinstance(settings_payload, dict) else {}
                )
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

    def _print_module_client(
        self, bundle: dict[str, Any], preview_card: Card
    ) -> ClientProfile | None:
        linked_client = self._find_client_or_none(bundle["clients"], preview_card.client_id)
        if linked_client is not None:
            return linked_client
        query = " ".join(
            part
            for part in (
                preview_card.repair_order.client,
                preview_card.repair_order.phone,
                preview_card.vehicle_profile.customer_name,
                preview_card.vehicle_profile.customer_phone,
            )
            if part
        ).strip()
        if not query:
            return None
        matches = self._rank_client_matches(bundle["clients"], query, bundle["cards"])
        if not matches:
            return None
        score, candidate = matches[0]
        if score < 8 or candidate.client_type not in {"ip", "ooo", "company"}:
            return None
        return candidate

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

    def _inspection_sheet_table_rows(
        self, value: Any, *, fallback_text: Any = ""
    ) -> list[dict[str, str]]:
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
        return [
            {"name": line, "quantity": ""} for line in self._inspection_sheet_lines(fallback_text)
        ]

    def autofill_vehicle_data(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            vehicle_profile_payload = payload.get("vehicle_profile")
            if vehicle_profile_payload is None:
                vehicle_profile_payload = payload.get("existing_profile")
            vehicle_label = normalize_text(
                payload.get("vehicle"), default="", limit=CARD_VEHICLE_LIMIT
            )
            if not vehicle_label:
                vehicle_label = normalize_text(
                    payload.get("explicit_vehicle"), default="", limit=CARD_VEHICLE_LIMIT
                )
            explicit_title = normalize_text(
                payload.get("title"), default="", limit=CARD_TITLE_LIMIT
            )
            if not explicit_title:
                explicit_title = normalize_text(
                    payload.get("explicit_title"), default="", limit=CARD_TITLE_LIMIT
                )
            explicit_description = normalize_text(
                payload.get("description"), default="", limit=CARD_DESCRIPTION_LIMIT
            )
            if not explicit_description:
                explicit_description = normalize_text(
                    payload.get("explicit_description"), default="", limit=CARD_DESCRIPTION_LIMIT
                )
            raw_text = normalize_text(payload.get("raw_text"), default="", limit=6000)
            analysis_parts: list[str] = []
            for part in (vehicle_label, explicit_title, explicit_description, raw_text):
                cleaned = normalize_text(part, default="", limit=6000)
                if cleaned and cleaned not in analysis_parts:
                    analysis_parts.append(cleaned)
            result = self._vehicle_profiles.autofill_preview(
                raw_text="\n\n".join(analysis_parts),
                image_base64=normalize_text(
                    payload.get("image_base64"), default="", limit=16_000_000
                )
                or None,
                image_filename=normalize_text(payload.get("image_filename"), default="", limit=240),
                image_mime_type=normalize_text(
                    payload.get("image_mime_type"), default="", limit=120
                ),
                existing_profile=vehicle_profile_payload,
                explicit_vehicle=vehicle_label,
                explicit_title=explicit_title,
                explicit_description=explicit_description,
            )
            return result.to_dict()

    def update_card(self, payload: dict) -> dict:
        with self._lock:
            updated_fields = {
                "vehicle",
                "title",
                "description",
                "deadline",
                "tags",
                "vehicle_profile",
                "repair_order",
            } & set(payload.keys())
            if not updated_fields:
                self._fail(
                    "validation_error",
                    "Для обновления карточки нужно передать хотя бы одно поле: vehicle, title, description, deadline, tags, vehicle_profile или repair_order.",
                    details={
                        "fields": [
                            "vehicle",
                            "title",
                            "description",
                            "deadline",
                            "tags",
                            "vehicle_profile",
                            "repair_order",
                        ]
                    },
                )
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            ready_column_id, ready_column_changed = self._ensure_ready_column_for_bundle(
                bundle, actor_name=actor_name, source=source
            )

            changed = False
            changed_fields: list[str] = []
            if "vehicle" in payload:
                vehicle_changed = self._update_vehicle(
                    card, payload.get("vehicle", ""), events, actor_name, source
                )
                changed = vehicle_changed or changed
                if vehicle_changed:
                    changed_fields.append("vehicle")
            if "vehicle_profile" in payload:
                profile_changed = self._update_vehicle_profile(
                    card, payload.get("vehicle_profile"), events, actor_name, source
                )
                changed = profile_changed or changed
                if profile_changed:
                    changed_fields.append("vehicle_profile")
            if "title" in payload:
                title_changed = self._update_title(
                    card, payload.get("title"), events, actor_name, source
                )
                changed = title_changed or changed
                if title_changed:
                    changed_fields.append("title")
            if "description" in payload:
                description_changed = self._update_description(
                    card, payload.get("description", ""), events, actor_name, source
                )
                changed = description_changed or changed
                if description_changed:
                    changed_fields.append("description")
            if "deadline" in payload:
                deadline_changed = self._update_deadline(
                    card, payload.get("deadline"), events, actor_name, source
                )
                changed = deadline_changed or changed
                if deadline_changed:
                    changed_fields.append("deadline")
            if "tags" in payload:
                tags_changed = self._update_tags(
                    card, payload.get("tags"), events, actor_name, source
                )
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
                    settings=bundle["settings"],
                )
                changed = repair_order_changed or changed
                if repair_order_changed:
                    changed_fields.append("repair_order")
            ready_state_changed = False
            if card.column == ready_column_id:
                ready_state_changed, _ready_warnings = self._apply_ready_column_side_effects(
                    card,
                    cards,
                    events,
                    actor_name,
                    source,
                    before_column=ready_column_id,
                    after_column=ready_column_id,
                    ready_column_id=ready_column_id,
                    bundle=bundle,
                )
                changed = ready_state_changed or changed
                if ready_state_changed and "ready_state" not in changed_fields:
                    changed_fields.append("ready_state")
            linked_vehicle_changed = False
            if changed and {"vehicle", "vehicle_profile", "repair_order"}.intersection(
                changed_fields
            ):
                linked_vehicle_changed = self._sync_linked_client_vehicle_from_card(
                    bundle["clients"], card
                )
                if linked_vehicle_changed and "client_vehicle" not in changed_fields:
                    changed_fields.append("client_vehicle")
            repair_order_profile_fields = []
            if changed and {"vehicle", "vehicle_profile"}.intersection(changed_fields):
                repair_order_profile_fields = self._fill_missing_repair_order_fields_from_card(
                    card
                )
                if repair_order_profile_fields:
                    changed = True
                    changed_fields.append("repair_order_profile_fields")
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="repair_order_vehicle_fields_synced",
                        message=f"{actor_name} дополнил заказ-наряд данными паспорта автомобиля",
                        card_id=card.id,
                        details={"fields": repair_order_profile_fields},
                    )

            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed or ready_column_changed or linked_vehicle_changed:
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=bundle["clients"],
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=events,
                )
            self._logger.info(
                "update_card id=%s changed=%s actor=%s source=%s",
                card.id,
                changed or numbering_changed or ready_column_changed,
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
                    "changed": changed
                    or numbering_changed
                    or ready_column_changed
                    or linked_vehicle_changed,
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
            next_order, autofill_report = self._autofill_repair_order(
                card, cards, overwrite=overwrite
            )
            changed = card.repair_order.to_storage_dict() != next_order.to_storage_dict()
            if changed:
                card.repair_order = next_order
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
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
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                ),
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
            self._logger.info(
                "set_card_indicator id=%s indicator=%s actor=%s source=%s",
                card.id,
                indicator,
                actor_name,
                source,
            )
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(bundle["columns"])
                )
            }

    def move_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            ready_column_id, ready_column_changed = self._ensure_ready_column_for_bundle(
                bundle, actor_name=actor_name, source=source
            )
            next_column = self._validated_column(payload.get("column", card.column), columns)
            before_card_id = (
                normalize_text(payload.get("before_card_id"), default="", limit=128) or None
            )
            column_labels = self._column_labels(columns)
            move_meta = self._reposition_card(
                cards, card, target_column=next_column, before_card_id=before_card_id
            )
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
            ready_state_changed, ready_warnings = self._apply_ready_column_side_effects(
                card,
                cards,
                events,
                actor_name,
                source,
                before_column=str(move_meta["before_column"] or ""),
                after_column=str(move_meta["after_column"] or ""),
                ready_column_id=ready_column_id,
                bundle=bundle,
            )
            if ready_state_changed and not changed:
                self._touch_card(card, actor_name)
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or ready_state_changed or ready_column_changed or numbering_changed:
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
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
                    "changed": changed or ready_state_changed or ready_column_changed,
                    "moved": changed,
                    "ready_state_changed": ready_state_changed,
                    "warnings": ready_warnings,
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
            card.ai_autofill_active = False
            card.ai_autofill_until = ""
            card.ai_next_run_at = ""
            self._append_card_ai_log(
                card,
                level="DONE",
                message="Автосопровождение остановлено: карточка отправлена в архив.",
            )
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
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(bundle["columns"])
                )
            }

    def bulk_move_cards(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            ready_column_id, ready_column_changed = self._ensure_ready_column_for_bundle(
                bundle, actor_name=actor_name, source=source
            )
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
            warnings: list[dict] = []
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
                    ready_state_changed, ready_warnings = self._apply_ready_column_side_effects(
                        card,
                        cards,
                        events,
                        actor_name,
                        source,
                        before_column=previous_column,
                        after_column=card.column,
                        ready_column_id=ready_column_id,
                        bundle=bundle,
                    )
                    if ready_state_changed and not changed:
                        self._touch_card(card, actor_name)
                    if ready_state_changed:
                        changed = True
                        changed_any = True
                    warnings.extend({"card_id": card.id, **warning} for warning in ready_warnings)
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

            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed_any or ready_column_changed or numbering_changed:
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
                    "warnings": warnings,
                },
            }

    def mark_card_ready(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            ready_column_id, ready_column_changed = self._ensure_ready_column_for_bundle(
                bundle, actor_name=actor_name, source=source
            )
            if ready_column_changed:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            next_payload = dict(payload)
            next_payload["column"] = ready_column_id
            next_payload["actor_name"] = actor_name
            next_payload["source"] = source
        return self.move_card(next_payload)

    def restore_card(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            if not card.archived:
                self._fail(
                    "validation_error",
                    "Карточка уже находится на доске.",
                    details={"card_id": card.id},
                )
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
            return {
                "card": self._serialize_card(
                    card, events, column_labels=self._column_labels(columns)
                )
            }

    def list_columns(self, payload: dict | None = None) -> dict:
        return self._column_service.list_columns(payload)

    def create_column(self, payload: dict) -> dict:
        return self._column_service.create_column(payload)

    def rename_column(self, payload: dict) -> dict:
        return self._column_service.rename_column(payload)

    def move_column(self, payload: dict) -> dict:
        return self._column_service.move_column(payload)

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
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                stickies=stickies,
                events=events,
            )
            self._logger.info(
                "create_sticky id=%s actor=%s source=%s", sticky.id, actor_name, source
            )
            return {
                "sticky": self._serialize_sticky(sticky),
                "stickies": [self._serialize_sticky(item) for item in stickies],
            }

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
                    sticky.deadline_timestamp = (
                        utc_now() + timedelta(seconds=next_deadline_seconds)
                    ).isoformat()
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
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    stickies=stickies,
                    events=events,
                )
            self._logger.info(
                "update_sticky id=%s changed=%s actor=%s source=%s",
                sticky.id,
                changed,
                actor_name,
                source,
            )
            return {
                "sticky": self._serialize_sticky(sticky),
                "stickies": [self._serialize_sticky(item) for item in stickies],
            }

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
                    details={
                        "sticky_id": sticky.id,
                        "before": before,
                        "after": {"x": sticky.x, "y": sticky.y},
                    },
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    stickies=stickies,
                    events=events,
                )
            self._logger.info(
                "move_sticky id=%s x=%s y=%s actor=%s source=%s",
                sticky.id,
                sticky.x,
                sticky.y,
                actor_name,
                source,
            )
            return {
                "sticky": self._serialize_sticky(sticky),
                "stickies": [self._serialize_sticky(item) for item in stickies],
            }

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
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                stickies=stickies,
                events=events,
            )
            self._logger.info(
                "delete_sticky id=%s actor=%s source=%s", sticky.id, actor_name, source
            )
            return {
                "deleted": True,
                "sticky_id": sticky.id,
                "stickies": [self._serialize_sticky(item) for item in stickies],
            }

    def add_card_attachment(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            actor_name, source = self._audit_identity(payload, default_source="api")
            file_bytes = self._validated_attachment_content(payload.get("content_base64"))
            file_name, mime_type, stored_extension = self._validated_attachment_upload(
                payload.get("file_name"),
                payload.get("mime_type"),
                file_bytes,
            )
            attachment_id = str(uuid.uuid4())
            stored_name = f"{attachment_id}{stored_extension}"
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
            self._logger.info(
                "add_attachment card_id=%s attachment_id=%s actor=%s",
                card.id,
                attachment.id,
                actor_name,
            )
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
            self._logger.info(
                "remove_attachment card_id=%s attachment_id=%s actor=%s",
                card.id,
                attachment.id,
                actor_name,
            )
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
            attachment_path, repaired = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            return attachment_path, attachment

    def list_card_attachments(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        include_removed = normalize_bool(payload.get("include_removed"), default=False)
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            attachments = card.attachments if include_removed else card.active_attachments()
            items = [
                self._attachment_agent_dict(card.id, attachment)
                for attachment in attachments
                if include_removed or not attachment.removed
            ]
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=self._column_labels(bundle["columns"]),
                ),
                "attachments": items,
                "meta": {
                    "card_id": card.id,
                    "include_removed": include_removed,
                    "total": len(items),
                    "read_tool": "read_card_attachment",
                    "metadata_tool": "get_card_attachment",
                },
            }

    def get_card_attachment(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            attachment = self._find_attachment(card, payload.get("attachment_id"))
            if attachment.removed:
                self._fail(
                    "not_found",
                    "Файл был удалён из карточки.",
                    status_code=404,
                    details={"attachment_id": attachment.id},
                )
            attachment_path = self._require_attachment_file(card.id, attachment)
            attachment_path, repaired = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=self._column_labels(bundle["columns"]),
                ),
                "attachment": self._attachment_agent_dict(
                    card.id, attachment, attachment_path=attachment_path
                ),
            }

    def read_card_attachment(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        mode = normalize_text(payload.get("mode"), default="preview", limit=24).lower()
        if mode not in {"preview", "text", "base64", "auto"}:
            self._fail(
                "validation_error",
                "Параметр mode должен быть preview, text, base64 или auto.",
                details={"field": "mode"},
            )
        max_chars = self._validated_numeric_limit(
            payload.get("max_chars"),
            field="max_chars",
            default=_ATTACHMENT_READ_DEFAULT_CHARS,
            maximum=_ATTACHMENT_READ_MAX_CHARS,
        )
        max_base64_bytes = self._validated_numeric_limit(
            payload.get("max_base64_bytes"),
            field="max_base64_bytes",
            default=_ATTACHMENT_BASE64_DEFAULT_BYTES,
            maximum=_ATTACHMENT_BASE64_MAX_BYTES,
        )
        include_base64 = normalize_bool(payload.get("include_base64"), default=False)
        if mode == "base64":
            include_base64 = True

        with self._lock:
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            attachment = self._find_attachment(card, payload.get("attachment_id"))
            if attachment.removed:
                self._fail(
                    "not_found",
                    "Файл был удалён из карточки.",
                    status_code=404,
                    details={"attachment_id": attachment.id},
                )
            attachment_path = self._require_attachment_file(card.id, attachment)
            attachment_path, repaired = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    events=bundle["events"],
                )
            content = attachment_path.read_bytes()
            attachment_meta = self._attachment_agent_dict(
                card.id, attachment, attachment_path=attachment_path
            )
            content_payload = self._attachment_content_payload(
                attachment=attachment,
                content=content,
                mode=mode,
                max_chars=max_chars,
                include_base64=include_base64,
                max_base64_bytes=max_base64_bytes,
            )
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=self._column_labels(bundle["columns"]),
                ),
                "attachment": attachment_meta,
                "content": content_payload,
                "meta": {
                    "card_id": card.id,
                    "attachment_id": attachment.id,
                    "mode": mode,
                    "max_chars": max_chars,
                    "include_base64": include_base64,
                    "max_base64_bytes": max_base64_bytes,
                },
            }

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
                self._logger.info(
                    "demo_board_seeded cards=%s columns=%s",
                    len(seeded["cards"]),
                    len(seeded["columns"]),
                )
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

    def _ordered_clients(self, clients: list[ClientProfile]) -> list[ClientProfile]:
        return sorted(clients, key=lambda item: (item.name().casefold(), item.created_at, item.id))

    def _find_client(self, clients: list[ClientProfile], client_id: str | None) -> ClientProfile:
        client = self._find_client_or_none(clients, client_id)
        if client is None:
            self._fail(
                "not_found",
                "Клиент не найден.",
                status_code=404,
                details={"client_id": normalize_text(client_id, default="", limit=128)},
            )
        return client

    def _find_client_or_none(
        self, clients: list[ClientProfile], client_id: str | None
    ) -> ClientProfile | None:
        requested_id = normalize_text(client_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for client in clients:
            if (
                client.id == requested_id
                or short_entity_id(client.id, prefix="CL").upper() == requested_short_id
            ):
                return client
        return None

    def _client_patch_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_payload: dict[str, Any] = {}
        for nested_key in ("client", "patch"):
            nested_payload = payload.get(nested_key)
            if isinstance(nested_payload, dict):
                source_payload.update(nested_payload)
        source_payload.update(
            {key: value for key, value in payload.items() if key not in {"client", "patch"}}
        )
        allowed = {
            "client_type",
            "type",
            "last_name",
            "first_name",
            "middle_name",
            "display_name",
            "phone",
            "phones",
            "email",
            "comment",
            "note",
            "legal_name",
            "short_name",
            "inn",
            "kpp",
            "ogrn",
            "ogrnip",
            "checking_account",
            "account",
            "bank_name",
            "bank",
            "bik",
            "correspondent_account",
            "corr_account",
            "legal_address",
            "actual_address",
            "contact_person",
            "contact_position",
            "vehicles",
        }
        return {key: value for key, value in source_payload.items() if key in allowed}

    def _validated_client_profile(self, payload: dict[str, Any]) -> ClientProfile:
        now_iso = utc_now_iso()
        raw_payload = self._client_patch_payload(payload)
        raw_payload["id"] = normalize_text(
            payload.get("client_id") or payload.get("id") or str(uuid.uuid4()),
            default=str(uuid.uuid4()),
            limit=128,
        )
        raw_payload["created_at"] = now_iso
        raw_payload["updated_at"] = now_iso
        client = ClientProfile.from_dict(raw_payload)
        if not client.name().strip() or client.name() in {"Без имени", "Без названия"}:
            self._fail(
                "validation_error",
                "У клиента должно быть ФИО или название организации.",
                details={"field": "display_name"},
            )
        return client

    def _client_stats(self, client: ClientProfile, cards: list[Card]) -> dict[str, Any]:
        return self._client_stats_from_related(
            client,
            cards,
            self._client_related_cards(client, cards),
        )

    def _client_stats_from_related(
        self,
        client: ClientProfile,
        cards: list[Card],
        related_cards: list[Card],
        *,
        vehicles_total: int | None = None,
    ) -> dict[str, Any]:
        repair_order_cards = [card for card in related_cards if self._card_has_repair_order(card)]
        closed_orders = [
            card
            for card in repair_order_cards
            if card.repair_order.status == REPAIR_ORDER_STATUS_CLOSED
        ]
        active_orders = [
            card
            for card in repair_order_cards
            if card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED
        ]
        last_visit = ""
        for card in sorted(
            repair_order_cards or related_cards,
            key=lambda item: (
                item.repair_order.closed_at
                or item.repair_order.opened_at
                or item.updated_at
                or item.created_at,
                item.id,
            ),
            reverse=True,
        ):
            last_visit = (
                card.repair_order.closed_at
                or card.repair_order.opened_at
                or card.updated_at
                or card.created_at
            )
            break
        return {
            "cards_total": len(related_cards),
            "repair_orders_total": len(repair_order_cards),
            "active_repair_orders": len(active_orders),
            "closed_repair_orders": len(closed_orders),
            "vehicles_total": (
                vehicles_total
                if vehicles_total is not None
                else len(self._client_vehicles(client, cards, related_cards=related_cards))
            ),
            "last_visit": last_visit,
        }

    def _client_related_cards(self, client: ClientProfile, cards: list[Card]) -> list[Card]:
        keys = self._client_match_keys(client)
        related: list[Card] = []
        for card in cards:
            if card.client_id == client.id:
                related.append(card)
                continue
            card_values = self._card_client_values(card)
            if any(key and key in card_values for key in keys):
                related.append(card)
        related.sort(key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True)
        return related

    def _client_related_cards_map(
        self, clients: list[ClientProfile], cards: list[Card]
    ) -> dict[str, list[Card]]:
        if not clients:
            return {}
        clients_by_id = {client.id: client for client in clients}
        client_ids_by_key: dict[str, set[str]] = {}
        for client in clients:
            for key in self._client_match_keys(client):
                if key:
                    client_ids_by_key.setdefault(key, set()).add(client.id)

        related_by_client_id: dict[str, list[Card]] = {client.id: [] for client in clients}
        seen_cards_by_client: dict[str, set[str]] = {client.id: set() for client in clients}

        def add_card(client_id: str, card: Card) -> None:
            if client_id not in clients_by_id:
                return
            seen_cards = seen_cards_by_client.setdefault(client_id, set())
            if card.id in seen_cards:
                return
            seen_cards.add(card.id)
            related_by_client_id.setdefault(client_id, []).append(card)

        for card in cards:
            if card.client_id:
                add_card(card.client_id, card)
            matched_client_ids: set[str] = set()
            for key in self._card_client_values(card):
                matched_client_ids.update(client_ids_by_key.get(key, set()))
            for client_id in matched_client_ids:
                add_card(client_id, card)

        for related_cards in related_by_client_id.values():
            related_cards.sort(
                key=lambda item: (item.updated_at, item.created_at, item.id),
                reverse=True,
            )
        return related_by_client_id

    def _client_related_vehicle_fields_index(
        self, clients: list[ClientProfile], cards: list[Card]
    ) -> dict[str, list[str]]:
        clients_by_id = {client.id: client for client in clients}
        client_ids_by_key: dict[str, set[str]] = {}
        for client in clients:
            for key in self._client_match_keys(client):
                if key:
                    client_ids_by_key.setdefault(key, set()).add(client.id)

        related_fields: dict[str, list[str]] = {}
        seen_cards_by_client: dict[str, set[str]] = {}

        def add_card(client_id: str, card: Card) -> None:
            if client_id not in clients_by_id:
                return
            seen_cards = seen_cards_by_client.setdefault(client_id, set())
            if card.id in seen_cards:
                return
            seen_cards.add(card.id)
            related_fields.setdefault(client_id, []).extend(
                [
                    card.vehicle_display(),
                    card.vehicle_profile.make_display,
                    card.vehicle_profile.model_display,
                    card.vehicle_profile.registration_plate,
                    card.vehicle_profile.vin,
                    card.repair_order.vehicle,
                    card.repair_order.license_plate,
                    card.repair_order.vin,
                    card.repair_order.number,
                ]
            )

        for card in cards:
            if card.client_id:
                add_card(card.client_id, card)
            matched_client_ids: set[str] = set()
            for key in self._card_client_values(card):
                matched_client_ids.update(client_ids_by_key.get(key, set()))
            for client_id in matched_client_ids:
                add_card(client_id, card)

        return related_fields

    def _client_vehicle_identity_key(
        self, vehicle: str = "", vin: str = "", license_plate: str = "", year: str = ""
    ) -> str:
        return "|".join(
            part.casefold()
            for part in (vehicle, vin, license_plate, year)
            if str(part or "").strip()
        )

    def _client_vehicles(
        self,
        client: ClientProfile,
        cards: list[Card],
        *,
        query: str = "",
        related_cards: list[Card] | None = None,
    ) -> list[dict[str, str]]:
        vehicles: list[dict[str, str]] = []
        seen: set[str] = set()
        deleted_keys = set(client.deleted_vehicle_keys or [])
        for stored_vehicle in client.vehicles:
            payload = stored_vehicle.to_dict()
            key = self._client_vehicle_identity_key(
                payload.get("vehicle", ""),
                payload.get("vin", ""),
                payload.get("license_plate", ""),
                payload.get("year", ""),
            )
            if not key or key in seen:
                continue
            seen.add(key)
            vehicles.append({**payload, "card_id": ""})
        for card in related_cards if related_cards is not None else self._client_related_cards(client, cards):
            vehicle = card.vehicle_display() or card.repair_order.vehicle
            vin = card.vehicle_profile.vin or card.repair_order.vin
            plate = card.vehicle_profile.registration_plate or card.repair_order.license_plate
            year = str(card.vehicle_profile.production_year or "")
            key = self._client_vehicle_identity_key(vehicle, vin, plate, year)
            if not key or key in seen or key in deleted_keys:
                continue
            seen.add(key)
            vehicles.append(
                {
                    "id": card.client_vehicle_id,
                    "vehicle": vehicle,
                    "vin": vin,
                    "license_plate": plate,
                    "year": year,
                    "mileage": str(card.vehicle_profile.mileage or card.repair_order.mileage or ""),
                    "body_number": card.vehicle_profile.body_number,
                    "chassis_number": card.vehicle_profile.chassis_number,
                    "engine_code": card.vehicle_profile.engine_code,
                    "engine_model": card.vehicle_profile.engine_model,
                    "gearbox_type": card.vehicle_profile.gearbox_type,
                    "gearbox_model": card.vehicle_profile.gearbox_model,
                    "drivetrain": card.vehicle_profile.drivetrain,
                    "card_id": card.id,
                }
            )
        query_text = self._normalize_search_text(query)
        query_compact = re.sub(r"[\W_]+", "", query_text)
        query_digits = re.sub(r"\D+", "", query)
        if query_text or query_digits:
            def vehicle_score(item: dict[str, str]) -> int:
                score = 0
                values = [
                    item.get("vin", ""),
                    item.get("license_plate", ""),
                    item.get("body_number", ""),
                    item.get("chassis_number", ""),
                    item.get("vehicle", ""),
                    item.get("brand", ""),
                    item.get("model", ""),
                    item.get("year", ""),
                ]
                for value in values:
                    normalized = self._normalize_search_text(value)
                    compact = re.sub(r"[\W_]+", "", normalized)
                    digits = re.sub(r"\D+", "", value)
                    if query_text and normalized == query_text:
                        score += 20
                    elif query_text and query_text in normalized:
                        score += 8
                    if query_compact and compact == query_compact:
                        score += 18
                    elif query_compact and query_compact in compact:
                        score += 6
                    if query_digits and digits and query_digits in digits:
                        score += 10
                return score

            vehicles.sort(key=vehicle_score, reverse=True)
        return vehicles

    def _client_orders(self, client: ClientProfile, cards: list[Card]) -> list[dict[str, Any]]:
        return [
            self._serialize_repair_order_list_item(card)
            for card in self._client_related_cards(client, cards)
            if self._card_has_repair_order(card)
        ]

    def _serialize_client(
        self,
        client: ClientProfile,
        cards: list[Card],
        *,
        include_stats: bool = False,
        compact: bool = False,
        include_vehicle_preview: bool = True,
        query: str = "",
        related_cards: list[Card] | None = None,
    ) -> dict[str, Any]:
        payload = client.to_dict()
        payload["short_id"] = short_entity_id(client.id, prefix="CL")
        preview_vehicles = (
            self._client_vehicles(client, cards, query=query, related_cards=related_cards)
            if include_vehicle_preview
            else [vehicle.to_dict() for vehicle in client.vehicles[:2]]
        )
        payload["vehicles_preview"] = (
            preview_vehicles[:2] if include_vehicle_preview else preview_vehicles
        )
        if include_stats:
            resolved_related_cards = (
                related_cards if related_cards is not None else self._client_related_cards(client, cards)
            )
            payload["stats"] = self._client_stats_from_related(
                client,
                cards,
                resolved_related_cards,
                vehicles_total=len(preview_vehicles) if include_vehicle_preview else None,
            )
        if compact:
            keep = {
                "id",
                "short_id",
                "client_type",
                "type_label",
                "name",
                "full_name",
                "display_name",
                "last_name",
                "first_name",
                "middle_name",
                "phone",
                "phones",
                "email",
                "inn",
                "kpp",
                "ogrn",
                "contact_person",
                "vehicles_preview",
                "vehicles",
                "updated_at",
                "stats",
            }
            return {key: value for key, value in payload.items() if key in keep}
        return payload

    def _client_profile_payload(
        self, client: ClientProfile, cards: list[Card], *, order_limit: int
    ) -> dict[str, Any]:
        related_cards = self._client_related_cards(client, cards)
        orders = [
            self._serialize_repair_order_list_item(card)
            for card in related_cards
            if self._card_has_repair_order(card)
        ]
        vehicles = self._client_vehicles(client, cards, related_cards=related_cards)
        return {
            "client": self._serialize_client(
                client,
                cards,
                include_stats=True,
                related_cards=related_cards,
            ),
            "vehicles": vehicles,
            "repair_orders": orders[:order_limit],
            "meta": {
                "repair_orders_total": len(orders),
                "repair_orders_returned": min(len(orders), order_limit),
                "vehicles_total": len(vehicles),
                "order_limit": order_limit,
            },
        }

    def _client_match_keys(self, client: ClientProfile) -> set[str]:
        values = {
            client.name(),
            client.full_name(),
            client.display_name,
            client.phone,
            client.email,
            client.inn,
            client.contact_person,
            *client.phones,
        }
        for vehicle in client.vehicles:
            values.update(
                {
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    vehicle.vin,
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                }
            )
        keys: set[str] = set()
        for value in values:
            normalized = self._normalize_search_text(value)
            if normalized:
                keys.add(normalized)
            keys.update(self._phone_match_keys(value))
        return keys

    def _client_search_digits_blob(self, client: ClientProfile) -> str:
        values = [client.inn, client.ogrn, client.contact_person]
        for phone in [client.phone, *client.phones]:
            digits = re.sub(r"\D+", "", str(phone or ""))
            if len(digits) < 7:
                continue
            values.append(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                values.extend([last_ten, "7" + last_ten, "8" + last_ten])
        for vehicle in client.vehicles:
            values.extend(
                [
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    vehicle.vin,
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                ]
            )
        return "".join(re.sub(r"\D+", "", str(value or "")) for value in values)

    def _card_client_values(self, card: Card) -> set[str]:
        values = {
            card.vehicle_profile.customer_name,
            card.vehicle_profile.customer_phone,
            card.repair_order.client,
            card.repair_order.phone,
        }
        normalized_values: set[str] = set()
        for value in values:
            normalized = self._normalize_search_text(value)
            if normalized:
                normalized_values.add(normalized)
            normalized_values.update(self._phone_match_keys(value))
        return normalized_values

    def _phone_match_keys(self, value: Any) -> set[str]:
        text = str(value or "")
        candidates = [text]
        candidates.extend(match.group(0) for match in _PHONE_PATTERN.finditer(text))
        candidates.extend(match.group(0) for match in re.finditer(r"\d[\d\s()+-]{7,}\d", text))
        keys: set[str] = set()
        for candidate in candidates:
            digits = re.sub(r"\D+", "", candidate)
            if len(digits) < 7:
                continue
            keys.add(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                keys.add(last_ten)
                keys.add("7" + last_ten)
                keys.add("8" + last_ten)
        return keys

    def _phone_search_variants(self, value: Any) -> set[str]:
        text = str(value or "")
        digits = re.sub(r"\D+", "", text)
        if len(digits) < 3:
            return set()
        variants = {digits}
        if len(digits) >= 4 and digits[0] in "78":
            variants.add(digits[1:])
        if len(digits) >= 10:
            last_ten = digits[-10:]
            variants.add(last_ten)
            if len(last_ten) >= 4 and last_ten[0] in "78":
                variants.add(last_ten[1:])
        return {variant for variant in variants if len(variant) >= 3}

    def _client_search_index_key(self, client: ClientProfile) -> tuple[Any, ...]:
        vehicle_keys = tuple(
            (
                vehicle.vehicle,
                vehicle.brand,
                vehicle.model,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
                vehicle.body_number,
                vehicle.chassis_number,
                vehicle.engine_code,
                vehicle.engine_model,
                vehicle.gearbox_model,
                vehicle.drivetrain,
            )
            for vehicle in client.vehicles
        )
        return (
            client.id,
            client.updated_at,
            client.client_type,
            client.last_name,
            client.first_name,
            client.middle_name,
            client.display_name,
            client.phone,
            tuple(client.phones),
            client.email,
            client.inn,
            client.ogrn,
            client.contact_person,
            vehicle_keys,
        )

    def _client_search_index_for(self, clients: list[ClientProfile]) -> dict[str, dict[str, Any]]:
        signature = tuple(self._client_search_index_key(client) for client in clients)
        if signature == self._client_search_index_signature:
            return self._client_search_index

        index: dict[str, dict[str, Any]] = {}
        for client in clients:
            fields = [
                client.name(),
                client.full_name(),
                client.display_name,
                client.phone,
                " ".join(client.phones),
                client.email,
                client.inn,
                client.ogrn,
                client.contact_person,
            ]
            vehicle_fields: list[str] = []
            for vehicle in client.vehicles:
                vehicle_fields.extend(
                    [
                        vehicle.vehicle,
                        vehicle.brand,
                        vehicle.model,
                        vehicle.vin,
                        vehicle.license_plate,
                        vehicle.year,
                        vehicle.body_number,
                        vehicle.chassis_number,
                        vehicle.engine_code,
                        vehicle.engine_model,
                        vehicle.gearbox_model,
                        vehicle.drivetrain,
                    ]
                )
            searchable = [self._normalize_search_text(value) for value in fields if value]
            vehicle_searchable = [
                self._normalize_search_text(value) for value in vehicle_fields if value
            ]
            compact_searchable = [
                re.sub(r"[\W_]+", "", value)
                for value in [*searchable, *vehicle_searchable]
                if value
            ]
            phone_variants: set[str] = set()
            for phone in [client.phone, *client.phones]:
                phone_variants.update(self._phone_search_variants(phone))
            index[client.id] = {
                "searchable": searchable,
                "vehicle_searchable": vehicle_searchable,
                "compact_searchable": compact_searchable,
                "digits_blob": self._client_search_digits_blob(client),
                "match_keys": self._client_match_keys(client),
                "phone_variants": phone_variants,
            }

        self._client_search_index_signature = signature
        self._client_search_index = index
        return index

    def _rank_client_matches(
        self, clients: list[ClientProfile], query: str, cards: list[Card] | None = None
    ) -> list[tuple[int, ClientProfile]]:
        query = normalize_text(query, default="", limit=500)
        if not query:
            return [(1, client) for client in self._ordered_clients(clients)]
        query_variants = self._search_text_variants(query)
        query_digits = re.sub(r"\D+", "", query)
        query_phone_variants = self._phone_search_variants(query)
        phone_like_query = bool(query_digits) and not re.search(r"[A-Za-zА-Яа-я]", query)
        client_search_index = self._client_search_index_for(clients)

        if phone_like_query:
            ranked: list[tuple[int, ClientProfile]] = []
            fallback_clients: list[ClientProfile] = []
            for client in clients:
                digits_blob = client_search_index.get(client.id, {}).get("digits_blob", "")
                if digits_blob and query_digits in digits_blob:
                    ranked.append((10, client))
                else:
                    fallback_clients.append(client)
            if not ranked and cards and fallback_clients:
                related_fields_by_client_id = self._client_related_vehicle_fields_index(
                    fallback_clients, cards
                )
                for client in fallback_clients:
                    score = 0
                    related_vehicle_fields = related_fields_by_client_id.get(client.id, [])
                    related_searchable = [
                        self._normalize_search_text(value)
                        for value in related_vehicle_fields
                        if value
                    ]
                    related_compact_searchable = [
                        re.sub(r"[\W_]+", "", value) for value in related_searchable if value
                    ]
                    for variant in query_variants:
                        if not variant:
                            continue
                        for value in related_searchable:
                            if value == variant:
                                score += 7
                            elif variant in value:
                                score += 5
                            elif all(part in value for part in variant.split()):
                                score += 3
                        compact_variant = re.sub(r"[\W_]+", "", variant)
                        if compact_variant and any(
                            compact_variant in value for value in related_compact_searchable
                        ):
                            score += 5
                    if score > 0:
                        ranked.append((score, client))
            ranked.sort(
                key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True
            )
            return ranked

        ranked: list[tuple[int, ClientProfile]] = []
        fallback_clients: list[ClientProfile] = []
        cards = cards or []
        for client in clients:
            indexed = client_search_index.get(client.id, {})
            searchable = indexed.get("searchable", [])
            vehicle_searchable = indexed.get("vehicle_searchable", [])
            compact_searchable = indexed.get("compact_searchable", [])
            score = 0
            for variant in query_variants:
                if not variant:
                    continue
                for value in searchable:
                    if value == variant:
                        score += 8
                    elif variant in value:
                        score += 4
                    elif all(part in value for part in variant.split()):
                        score += 2
                for value in vehicle_searchable:
                    if value == variant:
                        score += 7
                    elif variant in value:
                        score += 5
                    elif all(part in value for part in variant.split()):
                        score += 3
                compact_variant = re.sub(r"[\W_]+", "", variant)
                if compact_variant and any(
                    compact_variant in value for value in compact_searchable
                ):
                    score += 5
            if len(query_digits) >= 4:
                phone_digits = " ".join(re.sub(r"\D+", "", phone) for phone in client.phones)
                if query_digits in phone_digits:
                    score += 10
            if query_phone_variants:
                client_phone_variants = indexed.get("phone_variants", set())
                if client_phone_variants and any(
                    query_variant in client_variant or client_variant in query_variant
                    for query_variant in query_phone_variants
                    for client_variant in client_phone_variants
                ):
                    score += 10
                elif query_phone_variants.intersection(indexed.get("match_keys", set())):
                    score += 10
            if score > 0:
                ranked.append((score, client))
            else:
                fallback_clients.append(client)
        if not ranked and cards and fallback_clients:
            related_fields_by_client_id = self._client_related_vehicle_fields_index(
                fallback_clients, cards
            )
            for client in fallback_clients:
                score = 0
                related_vehicle_fields = related_fields_by_client_id.get(client.id, [])
                related_searchable = [
                    self._normalize_search_text(value) for value in related_vehicle_fields if value
                ]
                related_compact_searchable = [
                    re.sub(r"[\W_]+", "", value) for value in related_searchable if value
                ]
                for variant in query_variants:
                    if not variant:
                        continue
                    for value in related_searchable:
                        if value == variant:
                            score += 7
                        elif variant in value:
                            score += 5
                        elif all(part in value for part in variant.split()):
                            score += 3
                    compact_variant = re.sub(r"[\W_]+", "", variant)
                    if compact_variant and any(
                        compact_variant in value for value in related_compact_searchable
                    ):
                        score += 5
                if score > 0:
                    ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return ranked

    def _sync_card_client_fields(
        self, card: Card, client: ClientProfile, *, overwrite: bool = False
    ) -> bool:
        changed = False
        client_name = client.name()
        client_phone = client.phone
        client_phones = list(client.phones or ([client_phone] if client_phone else []))
        if client_name and (overwrite or not card.vehicle_profile.customer_name):
            if card.vehicle_profile.customer_name != client_name:
                card.vehicle_profile.customer_name = client_name
                changed = True
        if client_phone and (overwrite or not card.vehicle_profile.customer_phone):
            if card.vehicle_profile.customer_phone != client_phone:
                card.vehicle_profile.customer_phone = client_phone
                changed = True
        if client_phones and (
            overwrite or not getattr(card.vehicle_profile, "customer_phones", [])
        ):
            if list(card.vehicle_profile.customer_phones) != client_phones:
                card.vehicle_profile.customer_phones = client_phones
                changed = True
        if self._card_has_repair_order(card):
            if client_name and (overwrite or not card.repair_order.client):
                if card.repair_order.client != client_name:
                    card.repair_order.client = client_name
                    changed = True
            if client_phone and (overwrite or not card.repair_order.phone):
                if card.repair_order.phone != client_phone:
                    card.repair_order.phone = client_phone
                    changed = True
        return changed

    def _find_client_vehicle_or_none(
        self, client: ClientProfile, vehicle_id: str | None
    ) -> ClientVehicle | None:
        requested_id = normalize_text(vehicle_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for vehicle in client.vehicles:
            if (
                vehicle.id == requested_id
                or short_entity_id(vehicle.id, prefix="CV").upper() == requested_short_id
            ):
                return vehicle
        return None

    def _find_client_vehicle(self, client: ClientProfile, vehicle_id: str | None) -> ClientVehicle:
        vehicle = self._find_client_vehicle_or_none(client, vehicle_id)
        if vehicle is None:
            self._fail(
                "not_found",
                "Автомобиль клиента не найден.",
                status_code=404,
                details={
                    "client_id": client.id,
                    "client_vehicle_id": normalize_text(vehicle_id, default="", limit=128),
                },
            )
        return vehicle

    def _replace_client_vehicle(self, client: ClientProfile, vehicle: ClientVehicle) -> None:
        for index, candidate in enumerate(client.vehicles):
            if candidate.id == vehicle.id:
                client.vehicles[index] = vehicle
                return
        client.vehicles.append(vehicle)

    def _dedupe_client_vehicles(self, vehicles: list[ClientVehicle]) -> list[ClientVehicle]:
        return ClientProfile(
            id=str(uuid.uuid4()),
            display_name="temporary",
            vehicles=vehicles,
        ).vehicles

    def _validated_client_vehicle(self, payload: dict[str, Any]) -> ClientVehicle:
        source_payload: dict[str, Any] = {}
        nested = payload.get("vehicle")
        if isinstance(nested, dict):
            source_payload.update(nested)
        source_payload.update(
            {
                key: value
                for key, value in payload.items()
                if not (key == "vehicle" and isinstance(nested, dict))
                and key
                in {
                    "id",
                    "client_vehicle_id",
                    "vehicle",
                    "brand",
                    "make",
                    "model",
                    "vin",
                    "license_plate",
                    "registration_plate",
                    "plate",
                    "year",
                    "mileage",
                    "body_number",
                    "chassis_number",
                    "engine_code",
                    "engine_model",
                    "gearbox_type",
                    "gearbox_model",
                    "drivetrain",
                    "drive_type",
                    "notes",
                    "comment",
                }
            }
        )
        vehicle = ClientVehicle.from_value(source_payload)
        if vehicle is None:
            self._fail(
                "validation_error",
                "У автомобиля клиента должны быть модель, VIN, госномер или номер кузова.",
                details={"field": "vehicle"},
            )
        return vehicle

    def _client_vehicle_from_card(self, card: Card, *, vehicle_id: str = "") -> ClientVehicle:
        profile = card.vehicle_profile
        order = card.repair_order
        year = str(profile.production_year or "").strip()
        mileage = str(profile.mileage or order.mileage or "").strip()
        vehicle = ClientVehicle.from_value(
            {
                "id": vehicle_id or str(uuid.uuid4()),
                "vehicle": card.vehicle_display() or order.vehicle,
                "brand": profile.make_display,
                "model": profile.model_display,
                "vin": profile.vin or order.vin,
                "license_plate": profile.registration_plate or order.license_plate,
                "year": year,
                "mileage": mileage,
                "body_number": profile.body_number,
                "chassis_number": profile.chassis_number,
                "engine_code": profile.engine_code,
                "engine_model": profile.engine_model,
                "gearbox_type": profile.gearbox_type,
                "gearbox_model": profile.gearbox_model,
                "drivetrain": profile.drivetrain,
            }
        )
        if vehicle is None:
            self._fail(
                "validation_error",
                "В карточке нет достаточных данных для создания автомобиля клиента.",
                details={"card_id": card.id},
            )
        return vehicle

    def _merge_client_vehicle(
        self, existing: ClientVehicle, incoming: ClientVehicle
    ) -> ClientVehicle:
        payload = existing.to_dict()
        incoming_payload = incoming.to_dict()
        for key, value in incoming_payload.items():
            if key == "id":
                continue
            if str(value or "").strip():
                payload[key] = value
        payload["id"] = existing.id
        return ClientVehicle.from_value(payload) or existing

    def _client_vehicle_profile_patch(self, vehicle: ClientVehicle) -> dict[str, Any]:
        patch: dict[str, Any] = {
            "make_display": vehicle.brand,
            "model_display": vehicle.model,
            "vin": vehicle.vin,
            "registration_plate": vehicle.license_plate,
            "body_number": vehicle.body_number,
            "chassis_number": vehicle.chassis_number,
            "engine_code": vehicle.engine_code,
            "engine_model": vehicle.engine_model,
            "gearbox_type": vehicle.gearbox_type,
            "gearbox_model": vehicle.gearbox_model,
            "drivetrain": vehicle.drivetrain,
        }
        if str(vehicle.year or "").strip().isdigit():
            patch["production_year"] = int(str(vehicle.year).strip())
        if str(vehicle.mileage or "").strip().isdigit():
            patch["mileage"] = int(str(vehicle.mileage).strip())
        return {key: value for key, value in patch.items() if value not in ("", None)}

    def _sync_card_vehicle_fields(
        self, card: Card, vehicle: ClientVehicle, *, overwrite: bool = True
    ) -> bool:
        changed = False
        vehicle_label = vehicle.vehicle or " ".join(
            part for part in (vehicle.brand, vehicle.model, vehicle.year) if part
        ).strip()
        if vehicle_label and (overwrite or not card.vehicle):
            next_vehicle = self._validated_vehicle(vehicle_label)
            if card.vehicle != next_vehicle:
                card.vehicle = next_vehicle
                changed = True
        patch = self._client_vehicle_profile_patch(vehicle)
        if not overwrite:
            current = card.vehicle_profile.to_dict()
            patch = {
                key: value
                for key, value in patch.items()
                if not str(current.get(key) or "").strip()
            }
        if patch:
            profile, _changed_fields = self._merge_vehicle_profile_patch(
                card.vehicle_profile, patch
            )
            if profile.to_storage_dict() != card.vehicle_profile.to_storage_dict():
                card.vehicle_profile = profile
                card.vehicle = self._resolved_card_vehicle_label(card.vehicle, profile)
                changed = True
        return changed

    def _sync_linked_client_vehicle_from_card(
        self, clients: list[ClientProfile], card: Card
    ) -> bool:
        if not card.client_id or not card.client_vehicle_id:
            return False
        client = self._find_client_or_none(clients, card.client_id)
        if client is None:
            return False
        existing = self._find_client_vehicle_or_none(client, card.client_vehicle_id)
        if existing is None:
            return False
        incoming = self._client_vehicle_from_card(card, vehicle_id=existing.id)
        merged = self._merge_client_vehicle(existing, incoming)
        if merged.to_dict() == existing.to_dict():
            return False
        self._replace_client_vehicle(client, merged)
        client.updated_at = utc_now_iso()
        return True

    def _serialize_sticky(self, sticky: StickyNote) -> dict:
        return sticky.to_dict()

    def _serialize_cash_transaction(self, transaction: CashTransaction) -> dict[str, object]:
        return transaction.to_dict()

    def _cash_transaction_sortable_datetime(self, value: str | None) -> datetime:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.astimezone(UTC)
        return datetime.min.replace(tzinfo=UTC)

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
            if (
                transaction.id == requested_id
                or short_entity_id(transaction.id, prefix="CT").upper() == requested_short_id
            ):
                return transaction
        return None

    def _cashbox_transactions(
        self,
        transactions: list[CashTransaction],
        cashbox_id: str,
    ) -> list[CashTransaction]:
        matched = [item for item in transactions if item.cashbox_id == cashbox_id]
        matched.sort(
            key=lambda item: (
                self._cash_transaction_sortable_datetime(item.created_at),
                item.id,
            ),
            reverse=True,
        )
        return matched

    def _cash_journal_amount_text(
        self,
        amount_minor: int,
        *,
        allow_sign: bool = False,
        force_negative: bool = False,
    ) -> str:
        absolute_amount = round(abs(int(amount_minor)) / 100)
        sign = ""
        if force_negative:
            sign = "-"
        elif allow_sign and int(amount_minor) > 0:
            sign = "+"
        return f"{sign}{absolute_amount:,}".replace(",", " ") + " ₽"

    def _cash_journal_money_text(self, amount_minor: int, *, signed: bool = False) -> str:
        formatted = format_money_minor(abs(int(amount_minor)))
        if not signed:
            return formatted
        if int(amount_minor) > 0:
            return f"+{formatted}"
        if int(amount_minor) < 0:
            return f"-{formatted}"
        return formatted

    def _cash_transaction_source_label(self, transaction: CashTransaction) -> str:
        transaction_kind = self._cash_transaction_kind_label(transaction.transaction_kind)
        if transaction_kind:
            return transaction_kind
        note = normalize_text(transaction.note, default="", limit=240)
        if note.casefold().startswith("перемещение"):
            return "перемещение"
        if "заказ-наряд" in note.casefold():
            return "заказ-наряд"
        source = normalize_text(transaction.source, default="", limit=64).casefold()
        if source == "ui":
            return "ручное"
        if source == "mcp":
            return "mcp"
        return source or "система"

    def _cash_transaction_kind_label(self, transaction_kind: str) -> str:
        normalized = normalize_text(transaction_kind, default="", limit=32).casefold()
        if normalized == "salary_payout":
            return "зарплата"
        if normalized == "salary_advance":
            return "аванс"
        return ""

    def _normalize_salary_transaction_kind(self, value: Any) -> str:
        normalized = normalize_text(value, default="", limit=32).casefold()
        if normalized in {"salary_payout", "payout", "salary", "salary_payment"}:
            return "salary_payout"
        if normalized in {"salary_advance", "advance", "avans"}:
            return "salary_advance"
        self._fail(
            "validation_error",
            "Неверный тип операции по зарплате.",
            details={"field": "transaction_kind"},
        )

    def _salary_cashbox(self, cashboxes: list[CashBox]) -> CashBox | None:
        if not cashboxes:
            return None
        exact = [item for item in cashboxes if item.name.casefold() == "наличный"]
        if exact:
            return exact[0]
        loose = [
            item
            for item in cashboxes
            if "налич" in item.name.casefold() or "cash" in item.name.casefold()
        ]
        if loose:
            return loose[0]
        return None

    def _build_cash_journal(
        self,
        transactions: list[CashTransaction],
        cashboxes_by_id: dict[str, CashBox],
        *,
        months: int,
        limit: int,
        total: int,
        period_start: datetime,
    ) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        for item in transactions:
            created_at = parse_datetime(item.created_at)
            cashbox = cashboxes_by_id.get(item.cashbox_id)
            base = self._serialize_cash_transaction(item)
            direction_sign = 1 if item.direction == "income" else -1
            signed_amount_minor = int(item.amount_minor) * direction_sign
            if created_at is not None:
                iso_year, iso_week, _ = created_at.isocalendar()
                date_label = created_at.date().isoformat()
                time_label = created_at.strftime("%H:%M:%S")
                short_time_label = created_at.strftime("%H:%M")
                month_key = created_at.strftime("%Y-%m")
                week_key = f"{iso_year}-W{iso_week:02d}"
            else:
                date_label = "unknown"
                time_label = ""
                short_time_label = ""
                month_key = "unknown"
                week_key = "unknown"
            base.update(
                {
                    "schema_version": "cash_journal.entry.v2",
                    "cashbox_name": cashbox.name if cashbox else "Неизвестная касса",
                    "date": date_label,
                    "time": time_label,
                    "time_short": short_time_label,
                    "month_key": month_key,
                    "week_key": week_key,
                    "direction_label": "Поступление"
                    if item.direction == "income"
                    else "Списание",
                    "direction_sign": direction_sign,
                    "signed_amount_minor": signed_amount_minor,
                    "signed_amount_display": self._cash_journal_money_text(
                        signed_amount_minor, signed=True
                    ),
                    "amount_display": self._cash_journal_money_text(item.amount_minor),
                    "actor_label": normalize_actor_name(item.actor_name),
                    "source_label": self._cash_transaction_source_label(item),
                    "note": normalize_text(item.note, default="Без комментария", limit=240),
                }
            )
            entries.append(base)

        days = self._cash_journal_group_entries(entries, key="date", kind="day")
        weeks = self._cash_journal_group_entries(entries, key="week_key", kind="week")
        months_grouped = self._cash_journal_group_entries(entries, key="month_key", kind="month")
        totals = self._cash_journal_totals(entries)
        meta = {
            "schema_version": "cash_journal.v2",
            "months": months,
            "limit": limit,
            "total": total,
            "returned": len(transactions),
            "period_start": period_start.isoformat(),
            "format": "markdown+json",
            "text_alias": "markdown",
        }
        markdown = self._cash_journal_markdown(
            entries=entries,
            days=days,
            weeks=weeks,
            months=months_grouped,
            totals=totals,
            meta=meta,
        )
        return {
            "entries": entries,
            "days": days,
            "weeks": weeks,
            "months": months_grouped,
            "totals": totals,
            "markdown": markdown,
            "meta": meta,
        }

    def _cash_journal_totals(self, entries: list[dict[str, object]]) -> dict[str, object]:
        income_minor = sum(
            int(item.get("amount_minor") or 0)
            for item in entries
            if item.get("direction") == "income"
        )
        expense_minor = sum(
            int(item.get("amount_minor") or 0)
            for item in entries
            if item.get("direction") == "expense"
        )
        external_income_minor = sum(
            int(item.get("amount_minor") or 0)
            for item in entries
            if item.get("direction") == "income" and item.get("source_label") != "перемещение"
        )
        external_expense_minor = sum(
            int(item.get("amount_minor") or 0)
            for item in entries
            if item.get("direction") == "expense" and item.get("source_label") != "перемещение"
        )
        transfer_income_minor = income_minor - external_income_minor
        transfer_expense_minor = expense_minor - external_expense_minor
        balance_minor = income_minor - expense_minor
        return {
            "count": len(entries),
            "income_minor": income_minor,
            "expense_minor": expense_minor,
            "balance_minor": balance_minor,
            "external_income_minor": external_income_minor,
            "external_expense_minor": external_expense_minor,
            "transfer_income_minor": transfer_income_minor,
            "transfer_expense_minor": transfer_expense_minor,
            "income_display": self._cash_journal_money_text(income_minor),
            "expense_display": self._cash_journal_money_text(expense_minor),
            "balance_display": self._cash_journal_money_text(balance_minor, signed=True),
            "external_income_display": self._cash_journal_money_text(external_income_minor),
            "external_expense_display": self._cash_journal_money_text(external_expense_minor),
            "transfer_income_display": self._cash_journal_money_text(transfer_income_minor),
            "transfer_expense_display": self._cash_journal_money_text(transfer_expense_minor),
        }

    def _cash_journal_group_entries(
        self, entries: list[dict[str, object]], *, key: str, kind: str
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in entries:
            grouped.setdefault(str(item.get(key) or "unknown"), []).append(item)
        result: list[dict[str, object]] = []
        for group_key in sorted(grouped.keys(), reverse=True):
            group_entries = grouped[group_key]
            totals = self._cash_journal_totals(group_entries)
            payload: dict[str, object] = {
                "key": group_key,
                "entries": group_entries,
                **totals,
            }
            if kind == "day":
                payload["date"] = group_key
                payload["label"] = self._cash_journal_day_label(group_key)
            elif kind == "week":
                payload["week_key"] = group_key
                payload["label"] = self._cash_journal_week_label(group_key)
            else:
                payload["month_key"] = group_key
                payload["label"] = self._cash_journal_month_label(group_key)
            result.append(payload)
        return result

    def _cash_journal_day_label(self, date_key: str) -> str:
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

    def _cash_journal_week_label(self, week_key: str) -> str:
        try:
            year_text, week_text = week_key.split("-W", 1)
            start = datetime.fromisocalendar(int(year_text), int(week_text), 1)
            end = datetime.fromisocalendar(int(year_text), int(week_text), 7)
        except (ValueError, TypeError):
            return week_key
        return f"{week_text} неделя: {start.strftime('%d.%m')} - {end.strftime('%d.%m.%Y')}"

    def _cash_journal_month_label(self, month_key: str) -> str:
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

    def _cash_journal_markdown(
        self,
        *,
        entries: list[dict[str, object]],
        days: list[dict[str, object]],
        weeks: list[dict[str, object]],
        months: list[dict[str, object]],
        totals: dict[str, object],
        meta: dict[str, object],
    ) -> str:
        lines = [
            "# 💰 Кассовый журнал",
            "",
            "## 📊 Итоги периода",
            f"- Период: последние {meta['months']} мес.",
            f"- Показано операций: {totals['count']} из {meta['total']}",
            f"- Реальные поступления: {totals['external_income_display']}",
            f"- Реальные списания: {totals['external_expense_display']}",
            f"- Итог периода: {totals['balance_display']}",
            f"- Внутренние перемещения: пришло {totals['transfer_income_display']} | ушло {totals['transfer_expense_display']}",
            "",
        ]
        if int(meta["total"]) > int(totals["count"]):
            lines.extend(
                [
                    "⚠️ Выгрузка ограничена лимитом. Для полного журнала увеличьте лимит выгрузки.",
                    "",
                ]
            )
        if not entries:
            lines.extend(
                [
                    "## 🧾 Операции",
                    "За выбранный период движений нет.",
                ]
            )
            return "\n".join(lines).strip()

        lines.extend(["## 🗓️ По месяцам"])
        for item in months:
            lines.append(
                f"- **{item['label']}**: приход {item['external_income_display']} | "
                + f"расход {item['external_expense_display']} | итог {item['balance_display']} | "
                + f"перемещения {item['transfer_income_display']}/{item['transfer_expense_display']} | "
                + f"{item['count']} оп."
            )
        lines.extend(["", "## 📅 По неделям"])
        for item in weeks:
            lines.append(
                f"- **{item['label']}**: приход {item['external_income_display']} | "
                + f"расход {item['external_expense_display']} | итог {item['balance_display']} | "
                + f"перемещения {item['transfer_income_display']}/{item['transfer_expense_display']} | "
                + f"{item['count']} оп."
            )
        lines.extend(["", "## 🧾 Операции по дням"])
        for day in days:
            display_rows = self._cash_journal_display_rows(day["entries"])
            lines.extend(
                [
                    "",
                    f"### 📆 {day['label']}",
                    f"Итого: приход {day['external_income_display']} | расход {day['external_expense_display']} | "
                    + f"итог {day['balance_display']} | перемещения {day['transfer_income_display']}/{day['transfer_expense_display']} | "
                    + f"{day['count']} оп.",
                    "",
                ]
            )
            for row in display_rows:
                lines.append(str(row["line"]))
                detail = str(row.get("detail") or "")
                if detail:
                    lines.append(detail)
        return "\n".join(lines).strip()

    def _cash_journal_display_rows(
        self, entries: list[dict[str, object]]
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        used_ids: set[str] = set()
        for item in entries:
            item_id = str(item.get("id") or "")
            if item_id in used_ids:
                continue
            transfer_pair = self._cash_journal_transfer_pair(item, entries, used_ids)
            if transfer_pair is not None:
                source, target = transfer_pair
                used_ids.update({str(source.get("id") or ""), str(target.get("id") or "")})
                rows.append(self._cash_journal_transfer_row(source, target))
                continue
            used_ids.add(item_id)
            rows.append(self._cash_journal_operation_row(item))
        return rows

    def _cash_journal_transfer_pair(
        self,
        item: dict[str, object],
        entries: list[dict[str, object]],
        used_ids: set[str],
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        if item.get("source_label") != "перемещение":
            return None
        for candidate in entries:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id in used_ids or candidate_id == str(item.get("id") or ""):
                continue
            if candidate.get("source_label") != "перемещение":
                continue
            if candidate.get("time") != item.get("time"):
                continue
            if candidate.get("amount_minor") != item.get("amount_minor"):
                continue
            if candidate.get("direction") == item.get("direction"):
                continue
            source = item if item.get("direction") == "expense" else candidate
            target = item if item.get("direction") == "income" else candidate
            return source, target
        return None

    def _cash_journal_transfer_row(
        self, source: dict[str, object], target: dict[str, object]
    ) -> dict[str, str]:
        amount = self._cash_journal_money_text(int(source.get("amount_minor") or 0))
        line = (
            f"- {source.get('time_short') or '--:--'} | 🔁 {amount} | "
            + f"{source.get('cashbox_name')} → {target.get('cashbox_name')}"
        )
        return {
            "line": line,
            "detail": self._cash_journal_detail_line(
                source, prefix="  - Перемещение", include_source=False
            ),
        }

    def _cash_journal_operation_row(self, item: dict[str, object]) -> dict[str, str]:
        icon = "🟢" if item.get("direction") == "income" else "🔴"
        action = "приход" if item.get("direction") == "income" else "расход"
        line = (
            f"- {item.get('time_short') or '--:--'} | {icon} "
            + f"{item['signed_amount_display']} | {item['cashbox_name']} | "
            + f"{action}: {item['note']}"
        )
        return {"line": line, "detail": self._cash_journal_detail_line(item)}

    def _cash_journal_detail_line(
        self,
        item: dict[str, object],
        *,
        prefix: str = "  - Детали",
        include_source: bool = True,
    ) -> str:
        actor = str(item.get("actor_label") or "").strip()
        source = str(item.get("source_label") or "").strip()
        parts = []
        if actor:
            parts.append(f"оператор {actor}")
        if include_source and source and source not in {"ручное"}:
            parts.append(source)
        if not parts:
            return ""
        return f"{prefix}: " + ", ".join(parts)

    def _cash_journal_text(
        self,
        transactions: list[CashTransaction],
        cashboxes_by_id: dict[str, CashBox],
        *,
        months: int,
    ) -> str:
        journal = self._build_cash_journal(
            transactions,
            cashboxes_by_id,
            months=months,
            limit=len(transactions),
            total=len(transactions),
            period_start=utc_now() - timedelta(days=30 * months),
        )
        return str(journal["markdown"])
        for item in transactions:
            created_at = parse_datetime(item.created_at)
            if created_at is None:
                continue
            grouped.setdefault(created_at.strftime("%d.%m.%y"), []).append(item)

        lines = [
            "КАССОВЫЙ ЖУРНАЛ",
            f"ПЕРИОД: ПОСЛЕДНИЕ {months} МЕС.",
            f"ДВИЖЕНИЙ: {len(transactions)}",
            "",
        ]
        for date_label, day_items in grouped.items():
            income_minor = sum(
                item.amount_minor for item in day_items if item.direction == "income"
            )
            expense_minor = sum(
                item.amount_minor for item in day_items if item.direction == "expense"
            )
            day_balance_minor = income_minor - expense_minor
            lines.append(date_label)
            lines.append(
                "ИТОГО ЗА ДЕНЬ: "
                + f"ПОСТУПЛЕНИЯ {self._cash_journal_amount_text(income_minor)} | "
                + f"СПИСАНИЯ {self._cash_journal_amount_text(expense_minor)} | "
                + f"БАЛАНС {self._cash_journal_amount_text(day_balance_minor, allow_sign=True)}"
            )
            for item in day_items:
                created_at = parse_datetime(item.created_at)
                time_label = created_at.strftime("%H:%M") if created_at is not None else "—"
                cashbox_name = (
                    cashboxes_by_id.get(item.cashbox_id).name
                    if cashboxes_by_id.get(item.cashbox_id)
                    else "Неизвестная касса"
                )
                direction_label = "ПОСТУПЛЕНИЕ" if item.direction == "income" else "СПИСАНИЕ"
                amount_label = self._cash_journal_amount_text(
                    item.amount_minor,
                    allow_sign=item.direction == "income",
                    force_negative=item.direction == "expense",
                )
                lines.append(
                    f"  {time_label} | {cashbox_name} | {direction_label} | {amount_label}"
                )
                note = normalize_text(item.note, default="Без комментария", limit=240)
                lines.append(f"    {note}")
                lines.append(
                    f"    {normalize_actor_name(item.actor_name)} | {self._cash_transaction_source_label(item)}"
                )
            lines.append("")
        return "\n".join(lines).strip()

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

    def _ordered_cashboxes(
        self,
        cashboxes: list[CashBox],
        *,
        exclude_cashbox_id: str | None = None,
    ) -> list[CashBox]:
        ordered = [
            cashbox
            for cashbox in cashboxes
            if exclude_cashbox_id is None or cashbox.id != exclude_cashbox_id
        ]
        ordered.sort(
            key=lambda item: (
                item.order,
                item.created_at,
                item.updated_at,
                item.name.casefold(),
                item.id,
            )
        )
        return ordered

    def _renumber_cashboxes(self, cashboxes: list[CashBox]) -> bool:
        changed = False
        for index, cashbox in enumerate(cashboxes):
            if cashbox.order != index:
                cashbox.order = index
                changed = True
        return changed

    def _reposition_cashbox(
        self,
        cashboxes: list[CashBox],
        cashbox: CashBox,
        *,
        before_cashbox_id: str | None = None,
    ) -> tuple[list[CashBox], bool]:
        original_ids = [item.id for item in self._ordered_cashboxes(cashboxes)]
        ordered = self._ordered_cashboxes(cashboxes, exclude_cashbox_id=cashbox.id)
        insert_index = len(ordered)
        if before_cashbox_id:
            before_cashbox = self._find_cashbox(ordered, before_cashbox_id)
            insert_index = next(
                (index for index, item in enumerate(ordered) if item.id == before_cashbox.id),
                len(ordered),
            )
        ordered.insert(insert_index, cashbox)
        changed = [item.id for item in ordered] != original_ids
        if self._renumber_cashboxes(ordered):
            changed = True
        return ordered, changed

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
        normalized_column_ids = [
            str(column_id or "").strip() for column_id in column_ids if str(column_id or "").strip()
        ]
        if not normalized_column_ids:
            return []
        target_ids = set(normalized_column_ids)
        selected_cards = [card for card in cards if not card.archived and card.column in target_ids]
        selected_cards.sort(
            key=lambda item: (item.column, item.position, item.created_at, item.updated_at, item.id)
        )
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

        source_cards = self._ordered_cards_in_column(
            cards, previous_column, exclude_card_id=card.id
        )
        if previous_column == target_column:
            target_cards = list(source_cards)
        else:
            target_cards = self._ordered_cards_in_column(
                cards, target_column, exclude_card_id=card.id
            )

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

    def _cards_for_wall(
        self, cards: list[Card], columns: list[Column], *, include_archived: bool
    ) -> list[Card]:
        position_map = {column.id: column.position for column in columns}
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived] if include_archived else []
        active_cards.sort(
            key=lambda item: (
                position_map.get(item.column, 999),
                item.position,
                item.created_at,
                item.updated_at,
                item.id,
            )
        )
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
            payload["details_text"] = self._repair_mojibake_text(
                self._describe_wall_event_details(event, column_labels)
            )
            payloads.append(payload)
        return payloads

    def _describe_wall_event_details(self, event: AuditEvent, column_labels: dict[str, str]) -> str:
        details = event.details or {}
        parts: list[str] = []
        for key, value in details.items():
            parts.append(
                f"{self._wall_label(key)}={self._wall_value(value, key=key, column_labels=column_labels)}"
            )
        return " | ".join(parts)

    def _wall_label(self, key: str) -> str:
        return str(key).replace("_", " ").strip()

    def _wall_value(self, value, *, key: str, column_labels: dict[str, str]) -> str:
        if isinstance(value, list):
            return (
                ", ".join(
                    self._wall_value(item, key=key, column_labels=column_labels) for item in value
                )
                or "—"
            )
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
        return (
            repaired
            if self._text_quality_score(repaired) > self._text_quality_score(text)
            else text
        )

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
                archived_counts_by_column[card.column] = (
                    archived_counts_by_column.get(card.column, 0) + 1
                )
            else:
                active_cards_total += 1
                active_counts_by_column[card.column] = (
                    active_counts_by_column.get(card.column, 0) + 1
                )
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
            "vehicle_profile_compact_fields: "
            + ", ".join(context["vehicle_profile_compact_fields"]),
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
        query_variants = self._search_text_variants(query)
        query_token_variants = [
            [token for token in variant.split() if token.strip()]
            for variant in query_variants
            if variant
        ]
        if not query_token_variants:
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
            name: self._search_text_variants(value)
            for name, value in searchable_fields.items()
            if value
        }
        searchable_values = [value for values in normalized_fields.values() for value in values]
        matched_query_tokens = next(
            (
                tokens
                for tokens in query_token_variants
                if all(any(token in value for value in searchable_values) for token in tokens)
            ),
            None,
        )
        if matched_query_tokens is None:
            return 0, []

        if not any(
            all(any(token in value for value in searchable_values) for token in tokens)
            for tokens in query_token_variants
        ):
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
            field_values = normalized_fields.get(field_name, [])
            if field_values and any(
                any(token in field_value for field_value in field_values)
                for token in matched_query_tokens
            ):
                fields.append(field_name)
                score += weight
                if any(
                    query_variant
                    and any(query_variant in field_value for field_value in field_values)
                    for query_variant in query_variants
                ):
                    score += 2
        return score, fields

    def _normalize_search_text(self, value) -> str:
        text = normalize_text(value, default="", limit=500).casefold()
        if not text:
            return ""
        text = _SEARCH_SEPARATOR_PATTERN.sub(" ", text)
        return " ".join(text.split())

    def _search_text_variants(self, value) -> list[str]:
        normalized = self._normalize_search_text(value)
        if not normalized:
            return []

        variants = [normalized]
        latin_variant = self._transliterate_search_text(normalized, target="latin_to_cyrillic")
        cyrillic_variant = self._transliterate_search_text(normalized, target="cyrillic_to_latin")
        for variant in (latin_variant, cyrillic_variant):
            if variant and variant not in variants:
                variants.append(variant)
        return variants

    def _transliterate_search_text(self, value: str, *, target: str) -> str:
        if target == "latin_to_cyrillic":
            return self._latin_to_cyrillic(value)
        if target == "cyrillic_to_latin":
            return self._cyrillic_to_latin(value)
        return value

    def _latin_to_cyrillic(self, value: str) -> str:
        text = value.casefold()
        result: list[str] = []
        i = 0
        while i < len(text):
            matched = False
            for source, replacement in _SEARCH_LATIN_TO_CYRILLIC_PATTERNS:
                if text.startswith(source, i):
                    result.append(replacement)
                    i += len(source)
                    matched = True
                    break
            if matched:
                continue
            char = text[i]
            result.append(_SEARCH_LATIN_TO_CYRILLIC_SINGLE.get(char, char))
            i += 1
        return " ".join("".join(result).split())

    def _cyrillic_to_latin(self, value: str) -> str:
        text = value.casefold()
        result: list[str] = []
        for char in text:
            result.append(_SEARCH_CYRILLIC_TO_LATIN.get(char, char))
        return " ".join("".join(result).split())

    def _save_bundle(
        self,
        bundle: dict,
        *,
        columns: list[Column],
        cards: list[Card],
        clients: list[ClientProfile] | None = None,
        stickies: list[StickyNote] | None = None,
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
        events: list[AuditEvent],
        settings: dict[str, Any] | None = None,
    ) -> None:
        written_bundle = self._store.write_bundle(
            columns=columns,
            cards=cards,
            clients=bundle["clients"] if clients is None else clients,
            stickies=bundle["stickies"] if stickies is None else stickies,
            cashboxes=bundle["cashboxes"] if cashboxes is None else cashboxes,
            cash_transactions=bundle["cash_transactions"]
            if cash_transactions is None
            else cash_transactions,
            events=events,
            settings=bundle["settings"] if settings is None else settings,
        )
        written_cards = written_bundle["cards"]
        self._cleanup_repair_orders_directory(written_cards)
        self._cleanup_attachment_directories(written_cards)

    def _parse_payroll_decimal(self, value, *, default: Decimal = Decimal("0")) -> Decimal:
        raw = normalize_text(value, default="", limit=40).replace(" ", "").replace(",", ".")
        if not raw:
            return default
        try:
            return Decimal(raw)
        except InvalidOperation:
            return default

    def _format_payroll_decimal(self, value: Decimal) -> str:
        quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        text = format(quantized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _normalize_payroll_mode(self, value, *, default: str = PAYROLL_MODE_PERCENT_ONLY) -> str:
        normalized = normalize_text(value, default=default, limit=32).lower()
        if normalized not in PAYROLL_ALLOWED_MODES:
            return default
        return normalized

    def _validated_payroll_month(self, value) -> str:
        normalized = normalize_text(value, default="", limit=7)
        if re.fullmatch(r"\d{4}-\d{2}", normalized):
            return normalized
        return datetime.now().astimezone().strftime("%Y-%m")

    def _normalized_employee_record(
        self, payload: Any, *, existing: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        now_iso = utc_now_iso()
        existing = existing or {}
        employee_id = normalize_text(
            payload.get("id")
            or payload.get("employee_id")
            or existing.get("id")
            or str(uuid.uuid4()),
            default="",
            limit=64,
        )
        name = normalize_text(payload.get("name"), default=existing.get("name", ""), limit=80)
        if not employee_id or not name:
            return None
        position = normalize_text(
            payload.get("position"), default=existing.get("position", ""), limit=80
        )
        salary_mode = self._normalize_payroll_mode(
            payload.get("salary_mode", existing.get("salary_mode"))
        )
        base_salary = self._format_payroll_decimal(
            self._parse_payroll_decimal(payload.get("base_salary", existing.get("base_salary", "")))
        )
        work_percent = self._format_payroll_decimal(
            self._parse_payroll_decimal(
                payload.get("work_percent", existing.get("work_percent", ""))
            )
        )
        note = normalize_text(payload.get("note"), default=existing.get("note", ""), limit=240)
        is_active = normalize_bool(
            payload.get("is_active"),
            default=normalize_bool(existing.get("is_active"), default=True),
        )
        created_at = (
            normalize_text(existing.get("created_at"), default=now_iso, limit=40) or now_iso
        )
        updated_at = (
            normalize_text(
                payload.get("updated_at"), default=existing.get("updated_at", now_iso), limit=40
            )
            or now_iso
        )
        return {
            "id": employee_id,
            "name": name,
            "position": position,
            "salary_mode": salary_mode,
            "base_salary": base_salary,
            "work_percent": work_percent,
            "is_active": is_active,
            "note": note,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _employees_from_settings(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = settings.get(EMPLOYEES_SETTING_KEY)
        if not isinstance(raw_items, list):
            return []
        employees: list[dict[str, Any]] = []
        for item in raw_items:
            normalized = self._normalized_employee_record(item)
            if normalized is None:
                continue
            employees.append(normalized)
        employees.sort(
            key=lambda item: (not item["is_active"], item["name"].casefold(), item["id"])
        )
        return employees

    def _validated_employee_payload(
        self, payload: dict[str, Any], *, existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        employee = self._normalized_employee_record(payload, existing=existing)
        if employee is None:
            self._fail(
                "validation_error", "Нужно указать имя сотрудника.", details={"field": "name"}
            )
        employee["updated_at"] = utc_now_iso()
        return employee

    def _apply_repair_order_payroll_snapshot(
        self, order: RepairOrder, settings: dict[str, Any]
    ) -> RepairOrder:
        if order.status != REPAIR_ORDER_STATUS_CLOSED:
            next_rows: list[dict[str, str]] = []
            changed = False
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if any(
                    [
                        row.salary_mode_snapshot,
                        row.base_salary_snapshot,
                        row.work_percent_snapshot,
                        row.salary_amount,
                        row.salary_accrued_at,
                    ]
                ):
                    changed = True
                row.salary_mode_snapshot = ""
                row.base_salary_snapshot = ""
                row.work_percent_snapshot = ""
                row.salary_amount = ""
                row.salary_accrued_at = ""
                next_rows.append(row.to_dict())
            if not changed:
                return order
            return RepairOrder.from_dict({**order.to_storage_dict(), "works": next_rows})
        employees_by_id = {item["id"]: item for item in self._employees_from_settings(settings)}
        next_rows: list[dict[str, str]] = []
        accrued_at = order.closed_at or self._repair_order_now()
        for source_row in order.works:
            row = RepairOrderRow.from_dict(
                source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
            )
            employee = employees_by_id.get(row.executor_id)
            if employee is None:
                row.salary_mode_snapshot = ""
                row.base_salary_snapshot = ""
                row.work_percent_snapshot = ""
                row.salary_amount = ""
                row.salary_accrued_at = ""
                next_rows.append(row.to_dict())
                continue
            row.executor_name = employee["name"]
            row.salary_mode_snapshot = employee["salary_mode"]
            row.base_salary_snapshot = employee["base_salary"]
            row.work_percent_snapshot = employee["work_percent"]
            salary_amount = Decimal("0")
            if employee["salary_mode"] in {
                PAYROLL_MODE_PERCENT_ONLY,
                PAYROLL_MODE_SALARY_PLUS_PERCENT,
            }:
                salary_amount = (
                    row.total_value()
                    * self._parse_payroll_decimal(employee["work_percent"])
                    / Decimal("100")
                )
            row.salary_amount = self._format_payroll_decimal(salary_amount)
            row.salary_accrued_at = accrued_at
            next_rows.append(row.to_dict())
        return RepairOrder.from_dict({**order.to_storage_dict(), "works": next_rows})

    def _build_payroll_report(
        self,
        cards: list[Card],
        employees: list[dict[str, Any]],
        *,
        month: str,
        employee_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        selected_employee_id = normalize_text(employee_id, default="", limit=64)
        month_key = month.replace("-", "")
        summaries: dict[str, dict[str, Any]] = {}
        for employee in employees:
            if selected_employee_id and employee["id"] != selected_employee_id:
                continue
            base_salary = self._parse_payroll_decimal(employee["base_salary"])
            summaries[employee["id"]] = {
                "employee_id": employee["id"],
                "employee_name": employee["name"],
                "position": employee["position"],
                "salary_mode": employee["salary_mode"],
                "work_percent": employee["work_percent"],
                "base_salary": self._format_payroll_decimal(base_salary),
                "works_count": 0,
                "works_total": Decimal("0"),
                "accrued_total": Decimal("0"),
            }
        detail_rows_by_order: dict[tuple[str, str], dict[str, Any]] = {}
        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_sort_key = self._repair_order_closed_sort_value(card)
            if not closed_sort_key.startswith(month_key):
                continue
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                current_employee_id = row.executor_id
                if not current_employee_id:
                    continue
                if selected_employee_id and current_employee_id != selected_employee_id:
                    continue
                if current_employee_id not in summaries:
                    summaries[current_employee_id] = {
                        "employee_id": current_employee_id,
                        "employee_name": row.executor_name or "Сотрудник",
                        "position": "",
                        "salary_mode": row.salary_mode_snapshot,
                        "work_percent": row.work_percent_snapshot,
                        "base_salary": "0",
                        "works_count": 0,
                        "works_total": Decimal("0"),
                        "accrued_total": Decimal("0"),
                    }
                summary = summaries[current_employee_id]
                work_total = row.total_value()
                accrued_total = self._parse_payroll_decimal(row.salary_amount)
                summary["works_count"] += 1
                summary["works_total"] += work_total
                summary["accrued_total"] += accrued_total
                detail_key = (current_employee_id, card.id)
                detail_row = detail_rows_by_order.setdefault(
                    detail_key,
                    {
                        "employee_id": current_employee_id,
                        "employee_name": summary["employee_name"],
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "works_count": 0,
                        "work_total": Decimal("0"),
                        "salary_amount": Decimal("0"),
                    },
                )
                detail_row["works_count"] += 1
                detail_row["work_total"] += work_total
                detail_row["salary_amount"] += accrued_total
        summary_rows: list[dict[str, Any]] = []
        for item in summaries.values():
            base_salary = self._parse_payroll_decimal(item["base_salary"])
            works_total = item["works_total"]
            accrued_total = item["accrued_total"]
            total_salary = base_salary + accrued_total
            summary_rows.append(
                {
                    "employee_id": item["employee_id"],
                    "employee_name": item["employee_name"],
                    "position": item["position"],
                    "salary_mode": item["salary_mode"],
                    "work_percent": item["work_percent"],
                    "base_salary": self._format_payroll_decimal(base_salary),
                    "works_count": item["works_count"],
                    "works_total": self._format_payroll_decimal(works_total),
                    "accrued_total": self._format_payroll_decimal(accrued_total),
                    "total_salary": self._format_payroll_decimal(total_salary),
                }
            )
        summary_rows.sort(
            key=lambda item: (Decimal(item["total_salary"] or "0"), item["employee_name"]),
            reverse=True,
        )
        detail_rows: list[dict[str, Any]] = []
        for item in detail_rows_by_order.values():
            detail_rows.append(
                {
                    "employee_id": item["employee_id"],
                    "employee_name": item["employee_name"],
                    "closed_at": item["closed_at"],
                    "repair_order_number": item["repair_order_number"],
                    "card_id": item["card_id"],
                    "vehicle": item["vehicle"],
                    "works_count": item["works_count"],
                    "work_total": self._format_payroll_decimal(item["work_total"]),
                    "salary_amount": self._format_payroll_decimal(item["salary_amount"]),
                }
            )
        detail_rows.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item["closed_at"]),
                item["repair_order_number"],
                item["vehicle"],
            ),
            reverse=True,
        )
        return {"summary": summary_rows, "detail_rows": detail_rows}

    def _touch_card(self, card: Card, actor_name: str | None = None) -> str:
        updated_at = utc_now_iso()
        card.updated_at = updated_at
        card.mark_seen(actor_name, seen_at=updated_at)
        return updated_at

    def _notify_agent_card_created(self, card: Card) -> None:
        if self._agent_control is None:
            return
        try:
            self._agent_control.handle_card_created(
                {
                    "card_id": card.id,
                    "column": card.column,
                }
            )
        except Exception as exc:
            self._logger.warning("agent_card_created_hook_failed card_id=%s error=%s", card.id, exc)

    def _card_ai_fingerprint(self, card: Card) -> str:
        payload = {
            "title": card.title,
            "vehicle": card.vehicle,
            "description": card.description,
            "updated_at": card.updated_at,
            "vehicle_profile": card.vehicle_profile.to_storage_dict(),
            "repair_order": card.repair_order.to_storage_dict(),
            "tags": card.tag_labels(),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _card_ai_next_interval_minutes(self, card: Card, *, changed: bool) -> int:
        haystack = " ".join(
            filter(
                None,
                [
                    str(card.title or ""),
                    str(card.description or ""),
                    str(card.vehicle or ""),
                    str(card.repair_order.reason or ""),
                    str(card.repair_order.comment or ""),
                    str(card.repair_order.note or ""),
                ],
            )
        ).casefold()
        waiting = any(
            token in haystack
            for token in ("ожид", "в пути", "клиент дума", "согласован", "заказали", "ждем", "ждём")
        )
        active = any(
            token in haystack
            for token in (
                "ошибк",
                "dtc",
                "vin",
                "запчаст",
                "пробег",
                "течь",
                "стук",
                "ремонт",
                "диагност",
            )
        )
        if not changed:
            if int(card.ai_run_count or 0) >= 5:
                return 240 if waiting else 180
            if int(card.ai_run_count or 0) >= 3:
                return 120 if waiting else 90
            return 90 if waiting else 60
        if waiting:
            return 50
        if active and int(card.ai_run_count or 0) <= 3:
            return 25
        return 40

    def _card_ai_max_runs(self, card: Card) -> int:
        haystack = self._card_ai_source_text(card).casefold()
        if any(
            token in haystack
            for token in ("ошибк", "dtc", "vin", "запчаст", "ремонт", "диагност", "течь", "стук")
        ):
            return 10
        return 8

    def _append_card_ai_log(
        self, card: Card, *, level: str, message: str, task_id: str = ""
    ) -> None:
        normalized_level = str(level or "INFO").strip().upper()
        if normalized_level not in _CARD_AI_LEVELS:
            normalized_level = "INFO"
        text = normalize_text(message, default="", limit=240)
        if not text:
            return
        normalized_task_id = normalize_text(task_id, default="", limit=64)
        previous = list(card.ai_autofill_log or [])
        if previous:
            last_entry = previous[-1]
            if (
                str(last_entry.get("level", "")).strip().upper() == normalized_level
                and str(last_entry.get("message", "")).strip() == text
                and str(last_entry.get("task_id", "")).strip() == normalized_task_id
            ):
                return
        item = {
            "level": normalized_level,
            "message": text,
            "timestamp": utc_now_iso(),
            "task_id": normalized_task_id,
        }
        card.ai_autofill_log = [*previous, item][-_CARD_AI_LOG_LIMIT:]

    def _card_ai_source_text(self, card: Card) -> str:
        return "\n".join(
            filter(
                None,
                [
                    str(card.title or ""),
                    str(card.vehicle or ""),
                    str(card.description or ""),
                    str(card.repair_order.reason or ""),
                    str(card.repair_order.comment or ""),
                    str(card.repair_order.note or ""),
                ],
            )
        )

    def _card_ai_detect_vin(self, card: Card, source_text: str) -> str:
        vehicle_profile_vin = normalize_text(card.vehicle_profile.vin, default="", limit=32).upper()
        if vehicle_profile_vin:
            return vehicle_profile_vin
        repair_order_vin = normalize_text(card.repair_order.vin, default="", limit=32).upper()
        if repair_order_vin:
            return repair_order_vin
        match = _CARD_AI_VIN_PATTERN.search(source_text.upper())
        return match.group(0) if match else ""

    def _card_ai_context_messages(self, card: Card) -> list[tuple[str, str]]:
        source_text = self._card_ai_source_text(card)
        haystack = source_text.casefold()
        messages: list[tuple[str, str]] = []
        vin = self._card_ai_detect_vin(card, source_text)
        messages.append(("INFO", "Обнаружен VIN." if vin else "VIN не найден."))
        if (
            "то" in haystack
            or "техническ" in haystack
            or "обслуж" in haystack
            or "пробег" in haystack
        ):
            messages.append(("INFO", "Найден контекст по ТО."))
        if (
            "запчаст" in haystack
            or "детал" in haystack
            or "фильтр" in haystack
            or "масл" in haystack
        ):
            messages.append(("INFO", "Найден контекст по запчастям."))
        if _CARD_AI_DTC_PATTERN.search(source_text):
            messages.append(("INFO", "Найдены коды ошибок."))
        return messages[:4]

    def _refresh_card_ai_fingerprint_if_agent_changed(
        self, card: Card, actor_name: str, source: str
    ) -> None:
        _ = (card, actor_name, source)
        return

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
        self._fail(
            "not_found", "Карточка не найдена.", status_code=404, details={"card_id": card_id}
        )

    def _find_sticky(self, stickies: list[StickyNote], sticky_id: str | None) -> StickyNote:
        if not sticky_id:
            self._fail(
                "validation_error", "Нужно передать sticky_id.", details={"field": "sticky_id"}
            )
        requested_id = str(sticky_id).strip()
        requested_short_id = requested_id.upper()
        for sticky in stickies:
            if (
                sticky.id == requested_id
                or short_entity_id(sticky.id, prefix="S").upper() == requested_short_id
            ):
                return sticky
        self._fail(
            "not_found", "Стикер не найден.", status_code=404, details={"sticky_id": sticky_id}
        )

    def _find_cashbox(self, cashboxes: list[CashBox], cashbox_id: str | None) -> CashBox:
        if not cashbox_id:
            self._fail(
                "validation_error", "Нужно передать cashbox_id.", details={"field": "cashbox_id"}
            )
        requested_id = str(cashbox_id).strip()
        requested_short_id = requested_id.upper()
        for cashbox in cashboxes:
            if (
                cashbox.id == requested_id
                or short_entity_id(cashbox.id, prefix="CB").upper() == requested_short_id
            ):
                return cashbox
        self._fail(
            "not_found", "Касса не найдена.", status_code=404, details={"cashbox_id": cashbox_id}
        )

    def _find_attachment(self, card: Card, attachment_id: str | None) -> Attachment:
        if not attachment_id:
            self._fail(
                "validation_error",
                "Нужно передать attachment_id.",
                details={"field": "attachment_id"},
            )
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
        if (
            not changed_fields
            and profile.to_storage_dict() == card.vehicle_profile.to_storage_dict()
        ):
            return False
        previous_profile = card.vehicle_profile
        previous_vehicle = card.vehicle
        card.vehicle_profile = profile
        card.vehicle = self._sync_vehicle_label_with_profile(
            previous_vehicle, previous_profile, profile
        )
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
        if (
            previous_total_seconds == deadline_total_seconds
            and previous_timestamp == next_timestamp
        ):
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
                details={
                    "tag": shared_label,
                    "before_color": before_tag.color,
                    "after_color": after_tag.color,
                },
            )
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="tags_changed",
            message=f"{actor_name} обновил набор меток",
            card_id=card.id,
            details={
                "before": [tag.to_dict() for tag in previous_tags],
                "after": [tag.to_dict() for tag in tags],
            },
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
        settings: dict[str, Any] | None = None,
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
        if settings is not None:
            order = self._apply_repair_order_payroll_snapshot(order, settings)
        if (
            previous_order.status != REPAIR_ORDER_STATUS_CLOSED
            and order.status == REPAIR_ORDER_STATUS_CLOSED
        ):
            self._ensure_repair_order_can_change_status(card, order.status, order=order)
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

    def _repair_order_payment_financial_signature(
        self, payment: RepairOrderPayment
    ) -> tuple[str, str, str, str]:
        return (
            payment.amount or "",
            payment.paid_at or "",
            payment.note or "",
            payment.cashbox_id or "",
        )

    def _repair_order_payment_target_cashbox(
        self,
        cashboxes: list[CashBox],
        payment: RepairOrderPayment,
        *,
        default_method: str,
    ) -> tuple[CashBox | None, str]:
        payment_method = normalize_repair_order_payment_method(
            payment.payment_method or default_method
        )
        selected_cashbox = (
            self._find_cashbox(cashboxes, payment.cashbox_id) if payment.cashbox_id else None
        )
        if selected_cashbox is not None:
            selected_method = repair_order_payment_method_from_cashbox_name(
                selected_cashbox.name,
                default=payment_method,
            )
            if selected_method in {
                REPAIR_ORDER_PAYMENT_METHOD_CASHLESS,
                REPAIR_ORDER_PAYMENT_METHOD_CARD,
            }:
                return selected_cashbox, selected_method

        target = self._cashbox_for_repair_order_payment_method(cashboxes, payment_method)
        if target is not None:
            return target, payment_method
        if selected_cashbox is not None:
            return selected_cashbox, repair_order_payment_method_from_cashbox_name(
                selected_cashbox.name,
                default=payment_method,
            )
        return None, payment_method

    def _cashbox_for_repair_order_payment_method(
        self, cashboxes: list[CashBox], payment_method: str
    ) -> CashBox | None:
        normalized_method = normalize_repair_order_payment_method(payment_method)
        if normalized_method == REPAIR_ORDER_PAYMENT_METHOD_CASHLESS:
            return self._first_cashbox_matching(
                cashboxes,
                exact_names=("безналичный", "безналичная касса", "безнал"),
                contains=("безнал", "cashless", "wire", "bank"),
            )
        if normalized_method == REPAIR_ORDER_PAYMENT_METHOD_CARD:
            return self._first_cashbox_matching(
                cashboxes,
                exact_names=("на карту", "карта", "карта мария"),
                contains=("на карту", "карта", "card", "мария"),
            )
        return self._first_cashbox_matching(
            cashboxes,
            exact_names=("наличный", "наличные", "касса наличных оплат"),
            contains=("налич", "cash"),
            exclude_contains=("безнал", "cashless", "wire", "bank", "карта", "card", "мария"),
        )

    def _first_cashbox_matching(
        self,
        cashboxes: list[CashBox],
        *,
        exact_names: tuple[str, ...],
        contains: tuple[str, ...],
        exclude_contains: tuple[str, ...] = (),
    ) -> CashBox | None:
        def matches(cashbox: CashBox, *, exact: bool) -> bool:
            name = cashbox.name.casefold()
            if any(marker in name for marker in exclude_contains):
                return False
            if exact:
                return name in exact_names
            return any(marker in name for marker in contains)

        exact = [item for item in cashboxes if matches(item, exact=True)]
        if exact:
            return exact[0]
        loose = [item for item in cashboxes if matches(item, exact=False)]
        return loose[0] if loose else None

    def _is_cashbox_transfer_transaction(self, transaction: CashTransaction) -> bool:
        note = normalize_text(transaction.note, default="", limit=240).casefold()
        return note.startswith("перемещение в ") or note.startswith("перемещение из ")

    def _find_repair_order_payment_by_cash_transaction(
        self,
        cards: list[Card],
        transaction_id: str | None,
    ) -> tuple[Card | None, RepairOrderPayment | None]:
        requested_id = normalize_text(transaction_id, default="", limit=128)
        if not requested_id:
            return None, None
        for card in cards:
            for payment in card.repair_order.payments:
                if payment.cash_transaction_id == requested_id:
                    return card, payment
        return None, None

    def _refresh_cashbox_updated_at(
        self, cashbox: CashBox, transactions: list[CashTransaction]
    ) -> None:
        latest_transaction = next(iter(self._cashbox_transactions(transactions, cashbox.id)), None)
        cashbox.updated_at = (
            latest_transaction.created_at if latest_transaction is not None else cashbox.created_at
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
        next_payments = [
            RepairOrderPayment.from_dict(payment.to_storage_dict())
            for payment in next_order.payments
        ]
        for payment in next_payments:
            cashbox, payment_method = self._repair_order_payment_target_cashbox(
                cashboxes,
                payment,
                default_method=next_order.payment_method,
            )
            payment.payment_method = payment_method
            if cashbox is not None:
                payment.cashbox_id = cashbox.id
                payment.cashbox_name = cashbox.name
        next_by_id = {payment.id: payment for payment in next_payments if payment.id}

        for previous_payment in previous_order.payments:
            existing_transaction = self._find_cash_transaction(
                cash_transactions, previous_payment.cash_transaction_id
            )
            next_payment = next_by_id.get(previous_payment.id)
            keep_transaction = (
                existing_transaction is not None
                and next_payment is not None
                and self._repair_order_payment_financial_signature(previous_payment)
                == self._repair_order_payment_financial_signature(next_payment)
            )
            if keep_transaction:
                next_payment.cash_transaction_id = existing_transaction.id
                next_payment.actor_name = (
                    next_payment.actor_name
                    or previous_payment.actor_name
                    or existing_transaction.actor_name
                )
                next_payment.cashbox_id = (
                    next_payment.cashbox_id
                    or previous_payment.cashbox_id
                    or existing_transaction.cashbox_id
                )
                next_payment.cashbox_name = (
                    next_payment.cashbox_name or previous_payment.cashbox_name
                )
                continue
            if existing_transaction is None:
                continue
            cash_transactions[:] = [
                item for item in cash_transactions if item.id != existing_transaction.id
            ]
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
            if not payment.cashbox_id:
                payment.cashbox_name = ""
                payment.cash_transaction_id = ""
                continue
            cashbox = self._find_cashbox(cashboxes, payment.cashbox_id)
            payment.cashbox_id = cashbox.id
            payment.cashbox_name = cashbox.name
            payment.payment_method = normalize_repair_order_payment_method(
                payment.payment_method or next_order.payment_method
            )
            if (
                self._find_cash_transaction(cash_transactions, payment.cash_transaction_id)
                is not None
            ):
                continue
            transaction = CashTransaction(
                id=str(uuid.uuid4()),
                cashbox_id=cashbox.id,
                direction="income",
                amount_minor=normalize_money_minor(payment.amount, minimum=1),
                note=self._validated_cash_transaction_note(
                    payment.note or f"Заказ-наряд №{next_order.number or '-'}"
                ),
                created_at=payment.paid_at or utc_now_iso(),
                actor_name=payment.actor_name or actor_name,
                source=source,
            )
            cash_transactions.append(transaction)
            cash_transactions.sort(
                key=lambda item: (
                    self._cash_transaction_sortable_datetime(item.created_at),
                    item.id,
                )
            )
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

    def _autofill_repair_order(
        self, card: Card, cards: list[Card], *, overwrite: bool
    ) -> tuple[RepairOrder, dict[str, object]]:
        order = self._prepared_repair_order(
            RepairOrder.from_dict(card.repair_order.to_storage_dict()),
            cards,
            card=card,
            exclude_card_id=card.id,
        )
        profile = card.vehicle_profile
        changed_fields: list[str] = []
        original_order = order.to_storage_dict()
        suggested_client = profile.customer_name or self._extract_customer_name(card)
        if overwrite or not order.client:
            order.client = suggested_client or order.client
        if order.client != original_order.get("client", ""):
            changed_fields.append("client")
        suggested_phone = profile.customer_phone or self._extract_phone(card)
        if overwrite or not order.phone:
            order.phone = suggested_phone or order.phone
        if order.phone != original_order.get("phone", ""):
            changed_fields.append("phone")
        if overwrite or not order.vehicle:
            order.vehicle = card.vehicle_display() or order.vehicle
        if order.vehicle != original_order.get("vehicle", ""):
            changed_fields.append("vehicle")
        if overwrite or not order.opened_at:
            order.opened_at = self._repair_order_card_datetime(card.created_at) or order.opened_at
        if order.opened_at != original_order.get("opened_at", ""):
            changed_fields.append("opened_at")
        if overwrite or not order.vin:
            order.vin = profile.vin or self._extract_vin(card, fallback=order.vin)
        if order.vin != original_order.get("vin", ""):
            changed_fields.append("vin")
        if overwrite or not order.mileage:
            order.mileage = (
                str(profile.mileage) if profile.mileage else ""
            ) or self._extract_mileage(card, fallback=order.mileage)
        if order.mileage != original_order.get("mileage", ""):
            changed_fields.append("mileage")
        if overwrite or not order.reason:
            order.reason = self._build_repair_order_reason(card) or order.reason
        if order.reason != original_order.get("reason", ""):
            changed_fields.append("reason")
        if overwrite or not order.license_plate:
            order.license_plate = self._extract_license_plate(card, fallback=order.license_plate)
        if order.license_plate != original_order.get("license_plate", ""):
            changed_fields.append("license_plate")

        if overwrite or not order.comment:
            order.comment = self._build_client_description(card, order) or order.comment
        if order.comment != original_order.get("comment", ""):
            changed_fields.append("comment")
        if overwrite or not order.note:
            order.note = self._build_internal_repair_note(card, order) or order.note
        if order.note != original_order.get("note", ""):
            changed_fields.append("note")

        review_items: list[str] = []
        autofill_report: dict[str, object] = {
            "filled_fields": changed_fields,
            "review_items": review_items,
        }
        return order, autofill_report

    def _related_cards(self, card: Card, cards: list[Card]) -> list[Card]:
        current_vin = self._extract_vin(card, fallback="")
        current_license = self._extract_license_plate(card, fallback="")
        current_phone = self._extract_phone(card)
        current_vehicle = self._normalize_search_text(
            card.vehicle_display() or card.repair_order.vehicle
        )
        current_customer = self._normalize_search_text(
            card.vehicle_profile.customer_name or card.repair_order.client
        )
        ranked: list[tuple[int, str, Card]] = []
        for candidate in cards:
            if candidate.id == card.id:
                continue
            score = 0
            if current_vin and current_vin == self._extract_vin(candidate, fallback=""):
                score += 12
            if current_license and current_license == self._extract_license_plate(
                candidate, fallback=""
            ):
                score += 10
            if current_phone and current_phone == self._extract_phone(candidate):
                score += 8
            candidate_vehicle = self._normalize_search_text(
                candidate.vehicle_display() or candidate.repair_order.vehicle
            )
            if current_vehicle and current_vehicle == candidate_vehicle:
                score += 2
            candidate_customer = self._normalize_search_text(
                candidate.vehicle_profile.customer_name or candidate.repair_order.client
            )
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

    def _extract_phone(self, card: Card, *, fallback: str = "") -> str:
        if card.vehicle_profile.customer_phone:
            return normalize_text(card.vehicle_profile.customer_phone, default="", limit=32)
        if card.repair_order.phone:
            return self._format_phone(card.repair_order.phone)
        haystack = self._repair_analysis_text(card)
        match = _PHONE_PATTERN.search(haystack)
        if not match:
            return fallback
        return self._format_phone(match.group(0)) or fallback

    def _format_phone(self, value: str) -> str:
        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) == 11 and digits.startswith("7"):
            return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
        return normalize_text(value, default="", limit=32)

    def _extract_customer_name(self, card: Card, *, fallback: str = "") -> str:
        if card.vehicle_profile.customer_name:
            return normalize_text(card.vehicle_profile.customer_name, default="", limit=80)
        order_name = normalize_text(card.repair_order.client, default="", limit=80)
        if order_name:
            return order_name
        blocked_tokens = {
            "ТЕЛЕФОН",
            "PHONE",
            "VIN",
            "ГОСНОМЕР",
            "ПРОБЕГ",
            "MILEAGE",
            "ФАКТЫ",
            "СУТЬ",
            "ДАННЫЕ",
            "РАБОТЫ",
            "ПРОВЕРКИ",
        }

        def _normalize_match(value: str) -> str:
            parts: list[str] = []
            for part in str(value or "").strip().split():
                normalized = str(part or "").strip()
                if not normalized:
                    continue
                if normalized.upper().strip(":.,-") in blocked_tokens:
                    break
                parts.append(normalized)
            if not parts:
                return ""
            return " ".join(part[:1].upper() + part[1:].lower() for part in parts)[:80]

        for line in self._repair_text_lines(
            card.description,
            card.repair_order.reason,
            card.repair_order.comment,
            card.repair_order.note,
            card.vehicle_profile.oem_notes,
        ):
            match = _CUSTOMER_NAME_PATTERN.search(line)
            if not match:
                continue
            normalized_match = _normalize_match(str(match.group(1) or ""))
            if normalized_match:
                return normalized_match

        match = _CUSTOMER_NAME_PATTERN.search(self._repair_analysis_text(card))
        if not match:
            return fallback
        normalized_match = _normalize_match(str(match.group(1) or ""))
        return normalized_match or fallback

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

    def _build_card_cleanup_vehicle_label(self, card: Card) -> str:
        if normalize_text(card.vehicle, default="", limit=CARD_VEHICLE_LIMIT):
            return ""
        candidate = normalize_text(card.repair_order.vehicle, default="", limit=CARD_VEHICLE_LIMIT)
        if candidate:
            return candidate
        display_name = normalize_text(
            card.vehicle_profile.display_name(), default="", limit=CARD_VEHICLE_LIMIT
        )
        if display_name:
            return display_name
        return ""

    def _build_card_cleanup_vehicle_profile_patch(self, card: Card) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        profile = card.vehicle_profile
        if not normalize_text(profile.customer_name, default="", limit=120):
            customer_name = self._extract_customer_name(card)
            if customer_name:
                patch["customer_name"] = customer_name
        if not normalize_text(profile.customer_phone, default="", limit=40):
            customer_phone = self._extract_phone(card)
            if customer_phone:
                patch["customer_phone"] = customer_phone
        if not normalize_text(profile.vin, default="", limit=32):
            vin = normalize_text(
                card.repair_order.vin or self._extract_vin(card), default="", limit=32
            ).upper()
            if vin:
                patch["vin"] = vin
        if not profile.mileage:
            mileage = normalize_text(
                card.repair_order.mileage or self._extract_mileage(card), default="", limit=40
            )
            if mileage:
                patch["mileage"] = mileage
        if not normalize_text(profile.oem_notes, default="", limit=1200):
            notes = "\n".join(
                self._repair_text_lines(card.repair_order.comment, card.repair_order.note)
            )
            normalized_notes = normalize_text(notes, default="", limit=1200)
            if normalized_notes:
                patch["oem_notes"] = normalized_notes
        return patch

    def _build_card_cleanup_data_lines(self, card: Card) -> list[str]:
        lines: list[str] = []
        vehicle = normalize_text(
            card.vehicle or card.repair_order.vehicle or card.vehicle_profile.display_name(),
            default="",
            limit=120,
        )
        customer_name = self._extract_customer_name(card)
        customer_phone = self._extract_phone(card)
        vin = normalize_text(
            card.vehicle_profile.vin or card.repair_order.vin or self._extract_vin(card),
            default="",
            limit=32,
        ).upper()
        mileage = normalize_text(
            str(card.vehicle_profile.mileage or "")
            or card.repair_order.mileage
            or self._extract_mileage(card),
            default="",
            limit=40,
        )
        for label, value in (
            ("Автомобиль", vehicle),
            ("Клиент", customer_name),
            ("Телефон", customer_phone),
            ("VIN", vin),
            ("Пробег", mileage),
        ):
            if not value:
                continue
            line = f"{label}: {value}"
            if line not in lines:
                lines.append(line)
        return lines

    def _build_card_cleanup_description(self, card: Card) -> str:
        summary = normalize_text(
            card.repair_order.reason or self._build_repair_order_reason(card), default="", limit=320
        )
        raw_lines = self._repair_text_lines(
            card.description,
            card.repair_order.reason,
            card.repair_order.comment,
            card.repair_order.note,
            card.vehicle_profile.oem_notes,
        )
        seen: set[str] = set()
        fact_lines: list[str] = []
        work_lines: list[str] = []
        for line in raw_lines:
            key = line.casefold()
            if key in seen:
                continue
            seen.add(key)
            if summary and key == summary.casefold():
                continue
            if self._looks_like_work_item(line):
                work_lines.append(line)
            else:
                fact_lines.append(line)
        for row in card.repair_order.works:
            work_name = self._clean_repair_text_fragment(row.name, limit=200)
            if not work_name:
                continue
            quantity = normalize_text(row.quantity, default="", limit=40)
            line = (
                f"{work_name} x {quantity}"
                if quantity and quantity not in {"0", "1", "1.0"}
                else work_name
            )
            if line.casefold() in seen:
                continue
            seen.add(line.casefold())
            work_lines.append(line)
        sections: list[tuple[str, list[str]]] = []
        if summary:
            sections.append(("СУТЬ", [summary]))
        if fact_lines:
            sections.append(("ФАКТЫ", fact_lines[:8]))
        if work_lines:
            sections.append(("РАБОТЫ / ПРОВЕРКИ", work_lines[:8]))
        data_lines = self._build_card_cleanup_data_lines(card)
        if data_lines:
            sections.append(("ДАННЫЕ", data_lines[:6]))
        if not sections:
            return self._validated_description(
                normalize_text(card.description, default="", limit=CARD_DESCRIPTION_LIMIT)
            )
        cleaned = "\n\n".join(
            label + ":\n" + "\n".join(f"- {line}" for line in lines if line)
            for label, lines in sections
            if lines
        )
        return self._validated_description(cleaned)

    def _cleanup_profile_patch_applied(self, card: Card, patch: dict[str, Any]) -> bool:
        if not patch:
            return True
        profile_payload = card.vehicle_profile.to_storage_dict()
        for field_name, expected in patch.items():
            actual = profile_payload.get(field_name)
            if str(actual or "").strip() != str(expected or "").strip():
                return False
        return True

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
                any(
                    token in lowered
                    for token in (
                        "жалоб",
                        "пинки",
                        "рывк",
                        "стук",
                        "шум",
                        "вибрац",
                        "течь",
                        "не завод",
                        "горит",
                    )
                )
                and not self._looks_like_work_item(cleaned)
            ):
                if cleaned not in complaint_lines:
                    complaint_lines.append(cleaned)
        if complaint_lines:
            return normalize_text(" ".join(complaint_lines[:2]), default="", limit=400)
        first_description = next(
            (line for line in description_lines if not self._looks_like_work_item(line)), ""
        )
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
            if _REPAIR_FINDING_PREFIX_PATTERN.match(line) or any(
                keyword in lowered for keyword in _REPAIR_FINDING_KEYWORDS
            ):
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
            if _REPAIR_RECOMMENDATION_PREFIX_PATTERN.match(line) or any(
                keyword in lowered for keyword in _REPAIR_RECOMMENDATION_KEYWORDS
            ):
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
                    row = self._repair_row_from_item(
                        item, section=section, profile=profile, force_section=True
                    )
                    if row is not None:
                        self._append_repair_row(suggested, row)
                continue
            for item in self._split_repair_items(line):
                row = self._repair_row_from_item(
                    item, section=section, profile=profile, force_section=False
                )
                if row is not None:
                    self._append_repair_row(suggested, row)
        for row in self._template_repair_rows(card, profile=profile, section=section):
            self._append_repair_row(suggested, row)
        return suggested

    def _line_declares_repair_section(self, line: str, *, section: str) -> bool:
        lowered = line.casefold()
        markers = (
            _REPAIR_WORK_SECTION_MARKERS if section == "works" else _REPAIR_MATERIAL_SECTION_MARKERS
        )
        return any(
            lowered.startswith(f"{marker}:") or lowered.startswith(f"{marker} -")
            for marker in markers
        )

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
        if "atf" in lowered or (
            "масло" in lowered
            and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб"))
        ):
            return "ATF"
        if "антифриз" in lowered or "охлажда" in lowered:
            return "Антифриз"
        if "масля" in lowered and "фильтр" in lowered:
            return "Масляный фильтр"
        if "фильтр" in lowered and any(
            token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб")
        ):
            return "Фильтр АКПП"
        if "масло" in lowered and any(
            token in lowered for token in ("двиг", "мотор", "5w", "0w", "10w", "15w")
        ):
            return "Моторное масло"
        return cleaned[:1].upper() + cleaned[1:]

    def _extract_material_quantity(self, text: str, *, profile: VehicleProfile) -> str:
        explicit_quantity = self._extract_row_quantity(text, default="")
        if explicit_quantity:
            return explicit_quantity
        lowered = text.casefold()
        if "atf" in lowered or (
            "масло" in lowered
            and any(token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб"))
        ):
            return self._format_quantity_value(profile.oil_gearbox_capacity_l)
        if "антифриз" in lowered or "охлажда" in lowered:
            return self._format_quantity_value(profile.coolant_capacity_l)
        if "масло" in lowered and any(
            token in lowered for token in ("двиг", "мотор", "5w", "0w", "10w", "15w")
        ):
            return self._format_quantity_value(profile.oil_engine_capacity_l)
        if "фильтр" in lowered or "проклад" in lowered:
            return "1"
        return ""

    def _template_repair_rows(
        self, card: Card, *, profile: VehicleProfile, section: str
    ) -> list[RepairOrderRow]:
        combined_text = self._repair_analysis_text(card)
        lowered = combined_text.casefold()
        rows: list[RepairOrderRow] = []
        has_engine_oil_service = self._is_engine_oil_service(lowered)
        has_transmission_service = self._is_transmission_service(lowered)
        has_coolant_service = self._is_coolant_service(lowered)
        if section == "works":
            if "диагност" in lowered:
                diagnostic_name = (
                    "Диагностика DSG/АКПП" if has_transmission_service else "Диагностика"
                )
                self._append_repair_row(rows, RepairOrderRow(name=diagnostic_name, quantity="1"))
            if has_engine_oil_service:
                self._append_repair_row(
                    rows, RepairOrderRow(name="Замена масла двигателя", quantity="1")
                )
            if has_transmission_service:
                transmission_name = (
                    "ТО DSG/АКПП"
                    if any(token in lowered for token in ("dsg", "акпп"))
                    else "Обслуживание трансмиссии"
                )
                self._append_repair_row(rows, RepairOrderRow(name=transmission_name, quantity="1"))
            if has_coolant_service:
                self._append_repair_row(
                    rows, RepairOrderRow(name="Замена охлаждающей жидкости", quantity="1")
                )
            return rows
        if has_engine_oil_service:
            self._append_repair_row(
                rows,
                RepairOrderRow(
                    name="Моторное масло",
                    quantity=self._extract_specific_quantity(
                        combined_text, "масло", "двиг", "мотор"
                    )
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
                    quantity=self._extract_specific_quantity(
                        combined_text, "atf", "акпп", "dsg", "вариатор", "cvt", "короб"
                    )
                    or self._format_quantity_value(profile.oil_gearbox_capacity_l),
                ),
            )
            if "фильтр" in lowered and any(
                token in lowered for token in ("акпп", "dsg", "вариатор", "cvt", "короб")
            ):
                self._append_repair_row(rows, RepairOrderRow(name="Фильтр АКПП", quantity="1"))
            if "проклад" in lowered and "поддон" in lowered:
                self._append_repair_row(
                    rows, RepairOrderRow(name="Прокладка поддона", quantity="1")
                )
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
        has_oil = "масло" in lowered_text and any(
            token in lowered_text for token in ("двиг", "мотор", "5w", "0w", "10w", "15w")
        )
        has_service = any(token in lowered_text for token in ("замен", "обслуж")) or bool(
            re.search(r"\bто\b", lowered_text)
        )
        return has_oil and has_service and not self._is_transmission_service(lowered_text)

    def _is_transmission_service(self, lowered_text: str) -> bool:
        has_transmission = any(
            token in lowered_text
            for token in ("акпп", "dsg", "вариатор", "cvt", "короб", "трансмис")
        )
        has_service = any(token in lowered_text for token in ("замен", "обслуж", "atf")) or bool(
            re.search(r"\bто\b", lowered_text)
        )
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
                updated_rows.append(
                    RepairOrderRow(name=row.name, quantity=row.quantity, price=price, total="")
                )
                hits.append(
                    {"section": section, "name": row.name, "price": price, "source": source}
                )
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
                rows = (
                    candidate.repair_order.works
                    if section == "works"
                    else candidate.repair_order.materials
                )
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

        global_candidates = [
            candidate for candidate in cards if self._card_has_repair_order(candidate)
        ]
        global_prices = collect_prices(global_candidates)
        if len(global_prices) >= 2 and len(set(global_prices)) == 1:
            return global_prices[0], "board_history"
        return "", ""

    def _merge_repair_order_rows(
        self, existing: list[RepairOrderRow], suggested: list[RepairOrderRow]
    ) -> list[RepairOrderRow]:
        if not suggested:
            return list(existing)
        merged = [RepairOrderRow.from_dict(row.to_dict()) for row in existing]
        for row in suggested:
            self._append_repair_row(merged, row)
        return normalize_repair_order_rows([row.to_dict() for row in merged])

    def _build_client_description(self, card: Card, order: RepairOrder) -> str:
        parts: list[str] = []
        reason = normalize_text(
            order.reason or self._build_repair_order_reason(card), default="", limit=320
        )
        customer_name = self._extract_customer_name(card)
        customer_phone = self._extract_phone(card)
        vehicle = normalize_text(order.vehicle or card.vehicle_display(), default="", limit=120)
        license_plate = normalize_text(
            order.license_plate or self._extract_license_plate(card), default="", limit=40
        )
        vin = normalize_text(order.vin or self._extract_vin(card), default="", limit=32).upper()
        mileage = normalize_text(order.mileage or self._extract_mileage(card), default="", limit=40)
        findings = self._extract_repair_findings(card)
        recommendations = self._extract_repair_recommendations(card)
        client_facts: list[str] = []
        if customer_name:
            client_facts.append(f"клиент: {customer_name}")
        if customer_phone:
            client_facts.append(f"телефон: {customer_phone}")
        if vehicle:
            client_facts.append(f"автомобиль: {vehicle}")
        if license_plate:
            client_facts.append(f"госномер: {license_plate}")
        if vin:
            client_facts.append(f"VIN: {vin}")
        if mileage:
            client_facts.append(f"пробег: {mileage}")
        if client_facts:
            parts.append("Заявка принята. " + ", ".join(client_facts[:6]) + ".")
        if reason:
            parts.append(f"Клиент обратился с запросом: {reason.rstrip('.')} .".replace(" .", "."))
        if findings:
            parts.append(
                f"В ходе проверки выявлено: {'; '.join(findings[:2]).rstrip('.')} .".replace(
                    " .", "."
                )
            )
        if recommendations:
            parts.append(
                f"Рекомендовано далее: {'; '.join(recommendations[:2]).rstrip('.')} .".replace(
                    " .", "."
                )
            )
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
    ) -> str:
        parts: list[str] = []
        findings = self._extract_repair_findings(card)
        recommendations = self._extract_repair_recommendations(card)
        if findings:
            parts.append(f"Технические замечания: {'; '.join(findings[:3])}.")
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
            patch["works"] = self._validated_repair_order_rows(
                patch["works"], field_name="repair_order.works"
            )
        if "materials" in patch:
            patch["materials"] = self._validated_repair_order_rows(
                patch["materials"],
                field_name="repair_order.materials",
            )
        if "tags" in patch:
            patch["tags"] = self._validated_repair_order_tags(
                patch["tags"], field_name="repair_order.tags"
            )
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
            prepared.date = (
                self._repair_order_card_datetime(card.created_at if card is not None else "")
                or self._repair_order_now()
            )
        if not prepared.opened_at:
            prepared.opened_at = (
                self._repair_order_card_datetime(card.created_at if card is not None else "")
                or prepared.date
                or self._repair_order_now()
            )
        prepared.status = normalize_repair_order_status(
            prepared.status, default=REPAIR_ORDER_STATUS_OPEN
        )
        if prepared.status == REPAIR_ORDER_STATUS_CLOSED and not prepared.closed_at:
            prepared.closed_at = self._repair_order_now()
        if prepared.status != REPAIR_ORDER_STATUS_CLOSED:
            prepared.closed_at = ""
        return prepared

    def _next_repair_order_number(
        self, cards: list[Card], *, exclude_card_id: str | None = None
    ) -> str:
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

    def _ensure_ready_column_for_bundle(
        self,
        bundle: dict[str, Any],
        *,
        actor_name: str = "СИСТЕМА",
        source: str = "system",
    ) -> tuple[str, bool]:
        ready_column_id, changed = ensure_ready_column(bundle["columns"], bundle["settings"])
        if changed:
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="ready_column_synchronized",
                message=f"{actor_name} закрепил колонку готовых автомобилей",
                card_id=None,
                details={"column_id": ready_column_id},
            )
        return ready_column_id, changed

    def _apply_ready_column_side_effects(
        self,
        card: Card,
        cards: list[Card],
        events: list[AuditEvent],
        actor_name: str,
        source: str,
        *,
        before_column: str,
        after_column: str,
        ready_column_id: str,
        bundle: dict[str, Any],
    ) -> tuple[bool, list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        changed = False
        is_ready_after = after_column == ready_column_id
        was_ready_before = before_column == ready_column_id

        if is_ready_after:
            changed = self._set_ready_card_tag(
                card, events, actor_name, source, enabled=True
            ) or changed
            if self._card_has_repair_order(card):
                if card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED:
                    changed = self._set_repair_order_status_internal(
                        card,
                        cards,
                        REPAIR_ORDER_STATUS_READY,
                        events,
                        actor_name,
                        source,
                        bundle,
                    ) or changed
            else:
                warnings.append(
                    {
                        "code": "repair_order_missing",
                        "message": "Карточка готова, но заказ-наряд не найден.",
                    }
                )
        elif was_ready_before:
            changed = self._set_ready_card_tag(
                card, events, actor_name, source, enabled=False
            ) or changed
            if (
                self._card_has_repair_order(card)
                and card.repair_order.status == REPAIR_ORDER_STATUS_READY
            ):
                changed = self._set_repair_order_status_internal(
                    card,
                    cards,
                    REPAIR_ORDER_STATUS_OPEN,
                    events,
                    actor_name,
                    source,
                    bundle,
                ) or changed
        return changed, warnings

    def _set_ready_card_tag(
        self,
        card: Card,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
        *,
        enabled: bool,
    ) -> bool:
        previous_tags = list(card.tags)
        previous_has_tag = any(tag.label == _READY_CARD_TAG_NORMALIZED for tag in previous_tags)
        if enabled and previous_has_tag:
            return False
        if not enabled and not previous_has_tag:
            return False

        if enabled:
            card.tags = previous_tags + [
                CardTag(label=READY_CARD_TAG_LABEL, color=READY_CARD_TAG_COLOR)
            ]
            action = "tag_added"
            message = f"{actor_name} добавил метку готовности"
        else:
            card.tags = [tag for tag in previous_tags if tag.label != _READY_CARD_TAG_NORMALIZED]
            action = "tag_removed"
            message = f"{actor_name} снял метку готовности"

        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action=action,
            message=message,
            card_id=card.id,
            details={"tag": _READY_CARD_TAG_NORMALIZED, "system": True},
        )
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="tags_changed",
            message=f"{actor_name} обновил набор меток",
            card_id=card.id,
            details={
                "before": [tag.to_dict() for tag in previous_tags],
                "after": [tag.to_dict() for tag in card.tags],
            },
        )
        return True

    def _set_repair_order_status_internal(
        self,
        card: Card,
        cards: list[Card],
        status: str,
        events: list[AuditEvent],
        actor_name: str,
        source: str,
        bundle: dict[str, Any],
    ) -> bool:
        if card.repair_order.status == status:
            return False
        next_payload = card.repair_order.to_storage_dict()
        next_payload["status"] = status
        if status != REPAIR_ORDER_STATUS_CLOSED:
            next_payload["closed_at"] = ""
        changed = self._update_repair_order(
            card,
            cards,
            next_payload,
            events,
            actor_name,
            source,
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            settings=bundle["settings"],
        )
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
        return changed

    def _card_has_repair_order(self, card: Card) -> bool:
        return not card.repair_order.is_empty()

    def _card_has_inconsistent_repair_order_state(self, card: Card) -> bool:
        return card.archived and self._card_has_open_repair_order(card)

    def _card_has_open_repair_order(self, card: Card) -> bool:
        return (
            self._card_has_repair_order(card)
            and card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED
        )

    def _ensure_repair_order_state_supported(self, card: Card) -> None:
        if not self._card_has_inconsistent_repair_order_state(card):
            return
        repair_order_number = str(card.repair_order.number or "").strip()
        number_suffix = f" №{repair_order_number}" if repair_order_number else ""
        self._fail(
            "repair_order_archived_card_conflict",
            f"Обнаружено неконсистентное состояние: архивная карточка содержит открытый заказ-наряд{number_suffix}. "
            "Откройте рабочую карточку или закройте заказ-наряд перед дальнейшей работой.",
            status_code=409,
            details={"card_id": card.id, "repair_order_number": repair_order_number},
        )

    def _repair_order_seed_payload(self, card: Card) -> dict[str, Any]:
        return {
            **self._repair_order_card_field_suggestions(card),
            "reason": card.title,
            "comment": card.description,
        }

    def _repair_order_card_field_suggestions(self, card: Card) -> dict[str, str]:
        profile = card.vehicle_profile
        mileage = (
            str(profile.mileage)
            if profile.mileage is not None
            else self._extract_mileage(card, fallback=card.repair_order.mileage)
        )
        return {
            "client": profile.customer_name or self._extract_customer_name(card),
            "phone": profile.customer_phone or self._extract_phone(card),
            "vehicle": card.vehicle_display(),
            "license_plate": self._extract_license_plate(
                card, fallback=card.repair_order.license_plate
            ),
            "vin": profile.vin or self._extract_vin(card, fallback=card.repair_order.vin),
            "mileage": mileage,
        }

    def _fill_missing_repair_order_fields_from_card(self, card: Card) -> list[str]:
        if not self._card_has_repair_order(card):
            return []
        suggestions = self._repair_order_card_field_suggestions(card)
        changed_fields: list[str] = []
        for field_name in ("client", "phone", "vehicle", "license_plate", "vin", "mileage"):
            current_value = normalize_text(
                getattr(card.repair_order, field_name, ""), default="", limit=160
            )
            suggested_value = normalize_text(
                suggestions.get(field_name, ""), default="", limit=160
            )
            if current_value or not suggested_value:
                continue
            setattr(card.repair_order, field_name, suggested_value)
            changed_fields.append(field_name)
        return changed_fields

    def _ensure_repair_order_exists(
        self,
        card: Card,
        cards: list[Card],
        events: list[AuditEvent],
        actor_name: str,
        source: str,
        *,
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> bool:
        if self._card_has_repair_order(card) or card.archived:
            return False
        changed = self._update_repair_order(
            card,
            cards,
            self._repair_order_seed_payload(card),
            events,
            actor_name,
            source,
            cashboxes=cashboxes,
            cash_transactions=cash_transactions,
            settings=settings,
        )
        if changed:
            self._touch_card(card, actor_name)
            self._ensure_repair_order_text_file(card, force=True)
        return changed

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

    def _ensure_repair_order_can_change_status(
        self, card: Card, status: str, *, order: RepairOrder | None = None
    ) -> None:
        checked_order = order or card.repair_order
        if status != REPAIR_ORDER_STATUS_CLOSED or checked_order.is_paid():
            return
        self._fail(
            "repair_order_payment_required",
            "Для закрытия заказ-наряда необходимо выполнить оплату.",
            status_code=409,
            details={
                "card_id": card.id,
                "due_total": checked_order.due_total_amount(),
                "payment_status": checked_order.payment_status(),
            },
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
        if status == REPAIR_ORDER_STATUS_CLOSED:
            return "Закрыт"
        if status == REPAIR_ORDER_STATUS_READY:
            return "Готов"
        return "Открыт"

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

    def _parse_repair_order_datetime(self, value: str | None) -> datetime | None:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.astimezone(UTC)
        raw_value = normalize_text(value, default="", limit=32)
        if not raw_value:
            return None
        try:
            local_dt = datetime.strptime(raw_value, "%d.%m.%Y %H:%M")
        except ValueError:
            return None
        local_tz = datetime.now().astimezone().tzinfo or UTC
        return local_dt.replace(tzinfo=local_tz).astimezone(UTC)

    def _repair_order_opened_sort_value(self, card: Card) -> str:
        order = card.repair_order
        return self._repair_order_sortable_datetime(
            order.opened_at or card.created_at or order.date
        )

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
        number = (
            normalize_file_name(card.repair_order.number or f"card-{card.id}") or f"card-{card.id}"
        )
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
        if card.vehicle_profile.registration_plate:
            return card.vehicle_profile.registration_plate
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
            return normalize_license_plate(match.group(0))
        return normalize_license_plate(fallback)

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

    def _merge_vehicle_profile_patch(
        self, existing: VehicleProfile, value
    ) -> tuple[VehicleProfile, list[str]]:
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
        if not current_vehicle.strip():
            return self._resolved_card_vehicle_label("", next_profile)
        if previous_display and current_vehicle.strip() == self._validated_vehicle(
            previous_display
        ):
            return self._resolved_card_vehicle_label("", next_profile)
        return self._validated_vehicle(current_vehicle)

    def _attachment_agent_dict(
        self,
        card_id: str,
        attachment: Attachment,
        *,
        attachment_path: Path | None = None,
    ) -> dict[str, Any]:
        attachment_type = self._attachment_type_from_metadata(attachment)
        content_kind = self._attachment_content_kind(attachment_type)
        payload = {
            "id": attachment.id,
            "card_id": card_id,
            "file_name": attachment.file_name,
            "mime_type": attachment.mime_type,
            "size_bytes": attachment.size_bytes,
            "created_at": attachment.created_at,
            "created_by": attachment.created_by,
            "removed": attachment.removed,
            "removed_at": attachment.removed_at,
            "removed_by": attachment.removed_by,
            "extension": self._attachment_extension(attachment.file_name),
            "content_type": attachment_type,
            "content_kind": content_kind,
            "readable_as_text": content_kind in {"text", "pdf", "docx", "xlsx"},
            "supports_base64": True,
            "download_path": f"/api/attachment?card_id={card_id}&attachment_id={attachment.id}",
        }
        if attachment_path is not None:
            payload["exists_on_disk"] = attachment_path.exists()
            if attachment_path.exists():
                payload["sha256"] = hashlib.sha256(attachment_path.read_bytes()).hexdigest()
        return payload

    def _attachment_content_payload(
        self,
        *,
        attachment: Attachment,
        content: bytes,
        mode: str,
        max_chars: int,
        include_base64: bool,
        max_base64_bytes: int,
    ) -> dict[str, Any]:
        attachment_type = self._attachment_type_from_metadata(attachment)
        content_kind = self._attachment_content_kind(attachment_type)
        payload: dict[str, Any] = {
            "mode": mode,
            "content_kind": content_kind,
            "content_type": attachment_type,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "text": "",
            "text_length": 0,
            "text_truncated": False,
            "encoding": "",
            "extraction_status": "unsupported",
            "extraction_warnings": [],
            "base64_included": False,
        }
        if content_kind == "image":
            payload["image"] = self._attachment_image_metadata(content, attachment_type)
            payload["extraction_status"] = "image_binary"
            payload["extraction_warnings"].append(
                "Изображение не распознается OCR на стороне CRM; используйте base64/data_url для vision-модели агента."
            )
        elif content_kind == "text":
            text, encoding = self._decode_attachment_text(content)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = encoding
            payload["extraction_status"] = "ok"
        elif content_kind == "docx":
            text = self._extract_docx_text(content)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = "office-openxml"
            payload["extraction_status"] = "ok" if text.strip() else "empty"
        elif content_kind == "xlsx":
            text = self._extract_xlsx_text(content)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = "office-openxml"
            payload["extraction_status"] = "ok" if text.strip() else "empty"
        elif content_kind == "pdf":
            text = self._extract_pdf_text(content)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = "pdf-best-effort"
            payload["extraction_status"] = "best_effort" if text.strip() else "unsupported"
            if not text.strip():
                payload["extraction_warnings"].append(
                    "PDF не содержит простого текстового слоя, который можно извлечь штатными средствами."
                )
        elif content_kind == "office_legacy":
            payload["extraction_warnings"].append(
                "Старые DOC/XLS сохранены как бинарные OLE-файлы; для чтения агентом загрузите DOCX/XLSX или используйте base64."
            )

        if include_base64:
            if len(content) <= max_base64_bytes:
                encoded = base64.b64encode(content).decode("ascii")
                mime_type = attachment.mime_type or "application/octet-stream"
                payload["base64"] = encoded
                payload["data_url"] = f"data:{mime_type};base64,{encoded}"
                payload["base64_included"] = True
            else:
                payload["base64_omitted_reason"] = (
                    f"file_size_exceeds_limit:{len(content)}>{max_base64_bytes}"
                )
        return payload

    def _set_truncated_attachment_text(
        self, payload: dict[str, Any], text: str, max_chars: int
    ) -> None:
        text = str(text or "")
        payload["text_length"] = len(text)
        if len(text) > max_chars:
            payload["text"] = text[:max_chars]
            payload["text_truncated"] = True
        else:
            payload["text"] = text
            payload["text_truncated"] = False

    def _attachment_type_from_metadata(self, attachment: Attachment) -> str:
        extension = self._attachment_extension(attachment.file_name)
        if extension in _ATTACHMENT_EXTENSION_TO_TYPE:
            return _ATTACHMENT_EXTENSION_TO_TYPE[extension]
        mime_type = self._normalized_attachment_mime_type(attachment.mime_type)
        for type_name, spec in _ATTACHMENT_TYPE_SPECS.items():
            if mime_type in spec["mime_types"]:
                return type_name
        return "binary"

    def _attachment_content_kind(self, attachment_type: str) -> str:
        if attachment_type in {"png", "jpeg", "gif", "webp"}:
            return "image"
        if attachment_type == "txt":
            return "text"
        if attachment_type in {"pdf", "docx", "xlsx"}:
            return attachment_type
        if attachment_type in {"doc", "xls"}:
            return "office_legacy"
        return "binary"

    def _decode_attachment_text(self, content: bytes) -> tuple[str, str]:
        encodings = ("utf-8-sig", "utf-8", "cp1251", "utf-16", "latin-1")
        for encoding in encodings:
            try:
                decoded = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            return decoded, encoding
        return content.decode("utf-8", errors="replace"), "utf-8-replace"

    def _extract_docx_text(self, content: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > _ATTACHMENT_XML_READ_MAX_BYTES:
                    return ""
                root = ET.fromstring(archive.read(info))
        except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
            return ""
        lines: list[str] = []
        for paragraph in root.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        ):
            parts = [
                node.text or ""
                for node in paragraph.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            ]
            line = "".join(parts).strip()
            if line:
                lines.append(line)
        if lines:
            return "\n".join(lines)
        return "\n".join(
            (node.text or "").strip()
            for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
            if (node.text or "").strip()
        )

    def _extract_xlsx_text(self, content: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                shared_strings = self._xlsx_shared_strings(archive)
                rows: list[str] = []
                worksheet_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/") and name.endswith(".xml")
                )
                for worksheet_name in worksheet_names[:20]:
                    info = archive.getinfo(worksheet_name)
                    if info.file_size > _ATTACHMENT_XML_READ_MAX_BYTES:
                        continue
                    root = ET.fromstring(archive.read(info))
                    sheet_label = PurePath(worksheet_name).stem
                    cells: list[str] = []
                    for cell in root.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
                    ):
                        value = self._xlsx_cell_text(cell, shared_strings)
                        if value:
                            ref = cell.attrib.get("r", "")
                            cells.append(f"{ref}: {value}" if ref else value)
                    if cells:
                        rows.append(f"[{sheet_label}]\n" + "\n".join(cells))
                return "\n\n".join(rows)
        except (OSError, ET.ParseError, zipfile.BadZipFile):
            return ""
        return ""

    def _xlsx_shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        try:
            info = archive.getinfo("xl/sharedStrings.xml")
            if info.file_size > _ATTACHMENT_XML_READ_MAX_BYTES:
                return []
            root = ET.fromstring(archive.read(info))
        except (KeyError, OSError, ET.ParseError):
            return []
        result: list[str] = []
        for item in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
            parts = [
                node.text or ""
                for node in item.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                )
            ]
            result.append("".join(parts))
        return result

    def _xlsx_cell_text(self, cell: ET.Element, shared_strings: list[str]) -> str:
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            parts = [
                node.text or ""
                for node in cell.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                )
            ]
            return "".join(parts).strip()
        value_node = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
        raw_value = (value_node.text or "").strip() if value_node is not None else ""
        if cell_type == "s" and raw_value.isdigit():
            index = int(raw_value)
            if 0 <= index < len(shared_strings):
                return shared_strings[index].strip()
        return raw_value

    def _extract_pdf_text(self, content: bytes) -> str:
        snippets: list[str] = []
        for match in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj", content):
            snippets.append(self._decode_pdf_literal(match.group(0).rsplit(b")", 1)[0][1:]))
        for match in re.finditer(rb"\[(.*?)\]\s*TJ", content, flags=re.DOTALL):
            for literal in re.finditer(rb"\((?:\\.|[^\\)])*\)", match.group(1)):
                snippets.append(self._decode_pdf_literal(literal.group(0)[1:-1]))
        return "\n".join(item for item in snippets if item.strip())

    def _decode_pdf_literal(self, value: bytes) -> str:
        replacements = {
            b"\\n": b"\n",
            b"\\r": b"\r",
            b"\\t": b"\t",
            b"\\b": b"\b",
            b"\\f": b"\f",
            b"\\(": b"(",
            b"\\)": b")",
            b"\\\\": b"\\",
        }
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value.decode("utf-8", errors="replace")

    def _attachment_image_metadata(self, content: bytes, attachment_type: str) -> dict[str, Any]:
        dimensions = self._attachment_image_dimensions(content, attachment_type)
        return {
            "width": dimensions[0] if dimensions else None,
            "height": dimensions[1] if dimensions else None,
        }

    def _attachment_image_dimensions(
        self, content: bytes, attachment_type: str
    ) -> tuple[int, int] | None:
        if (
            attachment_type == "png"
            and len(content) >= 24
            and content.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
        if attachment_type == "gif" and len(content) >= 10:
            return int.from_bytes(content[6:8], "little"), int.from_bytes(content[8:10], "little")
        if attachment_type == "jpeg":
            return self._jpeg_dimensions(content)
        if attachment_type == "webp":
            return self._webp_dimensions(content)
        return None

    def _jpeg_dimensions(self, content: bytes) -> tuple[int, int] | None:
        if len(content) < 4 or not content.startswith(b"\xff\xd8"):
            return None
        position = 2
        while position + 9 < len(content):
            if content[position] != 0xFF:
                position += 1
                continue
            marker = content[position + 1]
            position += 2
            if marker in {0xD8, 0xD9}:
                continue
            if position + 2 > len(content):
                return None
            segment_length = int.from_bytes(content[position : position + 2], "big")
            if segment_length < 2:
                return None
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and position + 7 <= len(content):
                height = int.from_bytes(content[position + 3 : position + 5], "big")
                width = int.from_bytes(content[position + 5 : position + 7], "big")
                return width, height
            position += segment_length
        return None

    def _webp_dimensions(self, content: bytes) -> tuple[int, int] | None:
        if len(content) < 30 or not (content.startswith(b"RIFF") and content[8:12] == b"WEBP"):
            return None
        chunk = content[12:16]
        if chunk == b"VP8X" and len(content) >= 30:
            width = int.from_bytes(content[24:27], "little") + 1
            height = int.from_bytes(content[27:30], "little") + 1
            return width, height
        return None

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
        total_seconds_direct = self._validated_deadline_part(
            value, "total_seconds", maximum=31_536_000
        )
        days = self._validated_deadline_part(value, "days", maximum=365)
        hours = self._validated_deadline_part(value, "hours", maximum=23)
        minutes = self._validated_deadline_part(value, "minutes", maximum=59)
        seconds = self._validated_deadline_part(value, "seconds", maximum=59)
        total_seconds = (
            total_seconds_direct + days * 24 * 3600 + hours * 3600 + minutes * 60 + seconds
        )
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
            self._fail(
                "validation_error", "Нужно передать непустой title.", details={"field": "title"}
            )
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
                "Поле deadline должно быть JSON-объектом с числами days, hours, minutes, seconds или total_seconds.",
                details={"field": "deadline"},
            )
        total_seconds_direct = self._validated_deadline_part(
            value, "total_seconds", maximum=31_536_000
        )
        days = self._validated_deadline_part(value, "days", maximum=365)
        hours = self._validated_deadline_part(value, "hours", maximum=23)
        minutes = self._validated_deadline_part(value, "minutes", maximum=59)
        seconds = self._validated_deadline_part(value, "seconds", maximum=59)
        total_seconds = (
            total_seconds_direct + days * 24 * 3600 + hours * 3600 + minutes * 60 + seconds
        )
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
        manual_labels: set[str] = set()
        for item in value:
            tag = CardTag.from_value(item)
            if tag is None:
                continue
            unique_labels.add(tag.label)
            if tag.label != _READY_CARD_TAG_NORMALIZED:
                manual_labels.add(tag.label)
            if len(manual_labels) > CARD_MANUAL_TAG_LIMIT:
                self._fail(
                    "validation_error",
                    f"Количество ручных меток не должно превышать {CARD_MANUAL_TAG_LIMIT}.",
                    details={"field": "tags"},
                )
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

    def _validated_attachment_upload(
        self, file_name_value, mime_type_value, content: bytes
    ) -> tuple[str, str, str]:
        detected_type = self._detect_attachment_type(content)
        if not detected_type:
            self._fail(
                "validation_error",
                f"Разрешены только {_ALLOWED_ATTACHMENT_TYPES_LABEL}. Файл повреждён или его формат не распознан.",
                details={
                    "field": "content_base64",
                    "allowed_extensions": list(_ALLOWED_ATTACHMENT_EXTENSIONS),
                },
            )
        spec = _ATTACHMENT_TYPE_SPECS[detected_type]
        file_name = normalize_file_name(file_name_value)
        if file_name:
            self._ensure_safe_attachment_name(file_name)
            requested_extension = self._attachment_extension(file_name)
            if not requested_extension:
                requested_extension = spec["canonical_extension"]
                file_name = self._attachment_name_with_extension(file_name, requested_extension)
            elif requested_extension not in _ATTACHMENT_EXTENSION_TO_TYPE:
                if requested_extension in _ATTACHMENT_DANGEROUS_INTERMEDIATE_EXTENSIONS:
                    self._fail(
                        "validation_error",
                        f"Разрешены только {_ALLOWED_ATTACHMENT_TYPES_LABEL}.",
                        details={
                            "field": "file_name",
                            "file_name": file_name,
                            "allowed_extensions": list(_ALLOWED_ATTACHMENT_EXTENSIONS),
                        },
                    )
                requested_extension = spec["canonical_extension"]
                file_name = self._append_attachment_extension(file_name, requested_extension)
            elif _ATTACHMENT_EXTENSION_TO_TYPE[requested_extension] != detected_type:
                self._fail(
                    "validation_error",
                    "Расширение файла не соответствует его содержимому.",
                    details={
                        "field": "file_name",
                        "file_name": file_name,
                        "expected_extensions": sorted(spec["extensions"]),
                    },
                )
        else:
            requested_extension = spec["canonical_extension"]
            file_name = self._generated_attachment_name(requested_extension)

        normalized_mime_type = self._normalized_attachment_mime_type(mime_type_value)
        if (
            normalized_mime_type not in _ATTACHMENT_GENERIC_MIME_TYPES
            and normalized_mime_type not in spec["mime_types"]
        ):
            self._fail(
                "validation_error",
                "MIME-тип файла не соответствует его расширению и содержимому.",
                details={
                    "field": "mime_type",
                    "file_name": file_name,
                    "mime_type": normalized_mime_type,
                    "expected_mime_types": sorted(spec["mime_types"]),
                },
            )
        file_name = self._attachment_name_with_extension(file_name, requested_extension)
        return file_name, spec["canonical_mime"], requested_extension

    def _repair_attachment_metadata(
        self, card_id: str, attachment: Attachment, attachment_path: Path
    ) -> tuple[Path, bool]:
        content = attachment_path.read_bytes()
        detected_type = self._detect_attachment_type(content)
        if not detected_type:
            self._fail(
                "validation_error",
                "Сохранённый файл повреждён или его формат больше не поддерживается.",
                details={
                    "attachment_id": attachment.id,
                    "file_name": attachment.file_name,
                },
            )
        spec = _ATTACHMENT_TYPE_SPECS[detected_type]
        repaired = False

        normalized_name = normalize_file_name(attachment.file_name)
        if normalized_name:
            try:
                self._ensure_safe_attachment_name(normalized_name)
            except ServiceError:
                normalized_name = ""
        if not normalized_name:
            normalized_name = self._generated_attachment_name(spec["canonical_extension"])
        else:
            current_extension = self._attachment_extension(normalized_name)
            if current_extension not in spec["extensions"]:
                if (
                    current_extension in _ATTACHMENT_EXTENSION_TO_TYPE
                    or current_extension in _ATTACHMENT_DANGEROUS_INTERMEDIATE_EXTENSIONS
                ):
                    normalized_name = self._attachment_name_with_extension(
                        self._attachment_stem(normalized_name) or "attachment",
                        spec["canonical_extension"],
                    )
                else:
                    normalized_name = self._append_attachment_extension(
                        normalized_name, spec["canonical_extension"]
                    )
        if attachment.file_name != normalized_name:
            attachment.file_name = normalized_name
            repaired = True

        if attachment.mime_type != spec["canonical_mime"]:
            attachment.mime_type = spec["canonical_mime"]
            repaired = True

        preferred_extension = self._preferred_storage_extension(attachment.file_name, spec)
        preferred_stored_name = f"{attachment.id}{preferred_extension}"
        if attachment.stored_name != preferred_stored_name:
            target_path = self._attachment_path(card_id, preferred_stored_name)
            if target_path != attachment_path and not target_path.exists():
                attachment_path.rename(target_path)
                attachment_path = target_path
                attachment.stored_name = preferred_stored_name
                repaired = True
        return attachment_path, repaired

    def _ensure_safe_attachment_name(self, file_name: str) -> None:
        suffixes = [suffix.lower() for suffix in PurePath(file_name).suffixes]
        dangerous_suffixes = [
            suffix
            for suffix in suffixes[:-1]
            if suffix in _ATTACHMENT_DANGEROUS_INTERMEDIATE_EXTENSIONS
        ]
        if dangerous_suffixes:
            self._fail(
                "validation_error",
                "Имя файла содержит опасное двойное расширение.",
                details={
                    "field": "file_name",
                    "file_name": file_name,
                    "blocked_extensions": dangerous_suffixes,
                },
            )

    def _generated_attachment_name(self, extension: str) -> str:
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        return f"attachment-{stamp}{extension}"

    def _attachment_extension(self, file_name: str) -> str:
        return PurePath(str(file_name or "")).suffix.lower()

    def _attachment_stem(self, file_name: str) -> str:
        normalized_name = normalize_file_name(file_name)
        if not normalized_name:
            return ""
        suffix = PurePath(normalized_name).suffix
        if not suffix:
            return normalized_name
        return normalized_name[: -len(suffix)].rstrip(" .")

    def _attachment_name_with_extension(self, file_name: str, extension: str) -> str:
        normalized_name = normalize_file_name(file_name)
        stem = self._attachment_stem(normalized_name) if normalized_name else ""
        stem = stem or "attachment"
        return normalize_file_name(f"{stem}{extension}") or f"attachment{extension}"

    def _append_attachment_extension(self, file_name: str, extension: str) -> str:
        normalized_name = normalize_file_name(file_name)
        normalized_name = normalized_name or "attachment"
        return normalize_file_name(f"{normalized_name}{extension}") or f"attachment{extension}"

    def _preferred_storage_extension(self, file_name: str, spec: dict[str, Any]) -> str:
        extension = self._attachment_extension(file_name)
        if extension in spec["extensions"]:
            return extension
        return spec["canonical_extension"]

    def _normalized_attachment_mime_type(self, value) -> str:
        mime_type = normalize_text(value, default="", limit=160).lower()
        if not mime_type:
            return ""
        return mime_type.split(";", 1)[0].strip()

    def _detect_attachment_type(self, content: bytes) -> str | None:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if content.startswith(b"\xff\xd8\xff"):
            return "jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "gif"
        if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "webp"
        if content.startswith(b"%PDF-"):
            return "pdf"
        if content.startswith(_OLE_MAGIC):
            return self._detect_ole_attachment_type(content)
        if content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            return self._detect_openxml_attachment_type(content)
        if self._looks_like_text_content(content):
            return "txt"
        return None

    def _detect_openxml_attachment_type(self, content: bytes) -> str | None:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return None
        if "[Content_Types].xml" not in names:
            return None
        if any(name.startswith("word/") for name in names):
            return "docx"
        if any(name.startswith("xl/") for name in names):
            return "xlsx"
        return None

    def _detect_ole_attachment_type(self, content: bytes) -> str | None:
        if (
            b"WordDocument" in content
            or b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t\x00" in content
        ):
            return "doc"
        if (
            b"Workbook" in content
            or b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00" in content
            or b"Book" in content
            or b"B\x00o\x00o\x00k\x00" in content
        ):
            return "xls"
        return None

    def _looks_like_text_content(self, content: bytes) -> bool:
        sample = content[:8192]
        if not sample:
            return True
        if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            return self._looks_like_decoded_text(sample, ("utf-16", "utf-16-le", "utf-16-be"))
        if b"\x00" in sample:
            return False
        control_bytes = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
        if control_bytes / max(1, len(sample)) > 0.05:
            return False
        return self._looks_like_decoded_text(sample, ("utf-8-sig", "utf-8", "cp1251"))

    def _looks_like_decoded_text(self, content: bytes, encodings: tuple[str, ...]) -> bool:
        for encoding in encodings:
            try:
                decoded = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            if self._printable_text_ratio(decoded) >= 0.85:
                return True
        return False

    def _printable_text_ratio(self, value: str) -> float:
        if not value:
            return 1.0
        printable_chars = sum(1 for char in value if char.isprintable() or char in "\r\n\t")
        return printable_chars / len(value)

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

    def _validated_numeric_limit(self, value, *, field: str, default: int, maximum: int) -> int:
        if value in (None, ""):
            return default
        try:
            limit = int(value)
        except (TypeError, ValueError):
            self._fail(
                "validation_error",
                f"Параметр {field} должен быть целым числом.",
                details={"field": field},
            )
        if limit < 1 or limit > maximum:
            self._fail(
                "validation_error",
                f"Параметр {field} должен быть в диапазоне от 1 до {maximum}.",
                details={"field": field},
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

    def _normalized_ai_board_control_settings(self, value: Any) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        enabled = normalize_bool(payload.get("enabled"), default=False)
        try:
            interval_minutes = int(payload.get("interval_minutes", 20))
        except (TypeError, ValueError):
            interval_minutes = 20
        try:
            cooldown_minutes = int(payload.get("cooldown_minutes", 60))
        except (TypeError, ValueError):
            cooldown_minutes = 60
        return {
            "enabled": bool(enabled),
            "interval_minutes": min(max(interval_minutes, 5), 240),
            "cooldown_minutes": min(max(cooldown_minutes, 5), 1440),
        }

    def _extract_ai_board_control_settings_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        nested = payload.get("ai_board_control")
        if isinstance(nested, dict):
            return dict(nested)
        flat_keys = {
            "ai_board_control_enabled",
            "ai_board_control_interval_minutes",
            "ai_board_control_cooldown_minutes",
        }
        if not any(key in payload for key in flat_keys):
            return None
        return {
            "enabled": payload.get("ai_board_control_enabled"),
            "interval_minutes": payload.get("ai_board_control_interval_minutes"),
            "cooldown_minutes": payload.get("ai_board_control_cooldown_minutes"),
        }

    def _validated_ai_board_control_settings(
        self, value: Any, *, default: dict[str, Any]
    ) -> dict[str, Any]:
        payload = dict(default)
        if isinstance(value, dict):
            payload.update(value)
        return self._normalized_ai_board_control_settings(payload)

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
            self._fail(
                "validation_error", "Нужно передать название кассы.", details={"field": "name"}
            )
        existing_names = {
            item.name.casefold()
            for item in cashboxes
            if exclude_cashbox_id is None or item.id != exclude_cashbox_id
        }
        if name.casefold() in existing_names:
            self._fail(
                "validation_error",
                "Касса с таким названием уже существует.",
                details={"field": "name"},
            )
        return name

    def _validated_cash_transaction_note(self, value) -> str:
        return normalize_text(value, default="", limit=240)

    def _validated_cash_amount_minor(self, payload: dict) -> int:
        raw_value = payload.get("amount_minor")
        if raw_value in (None, ""):
            raw_value = payload.get("amount")
        if raw_value in (None, ""):
            self._fail(
                "validation_error", "Нужно передать сумму операции.", details={"field": "amount"}
            )
        amount_minor = normalize_money_minor(raw_value, minimum=1)
        if amount_minor < 1:
            self._fail(
                "validation_error",
                "Сумма операции должна быть больше нуля.",
                details={"field": "amount"},
            )
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
