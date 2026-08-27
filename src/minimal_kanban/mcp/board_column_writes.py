from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope

BOARD_COLUMN_WRITE_TOOL_NAMES = frozenset(
    {
        "create_column",
        "delete_column",
        "rename_column",
    }
)


@dataclass(frozen=True, slots=True)
class BoardColumnWriteContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    write_tool_annotations: Callable[..., ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]


def register_board_column_writes(
    server: FastMCP,
    context: BoardColumnWriteContext,
) -> frozenset[str]:
    @server.tool(
        name="create_column",
        description=context.scoped_description(
            "Create a new column on the current AutoStop CRM board."
        ),
        annotations=context.write_tool_annotations("Create Column"),
        structured_output=True,
    )
    def create_column(
        label: str | None = None,
        name: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "create_column",
            lambda: context.board_api.create_column(
                label,
                name=name,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="rename_column",
        description=context.scoped_description(
            "Rename an existing column on the current AutoStop CRM board while keeping the same column id."
        ),
        annotations=context.write_tool_annotations("Rename Column", idempotent=True),
        structured_output=True,
    )
    def rename_column(
        column_id: str,
        label: str,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "rename_column",
            lambda: context.board_api.rename_column(
                column_id,
                label,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="delete_column",
        description=context.scoped_description(
            "Delete an empty column from the current AutoStop CRM board. The last remaining column cannot be removed."
        ),
        annotations=context.write_tool_annotations("Delete Column", destructive=True),
        structured_output=True,
    )
    def delete_column(
        column_id: str,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "delete_column",
            lambda: context.board_api.delete_column(
                column_id,
                actor_name=actor_name,
            ),
        )

    return BOARD_COLUMN_WRITE_TOOL_NAMES
