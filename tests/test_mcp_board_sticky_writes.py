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

from minimal_kanban.mcp.board_sticky_writes import (  # noqa: E402
    BOARD_STICKY_CREATE_TOOL_NAMES,
    BOARD_STICKY_MUTATION_TOOL_NAMES,
    BOARD_STICKY_WRITE_TOOL_NAMES,
    BoardStickyWriteContext,
    register_board_sticky_create,
    register_board_sticky_mutations,
)
from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402


class FakeBoardStickyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}

    def create_sticky(
        self,
        *,
        text: str,
        x: Any,
        y: Any,
        deadline: dict[str, Any],
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "create_sticky",
            {
                "text": text,
                "x": x,
                "y": y,
                "deadline": deadline,
                "actor_name": actor_name,
            },
        )

    def update_sticky(
        self,
        *,
        sticky_id: str,
        text: str | None,
        deadline: dict[str, Any] | None,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "update_sticky",
            {
                "sticky_id": sticky_id,
                "text": text,
                "deadline": deadline,
                "actor_name": actor_name,
            },
        )

    def move_sticky(
        self,
        *,
        sticky_id: str,
        x: int,
        y: int,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "move_sticky",
            {
                "sticky_id": sticky_id,
                "x": x,
                "y": y,
                "actor_name": actor_name,
            },
        )

    def delete_sticky(
        self,
        *,
        sticky_id: str,
        actor_name: str | None,
    ) -> dict[str, Any]:
        return self._record(
            "delete_sticky",
            {"sticky_id": sticky_id, "actor_name": actor_name},
        )


class BoardStickyWriteRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeBoardStickyClient()
        self.server = FastMCP(name="board-sticky-writes-test")
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

        context = BoardStickyWriteContext(
            board_api=self.board_api,
            scoped_description=(
                lambda summary: f"{summary} Scope: current AutoStop CRM board only."
            ),
            write_tool_annotations=write_tool_annotations,
            relay_board_call=relay_board_call,
        )
        self.create_names = register_board_sticky_create(self.server, context)
        self.mutation_names = register_board_sticky_mutations(self.server, context)

    async def test_registers_exact_sticky_write_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "create_sticky": (
                "Create a sticky note on the current AutoStop CRM board. Sticky notes "
                "belong only to this board instance. The deadline accepts either "
                "days/hours/minutes/seconds or total_seconds. Scope: current AutoStop "
                "CRM board only."
            ),
            "update_sticky": (
                "Update the text or deadline of a sticky note on the current AutoStop "
                "CRM board. The deadline accepts either days/hours/minutes/seconds or "
                "total_seconds. Scope: current AutoStop CRM board only."
            ),
            "move_sticky": (
                "Move a sticky note on the current AutoStop CRM board to a new x/y "
                "position. Scope: current AutoStop CRM board only."
            ),
            "delete_sticky": (
                "Delete a sticky note from the current AutoStop CRM board. Scope: "
                "current AutoStop CRM board only."
            ),
        }

        self.assertEqual(self.create_names, BOARD_STICKY_CREATE_TOOL_NAMES)
        self.assertEqual(self.mutation_names, BOARD_STICKY_MUTATION_TOOL_NAMES)
        self.assertEqual(set(tools), BOARD_STICKY_WRITE_TOOL_NAMES)
        self.assertEqual(
            {name: tools[name].description for name in tools},
            expected_descriptions,
        )
        for tool_name, tool in tools.items():
            self.assertFalse(tool.annotations.readOnlyHint, tool_name)
            self.assertFalse(tool.annotations.idempotentHint, tool_name)
            self.assertFalse(tool.annotations.openWorldHint, tool_name)
            self.assertEqual(
                tool_name == "delete_sticky",
                bool(tool.annotations.destructiveHint),
                tool_name,
            )
            self.assertIsNotNone(tool.outputSchema, tool_name)

        create_schema = tools["create_sticky"].inputSchema
        self.assertEqual(create_schema["required"], ["text", "deadline"])
        self.assertEqual(create_schema["properties"]["x"]["default"], 0)
        self.assertEqual(create_schema["properties"]["y"]["default"], 0)
        self.assertIn("anyOf", create_schema["properties"]["x"])
        update_schema = tools["update_sticky"].inputSchema
        self.assertEqual(update_schema["required"], ["sticky_id"])
        self.assertIsNone(update_schema["properties"]["text"]["default"])
        self.assertIsNone(update_schema["properties"]["deadline"]["default"])
        move_schema = tools["move_sticky"].inputSchema
        self.assertEqual(move_schema["required"], ["sticky_id", "x", "y"])
        self.assertEqual(move_schema["properties"]["x"]["type"], "integer")
        self.assertEqual(move_schema["properties"]["y"]["type"], "integer")
        self.assertEqual(
            tools["delete_sticky"].inputSchema["required"],
            ["sticky_id"],
        )

    async def test_handlers_preserve_deadline_dumps_and_backend_arguments(self) -> None:
        await self.server._tool_manager.call_tool(
            "create_sticky",
            {
                "text": "CHECK PART",
                "deadline": {"total_seconds": 90},
                "x": 12,
                "y": 34,
                "actor_name": "OPERATOR",
            },
        )
        await self.server._tool_manager.call_tool(
            "update_sticky",
            {"sticky_id": "sticky-1", "text": "UPDATED"},
        )
        await self.server._tool_manager.call_tool(
            "update_sticky",
            {
                "sticky_id": "sticky-2",
                "deadline": {"days": 1},
                "actor_name": "OPERATOR",
            },
        )
        await self.server._tool_manager.call_tool(
            "move_sticky",
            {"sticky_id": "sticky-3", "x": -5, "y": 8},
        )
        await self.server._tool_manager.call_tool(
            "delete_sticky",
            {"sticky_id": "sticky-4", "actor_name": "OPERATOR"},
        )

        zero_deadline = {
            "days": 0,
            "hours": 0,
            "minutes": 0,
            "seconds": 0,
            "total_seconds": 0,
        }
        self.assertEqual(
            self.board_api.calls,
            [
                (
                    "create_sticky",
                    {
                        "text": "CHECK PART",
                        "x": 12,
                        "y": 34,
                        "deadline": {**zero_deadline, "total_seconds": 90},
                        "actor_name": "OPERATOR",
                    },
                ),
                (
                    "update_sticky",
                    {
                        "sticky_id": "sticky-1",
                        "text": "UPDATED",
                        "deadline": None,
                        "actor_name": None,
                    },
                ),
                (
                    "update_sticky",
                    {
                        "sticky_id": "sticky-2",
                        "text": None,
                        "deadline": {**zero_deadline, "days": 1},
                        "actor_name": "OPERATOR",
                    },
                ),
                (
                    "move_sticky",
                    {"sticky_id": "sticky-3", "x": -5, "y": 8, "actor_name": None},
                ),
                (
                    "delete_sticky",
                    {"sticky_id": "sticky-4", "actor_name": "OPERATOR"},
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            [
                "create_sticky",
                "update_sticky",
                "update_sticky",
                "move_sticky",
                "delete_sticky",
            ],
        )


if __name__ == "__main__":
    unittest.main()
