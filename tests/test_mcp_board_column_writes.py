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

from minimal_kanban.mcp.board_column_writes import (  # noqa: E402
    BOARD_COLUMN_WRITE_TOOL_NAMES,
    BoardColumnWriteContext,
    register_board_column_writes,
)
from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402


class FakeBoardColumnClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_column(
        self,
        label: str | None,
        *,
        name: str | None,
        actor_name: str | None,
    ) -> dict[str, Any]:
        arguments = {"label": label, "name": name, "actor_name": actor_name}
        self.calls.append(("create_column", arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}

    def rename_column(
        self,
        column_id: str,
        label: str,
        *,
        actor_name: str | None,
    ) -> dict[str, Any]:
        arguments = {
            "column_id": column_id,
            "label": label,
            "actor_name": actor_name,
        }
        self.calls.append(("rename_column", arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}

    def delete_column(
        self,
        column_id: str,
        *,
        actor_name: str | None,
    ) -> dict[str, Any]:
        arguments = {"column_id": column_id, "actor_name": actor_name}
        self.calls.append(("delete_column", arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}


class BoardColumnWriteRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeBoardColumnClient()
        self.server = FastMCP(name="board-column-writes-test")
        self.relay_calls: list[str] = []

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

        def relay_board_call(tool_name: str, fetcher: Any) -> JsonEnvelope:
            self.relay_calls.append(tool_name)
            return JsonEnvelope.model_validate(fetcher())

        context = BoardColumnWriteContext(
            board_api=self.board_api,
            scoped_description=(
                lambda summary: f"{summary} Scope: current AutoStop CRM board only."
            ),
            write_tool_annotations=write_tool_annotations,
            relay_board_call=relay_board_call,
        )
        self.registered_names = register_board_column_writes(self.server, context)

    async def test_registers_exact_column_write_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "create_column": (
                "Create a new column on the current AutoStop CRM board. "
                "Scope: current AutoStop CRM board only."
            ),
            "rename_column": (
                "Rename an existing column on the current AutoStop CRM board while "
                "keeping the same column id. Scope: current AutoStop CRM board only."
            ),
            "delete_column": (
                "Delete an empty column from the current AutoStop CRM board. The last "
                "remaining column cannot be removed. Scope: current AutoStop CRM board only."
            ),
        }

        self.assertEqual(self.registered_names, BOARD_COLUMN_WRITE_TOOL_NAMES)
        self.assertEqual(set(tools), BOARD_COLUMN_WRITE_TOOL_NAMES)
        self.assertEqual(
            {name: tools[name].description for name in tools},
            expected_descriptions,
        )
        self.assertFalse(tools["create_column"].annotations.readOnlyHint)
        self.assertFalse(tools["create_column"].annotations.idempotentHint)
        self.assertTrue(tools["rename_column"].annotations.idempotentHint)
        self.assertFalse(tools["rename_column"].annotations.destructiveHint)
        self.assertTrue(tools["delete_column"].annotations.destructiveHint)
        self.assertFalse(tools["delete_column"].annotations.openWorldHint)

        create_schema = tools["create_column"].inputSchema
        self.assertNotIn("required", create_schema)
        self.assertIsNone(create_schema["properties"]["label"]["default"])
        self.assertIsNone(create_schema["properties"]["name"]["default"])
        self.assertEqual(
            tools["rename_column"].inputSchema["required"],
            ["column_id", "label"],
        )
        self.assertEqual(
            tools["delete_column"].inputSchema["required"],
            ["column_id"],
        )

    async def test_handlers_preserve_backend_arguments_and_legacy_name_alias(self) -> None:
        created_by_name = await self.server._tool_manager.call_tool(
            "create_column",
            {"name": "LEGACY NAME", "actor_name": "OPERATOR"},
        )
        created_by_label = await self.server._tool_manager.call_tool(
            "create_column",
            {"label": "CURRENT LABEL"},
        )
        renamed = await self.server._tool_manager.call_tool(
            "rename_column",
            {"column_id": "column-1", "label": "RENAMED", "actor_name": "OPERATOR"},
        )
        deleted = await self.server._tool_manager.call_tool(
            "delete_column",
            {"column_id": "column-2"},
        )

        self.assertEqual(created_by_name.data["arguments"]["name"], "LEGACY NAME")
        self.assertEqual(created_by_label.data["arguments"]["label"], "CURRENT LABEL")
        self.assertEqual(renamed.data["arguments"]["column_id"], "column-1")
        self.assertEqual(deleted.data["arguments"]["column_id"], "column-2")
        self.assertEqual(
            self.board_api.calls,
            [
                (
                    "create_column",
                    {"label": None, "name": "LEGACY NAME", "actor_name": "OPERATOR"},
                ),
                (
                    "create_column",
                    {"label": "CURRENT LABEL", "name": None, "actor_name": None},
                ),
                (
                    "rename_column",
                    {
                        "column_id": "column-1",
                        "label": "RENAMED",
                        "actor_name": "OPERATOR",
                    },
                ),
                (
                    "delete_column",
                    {"column_id": "column-2", "actor_name": None},
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            ["create_column", "create_column", "rename_column", "delete_column"],
        )


if __name__ == "__main__":
    unittest.main()
