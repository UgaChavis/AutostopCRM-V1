"""Card attachment lifecycle, bounded content readers and safe file storage."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePath
from typing import Any

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from ..models import (
    MAX_ATTACHMENT_SIZE_BYTES,
    Attachment,
    Card,
    normalize_bool,
    normalize_file_name,
    normalize_text,
    utc_now,
    utc_now_iso,
)
from ..storage.limited_io import is_regular_file, read_bytes_limited
from .errors import ServiceError

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
_ATTACHMENT_BASE64_ENCODED_MAX_CHARS = ((MAX_ATTACHMENT_SIZE_BYTES + 2) // 3) * 4
_ATTACHMENT_XML_READ_MAX_BYTES = 5_000_000


@dataclass(frozen=True, slots=True)
class _AttachmentFileRepair:
    source_path: Path
    target_path: Path
    expected_size: int
    expected_sha256: str


class CardAttachmentsMixin:
    def add_card_attachment(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._read_card_bundle_for_update(payload.get("card_id", ""))
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
                    viewer_username=actor_name,
                ),
                "attachment": attachment.to_dict(),
            }

    def remove_card_attachment(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._read_card_bundle_for_update(payload.get("card_id", ""))
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
            # Persist the tombstone before deleting bytes: a failed state save must
            # leave the previously active attachment recoverable.
            try:
                self._delete_attachment_file(card.id, attachment.stored_name)
            except (OSError, ServiceError):
                self._logger.warning(
                    "attachment cleanup deferred card_id=%s attachment_id=%s",
                    card.id,
                    attachment.id,
                    exc_info=True,
                )
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
                    viewer_username=actor_name,
                )
            }

    def get_attachment_download(self, card_id: str, attachment_id: str) -> tuple[Path, Attachment]:
        with self._lock:
            bundle = self._read_card_bundle_for_update(card_id)
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
            attachment_path, repaired, file_repair = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_attachment_metadata_repair(
                    bundle, card_id=card.id, file_repair=file_repair
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
            bundle = self._read_card_bundle_for_update(payload.get("card_id", ""))
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
            attachment_path, repaired, file_repair = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_attachment_metadata_repair(
                    bundle, card_id=card.id, file_repair=file_repair
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
            bundle = self._read_card_bundle_for_update(payload.get("card_id", ""))
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
            attachment_path, repaired, file_repair = self._repair_attachment_metadata(
                card.id, attachment, attachment_path
            )
            if repaired:
                self._save_attachment_metadata_repair(
                    bundle, card_id=card.id, file_repair=file_repair
                )
            content = self._read_attachment_file_bytes(attachment_path, attachment)
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

    def _cleanup_attachment_directories(self, cards: list[Card]) -> None:
        keep_card_ids = {card.id for card in cards}
        root = self._attachments_dir.resolve(strict=False)
        for candidate in self._attachments_dir.iterdir():
            if candidate.name in keep_card_ids:
                continue
            try:
                if candidate.is_symlink():
                    candidate.unlink()
                    continue
                if not candidate.is_dir():
                    continue
                candidate.resolve(strict=False).relative_to(root)
            except OSError:
                continue
            except ValueError:
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                continue

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
            payload["exists_on_disk"] = self._attachment_is_regular_file(attachment_path)
            if payload["exists_on_disk"]:
                try:
                    payload["sha256"] = self._attachment_file_sha256(attachment_path)
                except ValueError:
                    payload["oversized_on_disk"] = True
                except OSError:
                    payload["exists_on_disk"] = False
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
            text = self._extract_docx_text(content, max_chars=max_chars)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = "office-openxml"
            payload["extraction_status"] = "ok" if text.strip() else "empty"
        elif content_kind == "xlsx":
            text = self._extract_xlsx_text(content, max_chars=max_chars)
            self._set_truncated_attachment_text(payload, text, max_chars)
            payload["encoding"] = "office-openxml"
            payload["extraction_status"] = "ok" if text.strip() else "empty"
        elif content_kind == "pdf":
            text = self._extract_pdf_text(content, max_chars=max_chars)
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

    def _append_limited_attachment_text(
        self,
        parts: list[str],
        current_chars: int,
        fragment: str,
        *,
        max_chars: int,
    ) -> tuple[int, bool]:
        fragment = str(fragment or "")
        if not fragment:
            return current_chars, False
        limit = max_chars + 1
        remaining = limit - current_chars
        if remaining <= 0:
            return current_chars, True
        if len(fragment) > remaining:
            parts.append(fragment[:remaining])
            return limit, True
        parts.append(fragment)
        return current_chars + len(fragment), False

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

    def _parse_attachment_xml(self, content: bytes) -> ET.Element:
        prefix = content.lstrip()[:2048].lower()
        if b"<!doctype" in prefix or b"<!entity" in prefix:
            raise ET.ParseError("XML entities are not supported in attachments.")
        return safe_xml_fromstring(content)

    def _read_attachment_zip_member(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
    ) -> bytes | None:
        if info.file_size > _ATTACHMENT_XML_READ_MAX_BYTES:
            return None
        try:
            with archive.open(info) as member:
                content = member.read(_ATTACHMENT_XML_READ_MAX_BYTES + 1)
        except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
            return None
        if len(content) > _ATTACHMENT_XML_READ_MAX_BYTES:
            return None
        return content

    def _extract_docx_text(
        self, content: bytes, *, max_chars: int = _ATTACHMENT_READ_MAX_CHARS
    ) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                info = archive.getinfo("word/document.xml")
                xml_content = self._read_attachment_zip_member(archive, info)
                if xml_content is None:
                    return ""
                root = self._parse_attachment_xml(xml_content)
        except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
            return ""
        parts: list[str] = []
        current_chars = 0
        for paragraph in root.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        ):
            text_parts = [
                node.text or ""
                for node in paragraph.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            ]
            line = "".join(text_parts).strip()
            if line:
                fragment = line if not parts else f"\n{line}"
                current_chars, done = self._append_limited_attachment_text(
                    parts,
                    current_chars,
                    fragment,
                    max_chars=max_chars,
                )
                if done:
                    return "".join(parts)
        if parts:
            return "".join(parts)
        for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
            line = (node.text or "").strip()
            if not line:
                continue
            fragment = line if not parts else f"\n{line}"
            current_chars, done = self._append_limited_attachment_text(
                parts,
                current_chars,
                fragment,
                max_chars=max_chars,
            )
            if done:
                break
        return "".join(parts)

    def _extract_xlsx_text(
        self, content: bytes, *, max_chars: int = _ATTACHMENT_READ_MAX_CHARS
    ) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                shared_strings = self._xlsx_shared_strings(archive)
                parts: list[str] = []
                current_chars = 0
                worksheet_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/") and name.endswith(".xml")
                )
                for worksheet_name in worksheet_names[:20]:
                    info = archive.getinfo(worksheet_name)
                    xml_content = self._read_attachment_zip_member(archive, info)
                    if xml_content is None:
                        continue
                    root = self._parse_attachment_xml(xml_content)
                    sheet_label = PurePath(worksheet_name).stem
                    sheet_started = False
                    for cell in root.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
                    ):
                        value = self._xlsx_cell_text(cell, shared_strings)
                        if value:
                            ref = cell.attrib.get("r", "")
                            cell_text = f"{ref}: {value}" if ref else value
                            if sheet_started:
                                fragment = f"\n{cell_text}"
                            else:
                                prefix = "\n\n" if parts else ""
                                fragment = f"{prefix}[{sheet_label}]\n{cell_text}"
                                sheet_started = True
                            current_chars, done = self._append_limited_attachment_text(
                                parts,
                                current_chars,
                                fragment,
                                max_chars=max_chars,
                            )
                            if done:
                                return "".join(parts)
                return "".join(parts)
        except (OSError, ET.ParseError, zipfile.BadZipFile):
            return ""
        return ""

    def _xlsx_shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        try:
            info = archive.getinfo("xl/sharedStrings.xml")
            xml_content = self._read_attachment_zip_member(archive, info)
            if xml_content is None:
                return []
            root = self._parse_attachment_xml(xml_content)
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
            try:
                index = int(raw_value)
            except (OverflowError, ValueError):
                return raw_value
            if 0 <= index < len(shared_strings):
                return shared_strings[index].strip()
        return raw_value

    def _extract_pdf_text(
        self, content: bytes, *, max_chars: int = _ATTACHMENT_READ_MAX_CHARS
    ) -> str:
        parts: list[str] = []
        current_chars = 0
        for match in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj", content):
            text = self._decode_pdf_literal(match.group(0).rsplit(b")", 1)[0][1:])
            if not text.strip():
                continue
            fragment = text if not parts else f"\n{text}"
            current_chars, done = self._append_limited_attachment_text(
                parts,
                current_chars,
                fragment,
                max_chars=max_chars,
            )
            if done:
                return "".join(parts)
        for match in re.finditer(rb"\[(.*?)\]\s*TJ", content, flags=re.DOTALL):
            for literal in re.finditer(rb"\((?:\\.|[^\\)])*\)", match.group(1)):
                text = self._decode_pdf_literal(literal.group(0)[1:-1])
                if not text.strip():
                    continue
                fragment = text if not parts else f"\n{text}"
                current_chars, done = self._append_limited_attachment_text(
                    parts,
                    current_chars,
                    fragment,
                    max_chars=max_chars,
                )
                if done:
                    return "".join(parts)
        return "".join(parts)

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
        card_dir = self._attachment_card_dir(card_id)
        safe_name = self._validated_attachment_stored_name(stored_name)
        root = self._attachments_dir.resolve(strict=False)
        attachment_path = card_dir / safe_name
        try:
            attachment_path.relative_to(root)
        except ValueError:
            self._fail("validation_error", "Некорректный путь файла вложения.")
        return attachment_path

    def _attachment_card_dir(self, card_id: str) -> Path:
        safe_card_id = self._validated_attachment_path_segment(card_id, field="card_id")
        root = self._attachments_dir.resolve(strict=False)
        card_dir = (root / safe_card_id).resolve(strict=False)
        try:
            card_dir.relative_to(root)
        except ValueError:
            self._fail("validation_error", "Некорректный каталог вложений карточки.")
        return card_dir

    def _validated_attachment_path_segment(self, value: Any, *, field: str) -> str:
        segment = str(value or "").strip()
        if (
            not segment
            or segment in {".", ".."}
            or "\x00" in segment
            or "/" in segment
            or "\\" in segment
        ):
            self._fail(
                "validation_error",
                "Некорректный путь файла вложения.",
                details={"field": field},
            )
        return segment

    def _validated_attachment_stored_name(self, stored_name: str) -> str:
        raw_name = str(stored_name or "").strip()
        safe_name = normalize_file_name(raw_name)
        if (
            not safe_name
            or safe_name != raw_name
            or safe_name in {".", ".."}
            or PurePath(safe_name).name != safe_name
        ):
            self._fail(
                "validation_error",
                "Некорректное имя файла вложения на диске.",
                details={"field": "stored_name"},
            )
        return safe_name

    def _attachment_exists_on_disk(self, card_id: str, attachment: Attachment | None) -> bool:
        if attachment is None or attachment.removed:
            return False
        try:
            return self._attachment_is_regular_file(
                self._attachment_path(card_id, attachment.stored_name)
            )
        except (OSError, ServiceError):
            return False

    _attachment_is_regular_file = staticmethod(is_regular_file)

    def _read_attachment_file_bytes(self, attachment_path: Path, attachment: Attachment) -> bytes:
        try:
            return read_bytes_limited(
                attachment_path,
                max_bytes=MAX_ATTACHMENT_SIZE_BYTES,
                label="attachment file",
            )
        except OSError:
            self._fail(
                "not_found",
                "Файл не найден на диске.",
                status_code=404,
                details={"attachment_id": attachment.id},
            )
        except ValueError:
            self._fail(
                "validation_error",
                "Сохранённый файл вложения превышает допустимый размер.",
                details={
                    "attachment_id": attachment.id,
                    "file_name": attachment.file_name,
                    "size_bytes": MAX_ATTACHMENT_SIZE_BYTES + 1,
                    "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES,
                },
            )

    def _attachment_file_sha256(self, attachment_path: Path) -> str:
        if attachment_path.stat().st_size > MAX_ATTACHMENT_SIZE_BYTES:
            raise ValueError("attachment file is too large")
        digest = hashlib.sha256()
        bytes_read = 0
        with attachment_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                bytes_read += len(chunk)
                if bytes_read > MAX_ATTACHMENT_SIZE_BYTES:
                    raise ValueError("attachment file is too large")
                digest.update(chunk)
        return digest.hexdigest()

    def _write_attachment_file(self, card_id: str, stored_name: str, content: bytes) -> Path:
        if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
            raise ValueError("attachment file is too large")
        attachment_path = self._attachment_path(card_id, stored_name)
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = attachment_path.with_name(f".{attachment_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_bytes(content)
            temp_path.replace(attachment_path)
        finally:
            temp_path.unlink(missing_ok=True)
        return attachment_path

    def _delete_attachment_file(self, card_id: str, stored_name: str) -> None:
        attachment_path = self._attachment_path(card_id, stored_name)
        if attachment_path.is_file() or attachment_path.is_symlink():
            attachment_path.unlink()
        self._cleanup_empty_attachment_directory(card_id)

    def _require_attachment_file(self, card_id: str, attachment: Attachment) -> Path:
        attachment_path = self._attachment_path(card_id, attachment.stored_name)
        for _ in range(20):
            if self._attachment_is_regular_file(attachment_path):
                return attachment_path
            if attachment_path.exists():
                break
            time.sleep(0.05)
        self._fail(
            "not_found",
            "Файл не найден на диске.",
            status_code=404,
            details={"attachment_id": attachment.id},
        )

    def _cleanup_empty_attachment_directory(self, card_id: str) -> None:
        try:
            attachment_dir = self._attachment_card_dir(card_id)
        except ServiceError:
            return
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
    ) -> tuple[Path, bool, _AttachmentFileRepair | None]:
        source_path = attachment_path
        content = self._read_attachment_file_bytes(attachment_path, attachment)
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
        target_path = self._attachment_path(card_id, preferred_stored_name)
        file_repair: _AttachmentFileRepair | None = None
        if target_path != attachment_path:
            target_available = False
            if target_path.exists():
                try:
                    target_available = (
                        self._attachment_is_regular_file(target_path)
                        and self._read_attachment_file_bytes(target_path, attachment) == content
                    )
                    if not target_available:
                        self._write_attachment_file(card_id, preferred_stored_name, content)
                        target_available = True
                except (OSError, ValueError, ServiceError):
                    self._logger.warning(
                        "attachment repair target refresh deferred card_id=%s target=%s",
                        card_id,
                        target_path.name,
                        exc_info=True,
                    )
                    target_available = False
            else:
                self._write_attachment_file(card_id, preferred_stored_name, content)
                target_available = True
            if target_available:
                attachment_path = target_path
                attachment.stored_name = preferred_stored_name
                repaired = True
                file_repair = _AttachmentFileRepair(
                    source_path=source_path,
                    target_path=target_path,
                    expected_size=len(content),
                    expected_sha256=hashlib.sha256(content).hexdigest(),
                )
        elif attachment.stored_name != preferred_stored_name:
            attachment.stored_name = preferred_stored_name
            repaired = True
        return attachment_path, repaired, file_repair

    def _save_attachment_metadata_repair(
        self,
        bundle: dict,
        *,
        card_id: str,
        file_repair: _AttachmentFileRepair | None,
    ) -> None:
        # Keep a staged target when the state write fails. Removing it after a
        # separate authority check has a TOCTOU race with another process that
        # can commit metadata pointing at the same byte-identical target. A
        # later repair safely adopts this copy; the authoritative source stays
        # available until one metadata commit succeeds.
        self._save_bundle(
            bundle,
            columns=bundle["columns"],
            cards=bundle["cards"],
            events=bundle["events"],
        )
        self._finish_attachment_file_repair(card_id, file_repair)

    def _finish_attachment_file_repair(
        self, card_id: str, file_repair: _AttachmentFileRepair | None
    ) -> None:
        if file_repair is None or file_repair.source_path == file_repair.target_path:
            return
        try:
            target_matches = (
                self._attachment_is_regular_file(file_repair.target_path)
                and file_repair.target_path.stat().st_size == file_repair.expected_size
                and self._attachment_file_sha256(file_repair.target_path)
                == file_repair.expected_sha256
            )
        except (OSError, ValueError):
            target_matches = False
        if not target_matches:
            self._logger.warning(
                "attachment repair target changed; source retained card_id=%s target=%s",
                card_id,
                file_repair.target_path.name,
            )
            return
        try:
            if file_repair.source_path.is_file() or file_repair.source_path.is_symlink():
                file_repair.source_path.unlink()
        except OSError:
            self._logger.warning(
                "attachment repair source cleanup deferred card_id=%s source=%s",
                card_id,
                file_repair.source_path.name,
                exc_info=True,
            )

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
        if len(raw_value) > _ATTACHMENT_BASE64_ENCODED_MAX_CHARS:
            self._fail(
                "validation_error",
                "Файл слишком большой.",
                details={"field": "content_base64", "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES},
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
