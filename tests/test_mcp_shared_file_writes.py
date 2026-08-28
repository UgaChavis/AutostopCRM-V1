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
from minimal_kanban.mcp.shared_file_writes import (  # noqa: E402
    SHARED_FILE_WRITE_TOOL_NAMES,
    SharedFileWriteContext,
    register_shared_file_writes,
)


class FakeSharedFileWriteClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}

    def upload_shared_file(
        self,
        *,
        file_name: str,
        content_base64: str,
        mime_type: str,
        x: int,
        y: int,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "upload_shared_file",
            {
                "file_name": file_name,
                "content_base64": content_base64,
                "mime_type": mime_type,
                "x": x,
                "y": y,
                "actor_name": actor_name,
            },
        )

    def delete_shared_file(
        self,
        file_id: str,
        *,
        expected_updated_at: str | None,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "delete_shared_file",
            {
                "file_id": file_id,
                "expected_updated_at": expected_updated_at,
                "actor_name": actor_name,
            },
        )

    def update_shared_file_position(
        self,
        file_id: str,
        *,
        x: int,
        y: int,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "update_shared_file_position",
            {
                "file_id": file_id,
                "x": x,
                "y": y,
                "actor_name": actor_name,
            },
        )


class SharedFileWriteRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeSharedFileWriteClient()
        self.server = FastMCP(name="shared-file-writes-test")
        self.relay_calls: list[tuple[str, dict[str, Any] | None]] = []

        def write_tool_annotations(
            title: str,
            *,
            destructive: bool = False,
            idempotent: bool = False,
        ) -> ToolAnnotations:
            return ToolAnnotations(
                title=title,
                readOnlyHint=False,
                destructiveHint=destructive,
                idempotentHint=idempotent,
                openWorldHint=False,
            )

        def relay_board_call(
            tool_name: str,
            fetcher: Any,
            *,
            params: dict[str, Any] | None = None,
        ) -> JsonEnvelope:
            self.relay_calls.append((tool_name, params))
            return JsonEnvelope.model_validate(fetcher())

        context = SharedFileWriteContext(
            board_api=self.board_api,
            scoped_description=(
                lambda summary: f"{summary} Scope: current AutoStop CRM board only."
            ),
            write_tool_annotations=write_tool_annotations,
            relay_board_call=relay_board_call,
        )
        self.registered_names = register_shared_file_writes(self.server, context)

    async def test_registers_exact_shared_file_write_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "upload_shared_file": (
                "Upload one file into the AutoStop CRM Files module. Pass file_name "
                "and base64 content; executable script/install extensions are rejected "
                "by the backend. Scope: current AutoStop CRM board only."
            ),
            "delete_shared_file": (
                "Delete one file from the AutoStop CRM Files module. This is a "
                "destructive write action. Scope: current AutoStop CRM board only."
            ),
            "update_shared_file_position": (
                "Update the saved x/y icon position for one file in the AutoStop CRM "
                "Files module. Scope: current AutoStop CRM board only."
            ),
        }

        self.assertEqual(self.registered_names, SHARED_FILE_WRITE_TOOL_NAMES)
        self.assertEqual(set(tools), SHARED_FILE_WRITE_TOOL_NAMES)
        self.assertEqual(
            {name: tools[name].description for name in tools},
            expected_descriptions,
        )
        for tool_name, tool in tools.items():
            self.assertFalse(tool.annotations.readOnlyHint, tool_name)
            self.assertFalse(tool.annotations.openWorldHint, tool_name)
            self.assertEqual(
                tool_name == "delete_shared_file",
                bool(tool.annotations.destructiveHint),
                tool_name,
            )
            self.assertEqual(
                tool_name == "update_shared_file_position",
                bool(tool.annotations.idempotentHint),
                tool_name,
            )
            self.assertIsNotNone(tool.outputSchema, tool_name)

        upload_schema = tools["upload_shared_file"].inputSchema
        self.assertEqual(upload_schema["required"], ["file_name", "content_base64"])
        self.assertEqual(
            upload_schema["properties"]["mime_type"]["default"],
            "application/octet-stream",
        )
        self.assertEqual(upload_schema["properties"]["x"]["default"], 0)
        self.assertEqual(upload_schema["properties"]["y"]["default"], 0)
        self.assertIn("anyOf", upload_schema["properties"]["x"])
        delete_schema = tools["delete_shared_file"].inputSchema
        self.assertEqual(delete_schema["required"], ["file_id"])
        self.assertIsNone(delete_schema["properties"]["expected_updated_at"]["default"])
        position_schema = tools["update_shared_file_position"].inputSchema
        self.assertEqual(position_schema["required"], ["file_id", "x", "y"])
        self.assertEqual(position_schema["properties"]["x"]["type"], "integer")
        self.assertEqual(position_schema["properties"]["y"]["type"], "integer")

    async def test_handlers_preserve_backend_arguments_and_safe_relay_params(
        self,
    ) -> None:
        default_upload = await self.server._tool_manager.call_tool(
            "upload_shared_file",
            {"file_name": "default.bin", "content_base64": "REDACTED-DEFAULT"},
        )
        explicit_upload = await self.server._tool_manager.call_tool(
            "upload_shared_file",
            {
                "file_name": "manual.pdf",
                "content_base64": "REDACTED-EXPLICIT",
                "mime_type": "application/pdf",
                "x": 12,
                "y": -34,
                "actor_name": "OPERATOR",
            },
        )
        deleted = await self.server._tool_manager.call_tool(
            "delete_shared_file",
            {
                "file_id": "file-delete",
                "expected_updated_at": "2026-08-28T01:02:03Z",
                "actor_name": "OPERATOR",
            },
        )
        moved = await self.server._tool_manager.call_tool(
            "update_shared_file_position",
            {"file_id": "file-move", "x": -5, "y": 8, "actor_name": "OPERATOR"},
        )

        self.assertEqual(default_upload.data["arguments"]["x"], 0)
        self.assertEqual(explicit_upload.data["arguments"]["mime_type"], "application/pdf")
        self.assertEqual(
            deleted.data["arguments"]["expected_updated_at"],
            "2026-08-28T01:02:03Z",
        )
        self.assertEqual(moved.data["arguments"]["file_id"], "file-move")
        self.assertEqual(
            self.board_api.calls,
            [
                (
                    "upload_shared_file",
                    {
                        "file_name": "default.bin",
                        "content_base64": "REDACTED-DEFAULT",
                        "mime_type": "application/octet-stream",
                        "x": 0,
                        "y": 0,
                        "actor_name": None,
                    },
                ),
                (
                    "upload_shared_file",
                    {
                        "file_name": "manual.pdf",
                        "content_base64": "REDACTED-EXPLICIT",
                        "mime_type": "application/pdf",
                        "x": 12,
                        "y": -34,
                        "actor_name": "OPERATOR",
                    },
                ),
                (
                    "delete_shared_file",
                    {
                        "file_id": "file-delete",
                        "expected_updated_at": "2026-08-28T01:02:03Z",
                        "actor_name": "OPERATOR",
                    },
                ),
                (
                    "update_shared_file_position",
                    {
                        "file_id": "file-move",
                        "x": -5,
                        "y": 8,
                        "actor_name": "OPERATOR",
                    },
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            [
                (
                    "upload_shared_file",
                    {
                        "file_name": "default.bin",
                        "mime_type": "application/octet-stream",
                        "x": 0,
                        "y": 0,
                    },
                ),
                (
                    "upload_shared_file",
                    {
                        "file_name": "manual.pdf",
                        "mime_type": "application/pdf",
                        "x": 12,
                        "y": -34,
                    },
                ),
                ("delete_shared_file", {"file_id": "file-delete"}),
                (
                    "update_shared_file_position",
                    {"file_id": "file-move", "x": -5, "y": 8},
                ),
            ],
        )
        self.assertNotIn("REDACTED", repr(self.relay_calls))
        self.assertNotIn("OPERATOR", repr(self.relay_calls))


if __name__ == "__main__":
    unittest.main()
