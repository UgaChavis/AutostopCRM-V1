from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Any, BinaryIO

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
AUDIT_ARCHIVE_SCAN_MAX_BYTES = 10 * 1024 * 1024
AUDIT_ARCHIVE_LINE_MAX_BYTES = 2 * 1024 * 1024
AUDIT_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024


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
        line = (
            json.dumps(
                _json_safe_value(record),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        payload = line.encode("utf-8")
        if len(payload.rstrip(b"\n")) > AUDIT_ARCHIVE_LINE_MAX_BYTES:
            raise ValueError("audit archive record exceeds line size limit")
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
            for line in self._iter_archive_lines(archive_file):
                if not line:
                    continue
                try:
                    record = json.loads(line, parse_constant=_reject_json_constant)
                except (ValueError, json.JSONDecodeError, RecursionError):
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

    def _iter_archive_lines(self, archive_file: Path) -> Iterator[str]:
        size = archive_file.stat().st_size
        with archive_file.open("rb") as handle:
            if size <= AUDIT_ARCHIVE_SCAN_MAX_BYTES:
                yield from self._iter_archive_window(
                    archive_file,
                    handle,
                    byte_budget=size,
                    allow_trailing_partial=True,
                )
                return

            if self._logger is not None:
                self._logger.warning(
                    "audit_archive_window_scan_used path=%s size=%s limit=%s",
                    archive_file,
                    size,
                    AUDIT_ARCHIVE_SCAN_MAX_BYTES,
                )
            yield from self._iter_archive_window(
                archive_file,
                handle,
                byte_budget=AUDIT_ARCHIVE_SCAN_MAX_BYTES,
                allow_trailing_partial=False,
            )

            tail_start = max(AUDIT_ARCHIVE_SCAN_MAX_BYTES, size - AUDIT_ARCHIVE_SCAN_MAX_BYTES)
            handle.seek(tail_start)
            tail_budget = size - tail_start
            if tail_start > 0 and not self._starts_at_archive_line_boundary(handle, tail_start):
                skipped = self._discard_partial_archive_line(handle, byte_budget=tail_budget)
                tail_budget = max(0, tail_budget - skipped)
            if tail_budget > 0:
                yield from self._iter_archive_window(
                    archive_file,
                    handle,
                    byte_budget=tail_budget,
                    allow_trailing_partial=True,
                )

    def _starts_at_archive_line_boundary(self, handle: BinaryIO, offset: int) -> bool:
        if offset <= 0:
            return True
        handle.seek(offset - 1)
        previous = handle.read(1)
        handle.seek(offset)
        return previous == b"\n"

    def _iter_archive_window(
        self,
        archive_file: Path,
        handle: BinaryIO,
        *,
        byte_budget: int,
        allow_trailing_partial: bool,
    ) -> Iterator[str]:
        buffer = b""
        bytes_read = 0
        skipping_oversized = False
        while bytes_read < byte_budget:
            chunk_size = min(AUDIT_ARCHIVE_READ_CHUNK_BYTES, byte_budget - bytes_read)
            if chunk_size <= 0:
                break
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            bytes_read += len(chunk)

            while chunk:
                if skipping_oversized:
                    newline_index = chunk.find(b"\n")
                    if newline_index < 0:
                        chunk = b""
                        continue
                    chunk = chunk[newline_index + 1 :]
                    skipping_oversized = False
                    buffer = b""
                    continue

                buffer += chunk
                chunk = b""
                while True:
                    newline_index = buffer.find(b"\n")
                    if newline_index < 0:
                        if len(buffer) > AUDIT_ARCHIVE_LINE_MAX_BYTES:
                            self._log_archive_line_too_large(archive_file)
                            buffer = b""
                            skipping_oversized = True
                        break

                    raw_line = buffer[:newline_index]
                    buffer = buffer[newline_index + 1 :]
                    if raw_line.endswith(b"\r"):
                        raw_line = raw_line[:-1]
                    line = self._decode_archive_line(archive_file, raw_line)
                    if line:
                        yield line

        if allow_trailing_partial and buffer and not skipping_oversized:
            line = self._decode_archive_line(archive_file, buffer.rstrip(b"\r"))
            if line:
                yield line

    def _discard_partial_archive_line(self, handle: BinaryIO, *, byte_budget: int) -> int:
        bytes_read = 0
        while bytes_read < byte_budget:
            chunk_size = min(AUDIT_ARCHIVE_READ_CHUNK_BYTES, byte_budget - bytes_read)
            if chunk_size <= 0:
                break
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            bytes_read += len(chunk)
            newline_index = chunk.find(b"\n")
            if newline_index < 0:
                continue
            unread = len(chunk) - newline_index - 1
            if unread:
                handle.seek(-unread, 1)
            return bytes_read - unread
        return bytes_read

    def _decode_archive_line(self, archive_file: Path, raw_line: bytes) -> str:
        if len(raw_line) > AUDIT_ARCHIVE_LINE_MAX_BYTES:
            self._log_archive_line_too_large(archive_file)
            return ""
        try:
            return raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            if self._logger is not None:
                self._logger.warning("audit_archive_bad_utf8 path=%s", archive_file)
            return ""

    def _log_archive_line_too_large(self, archive_file: Path) -> None:
        if self._logger is not None:
            self._logger.warning(
                "audit_archive_line_too_large path=%s limit=%s",
                archive_file,
                AUDIT_ARCHIVE_LINE_MAX_BYTES,
            )


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

    compact: dict[str, Any] = {}
    for key, value in details.items():
        if key in {"before", "after"}:
            continue
        safe_value = _compact_json_safe_value(value)
        if safe_value is not _UNSAFE_COMPACT_VALUE:
            compact[key] = safe_value
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
            "preview": _json_safe_value(value[:3], depth=3),
        }
    return _json_safe_value(value, depth=3)


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
    preview = {
        key: _json_safe_value(value.get(key), depth=3) for key in preferred_keys if key in value
    }
    if not preview:
        for key in sorted(value.keys())[:8]:
            preview[key] = _json_safe_value(value.get(key), depth=3)
    preview["_keys"] = len(value)
    return preview


def _stable_value_hash(value: Any) -> str:
    payload = json.dumps(
        _json_safe_value(value),
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_size_bytes(value: Any) -> int:
    return len(
        json.dumps(
            _json_safe_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


_UNSAFE_COMPACT_VALUE = object()


def _compact_json_safe_value(value: Any) -> Any:
    try:
        safe_value = _json_safe_value(value)
        json.dumps(safe_value, ensure_ascii=False, allow_nan=False)
    except (RecursionError, TypeError, ValueError):
        return _UNSAFE_COMPACT_VALUE
    return safe_value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)
