from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any

from .file_lock import ProcessFileLock

AUDIT_ARCHIVE_SCHEMA_VERSION = 1
AUDIT_ARCHIVE_DIR_NAME = "audit-archive"
AUDIT_FULL_DETAILS_REF_KEY = "full_details_ref"
AUDIT_FULL_DETAILS_ARCHIVED_KEY = "full_details_archived"
HEAVY_AUDIT_ACTIONS = frozenset(
    {
        "description_changed",
        "repair_order_updated",
        "vehicle_profile_updated",
    }
)
TEXT_PREVIEW_LIMIT = 600


@dataclass(frozen=True)
class AuditArchiveWrite:
    ref: str
    bytes_written: int


class AuditArchiveStore:
    """Append-only archive for full audit details that are too large for active state."""

    def __init__(self, archive_dir: Path, logger: Logger | None = None) -> None:
        self._archive_dir = archive_dir
        self._logger = logger
        self._lock = ProcessFileLock(self._archive_dir / ".audit-archive.lock")

    @property
    def archive_dir(self) -> Path:
        return self._archive_dir

    def archive_details(
        self,
        *,
        event_id: str,
        action: str,
        card_id: str | None,
        timestamp: str,
        details: dict[str, Any],
    ) -> AuditArchiveWrite:
        month = _archive_month(timestamp)
        archive_file = self._archive_dir / f"{month}.jsonl"
        record = {
            "schema_version": AUDIT_ARCHIVE_SCHEMA_VERSION,
            "event_id": event_id,
            "action": action,
            "card_id": card_id,
            "timestamp": timestamp,
            "details": details,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        payload = line.encode("utf-8")
        with self._lock.acquire():
            self._archive_dir.mkdir(parents=True, exist_ok=True)
            with archive_file.open("ab") as handle:
                handle.write(payload)
        return AuditArchiveWrite(ref=f"{archive_file.name}#{event_id}", bytes_written=len(payload))

    def load_details(self, ref: str, *, event_id: str | None = None) -> dict[str, Any] | None:
        archive_file, requested_event_id = self._resolve_ref(ref)
        if event_id:
            requested_event_id = event_id
        if not archive_file.exists():
            return None
        try:
            with archive_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if requested_event_id and record.get("event_id") != requested_event_id:
                        continue
                    details = record.get("details")
                    return details if isinstance(details, dict) else None
        except OSError as exc:
            if self._logger is not None:
                self._logger.warning("audit_archive_load_failed ref=%s error=%s", ref, exc)
            return None
        return None

    def _resolve_ref(self, ref: str) -> tuple[Path, str | None]:
        raw_ref = str(ref or "").strip()
        file_part, sep, event_id = raw_ref.partition("#")
        file_part = file_part.replace("\\", "/").lstrip("/")
        archive_name = self._archive_dir.name
        if file_part.startswith(f"{archive_name}/"):
            file_part = file_part[len(archive_name) + 1 :]
        safe_name = Path(file_part).name or file_part
        return self._archive_dir / safe_name, event_id if sep else None


def compact_audit_event_details(
    *,
    action: str,
    details: dict[str, Any],
    archive_ref: str,
) -> dict[str, Any]:
    if action not in HEAVY_AUDIT_ACTIONS:
        return dict(details)
    if not isinstance(details, dict) or details.get(AUDIT_FULL_DETAILS_ARCHIVED_KEY) is True:
        return dict(details or {})
    if "before" not in details and "after" not in details:
        return dict(details)

    compact = {
        key: value
        for key, value in details.items()
        if key not in {"before", "after"} and _compact_value_is_json_safe(value)
    }
    before = details.get("before")
    after = details.get("after")
    compact[AUDIT_FULL_DETAILS_ARCHIVED_KEY] = True
    compact[AUDIT_FULL_DETAILS_REF_KEY] = archive_ref
    if "before" in details:
        compact["before_preview"] = _preview_value(before)
        compact["before_sha256"] = _stable_value_hash(before)
        compact["before_bytes"] = _json_size_bytes(before)
    if "after" in details:
        compact["after_preview"] = _preview_value(after)
        compact["after_sha256"] = _stable_value_hash(after)
        compact["after_bytes"] = _json_size_bytes(after)
    if action == "description_changed":
        compact["description_preview"] = compact.get("after_preview", "")
    return compact


def hydrate_audit_event_details(
    *,
    action: str,
    details: dict[str, Any],
    archive_store: AuditArchiveStore,
    event_id: str | None = None,
) -> dict[str, Any]:
    if action not in HEAVY_AUDIT_ACTIONS or not isinstance(details, dict):
        return dict(details or {})
    ref = str(details.get(AUDIT_FULL_DETAILS_REF_KEY) or "").strip()
    if not ref:
        return dict(details)
    archived = archive_store.load_details(ref, event_id=event_id)
    if not isinstance(archived, dict):
        return dict(details)
    return dict(archived)


def details_need_archive(action: str, details: dict[str, Any] | None) -> bool:
    if action not in HEAVY_AUDIT_ACTIONS or not isinstance(details, dict):
        return False
    if details.get(AUDIT_FULL_DETAILS_ARCHIVED_KEY) is True:
        return False
    return "before" in details or "after" in details


def _archive_month(timestamp: str) -> str:
    raw = str(timestamp or "").strip()
    if len(raw) >= 7 and raw[4:5] == "-" and raw[7:8] in {"", "-", "T"}:
        return raw[:7]
    return "unknown"


def _preview_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.replace("\r", "").strip()
        return (
            text if len(text) <= TEXT_PREVIEW_LIMIT else text[:TEXT_PREVIEW_LIMIT].rstrip() + "..."
        )
    if isinstance(value, dict):
        return _preview_mapping(value)
    if isinstance(value, list):
        return {
            "type": "list",
            "items": len(value),
            "preview": value[:3],
        }
    return value


def _preview_mapping(value: dict[str, Any]) -> dict[str, Any]:
    preferred_keys = (
        "number",
        "status",
        "date",
        "client",
        "phone",
        "vehicle",
        "license_plate",
        "vin",
        "mileage",
        "reason",
        "comment",
        "make_display",
        "model_display",
        "generation_or_platform",
        "production_year",
        "registration_plate",
        "data_completion_state",
        "source_confidence",
    )
    preview = {key: value.get(key) for key in preferred_keys if key in value}
    if not preview:
        for key in sorted(value.keys())[:8]:
            preview[key] = value.get(key)
    preview["_keys"] = len(value)
    return preview


def _stable_value_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_size_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def _compact_value_is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return False
    return True
