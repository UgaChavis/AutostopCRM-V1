from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from logging import Logger
from pathlib import Path, PurePath
from typing import Any
from uuid import uuid4

from ..config import get_shared_files_dir, get_shared_files_index_file
from ..models import normalize_actor_name, normalize_file_name, normalize_int
from ..storage.file_lock import ProcessFileLock
from .card_service import ServiceError

SHARED_FILES_STORAGE_LIMIT_BYTES = 500 * 1024 * 1024
SHARED_FILES_INDEX_SCHEMA_VERSION = 1
_FETCH_BASE64_DEFAULT_BYTES = 2 * 1024 * 1024
_FETCH_BASE64_MAX_BYTES = 8 * 1024 * 1024
_DISALLOWED_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".msi", ".scr", ".vbs"}


@dataclass(slots=True)
class SharedFile:
    id: str
    original_name: str
    stored_name: str
    extension: str
    size_bytes: int
    created_at: str
    updated_at: str
    x: int = 0
    y: int = 0
    mime_type: str = "application/octet-stream"
    copied_from: str = ""
    source_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SharedFile | None:
        file_id = str(payload.get("id") or "").strip()
        original_name = normalize_file_name(payload.get("original_name"))
        stored_name = normalize_file_name(payload.get("stored_name"))
        if not file_id or not original_name or not stored_name:
            return None
        extension = _normalized_extension(
            payload.get("extension") or PurePath(original_name).suffix
        )
        created_at = _normalize_iso(payload.get("created_at"))
        updated_at = _normalize_iso(payload.get("updated_at")) or created_at
        return cls(
            id=file_id,
            original_name=original_name,
            stored_name=stored_name,
            extension=extension,
            size_bytes=normalize_int(payload.get("size_bytes"), default=0, minimum=0),
            created_at=created_at or _utc_now_iso(),
            updated_at=updated_at or _utc_now_iso(),
            x=_normalize_position(payload.get("x")),
            y=_normalize_position(payload.get("y")),
            mime_type=_normalize_mime_type(payload.get("mime_type")),
            copied_from=str(payload.get("copied_from") or "").strip(),
            source_id=str(payload.get("source_id") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "stored_name": self.stored_name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "x": self.x,
            "y": self.y,
            "mime_type": self.mime_type,
            "copied_from": self.copied_from,
            "source_id": self.source_id,
        }


class SharedFilesService:
    def __init__(
        self,
        *,
        storage_dir: Path | None = None,
        index_file: Path | None = None,
        logger: Logger | None = None,
        storage_limit_bytes: int = SHARED_FILES_STORAGE_LIMIT_BYTES,
    ) -> None:
        self._storage_dir = storage_dir or get_shared_files_dir()
        self._index_file = index_file or get_shared_files_index_file()
        self._logger = logger
        self._storage_limit_bytes = max(1, int(storage_limit_bytes))
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._index_file.with_suffix(".lock"))
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._index_file.exists():
            with self._process_lock.acquire():
                if not self._index_file.exists():
                    self._write_index([])

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    @property
    def index_file(self) -> Path:
        return self._index_file

    def list_shared_files(self, payload: dict | None = None) -> dict:
        del payload
        with self._locked_files() as files:
            return {
                "files": [self._public_file_dict(item) for item in files],
                "storage": self._storage_payload(files),
            }

    def get_shared_file_info(self, payload: dict | None = None) -> dict:
        with self._locked_files() as files:
            item = self._find_file(files, (payload or {}).get("file_id"))
            return {"file": self._public_file_dict(item), "storage": self._storage_payload(files)}

    def upload_shared_file(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        actor_name, source = self._audit_identity(payload)
        file_name = self._validated_file_name(payload.get("file_name"))
        content = self._validated_content(payload.get("content_base64"))
        mime_type = _normalize_mime_type(payload.get("mime_type"))
        x = _normalize_position(payload.get("x"))
        y = _normalize_position(payload.get("y"))
        with self._locked_files(write=True) as files:
            self._ensure_storage_capacity(files, len(content))
            file_id = str(uuid4())
            stored_name = self._unique_stored_name(file_id, PurePath(file_name).suffix)
            file_path = self._storage_path(stored_name)
            file_path.write_bytes(content)
            now = _utc_now_iso()
            item = SharedFile(
                id=file_id,
                original_name=file_name,
                stored_name=stored_name,
                extension=_normalized_extension(PurePath(file_name).suffix),
                size_bytes=len(content),
                created_at=now,
                updated_at=now,
                x=x,
                y=y,
                mime_type=mime_type,
            )
            files.append(item)
            self._write_index(files)
            self._audit(
                "shared_file_uploaded",
                actor_name=actor_name,
                source=source,
                file_id=file_id,
                file_name=file_name,
                size_bytes=len(content),
            )
            return {"file": self._public_file_dict(item), "storage": self._storage_payload(files)}

    def rename_shared_file(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        actor_name, source = self._audit_identity(payload)
        new_name = self._validated_file_name(payload.get("file_name"))
        with self._locked_files(write=True) as files:
            item = self._find_file(files, payload.get("file_id"))
            previous_name = item.original_name
            item.original_name = new_name
            item.extension = _normalized_extension(PurePath(new_name).suffix)
            item.updated_at = _utc_now_iso()
            self._write_index(files)
            self._audit(
                "shared_file_renamed",
                actor_name=actor_name,
                source=source,
                file_id=item.id,
                file_name=new_name,
                previous_name=previous_name,
            )
            return {"file": self._public_file_dict(item), "storage": self._storage_payload(files)}

    def delete_shared_file(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        actor_name, source = self._audit_identity(payload)
        with self._locked_files(write=True) as files:
            item = self._find_file(files, payload.get("file_id"))
            file_path = self._storage_path(item.stored_name)
            remaining = [candidate for candidate in files if candidate.id != item.id]
            files[:] = remaining
            if file_path.exists():
                file_path.unlink()
            self._write_index(remaining)
            self._audit(
                "shared_file_deleted",
                actor_name=actor_name,
                source=source,
                file_id=item.id,
                file_name=item.original_name,
            )
            return {
                "deleted": True,
                "file_id": item.id,
                "storage": self._storage_payload(remaining),
            }

    def copy_shared_file(self, payload: dict | None = None) -> dict:
        with self._locked_files() as files:
            item = self._find_file(files, (payload or {}).get("file_id"))
            return {
                "clipboard": {
                    "source_id": item.id,
                    "file_name": item.original_name,
                    "size_bytes": item.size_bytes,
                },
                "file": self._public_file_dict(item),
            }

    def paste_shared_file(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        actor_name, source = self._audit_identity(payload)
        source_id = payload.get("source_id") or payload.get("file_id") or payload.get("copied_from")
        x = _normalize_position(payload.get("x"))
        y = _normalize_position(payload.get("y"))
        with self._locked_files(write=True) as files:
            source_file = self._find_file(files, source_id)
            source_path = self._require_file_on_disk(source_file)
            self._ensure_storage_capacity(files, source_file.size_bytes)
            file_id = str(uuid4())
            original_name = self._copy_name(source_file.original_name, files)
            stored_name = self._unique_stored_name(file_id, source_file.extension)
            target_path = self._storage_path(stored_name)
            shutil.copyfile(source_path, target_path)
            now = _utc_now_iso()
            item = SharedFile(
                id=file_id,
                original_name=original_name,
                stored_name=stored_name,
                extension=_normalized_extension(PurePath(original_name).suffix),
                size_bytes=source_file.size_bytes,
                created_at=now,
                updated_at=now,
                x=x,
                y=y,
                mime_type=source_file.mime_type,
                copied_from=source_file.id,
                source_id=source_file.id,
            )
            files.append(item)
            self._write_index(files)
            self._audit(
                "shared_file_copied",
                actor_name=actor_name,
                source=source,
                file_id=item.id,
                file_name=item.original_name,
                source_id=source_file.id,
            )
            return {"file": self._public_file_dict(item), "storage": self._storage_payload(files)}

    def update_shared_file_position(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self._locked_files(write=True) as files:
            item = self._find_file(files, payload.get("file_id"))
            item.x = _normalize_position(payload.get("x"))
            item.y = _normalize_position(payload.get("y"))
            item.updated_at = _utc_now_iso()
            self._write_index(files)
            return {"file": self._public_file_dict(item), "storage": self._storage_payload(files)}

    def fetch_shared_file(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        include_base64 = _normalize_bool(payload.get("include_base64"), default=True)
        max_base64_bytes = min(
            _FETCH_BASE64_MAX_BYTES,
            max(
                0,
                normalize_int(payload.get("max_base64_bytes"), default=_FETCH_BASE64_DEFAULT_BYTES),
            ),
        )
        with self._locked_files() as files:
            item = self._find_file(files, payload.get("file_id"))
            path = self._require_file_on_disk(item)
            content: dict[str, Any] = {
                "base64_included": False,
                "size_bytes": item.size_bytes,
                "max_base64_bytes": max_base64_bytes,
            }
            if include_base64 and item.size_bytes <= max_base64_bytes:
                content["base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
                content["base64_included"] = True
                content["encoding"] = "base64"
            return {
                "file": self._public_file_dict(item),
                "content": content,
                "storage": self._storage_payload(files),
            }

    def get_shared_file_download(self, file_id: str) -> tuple[Path, dict[str, Any]]:
        with self._locked_files() as files:
            item = self._find_file(files, file_id)
            path = self._require_file_on_disk(item)
            return path, self._public_file_dict(item)

    def _validated_file_name(self, value: Any) -> str:
        file_name = normalize_file_name(value)
        if not file_name:
            raise ServiceError(
                "validation_error",
                "Нужно указать имя файла.",
                details={"field": "file_name"},
            )
        suffixes = [suffix.casefold() for suffix in PurePath(file_name).suffixes]
        blocked = next((suffix for suffix in suffixes if suffix in _DISALLOWED_EXTENSIONS), "")
        if blocked:
            raise ServiceError(
                "validation_error",
                "Этот тип файла запрещён для общего хранилища.",
                details={"extension": blocked},
            )
        return file_name

    def _validated_content(self, value: Any) -> bytes:
        if value is None:
            raise ServiceError(
                "validation_error",
                "Нужно передать content_base64.",
                details={"field": "content_base64"},
            )
        try:
            return base64.b64decode(str(value), validate=True)
        except (binascii.Error, ValueError):
            raise ServiceError(
                "validation_error",
                "content_base64 должен быть корректной base64-строкой.",
                details={"field": "content_base64"},
            ) from None

    def _ensure_storage_capacity(self, files: list[SharedFile], additional_bytes: int) -> None:
        used = self._used_bytes(files)
        if used + max(0, int(additional_bytes)) > self._storage_limit_bytes:
            raise ServiceError(
                "storage_limit_exceeded",
                "Общее хранилище файлов заполнено.",
                status_code=HTTPStatus.CONFLICT,
                details={
                    "used_bytes": used,
                    "limit_bytes": self._storage_limit_bytes,
                    "incoming_bytes": max(0, int(additional_bytes)),
                },
            )

    def _find_file(self, files: list[SharedFile], file_id: Any) -> SharedFile:
        normalized_id = str(file_id or "").strip()
        if not normalized_id:
            raise ServiceError(
                "validation_error",
                "Нужно передать file_id.",
                details={"field": "file_id"},
            )
        for item in files:
            if item.id == normalized_id:
                return item
        raise ServiceError(
            "not_found",
            "Файл не найден.",
            status_code=HTTPStatus.NOT_FOUND,
            details={"file_id": normalized_id},
        )

    def _require_file_on_disk(self, item: SharedFile) -> Path:
        path = self._storage_path(item.stored_name)
        if not path.exists() or not path.is_file():
            raise ServiceError(
                "not_found",
                "Файл не найден на диске.",
                status_code=HTTPStatus.NOT_FOUND,
                details={"file_id": item.id},
            )
        return path

    def _public_file_dict(self, item: SharedFile) -> dict[str, Any]:
        payload = item.to_dict()
        payload["download_path"] = f"/api/shared_file?file_id={item.id}"
        payload["open_path"] = f"/api/shared_file?file_id={item.id}&disposition=inline"
        payload["exists_on_disk"] = self._storage_path(item.stored_name).exists()
        return payload

    def _storage_payload(self, files: list[SharedFile]) -> dict[str, Any]:
        used = self._used_bytes(files)
        return {
            "used_bytes": used,
            "limit_bytes": self._storage_limit_bytes,
            "remaining_bytes": max(0, self._storage_limit_bytes - used),
            "total_files": len(files),
            "used_percent": round((used / self._storage_limit_bytes) * 100, 2),
        }

    def _used_bytes(self, files: list[SharedFile]) -> int:
        return sum(max(0, int(item.size_bytes)) for item in files)

    def _storage_path(self, stored_name: str) -> Path:
        safe_name = normalize_file_name(stored_name)
        if not safe_name or safe_name != stored_name or PurePath(safe_name).name != safe_name:
            raise ServiceError("validation_error", "Некорректное имя файла на диске.")
        root = self._storage_dir.resolve(strict=False)
        path = (self._storage_dir / safe_name).resolve(strict=False)
        try:
            if os.path.commonpath([str(root), str(path)]) != str(root):
                raise ValueError
        except ValueError:
            raise ServiceError("validation_error", "Некорректный путь файла.") from None
        return self._storage_dir / safe_name

    def _unique_stored_name(self, file_id: str, extension: str) -> str:
        safe_extension = _normalized_extension(extension)
        for index in range(8):
            suffix = "" if index == 0 else f"-{index}"
            stored_name = f"{file_id}{suffix}{safe_extension}"
            if not self._storage_path(stored_name).exists():
                return stored_name
        raise ServiceError("internal_error", "Не удалось подобрать имя файла на диске.")

    def _copy_name(self, original_name: str, files: list[SharedFile]) -> str:
        suffix = PurePath(original_name).suffix
        stem = original_name[: -len(suffix)] if suffix else original_name
        base_name = f"{stem} - копия"
        existing = {item.original_name.casefold() for item in files}
        for index in range(1, 1000):
            candidate_stem = base_name if index == 1 else f"{base_name} {index}"
            candidate = normalize_file_name(f"{candidate_stem}{suffix}") or f"copy-{index}{suffix}"
            if candidate.casefold() not in existing:
                return candidate
        return normalize_file_name(f"{uuid4().hex}{suffix}") or uuid4().hex

    def _read_index(self) -> list[SharedFile]:
        if not self._index_file.exists():
            self._write_index([])
            return []
        try:
            raw = json.loads(self._index_file.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            raise ServiceError(
                "storage_error",
                "Не удалось прочитать индекс общего хранилища файлов.",
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from None
        raw_files = raw.get("files") if isinstance(raw, dict) else []
        if not isinstance(raw_files, list):
            return []
        files: list[SharedFile] = []
        seen_ids: set[str] = set()
        for raw_item in raw_files:
            if not isinstance(raw_item, dict):
                continue
            item = SharedFile.from_dict(raw_item)
            if item is None or item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            files.append(item)
        return files

    def _write_index(self, files: list[SharedFile]) -> None:
        payload = {
            "schema_version": SHARED_FILES_INDEX_SCHEMA_VERSION,
            "files": [item.to_dict() for item in files],
        }
        temp_file = self._index_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(self._index_file)

    def _locked_files(self, *, write: bool = False):
        service = self

        class _FilesContext:
            def __enter__(self_inner) -> list[SharedFile]:
                service._lock.acquire()
                self_inner._process_context = service._process_lock.acquire()
                self_inner._process_context.__enter__()
                self_inner.files = service._read_index()
                return self_inner.files

            def __exit__(self_inner, exc_type, exc, tb) -> None:
                try:
                    self_inner._process_context.__exit__(exc_type, exc, tb)
                finally:
                    service._lock.release()
                return None

        return _FilesContext()

    def _audit_identity(self, payload: dict[str, Any]) -> tuple[str, str]:
        actor_name = normalize_actor_name(payload.get("actor_name"), default="СИСТЕМА")
        source = str(payload.get("source") or "api").strip().lower() or "api"
        if source not in {"ui", "api", "mcp", "system"}:
            source = "api"
        return actor_name, source

    def _audit(self, event: str, **fields: Any) -> None:
        if self._logger is not None:
            self._logger.info("%s %s", event, fields)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return ""
    return text


def _normalize_position(value: Any) -> int:
    return min(100_000, normalize_int(value, default=0, minimum=0))


def _normalize_mime_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or "/" not in text or len(text) > 120:
        return "application/octet-stream"
    return text


def _normalized_extension(value: Any) -> str:
    raw = str(value or "").strip()
    extension = (
        raw if raw.startswith(".") and "/" not in raw and "\\" not in raw else PurePath(raw).suffix
    )
    extension = extension.casefold()
    if not extension or len(extension) > 32:
        return ""
    if not extension.startswith("."):
        extension = f".{extension}"
    return extension


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default
