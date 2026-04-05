from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from pathlib import PurePath
import re
from typing import Any, Collection, Literal
import uuid

from .vehicle_profile import VehicleProfile, build_vehicle_display


ColumnId = str
Indicator = Literal["green", "yellow", "red"]
Status = Literal["ok", "warning", "critical", "expired"]
AuditSource = Literal["ui", "api", "mcp", "system"]

DEFAULT_COLUMN_IDS: tuple[str, ...] = ("inbox", "in_progress", "control", "done")
VALID_INDICATORS: tuple[Indicator, ...] = ("green", "yellow", "red")
VALID_STATUSES: tuple[Status, ...] = ("ok", "warning", "critical", "expired")
VALID_AUDIT_SOURCES: tuple[AuditSource, ...] = ("ui", "api", "mcp", "system")
DEFAULT_INDICATOR: Indicator = "green"
DEFAULT_DEADLINE_TOTAL_SECONDS = 24 * 3600
WARNING_THRESHOLD_RATIO = 0.6
CRITICAL_THRESHOLD_RATIO = 0.15
BLINK_THRESHOLD_RATIO = 0.05
COLUMN_LABEL_LIMIT = 40
CARD_TITLE_LIMIT = 120
CARD_VEHICLE_LIMIT = 60
CARD_DESCRIPTION_LIMIT = 20000
TAG_LIMIT = 10
TAG_LABEL_LIMIT = 24
STICKY_TEXT_LIMIT = 1000
STICKY_DEFAULT_TOTAL_SECONDS = 24 * 3600
ACTOR_NAME_LIMIT = 40
ATTACHMENT_FILE_NAME_LIMIT = 120
ARCHIVE_PREVIEW_LIMIT = 10
MAX_ATTACHMENT_SIZE_BYTES = 15 * 1024 * 1024
_COLUMN_ID_PATTERN = re.compile(r"[^a-z0-9_]+")
_SPACES_PATTERN = re.compile(r"\s+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_bool(value, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def normalize_int(value, *, default: int = 0, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def normalize_indicator(value) -> Indicator:
    indicator = str(value or "").strip().lower()
    if indicator not in VALID_INDICATORS:
        return DEFAULT_INDICATOR
    return indicator


def normalize_source(value, *, default: AuditSource = "api") -> AuditSource:
    source = str(value or "").strip().lower()
    if source not in VALID_AUDIT_SOURCES:
        return default
    return source  # type: ignore[return-value]


def normalize_column(value, *, valid_columns: Collection[str] | None = None, default: str = "inbox") -> ColumnId:
    column = str(value or "").strip().lower()
    if not column:
        return default
    if valid_columns is not None and column not in set(valid_columns):
        return default
    return column


def normalize_column_id(value) -> str:
    column_id = str(value or "").strip().lower()
    column_id = _COLUMN_ID_PATTERN.sub("_", column_id)
    column_id = re.sub(r"_+", "_", column_id).strip("_")
    return column_id[:64]


def normalize_text(value, *, default: str = "", limit: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    if limit is not None:
        text = text[:limit]
    return text


def split_legacy_card_title(value) -> tuple[str, str]:
    title = normalize_text(value, default="Без названия", limit=CARD_TITLE_LIMIT)
    if " / " not in title:
        return "", title
    vehicle_part, task_part = title.split(" / ", 1)
    vehicle = normalize_text(vehicle_part, default="", limit=CARD_VEHICLE_LIMIT)
    task_title = normalize_text(task_part, default="", limit=CARD_TITLE_LIMIT)
    if vehicle and task_title:
        return vehicle, task_title
    return "", title


def normalize_actor_name(value, *, default: str = "СИСТЕМА") -> str:
    actor = normalize_text(value, default=default, limit=ACTOR_NAME_LIMIT)
    return _SPACES_PATTERN.sub(" ", actor)


def normalize_tag_label(value) -> str:
    normalized = normalize_text(value, limit=TAG_LABEL_LIMIT)
    normalized = normalized.replace(",", " ")
    normalized = _SPACES_PATTERN.sub(" ", normalized)
    return normalized.upper()


def normalize_tags(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for raw in value:
        tag = normalize_tag_label(raw)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= TAG_LIMIT:
            break
    return tags


def normalize_file_name(value) -> str:
    raw_name = normalize_text(value, limit=ATTACHMENT_FILE_NAME_LIMIT)
    if not raw_name:
        return ""
    safe_name = PurePath(raw_name).name
    safe_name = safe_name.replace("\x00", "")
    return safe_name[:ATTACHMENT_FILE_NAME_LIMIT]


def indicator_from_status(status: Status) -> Indicator:
    if status == "warning":
        return "yellow"
    if status in {"critical", "expired"}:
        return "red"
    return "green"


def format_remaining_seconds(seconds: int) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 24 * 3600)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{days}д {hours:02d}:{minutes:02d}:{secs:02d}"


def short_entity_id(value, *, prefix: str) -> str:
    token = str(value or "").strip().split("-", 1)[0].upper()
    token = re.sub(r"[^A-Z0-9]+", "", token)[:8]
    if not token:
        token = "UNKNOWN"
    return f"{prefix}-{token}"


def split_seconds_to_days_hours(seconds: int) -> tuple[int, int]:
    total_seconds = max(0, int(seconds))
    if total_seconds == 0:
        return 0, 1
    rounded_hours = max(1, math.ceil(total_seconds / 3600))
    days, hours = divmod(rounded_hours, 24)
    return days, hours


@dataclass(slots=True)
class Column:
    id: str
    label: str
    position: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, payload: dict, *, fallback_position: int = 0) -> "Column":
        if not isinstance(payload, dict):
            raise TypeError("Column payload must be a dictionary.")
        column_id = normalize_column_id(payload.get("id"))
        label = normalize_text(payload.get("label"), limit=COLUMN_LABEL_LIMIT)
        position = normalize_int(payload.get("position"), default=fallback_position, minimum=0)
        if not column_id or not label:
            raise ValueError("Column id and label are required.")
        return cls(id=column_id, label=label, position=position)


@dataclass(slots=True)
class Attachment:
    id: str
    file_name: str
    stored_name: str
    mime_type: str
    size_bytes: int
    created_at: str
    created_by: str
    removed: bool = False
    removed_at: str = ""
    removed_by: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "stored_name": self.stored_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "removed": self.removed,
            "removed_at": self.removed_at,
            "removed_by": self.removed_by,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Attachment":
        if not isinstance(payload, dict):
            raise TypeError("Attachment payload must be a dictionary.")
        file_name = normalize_file_name(payload.get("file_name"))
        stored_name = normalize_file_name(payload.get("stored_name"))
        created_at = parse_datetime(payload.get("created_at")) or utc_now()
        if not file_name or not stored_name:
            raise ValueError("Attachment file_name and stored_name are required.")
        return cls(
            id=normalize_text(payload.get("id"), default=str(uuid.uuid4()), limit=128),
            file_name=file_name,
            stored_name=stored_name,
            mime_type=normalize_text(payload.get("mime_type"), default="application/octet-stream", limit=100),
            size_bytes=normalize_int(payload.get("size_bytes"), default=0, minimum=0),
            created_at=created_at.isoformat(),
            created_by=normalize_actor_name(payload.get("created_by"), default="СИСТЕМА"),
            removed=normalize_bool(payload.get("removed"), default=False),
            removed_at=(parse_datetime(payload.get("removed_at")) or "").isoformat()
            if parse_datetime(payload.get("removed_at"))
            else "",
            removed_by=normalize_actor_name(payload.get("removed_by"), default=""),
        )


@dataclass(slots=True)
class StickyNote:
    id: str
    text: str
    x: int
    y: int
    created_at: str
    updated_at: str
    deadline_timestamp: str
    deadline_total_seconds: int = STICKY_DEFAULT_TOTAL_SECONDS

    def deadline_datetime(self) -> datetime:
        deadline = parse_datetime(self.deadline_timestamp)
        if deadline is None:
            deadline = utc_now() + timedelta(seconds=max(1, int(self.deadline_total_seconds)))
            self.deadline_timestamp = deadline.isoformat()
        return deadline

    def remaining_seconds(self, reference_time: datetime | None = None) -> int:
        if reference_time is None:
            reference_time = utc_now()
        return max(0, int((self.deadline_datetime() - reference_time).total_seconds()))

    def remaining_ratio(self, reference_time: datetime | None = None) -> float:
        total = max(1, int(self.deadline_total_seconds))
        return self.remaining_seconds(reference_time) / total

    def opacity(self, reference_time: datetime | None = None) -> float:
        ratio = max(0.0, min(1.0, self.remaining_ratio(reference_time)))
        return round(0.5 + (0.4 * ratio), 3)

    def to_dict(self, reference_time: datetime | None = None) -> dict:
        remaining = self.remaining_seconds(reference_time)
        ratio = self.remaining_ratio(reference_time)
        return {
            "id": self.id,
            "short_id": short_entity_id(self.id, prefix="S"),
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_timestamp": self.deadline_timestamp,
            "deadline_total_seconds": self.deadline_total_seconds,
            "remaining_seconds": remaining,
            "remaining_ratio": ratio,
            "opacity": self.opacity(reference_time),
            "is_expired": remaining <= 0,
        }

    def to_storage_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_timestamp": self.deadline_timestamp,
            "deadline_total_seconds": self.deadline_total_seconds,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "StickyNote":
        if not isinstance(payload, dict):
            raise TypeError("Sticky note payload must be a dictionary.")
        created_at = parse_datetime(payload.get("created_at")) or utc_now()
        updated_at = parse_datetime(payload.get("updated_at")) or created_at
        deadline_total_seconds = normalize_int(
            payload.get("deadline_total_seconds"),
            default=STICKY_DEFAULT_TOTAL_SECONDS,
            minimum=1,
        )
        deadline = parse_datetime(payload.get("deadline_timestamp"))
        if deadline is None:
            deadline = created_at + timedelta(seconds=deadline_total_seconds)
        return cls(
            id=normalize_text(payload.get("id"), default=str(uuid.uuid4()), limit=128),
            text=normalize_text(payload.get("text"), default="ЗАМЕТКА", limit=STICKY_TEXT_LIMIT),
            x=normalize_int(payload.get("x"), default=0, minimum=0),
            y=normalize_int(payload.get("y"), default=0, minimum=0),
            created_at=created_at.isoformat(),
            updated_at=updated_at.isoformat(),
            deadline_timestamp=deadline.isoformat(),
            deadline_total_seconds=deadline_total_seconds,
        )


@dataclass(slots=True)
class AuditEvent:
    id: str
    timestamp: str
    actor_name: str
    source: AuditSource
    action: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    card_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "actor_name": self.actor_name,
            "source": self.source,
            "action": self.action,
            "message": self.message,
            "details": dict(self.details),
            "card_id": self.card_id,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AuditEvent":
        if not isinstance(payload, dict):
            raise TypeError("Audit event payload must be a dictionary.")
        timestamp = parse_datetime(payload.get("timestamp")) or utc_now()
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}
        card_id = normalize_text(payload.get("card_id"), default="", limit=128) or None
        return cls(
            id=normalize_text(payload.get("id"), default=str(uuid.uuid4()), limit=128),
            timestamp=timestamp.isoformat(),
            actor_name=normalize_actor_name(payload.get("actor_name")),
            source=normalize_source(payload.get("source"), default="system"),
            action=normalize_text(payload.get("action"), default="unknown", limit=80),
            message=normalize_text(payload.get("message"), default="Событие", limit=300),
            details=details,
            card_id=card_id,
        )


@dataclass(slots=True)
class Card:
    id: str
    title: str
    description: str
    column: ColumnId
    archived: bool
    created_at: str
    updated_at: str
    deadline_timestamp: str
    deadline_total_seconds: int = DEFAULT_DEADLINE_TOTAL_SECONDS
    vehicle: str = ""
    vehicle_profile: VehicleProfile = field(default_factory=VehicleProfile)
    tags: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    def deadline_datetime(self) -> datetime:
        deadline = parse_datetime(self.deadline_timestamp)
        if deadline is None:
            deadline = utc_now() + timedelta(seconds=max(1, int(self.deadline_total_seconds)))
            self.deadline_timestamp = deadline.isoformat()
        return deadline

    def remaining_seconds(self, reference_time: datetime | None = None) -> int:
        if reference_time is None:
            reference_time = utc_now()
        return max(0, int((self.deadline_datetime() - reference_time).total_seconds()))

    def remaining_ratio(self, reference_time: datetime | None = None) -> float:
        total = max(1, int(self.deadline_total_seconds))
        return self.remaining_seconds(reference_time) / total

    def status(self, reference_time: datetime | None = None) -> Status:
        remaining = self.remaining_seconds(reference_time)
        if remaining <= 0:
            return "expired"
        remaining_ratio = self.remaining_ratio(reference_time)
        if remaining_ratio <= CRITICAL_THRESHOLD_RATIO:
            return "critical"
        if remaining_ratio <= WARNING_THRESHOLD_RATIO:
            return "warning"
        return "ok"

    def indicator(self, reference_time: datetime | None = None) -> Indicator:
        return indicator_from_status(self.status(reference_time))

    def is_blinking(self, reference_time: datetime | None = None) -> bool:
        remaining = self.remaining_seconds(reference_time)
        if remaining <= 0:
            return True
        return self.remaining_ratio(reference_time) <= BLINK_THRESHOLD_RATIO

    def active_attachments(self) -> list[Attachment]:
        return [attachment for attachment in self.attachments if not attachment.removed]

    def vehicle_display(self) -> str:
        vehicle = self.vehicle.strip()
        if vehicle:
            return vehicle
        profile_display = self.vehicle_profile.display_name().strip()
        if profile_display:
            return profile_display[:CARD_VEHICLE_LIMIT]
        return ""

    def heading(self) -> str:
        vehicle = self.vehicle_display()
        title = self.title.strip()
        if vehicle and title:
            return f"{vehicle} / {title}"
        return title or vehicle or "Без названия"

    def to_dict(
        self,
        reference_time: datetime | None = None,
        *,
        events_count: int = 0,
        include_removed_attachments: bool = False,
    ) -> dict:
        remaining = self.remaining_seconds(reference_time)
        status = self.status(reference_time)
        remaining_ratio = self.remaining_ratio(reference_time)
        attachments = self.attachments if include_removed_attachments else self.active_attachments()
        vehicle_display = self.vehicle_display()
        return {
            "id": self.id,
            "short_id": short_entity_id(self.id, prefix="C"),
            "heading": self.heading(),
            "vehicle": vehicle_display,
            "vehicle_raw": self.vehicle,
            "vehicle_profile": self.vehicle_profile.to_dict(),
            "title": self.title,
            "description": self.description,
            "column": self.column,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_timestamp": self.deadline_timestamp,
            "signal_timestamp": self.deadline_timestamp,
            "remaining_seconds": remaining,
            "remaining_ratio": remaining_ratio,
            "remaining_display": format_remaining_seconds(remaining),
            "status": status,
            "indicator": indicator_from_status(status),
            "is_blinking": self.is_blinking(reference_time),
            "tags": list(self.tags),
            "attachments": [attachment.to_dict() for attachment in attachments],
            "attachment_count": len(self.active_attachments()),
            "events_count": max(0, int(events_count)),
        }

    def to_storage_dict(self) -> dict:
        return {
            "id": self.id,
            "vehicle": self.vehicle,
            "vehicle_profile": self.vehicle_profile.to_storage_dict(),
            "title": self.title,
            "description": self.description,
            "column": self.column,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_timestamp": self.deadline_timestamp,
            "deadline_total_seconds": self.deadline_total_seconds,
            "tags": list(self.tags),
            "attachments": [attachment.to_dict() for attachment in self.attachments],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict,
        *,
        valid_columns: Collection[str] | None = None,
        default_column: str = "inbox",
    ) -> "Card":
        if not isinstance(payload, dict):
            raise TypeError("Card payload must be a dictionary.")

        created_at = parse_datetime(payload.get("created_at")) or utc_now()
        updated_at = parse_datetime(payload.get("updated_at")) or created_at
        archived = normalize_bool(payload.get("archived"), default=False)
        deadline_total_seconds = normalize_int(
            payload.get("deadline_total_seconds"),
            default=DEFAULT_DEADLINE_TOTAL_SECONDS,
            minimum=1,
        )
        deadline = parse_datetime(payload.get("deadline_timestamp"))
        if deadline is None:
            deadline = created_at + timedelta(seconds=deadline_total_seconds)

        attachments_payload = payload.get("attachments", [])
        attachments: list[Attachment] = []
        if isinstance(attachments_payload, list):
            for item in attachments_payload:
                try:
                    attachments.append(Attachment.from_dict(item))
                except (TypeError, ValueError):
                    continue

        vehicle = normalize_text(payload.get("vehicle"), default="", limit=CARD_VEHICLE_LIMIT)
        vehicle_profile = VehicleProfile.from_dict(payload.get("vehicle_profile"))
        profile_display = build_vehicle_display(
            vehicle_profile.make_display,
            vehicle_profile.model_display,
            vehicle_profile.production_year,
        )
        title = normalize_text(payload.get("title"), default="Без названия", limit=CARD_TITLE_LIMIT)
        if not vehicle:
            vehicle, title = split_legacy_card_title(title)
        if not vehicle and profile_display:
            vehicle = normalize_text(profile_display, default="", limit=CARD_VEHICLE_LIMIT)

        card = cls(
            id=normalize_text(payload.get("id"), default=str(uuid.uuid4()), limit=128),
            title=normalize_text(payload.get("title"), default="Без названия", limit=CARD_TITLE_LIMIT),
            description=normalize_text(payload.get("description"), default="", limit=CARD_DESCRIPTION_LIMIT),
            column=normalize_column(payload.get("column"), valid_columns=valid_columns, default=default_column),
            archived=archived,
            created_at=created_at.isoformat(),
            updated_at=updated_at.isoformat(),
            deadline_timestamp=deadline.isoformat(),
            deadline_total_seconds=deadline_total_seconds,
            vehicle=vehicle,
            vehicle_profile=vehicle_profile,
            tags=normalize_tags(payload.get("tags")),
            attachments=attachments,
        )
        card.title = title
        return card
