from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope, McpInt

BOARD_READ_TOOL_NAMES = frozenset(
    {
        "get_board_snapshot",
        "get_card",
        "get_cards",
        "list_columns",
    }
)


@dataclass(frozen=True, slots=True)
class BoardReadContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    read_tool_annotations: Callable[[str], ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]
    with_cards_list_meta: Callable[..., dict[str, Any]]
    normalize_limit: Callable[..., int]
    with_data_meta: Callable[..., dict[str, Any]]


def register_board_reads(
    server: FastMCP,
    context: BoardReadContext,
) -> frozenset[str]:
    @server.tool(
        name="list_columns",
        description=context.scoped_description(
            "List all columns of the current AutoStop CRM board."
        ),
        annotations=context.read_tool_annotations("List Columns"),
        structured_output=True,
    )
    def list_columns() -> JsonEnvelope:
        return context.relay_board_call("list_columns", context.board_api.list_columns)

    @server.tool(
        name="get_cards",
        description=context.scoped_description(
            "Return cards from the current AutoStop CRM board. Archived cards are excluded by default. "
            "Use compact=true for board scans with lighter payloads; set compact=false when full vehicle_profile, repair_order, attachments, and ai_autofill_log are needed."
        ),
        annotations=context.read_tool_annotations("List Cards"),
        structured_output=True,
    )
    def get_cards(include_archived: bool = False, compact: bool = True) -> JsonEnvelope:
        return context.relay_board_call(
            "get_cards",
            lambda: context.board_api.get_cards(
                include_archived=include_archived,
                compact=compact,
            ),
            params={"include_archived": include_archived, "compact": compact},
            transform=lambda response: context.with_cards_list_meta(
                response,
                include_archived=include_archived,
                compact=compact,
                response_mode="list",
            ),
        )

    @server.tool(
        name="get_card",
        description=context.scoped_description(
            "Return one card by card_id from the current AutoStop CRM board, including the full vehicle_profile and the compact vehicle_profile_compact used by the 1.1 card layout."
        ),
        annotations=context.read_tool_annotations("Get Card"),
        structured_output=True,
    )
    def get_card(card_id: str) -> JsonEnvelope:
        return context.relay_board_call(
            "get_card",
            lambda: context.board_api.get_card(card_id),
        )

    @server.tool(
        name="get_board_snapshot",
        description=context.scoped_description(
            "Return a structured snapshot of the current AutoStop CRM board: columns, active cards, archived tail, stickies, and settings. "
            "Cards in the snapshot include vehicle_profile_compact for the 1.1 vehicle card view. "
            "Use compact=true for lighter GPT scans and include_archive=false when the archived tail is not needed."
        ),
        annotations=context.read_tool_annotations("Board Snapshot"),
        structured_output=True,
    )
    def get_board_snapshot(
        archive_limit: McpInt = 10,
        compact: bool = False,
        include_archive: bool = True,
    ) -> JsonEnvelope:
        effective_archive_limit = (
            context.normalize_limit(archive_limit, default=30, maximum=50) if include_archive else 0
        )
        return context.relay_board_call(
            "get_board_snapshot",
            lambda: context.board_api.get_board_snapshot(
                archive_limit=effective_archive_limit,
                compact=compact,
                include_archive=include_archive,
            ),
            params={
                "archive_limit": effective_archive_limit,
                "compact": compact,
                "include_archive": include_archive,
            },
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="snapshot",
                view_mode="compact" if compact else "full",
                archive_limit=effective_archive_limit,
                include_archive=include_archive,
                compact=compact,
            ),
        )

    return BOARD_READ_TOOL_NAMES
