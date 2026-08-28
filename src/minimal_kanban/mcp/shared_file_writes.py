from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope, McpInt

SHARED_FILE_WRITE_TOOL_NAMES = frozenset(
    {
        "delete_shared_file",
        "update_shared_file_position",
        "upload_shared_file",
    }
)


@dataclass(frozen=True, slots=True)
class SharedFileWriteContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    write_tool_annotations: Callable[..., ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]


def register_shared_file_writes(
    server: FastMCP,
    context: SharedFileWriteContext,
) -> frozenset[str]:
    @server.tool(
        name="upload_shared_file",
        description=context.scoped_description(
            "Upload one file into the AutoStop CRM Files module. Pass file_name and base64 content; executable script/install extensions are rejected by the backend."
        ),
        annotations=context.write_tool_annotations("Upload Shared File"),
        structured_output=True,
    )
    def upload_shared_file(
        file_name: str,
        content_base64: str,
        mime_type: str = "application/octet-stream",
        x: McpInt = 0,
        y: McpInt = 0,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "upload_shared_file",
            lambda: context.board_api.upload_shared_file(
                file_name=file_name,
                content_base64=content_base64,
                mime_type=mime_type,
                x=x,
                y=y,
                actor_name=actor_name,
            ),
            params={"file_name": file_name, "mime_type": mime_type, "x": x, "y": y},
        )

    @server.tool(
        name="delete_shared_file",
        description=context.scoped_description(
            "Delete one file from the AutoStop CRM Files module. This is a destructive write action."
        ),
        annotations=context.write_tool_annotations(
            "Delete Shared File",
            destructive=True,
        ),
        structured_output=True,
    )
    def delete_shared_file(
        file_id: str,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "delete_shared_file",
            lambda: context.board_api.delete_shared_file(
                file_id,
                expected_updated_at=expected_updated_at,
                actor_name=actor_name,
            ),
            params={"file_id": file_id},
        )

    @server.tool(
        name="update_shared_file_position",
        description=context.scoped_description(
            "Update the saved x/y icon position for one file in the AutoStop CRM Files module."
        ),
        annotations=context.write_tool_annotations(
            "Update Shared File Position",
            idempotent=True,
        ),
        structured_output=True,
    )
    def update_shared_file_position(
        file_id: str,
        x: int,
        y: int,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "update_shared_file_position",
            lambda: context.board_api.update_shared_file_position(
                file_id,
                x=x,
                y=y,
                actor_name=actor_name,
            ),
            params={"file_id": file_id, "x": x, "y": y},
        )

    return SHARED_FILE_WRITE_TOOL_NAMES
