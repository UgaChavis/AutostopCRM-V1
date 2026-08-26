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

from minimal_kanban.mcp.board_reads import (  # noqa: E402
    BOARD_READ_TOOL_NAMES,
    BoardReadContext,
    register_board_reads,
)
from minimal_kanban.mcp.payloads import JsonEnvelope  # noqa: E402


class FakeBoardReadClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_columns(self) -> dict[str, Any]:
        self.calls.append(("list_columns", {}))
        return {
            "ok": True,
            "data": {"source": "columns", "columns": [{"id": "inbox"}]},
            "error": None,
        }

    def get_cards(self, *, include_archived: bool, compact: bool) -> dict[str, Any]:
        self.calls.append(
            (
                "get_cards",
                {"include_archived": include_archived, "compact": compact},
            )
        )
        return {
            "ok": True,
            "data": {"source": "cards", "cards": [{"id": "card-1"}]},
            "error": None,
        }

    def get_card(self, card_id: str) -> dict[str, Any]:
        self.calls.append(("get_card", {"card_id": card_id}))
        return {
            "ok": True,
            "data": {"source": "card", "card": {"id": card_id}},
            "error": None,
        }

    def get_board_snapshot(
        self,
        *,
        archive_limit: int,
        compact: bool,
        include_archive: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "get_board_snapshot",
                {
                    "archive_limit": archive_limit,
                    "compact": compact,
                    "include_archive": include_archive,
                },
            )
        )
        return {
            "ok": True,
            "data": {"source": "snapshot", "cards": [], "columns": []},
            "error": None,
        }


class BoardReadRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.board_api = FakeBoardReadClient()
        self.server = FastMCP(name="board-reads-test")
        self.relay_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.cards_meta_calls: list[dict[str, Any]] = []
        self.data_meta_calls: list[tuple[str, dict[str, Any]]] = []
        self.limit_calls: list[dict[str, Any]] = []

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

        def with_cards_list_meta(
            response: dict[str, Any],
            *,
            include_archived: bool,
            compact: bool,
            response_mode: str,
        ) -> dict[str, Any]:
            fields = {
                "include_archived": include_archived,
                "compact": compact,
                "response_mode": response_mode,
                "view_mode": "compact" if compact else "full",
            }
            self.cards_meta_calls.append(fields)
            data = {**response["data"], "meta": fields}
            return {**response, "data": data}

        def normalize_limit(
            value: Any,
            *,
            default: int,
            minimum: int = 1,
            maximum: int | None = None,
        ) -> int:
            self.limit_calls.append(
                {
                    "value": value,
                    "default": default,
                    "minimum": minimum,
                    "maximum": maximum,
                }
            )
            normalized = int(value)
            normalized = max(normalized, minimum)
            return min(normalized, maximum) if maximum is not None else normalized

        def with_data_meta(
            response: dict[str, Any],
            **fields: Any,
        ) -> dict[str, Any]:
            source = str(response["data"].get("source") or "")
            self.data_meta_calls.append((source, fields))
            data = {**response["data"], "meta": fields}
            return {**response, "data": data}

        context = BoardReadContext(
            board_api=self.board_api,
            scoped_description=lambda summary: f"{summary} Scope: current board only.",
            read_tool_annotations=read_tool_annotations,
            relay_board_call=relay_board_call,
            with_cards_list_meta=with_cards_list_meta,
            normalize_limit=normalize_limit,
            with_data_meta=with_data_meta,
        )
        self.registered_names = register_board_reads(self.server, context)

    async def test_registers_exact_core_board_read_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "list_columns": (
                "List all columns of the current AutoStop CRM board. Scope: current board only."
            ),
            "get_cards": (
                "Return cards from the current AutoStop CRM board. Archived cards are "
                "excluded by default. Use compact=true for board scans with lighter "
                "payloads; set compact=false when full vehicle_profile, repair_order, "
                "attachments, and ai_autofill_log are needed. Scope: current board only."
            ),
            "get_card": (
                "Return one card by card_id from the current AutoStop CRM board, "
                "including the full vehicle_profile and the compact "
                "vehicle_profile_compact used by the 1.1 card layout. Scope: current "
                "board only."
            ),
            "get_board_snapshot": (
                "Return a structured snapshot of the current AutoStop CRM board: "
                "columns, active cards, archived tail, stickies, and settings. Cards in "
                "the snapshot include vehicle_profile_compact for the 1.1 vehicle card "
                "view. Use compact=true for lighter GPT scans and include_archive=false "
                "when the archived tail is not needed. Scope: current board only."
            ),
        }

        self.assertEqual(self.registered_names, BOARD_READ_TOOL_NAMES)
        self.assertEqual(set(tools), BOARD_READ_TOOL_NAMES)
        for tool_name in BOARD_READ_TOOL_NAMES:
            with self.subTest(tool=tool_name):
                self.assertEqual(tools[tool_name].description, expected_descriptions[tool_name])
                self.assertTrue(tools[tool_name].annotations.readOnlyHint)
                self.assertFalse(tools[tool_name].annotations.destructiveHint)
                self.assertFalse(tools[tool_name].annotations.openWorldHint)

        self.assertTrue(tools["get_cards"].inputSchema["properties"]["compact"]["default"])
        self.assertEqual(
            tools["get_board_snapshot"].inputSchema["properties"]["archive_limit"]["default"],
            10,
        )

    async def test_handlers_preserve_backend_arguments_defaults_limits_and_meta(self) -> None:
        columns = await self.server._tool_manager.call_tool("list_columns", {})
        cards = await self.server._tool_manager.call_tool("get_cards", {})
        card = await self.server._tool_manager.call_tool("get_card", {"card_id": "card-1"})
        snapshot = await self.server._tool_manager.call_tool(
            "get_board_snapshot",
            {"archive_limit": 999, "compact": True},
        )
        snapshot_without_archive = await self.server._tool_manager.call_tool(
            "get_board_snapshot",
            {"archive_limit": 999, "include_archive": False},
        )

        self.assertEqual(columns.data["source"], "columns")
        self.assertEqual(cards.data["meta"]["response_mode"], "list")
        self.assertEqual(cards.data["meta"]["view_mode"], "compact")
        self.assertEqual(card.data["card"]["id"], "card-1")
        self.assertEqual(snapshot.data["meta"]["archive_limit"], 50)
        self.assertEqual(snapshot_without_archive.data["meta"]["archive_limit"], 0)
        self.assertEqual(
            self.board_api.calls,
            [
                ("list_columns", {}),
                ("get_cards", {"include_archived": False, "compact": True}),
                ("get_card", {"card_id": "card-1"}),
                (
                    "get_board_snapshot",
                    {"archive_limit": 50, "compact": True, "include_archive": True},
                ),
                (
                    "get_board_snapshot",
                    {"archive_limit": 0, "compact": False, "include_archive": False},
                ),
            ],
        )
        self.assertEqual(
            self.limit_calls,
            [{"value": 999, "default": 30, "minimum": 1, "maximum": 50}],
        )
        self.assertEqual(
            self.cards_meta_calls,
            [
                {
                    "include_archived": False,
                    "compact": True,
                    "response_mode": "list",
                    "view_mode": "compact",
                }
            ],
        )
        self.assertEqual(
            self.data_meta_calls,
            [
                (
                    "snapshot",
                    {
                        "response_mode": "snapshot",
                        "view_mode": "compact",
                        "archive_limit": 50,
                        "include_archive": True,
                        "compact": True,
                    },
                ),
                (
                    "snapshot",
                    {
                        "response_mode": "snapshot",
                        "view_mode": "full",
                        "archive_limit": 0,
                        "include_archive": False,
                        "compact": False,
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
