from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope, McpInt, StickyDeadlinePayload

BOARD_STICKY_CREATE_TOOL_NAMES = frozenset({"create_sticky"})
BOARD_STICKY_MUTATION_TOOL_NAMES = frozenset(
    {
        "delete_sticky",
        "move_sticky",
        "update_sticky",
    }
)
BOARD_STICKY_WRITE_TOOL_NAMES = BOARD_STICKY_CREATE_TOOL_NAMES | BOARD_STICKY_MUTATION_TOOL_NAMES


@dataclass(frozen=True, slots=True)
class BoardStickyWriteContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    write_tool_annotations: Callable[..., ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]


def register_board_sticky_create(
    server: FastMCP,
    context: BoardStickyWriteContext,
) -> frozenset[str]:
    @server.tool(
        name="create_sticky",
        description=context.scoped_description(
            "Create a sticky note on the current AutoStop CRM board. Sticky notes belong only to this board instance. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=context.write_tool_annotations("Create Sticky"),
        structured_output=True,
    )
    def create_sticky(
        text: str,
        deadline: StickyDeadlinePayload,
        x: McpInt = 0,
        y: McpInt = 0,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "create_sticky",
            lambda: context.board_api.create_sticky(
                text=text,
                x=x,
                y=y,
                deadline=deadline.model_dump(),
                actor_name=actor_name,
            ),
        )

    return BOARD_STICKY_CREATE_TOOL_NAMES


def register_board_sticky_mutations(
    server: FastMCP,
    context: BoardStickyWriteContext,
) -> frozenset[str]:
    @server.tool(
        name="update_sticky",
        description=context.scoped_description(
            "Update the text or deadline of a sticky note on the current AutoStop CRM board. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=context.write_tool_annotations("Update Sticky"),
        structured_output=True,
    )
    def update_sticky(
        sticky_id: str,
        text: str | None = None,
        deadline: StickyDeadlinePayload | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "update_sticky",
            lambda: context.board_api.update_sticky(
                sticky_id=sticky_id,
                text=text,
                deadline=deadline.model_dump() if deadline is not None else None,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="move_sticky",
        description=context.scoped_description(
            "Move a sticky note on the current AutoStop CRM board to a new x/y position."
        ),
        annotations=context.write_tool_annotations("Move Sticky"),
        structured_output=True,
    )
    def move_sticky(
        sticky_id: str,
        x: int,
        y: int,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "move_sticky",
            lambda: context.board_api.move_sticky(
                sticky_id=sticky_id,
                x=x,
                y=y,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="delete_sticky",
        description=context.scoped_description(
            "Delete a sticky note from the current AutoStop CRM board."
        ),
        annotations=context.write_tool_annotations("Delete Sticky", destructive=True),
        structured_output=True,
    )
    def delete_sticky(
        sticky_id: str,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "delete_sticky",
            lambda: context.board_api.delete_sticky(
                sticky_id=sticky_id,
                actor_name=actor_name,
            ),
        )

    return BOARD_STICKY_MUTATION_TOOL_NAMES
