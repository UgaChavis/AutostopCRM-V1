from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402
from minimal_kanban.mcp.shared_file_reads import (  # noqa: E402
    SHARED_FILE_READ_TOOL_NAMES,
    SharedFileReadContext,
    register_shared_file_reads,
)


class FakeSharedFileReadClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_shared_files(self) -> dict[str, Any]:
        self.calls.append(("list_shared_files", {}))
        return {
            "ok": True,
            "data": {"source": "shared-file-list", "files": []},
            "error": None,
        }

    def get_shared_file_info(self, file_id: str) -> dict[str, Any]:
        self.calls.append(("get_shared_file_info", {"file_id": file_id}))
        return {
            "ok": True,
            "data": {"source": "shared-file-metadata", "id": file_id},
            "error": None,
        }

    def download_shared_file(
        self,
        file_id: str,
        *,
        include_base64: bool,
        max_base64_bytes: int,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "download_shared_file",
                {
                    "file_id": file_id,
                    "include_base64": include_base64,
                    "max_base64_bytes": max_base64_bytes,
                },
            )
        )
        return {
            "ok": True,
            "data": {"source": "shared-file-download", "id": file_id},
            "error": None,
        }


class SharedFileReadRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeSharedFileReadClient()
        self.server = FastMCP(name="shared-file-reads-test")
        self.relay_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.data_meta_calls: list[tuple[str, dict[str, Any]]] = []

        def read_tool_annotations(title: str) -> ToolAnnotations:
            return ToolAnnotations(
                title=title,
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            )

        def relay_board_call(
            tool_name: str,
            fetcher: Any,
            *,
            params: dict[str, Any] | None = None,
            transform: Any = None,
        ) -> JsonEnvelope:
            self.relay_calls.append((tool_name, params))
            response = fetcher()
            if transform is not None:
                response = transform(response)
            return JsonEnvelope.model_validate(response)

        def with_data_meta(response: dict[str, Any], **fields: Any) -> dict[str, Any]:
            source = str(response["data"].get("source") or "")
            self.data_meta_calls.append((source, fields))
            data = {**response["data"], "meta": fields}
            return {**response, "data": data}

        context = SharedFileReadContext(
            board_api=self.board_api,
            scoped_description=lambda summary: f"{summary} Scope: current board only.",
            read_tool_annotations=read_tool_annotations,
            relay_board_call=relay_board_call,
            with_data_meta=with_data_meta,
        )
        self.registered_names = register_shared_file_reads(self.server, context)

    async def test_registers_exact_shared_file_read_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "list_shared_files": (
                "List shared workshop files from the AutoStop CRM Files module "
                "without returning file bytes. Scope: current board only."
            ),
            "get_shared_file_info": (
                "Return metadata for one shared workshop file from the AutoStop CRM "
                "Files module, including size, name, position, and download path. "
                "Scope: current board only."
            ),
            "download_shared_file": (
                "Fetch one shared workshop file through the AutoStop CRM backend. "
                "Small files can return base64; larger files return metadata and "
                "download path without file bytes. Scope: current board only."
            ),
        }

        self.assertEqual(self.registered_names, SHARED_FILE_READ_TOOL_NAMES)
        self.assertEqual(set(tools), SHARED_FILE_READ_TOOL_NAMES)
        self.assertEqual(
            {name: tools[name].description for name in tools},
            expected_descriptions,
        )
        for tool_name, tool in tools.items():
            self.assertTrue(tool.annotations.readOnlyHint, tool_name)
            self.assertFalse(tool.annotations.destructiveHint, tool_name)
            self.assertTrue(tool.annotations.idempotentHint, tool_name)
            self.assertFalse(tool.annotations.openWorldHint, tool_name)
            self.assertIsNotNone(tool.outputSchema, tool_name)

        list_schema = tools["list_shared_files"].inputSchema
        self.assertEqual(list_schema["properties"], {})
        self.assertEqual(list_schema.get("required", []), [])
        get_schema = tools["get_shared_file_info"].inputSchema
        self.assertEqual(get_schema["required"], ["file_id"])
        download_schema = tools["download_shared_file"].inputSchema
        self.assertEqual(download_schema["required"], ["file_id"])
        self.assertTrue(download_schema["properties"]["include_base64"]["default"])
        self.assertEqual(
            download_schema["properties"]["max_base64_bytes"]["default"],
            2_097_152,
        )

    async def test_handlers_preserve_backend_arguments_defaults_and_meta(self) -> None:
        listed = await self.server._tool_manager.call_tool("list_shared_files", {})
        metadata = await self.server._tool_manager.call_tool(
            "get_shared_file_info",
            {"file_id": "file-1"},
        )
        downloaded = await self.server._tool_manager.call_tool(
            "download_shared_file",
            {"file_id": "file-1"},
        )
        metadata_only = await self.server._tool_manager.call_tool(
            "download_shared_file",
            {
                "file_id": "file-2",
                "include_base64": False,
                "max_base64_bytes": 321,
            },
        )

        self.assertEqual(listed.data["meta"]["response_mode"], "shared_file_list")
        self.assertEqual(
            metadata.data["meta"]["response_mode"],
            "shared_file_metadata",
        )
        self.assertEqual(downloaded.data["meta"]["view_mode"], "base64")
        self.assertEqual(metadata_only.data["meta"]["view_mode"], "metadata")
        self.assertEqual(
            self.board_api.calls,
            [
                ("list_shared_files", {}),
                ("get_shared_file_info", {"file_id": "file-1"}),
                (
                    "download_shared_file",
                    {
                        "file_id": "file-1",
                        "include_base64": True,
                        "max_base64_bytes": 2_097_152,
                    },
                ),
                (
                    "download_shared_file",
                    {
                        "file_id": "file-2",
                        "include_base64": False,
                        "max_base64_bytes": 321,
                    },
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            [
                ("list_shared_files", None),
                ("get_shared_file_info", {"file_id": "file-1"}),
                (
                    "download_shared_file",
                    {
                        "file_id": "file-1",
                        "include_base64": True,
                        "max_base64_bytes": 2_097_152,
                    },
                ),
                (
                    "download_shared_file",
                    {
                        "file_id": "file-2",
                        "include_base64": False,
                        "max_base64_bytes": 321,
                    },
                ),
            ],
        )
        self.assertEqual(
            self.data_meta_calls,
            [
                (
                    "shared-file-list",
                    {
                        "response_mode": "shared_file_list",
                        "view_mode": "metadata",
                    },
                ),
                (
                    "shared-file-metadata",
                    {
                        "response_mode": "shared_file_metadata",
                        "view_mode": "metadata",
                    },
                ),
                (
                    "shared-file-download",
                    {
                        "response_mode": "shared_file_download",
                        "view_mode": "base64",
                    },
                ),
                (
                    "shared-file-download",
                    {
                        "response_mode": "shared_file_download",
                        "view_mode": "metadata",
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
