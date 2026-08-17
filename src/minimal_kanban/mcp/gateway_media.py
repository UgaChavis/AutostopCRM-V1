from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

STORE_VIN_PHOTO_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
_BINARY_CONTENT_KEYS = frozenset(
    {"base64", "content_base64", "content_bytes", "pdf_base64"}
)


def without_binary_content(value: Any) -> Any:
    """Remove binary payload fields before data enters a structured MCP response."""

    if isinstance(value, dict):
        return {
            str(key): without_binary_content(item)
            for key, item in value.items()
            if str(key) not in _BINARY_CONTENT_KEYS
        }
    if isinstance(value, list):
        return [without_binary_content(item) for item in value]
    return value


def _tool_summary_text(payload: dict[str, Any], *, label: str) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    text_payload = {
        "ok": bool(payload.get("ok")),
        "tool": label,
        "status": payload.get("status"),
        "run_id": payload.get("run_id"),
        "summary": summary,
        "warnings": payload.get("warnings") or [],
        "next_actions": payload.get("next_actions") or [],
    }
    text = json.dumps(text_payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= 1000 else text[:997] + "..."


def tool_result(payload: dict[str, Any], *, label: str) -> CallToolResult:
    """Return the compact text and structured result shared by Gateway tools."""

    return CallToolResult(
        content=[TextContent(type="text", text=_tool_summary_text(payload, label=label))],
        structuredContent=payload,
        isError=not bool(payload.get("ok")),
    )


def tool_result_with_image(
    payload: dict[str, Any],
    *,
    label: str,
    image_base64: str,
    mime_type: str,
) -> CallToolResult:
    """Return a compact text summary plus an MCP image content block."""

    return CallToolResult(
        content=[
            TextContent(type="text", text=_tool_summary_text(payload, label=label)),
            ImageContent(type="image", data=image_base64, mimeType=mime_type),
        ],
        structuredContent=payload,
        isError=not bool(payload.get("ok")),
    )


def store_vin_photo_image(result: Mapping[str, Any]) -> tuple[str, str]:
    """Validate the bounded JPEG returned by the internal Store adapter."""

    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    image_base64 = data.get("content_base64")
    mime_type = data.get("content_type")
    if not isinstance(image_base64, str) or not isinstance(mime_type, str):
        raise ValueError("store_attachment_payload_missing")
    if mime_type.casefold() != "image/jpeg":
        raise ValueError("store_attachment_mime_invalid")
    try:
        content = base64.b64decode(image_base64, validate=True)
    except (ValueError, TypeError):
        raise ValueError("store_attachment_base64_invalid") from None
    if not content or len(content) > STORE_VIN_PHOTO_PREVIEW_MAX_BYTES:
        raise ValueError("store_attachment_payload_too_large")
    return image_base64, "image/jpeg"
