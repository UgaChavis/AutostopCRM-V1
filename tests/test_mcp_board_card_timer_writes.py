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

from minimal_kanban.mcp.board_card_timer_writes import (  # noqa: E402
    BOARD_CARD_TIMER_WRITE_TOOL_NAMES,
    BoardCardTimerWriteContext,
    register_board_card_timer_writes,
)
from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402


class FakeBoardCardTimerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"ok": True, "data": {"arguments": arguments}, "error": None}

    def set_card_deadline(
        self,
        *,
        card_id: str,
        deadline: dict[str, Any],
        actor_name: str | None,
        response_mode: str,
    ) -> dict[str, Any]:
        return self._record(
            "set_card_deadline",
            {
                "card_id": card_id,
                "deadline": deadline,
                "actor_name": actor_name,
                "response_mode": response_mode,
            },
        )

    def start_card_timer(
        self,
        *,
        card_id: str,
        deadline: dict[str, Any] | None,
        expected_updated_at: str | None,
        actor_name: str | None,
        response_mode: str,
    ) -> dict[str, Any]:
        return self._record(
            "start_card_timer",
            {
                "card_id": card_id,
                "deadline": deadline,
                "expected_updated_at": expected_updated_at,
                "actor_name": actor_name,
                "response_mode": response_mode,
            },
        )

    def stop_card_timer(
        self,
        *,
        card_id: str,
        expected_updated_at: str | None,
        actor_name: str | None,
        response_mode: str,
    ) -> dict[str, Any]:
        return self._record(
            "stop_card_timer",
            {
                "card_id": card_id,
                "expected_updated_at": expected_updated_at,
                "actor_name": actor_name,
                "response_mode": response_mode,
            },
        )

    def set_card_indicator(
        self,
        *,
        card_id: str,
        indicator: str,
        actor_name: str | None,
        response_mode: str,
    ) -> dict[str, Any]:
        return self._record(
            "set_card_indicator",
            {
                "card_id": card_id,
                "indicator": indicator,
                "actor_name": actor_name,
                "response_mode": response_mode,
            },
        )


class BoardCardTimerWriteRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeBoardCardTimerClient()
        self.server = FastMCP(name="board-card-timer-writes-test")
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

        context = BoardCardTimerWriteContext(
            board_api=self.board_api,
            scoped_description=(
                lambda summary: f"{summary} Scope: current AutoStop CRM board only."
            ),
            write_tool_annotations=write_tool_annotations,
            relay_board_call=relay_board_call,
        )
        self.registered_names = register_board_card_timer_writes(self.server, context)

    async def test_registers_exact_card_timer_write_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "set_card_deadline": (
                "Change only the deadline of a card on the current AutoStop CRM board. "
                "The deadline accepts either days/hours/minutes/seconds or "
                "total_seconds. Scope: current AutoStop CRM board only."
            ),
            "start_card_timer": (
                "Start or restart a card timer. Supply a deadline to change the "
                "duration; omit it to reuse the card's saved duration. Scope: current "
                "AutoStop CRM board only."
            ),
            "stop_card_timer": (
                "Stop a card timer without deleting the saved duration. A later start "
                "begins the full saved duration again. Scope: current AutoStop CRM "
                "board only."
            ),
            "set_card_indicator": (
                "Service tool for changing the signal lamp state of a card. Because "
                "the indicator is derived from time, this operation recalculates the "
                "deadline to reach the requested color. Scope: current AutoStop CRM "
                "board only."
            ),
        }

        self.assertEqual(self.registered_names, BOARD_CARD_TIMER_WRITE_TOOL_NAMES)
        self.assertEqual(set(tools), BOARD_CARD_TIMER_WRITE_TOOL_NAMES)
        self.assertEqual(
            {name: tools[name].description for name in tools},
            expected_descriptions,
        )
        for tool_name, tool in tools.items():
            self.assertFalse(tool.annotations.readOnlyHint, tool_name)
            self.assertFalse(tool.annotations.destructiveHint, tool_name)
            self.assertFalse(tool.annotations.idempotentHint, tool_name)
            self.assertFalse(tool.annotations.openWorldHint, tool_name)
            self.assertIsNotNone(tool.outputSchema, tool_name)

        deadline_schema = tools["set_card_deadline"].inputSchema
        self.assertEqual(deadline_schema["required"], ["card_id", "deadline"])
        self.assertNotIn("expected_updated_at", deadline_schema["properties"])
        self.assertEqual(deadline_schema["properties"]["response_mode"]["default"], "full")
        start_schema = tools["start_card_timer"].inputSchema
        self.assertEqual(start_schema["required"], ["card_id"])
        self.assertIsNone(start_schema["properties"]["deadline"]["default"])
        self.assertIsNone(start_schema["properties"]["expected_updated_at"]["default"])
        stop_schema = tools["stop_card_timer"].inputSchema
        self.assertEqual(stop_schema["required"], ["card_id"])
        self.assertNotIn("deadline", stop_schema["properties"])
        indicator_schema = tools["set_card_indicator"].inputSchema
        self.assertEqual(indicator_schema["required"], ["card_id", "indicator"])
        self.assertEqual(
            indicator_schema["properties"]["indicator"]["enum"],
            ["green", "yellow", "red"],
        )
        self.assertNotIn("expected_updated_at", indicator_schema["properties"])

    async def test_handlers_preserve_deadlines_revisions_and_response_modes(self) -> None:
        await self.server._tool_manager.call_tool(
            "set_card_deadline",
            {
                "card_id": "card-1",
                "deadline": {"hours": 2},
                "actor_name": "OPERATOR",
                "response_mode": "compact",
            },
        )
        await self.server._tool_manager.call_tool(
            "start_card_timer",
            {"card_id": "card-2"},
        )
        await self.server._tool_manager.call_tool(
            "start_card_timer",
            {
                "card_id": "card-3",
                "deadline": {"total_seconds": 120},
                "expected_updated_at": "2026-08-27T10:00:00Z",
                "actor_name": "OPERATOR",
                "response_mode": "compact",
            },
        )
        await self.server._tool_manager.call_tool(
            "stop_card_timer",
            {
                "card_id": "card-4",
                "expected_updated_at": "2026-08-27T11:00:00Z",
                "actor_name": "OPERATOR",
            },
        )
        await self.server._tool_manager.call_tool(
            "set_card_indicator",
            {"card_id": "card-5", "indicator": "yellow", "response_mode": "compact"},
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
                    "set_card_deadline",
                    {
                        "card_id": "card-1",
                        "deadline": {**zero_deadline, "hours": 2},
                        "actor_name": "OPERATOR",
                        "response_mode": "compact",
                    },
                ),
                (
                    "start_card_timer",
                    {
                        "card_id": "card-2",
                        "deadline": None,
                        "expected_updated_at": None,
                        "actor_name": None,
                        "response_mode": "full",
                    },
                ),
                (
                    "start_card_timer",
                    {
                        "card_id": "card-3",
                        "deadline": {**zero_deadline, "total_seconds": 120},
                        "expected_updated_at": "2026-08-27T10:00:00Z",
                        "actor_name": "OPERATOR",
                        "response_mode": "compact",
                    },
                ),
                (
                    "stop_card_timer",
                    {
                        "card_id": "card-4",
                        "expected_updated_at": "2026-08-27T11:00:00Z",
                        "actor_name": "OPERATOR",
                        "response_mode": "full",
                    },
                ),
                (
                    "set_card_indicator",
                    {
                        "card_id": "card-5",
                        "indicator": "yellow",
                        "actor_name": None,
                        "response_mode": "compact",
                    },
                ),
            ],
        )
        self.assertEqual(
            self.relay_calls,
            [
                "set_card_deadline",
                "start_card_timer",
                "start_card_timer",
                "stop_card_timer",
                "set_card_indicator",
            ],
        )


if __name__ == "__main__":
    unittest.main()
