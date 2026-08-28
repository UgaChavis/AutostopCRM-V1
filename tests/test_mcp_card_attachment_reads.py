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

from minimal_kanban.mcp.card_attachment_reads import (  # noqa: E402
    CARD_ATTACHMENT_READ_TOOL_NAMES,
    CardAttachmentReadContext,
    register_card_attachment_reads,
)
from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402


class FakeCardAttachmentReadClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_card_attachments(
        self,
        card_id: str,
        *,
        include_removed: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "list_card_attachments",
                {"card_id": card_id, "include_removed": include_removed},
            )
        )
        return {
            "ok": True,
            "data": {"source": "attachment-list", "attachments": []},
            "error": None,
        }

    def get_card_attachment(self, card_id: str, attachment_id: str) -> dict[str, Any]:
        self.calls.append(
            (
                "get_card_attachment",
                {"card_id": card_id, "attachment_id": attachment_id},
            )
        )
        return {
            "ok": True,
            "data": {"source": "attachment-metadata", "id": attachment_id},
            "error": None,
        }

    def read_card_attachment(
        self,
        card_id: str,
        attachment_id: str,
        *,
        mode: str,
        max_chars: int,
        include_base64: bool,
        max_base64_bytes: int,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "read_card_attachment",
                {
                    "card_id": card_id,
                    "attachment_id": attachment_id,
                    "mode": mode,
                    "max_chars": max_chars,
                    "include_base64": include_base64,
                    "max_base64_bytes": max_base64_bytes,
                },
            )
        )
        return {
            "ok": True,
            "data": {"source": "attachment-read", "id": attachment_id},
            "error": None,
        }


class CardAttachmentReadRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeCardAttachmentReadClient()
        self.server = FastMCP(name="card-attachment-reads-test")
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

        context = CardAttachmentReadContext(
            board_api=self.board_api,
            scoped_description=lambda summary: f"{summary} Scope: current board only.",
            read_tool_annotations=read_tool_annotations,
            relay_board_call=relay_board_call,
            with_data_meta=with_data_meta,
        )
        self.registered_names = register_card_attachment_reads(self.server, context)

    async def test_registers_exact_card_attachment_read_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "list_card_attachments": (
                "List attachment metadata for one card from the current AutoStop CRM "
                "board without returning file bytes. Use this before reading any "
                "attached file. Scope: current board only."
            ),
            "get_card_attachment": (
                "Return safe metadata for one card attachment from the current AutoStop "
                "CRM board, including content kind, size, hash, and download path, but "
                "not file bytes. Scope: current board only."
            ),
            "read_card_attachment": (
                "Read one card attachment for an agent. Text, DOCX, XLSX, and simple "
                "PDFs return bounded text; images return dimensions and can include "
                "bounded base64/data_url when include_base64=true or mode=base64. "
                "Scope: current board only."
            ),
        }

        self.assertEqual(self.registered_names, CARD_ATTACHMENT_READ_TOOL_NAMES)
        self.assertEqual(set(tools), CARD_ATTACHMENT_READ_TOOL_NAMES)
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

        list_schema = tools["list_card_attachments"].inputSchema
        self.assertEqual(list_schema["required"], ["card_id"])
        self.assertFalse(list_schema["properties"]["include_removed"]["default"])
        get_schema = tools["get_card_attachment"].inputSchema
        self.assertEqual(get_schema["required"], ["card_id", "attachment_id"])
        read_schema = tools["read_card_attachment"].inputSchema
        self.assertEqual(read_schema["required"], ["card_id", "attachment_id"])
        self.assertEqual(
            read_schema["properties"]["mode"]["enum"],
            ["preview", "text", "base64", "auto"],
        )
        self.assertEqual(read_schema["properties"]["mode"]["default"], "preview")
        self.assertEqual(read_schema["properties"]["max_chars"]["default"], 12_000)
        self.assertFalse(read_schema["properties"]["include_base64"]["default"])
        self.assertEqual(
            read_schema["properties"]["max_base64_bytes"]["default"],
            1_048_576,
        )

    async def test_handlers_preserve_backend_arguments_defaults_and_meta(self) -> None:
        listed = await self.server._tool_manager.call_tool(
            "list_card_attachments",
            {"card_id": "card-1", "include_removed": True},
        )
        metadata = await self.server._tool_manager.call_tool(
            "get_card_attachment",
            {"card_id": "card-1", "attachment_id": "attachment-1"},
        )
        content = await self.server._tool_manager.call_tool(
            "read_card_attachment",
            {
                "card_id": "card-1",
                "attachment_id": "attachment-1",
                "mode": "base64",
                "max_chars": 321,
                "include_base64": True,
                "max_base64_bytes": 654,
            },
        )

        self.assertEqual(listed.data["meta"]["response_mode"], "attachment_list")
        self.assertEqual(metadata.data["meta"]["response_mode"], "attachment_metadata")
        self.assertEqual(content.data["meta"]["response_mode"], "attachment_read")
        self.assertEqual(content.data["meta"]["view_mode"], "base64")
        self.assertEqual(
            self.board_api.calls,
            [
                (
                    "list_card_attachments",
                    {"card_id": "card-1", "include_removed": True},
                ),
                (
                    "get_card_attachment",
                    {"card_id": "card-1", "attachment_id": "attachment-1"},
                ),
                (
                    "read_card_attachment",
                    {
                        "card_id": "card-1",
                        "attachment_id": "attachment-1",
                        "mode": "base64",
                        "max_chars": 321,
                        "include_base64": True,
                        "max_base64_bytes": 654,
                    },
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            [
                (
                    "list_card_attachments",
                    {"card_id": "card-1", "include_removed": True},
                ),
                (
                    "get_card_attachment",
                    {"card_id": "card-1", "attachment_id": "attachment-1"},
                ),
                (
                    "read_card_attachment",
                    {
                        "card_id": "card-1",
                        "attachment_id": "attachment-1",
                        "mode": "base64",
                        "max_chars": 321,
                        "include_base64": True,
                        "max_base64_bytes": 654,
                    },
                ),
            ],
        )
        self.assertEqual(
            self.data_meta_calls,
            [
                (
                    "attachment-list",
                    {
                        "response_mode": "attachment_list",
                        "view_mode": "metadata",
                        "include_removed": True,
                    },
                ),
                (
                    "attachment-metadata",
                    {
                        "response_mode": "attachment_metadata",
                        "view_mode": "metadata",
                    },
                ),
                (
                    "attachment-read",
                    {"response_mode": "attachment_read", "view_mode": "base64"},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
