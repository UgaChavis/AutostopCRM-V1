from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TelegramAttachment:
    kind: str
    file_id: str
    file_unique_id: str = ""
    mime_type: str = ""
    file_name: str = ""
    file_size: int | None = None
    width: int | None = None
    height: int | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file_unique_id": self.file_unique_id,
            "mime_type": self.mime_type,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class DownloadedAttachment:
    attachment: TelegramAttachment
    content: bytes
    file_path: str = ""

    @property
    def mime_type(self) -> str:
        return self.attachment.mime_type or _mime_from_path(self.file_path)

    @property
    def file_name(self) -> str:
        return self.attachment.file_name or self.file_path.rsplit("/", 1)[-1] or "telegram-file"


@dataclass(frozen=True)
class NormalizedTelegramInput:
    update_id: int
    message_id: int
    chat_id: int
    user_id: int
    username: str = ""
    first_name: str = ""
    input_type: str = "text"
    text: str = ""
    caption: str = ""
    attachments: tuple[TelegramAttachment, ...] = ()
    raw_date: int | None = None

    @property
    def command_text(self) -> str:
        return (self.text or self.caption or "").strip()

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id,
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "input_type": self.input_type,
            "raw_text": self.text,
            "caption": self.caption,
            "attachments": [item.to_audit_dict() for item in self.attachments],
            "raw_date": self.raw_date,
        }


@dataclass
class RunContext:
    run_id: str
    role: str
    normalized_input: NormalizedTelegramInput
    transcribed_text: str = ""
    voice_transcription_error: str = ""
    image_facts: dict[str, Any] = field(default_factory=dict)
    context_summary: dict[str, Any] = field(default_factory=dict)
    model_decision: dict[str, Any] = field(default_factory=dict)
    planned_actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    verify_result: dict[str, Any] = field(default_factory=dict)
    final_status: str = "running"
    telegram_response: str = ""
    error: str = ""


def _mime_from_path(file_path: str) -> str:
    suffix = str(file_path or "").rsplit(".", 1)[-1].lower()
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    if suffix == "webp":
        return "image/webp"
    if suffix == "gif":
        return "image/gif"
    if suffix in {"oga", "ogg"}:
        return "audio/ogg"
    if suffix == "mp3":
        return "audio/mpeg"
    if suffix == "wav":
        return "audio/wav"
    return "application/octet-stream"
