from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope, McpInt

CARD_ATTACHMENT_READ_TOOL_NAMES = frozenset(
    {
        "get_card_attachment",
        "list_card_attachments",
        "read_card_attachment",
    }
)


@dataclass(frozen=True, slots=True)
class CardAttachmentReadContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    read_tool_annotations: Callable[[str], ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]
    with_data_meta: Callable[..., dict[str, Any]]


def register_card_attachment_reads(
    server: FastMCP,
    context: CardAttachmentReadContext,
) -> frozenset[str]:
    @server.tool(
        name="list_card_attachments",
        description=context.scoped_description(
            "List attachment metadata for one card from the current AutoStop CRM board without returning file bytes. Use this before reading any attached file."
        ),
        annotations=context.read_tool_annotations("List Card Attachments"),
        structured_output=True,
    )
    def list_card_attachments(
        card_id: str,
        include_removed: bool = False,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "list_card_attachments",
            lambda: context.board_api.list_card_attachments(
                card_id,
                include_removed=include_removed,
            ),
            params={"card_id": card_id, "include_removed": include_removed},
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="attachment_list",
                view_mode="metadata",
                include_removed=include_removed,
            ),
        )

    @server.tool(
        name="get_card_attachment",
        description=context.scoped_description(
            "Return safe metadata for one card attachment from the current AutoStop CRM board, including content kind, size, hash, and download path, but not file bytes."
        ),
        annotations=context.read_tool_annotations("Get Card Attachment"),
        structured_output=True,
    )
    def get_card_attachment(card_id: str, attachment_id: str) -> JsonEnvelope:
        return context.relay_board_call(
            "get_card_attachment",
            lambda: context.board_api.get_card_attachment(card_id, attachment_id),
            params={"card_id": card_id, "attachment_id": attachment_id},
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="attachment_metadata",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="read_card_attachment",
        description=context.scoped_description(
            "Read one card attachment for an agent. Text, DOCX, XLSX, and simple PDFs return bounded text; images return dimensions and can include bounded base64/data_url when include_base64=true or mode=base64."
        ),
        annotations=context.read_tool_annotations("Read Card Attachment"),
        structured_output=True,
    )
    def read_card_attachment(
        card_id: str,
        attachment_id: str,
        mode: Literal["preview", "text", "base64", "auto"] = "preview",
        max_chars: McpInt = 12_000,
        include_base64: bool = False,
        max_base64_bytes: McpInt = 1_048_576,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "read_card_attachment",
            lambda: context.board_api.read_card_attachment(
                card_id,
                attachment_id,
                mode=mode,
                max_chars=max_chars,
                include_base64=include_base64,
                max_base64_bytes=max_base64_bytes,
            ),
            params={
                "card_id": card_id,
                "attachment_id": attachment_id,
                "mode": mode,
                "max_chars": max_chars,
                "include_base64": include_base64,
                "max_base64_bytes": max_base64_bytes,
            },
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="attachment_read",
                view_mode=mode,
            ),
        )

    return CARD_ATTACHMENT_READ_TOOL_NAMES
