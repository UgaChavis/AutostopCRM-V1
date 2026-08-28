from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import JsonEnvelope, McpInt

SHARED_FILE_READ_TOOL_NAMES = frozenset(
    {
        "download_shared_file",
        "get_shared_file_info",
        "list_shared_files",
    }
)


@dataclass(frozen=True, slots=True)
class SharedFileReadContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    read_tool_annotations: Callable[[str], ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]
    with_data_meta: Callable[..., dict[str, Any]]


def register_shared_file_reads(
    server: FastMCP,
    context: SharedFileReadContext,
) -> frozenset[str]:
    @server.tool(
        name="list_shared_files",
        description=context.scoped_description(
            "List shared workshop files from the AutoStop CRM Files module without returning file bytes."
        ),
        annotations=context.read_tool_annotations("List Shared Files"),
        structured_output=True,
    )
    def list_shared_files() -> JsonEnvelope:
        return context.relay_board_call(
            "list_shared_files",
            context.board_api.list_shared_files,
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="shared_file_list",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="get_shared_file_info",
        description=context.scoped_description(
            "Return metadata for one shared workshop file from the AutoStop CRM Files module, including size, name, position, and download path."
        ),
        annotations=context.read_tool_annotations("Get Shared File Info"),
        structured_output=True,
    )
    def get_shared_file_info(file_id: str) -> JsonEnvelope:
        return context.relay_board_call(
            "get_shared_file_info",
            lambda: context.board_api.get_shared_file_info(file_id),
            params={"file_id": file_id},
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="shared_file_metadata",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="download_shared_file",
        description=context.scoped_description(
            "Fetch one shared workshop file through the AutoStop CRM backend. Small files can return base64; larger files return metadata and download path without file bytes."
        ),
        annotations=context.read_tool_annotations("Download Shared File"),
        structured_output=True,
    )
    def download_shared_file(
        file_id: str,
        include_base64: bool = True,
        max_base64_bytes: McpInt = 2_097_152,
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "download_shared_file",
            lambda: context.board_api.download_shared_file(
                file_id,
                include_base64=include_base64,
                max_base64_bytes=max_base64_bytes,
            ),
            params={
                "file_id": file_id,
                "include_base64": include_base64,
                "max_base64_bytes": max_base64_bytes,
            },
            transform=lambda response: context.with_data_meta(
                response,
                response_mode="shared_file_download",
                view_mode="base64" if include_base64 else "metadata",
            ),
        )

    return SHARED_FILE_READ_TOOL_NAMES
