from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import logging
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import asynccontextmanager
from contextlib import suppress
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
import anyio
from mcp import ClientSession
from mcp.client.streamable_http import StreamableHTTPTransport


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError
from minimal_kanban.mcp.runtime import McpServerRuntime
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def open_mcp_session(url: str, *, http_client: httpx.AsyncClient | None = None):
    client = http_client or httpx.AsyncClient()
    client_provided = http_client is not None
    transport = StreamableHTTPTransport(url)
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    try:
        async with read_stream_writer, read_stream, write_stream, write_stream_reader:
            async with anyio.create_task_group() as tg:
                if not client_provided:
                    client_cm = client
                else:
                    client_cm = _yield_client(client)
                async with client_cm:
                    def start_get_stream() -> None:
                        tg.start_soon(transport.handle_get_stream, client, read_stream_writer)

                    tg.start_soon(
                        transport.post_writer,
                        client,
                        write_stream_reader,
                        read_stream_writer,
                        write_stream,
                        start_get_stream,
                        tg,
                    )
                    try:
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            yield session
                    finally:
                        if transport.session_id:
                            with suppress(Exception):
                                await transport.terminate_session(client)
                        tg.cancel_scope.cancel()
    finally:
        if not client_provided:
            with suppress(Exception):
                await client.aclose()


@asynccontextmanager
async def _yield_client(client: httpx.AsyncClient):
    yield client


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        self.oauth_state_file = Path(self.temp_dir.name) / "mcp-oauth-state.json"
        self.logger = logging.getLogger(f"test.mcp.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)
        self.api_port = reserve_port()
        self.mcp_port = reserve_port()
        self.api_server = ApiServer(
            self.service,
            self.logger,
            start_port=self.api_port,
            fallback_limit=1,
            bearer_token="api-secret",
        )
        self.api_server.start()
        board_api = BoardApiClient(self.api_server.base_url, bearer_token="api-secret", logger=self.logger)
        mcp_server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=self.mcp_port,
            path="/bridge",
            bearer_token="mcp-secret",
            public_endpoint_url="https://agent.example/bridge",
            oauth_state_file=self.oauth_state_file,
        )
        self.runtime = McpServerRuntime(mcp_server, self.logger)
        self.runtime.start()

    async def asyncTearDown(self) -> None:
        self.runtime.stop()
        self.api_server.stop()
        await asyncio.sleep(0.1)
        gc.collect()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_mcp_tools_reach_backend(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                self.assertTrue(
                    {
                        "ping_connector",
                        "bootstrap_context",
                        "get_connector_identity",
                        "get_runtime_status",
                        "get_board_context",
                        "list_columns",
                        "create_column",
                        "rename_column",
                        "delete_column",
                        "create_sticky",
                        "get_cards",
                        "get_card",
                        "get_card_context",
                        "get_board_snapshot",
                        "review_board",
                        "list_cashboxes",
                        "get_cashbox",
                        "autofill_vehicle_data",
                        "autofill_repair_order",
                        "update_board_settings",
                        "get_gpt_wall",
                        "get_board_content",
                        "get_board_events",
                        "get_card_log",
                        "list_archived_cards",
                        "list_repair_orders",
                        "get_repair_order",
                        "get_repair_order_text",
                        "search_cards",
                        "create_card",
                        "create_cashbox",
                        "delete_cashbox",
                        "create_cash_transaction",
                        "update_card",
                        "update_repair_order",
                        "set_repair_order_status",
                        "replace_repair_order_works",
                        "replace_repair_order_materials",
                        "update_sticky",
                        "move_card",
                        "bulk_move_cards",
                        "move_sticky",
                        "archive_card",
                        "restore_card",
                        "delete_sticky",
                        "set_card_indicator",
                        "set_card_deadline",
                        "list_overdue_cards",
                    }.issubset(tool_names)
                )
                tool_map = {tool.name: tool for tool in tools.tools}
                self.assertTrue(tool_map["ping_connector"].annotations.readOnlyHint)
                self.assertFalse(tool_map["ping_connector"].annotations.destructiveHint)
                self.assertFalse(tool_map["get_runtime_status"].annotations.openWorldHint)
                self.assertTrue(tool_map["get_runtime_status"].annotations.readOnlyHint)
                self.assertFalse(tool_map["create_card"].annotations.readOnlyHint)
                self.assertTrue(tool_map["delete_sticky"].annotations.destructiveHint)
                self.assertIn("vehicle_profile_compact", tool_map["get_card"].description)
                self.assertIn("card body content first", tool_map["autofill_vehicle_data"].description)

                ping = await session.call_tool("ping_connector", {})
                self.assertFalse(ping.isError)
                self.assertTrue(ping.structuredContent["ok"])
                self.assertEqual(ping.structuredContent["data"]["message"], "pong")
                self.assertIn("[CONNECTOR PING]", ping.structuredContent["data"]["text"])

                bootstrap = await session.call_tool("bootstrap_context", {"include_archived": True, "event_limit": 20})
                self.assertFalse(bootstrap.isError)
                self.assertTrue(bootstrap.structuredContent["ok"])
                self.assertEqual(
                    bootstrap.structuredContent["data"]["identity"]["board_scope"],
                    "single_local_board_instance",
                )
                self.assertIn("recommended_write_flow", bootstrap.structuredContent["data"])
                self.assertIn("[BOOTSTRAP CONTEXT]", bootstrap.structuredContent["data"]["text"])
                self.assertIn("gpt_wall_preview", bootstrap.structuredContent["data"])
                self.assertNotIn("gpt_wall", bootstrap.structuredContent["data"])

                identity = await session.call_tool("get_connector_identity", {})
                self.assertFalse(identity.isError)
                self.assertTrue(identity.structuredContent["ok"])
                self.assertIn("resource_url", identity.structuredContent["data"]["identity"])
                self.assertIn("connector_name", identity.structuredContent["data"]["identity"])
                self.assertEqual(
                    identity.structuredContent["data"]["identity"]["board_scope"],
                    "single_local_board_instance",
                )

                board_context = await session.call_tool("get_board_context", {})
                self.assertFalse(board_context.isError)
                self.assertTrue(board_context.structuredContent["ok"])
                self.assertEqual(
                    board_context.structuredContent["data"]["context"]["board_name"],
                    "Current AutoStop CRM Board",
                )
                self.assertIn("[BOARD CONTEXT]", board_context.structuredContent["data"]["text"])

                review = await session.call_tool("review_board", {})
                self.assertFalse(review.isError)
                self.assertTrue(review.structuredContent["ok"])
                self.assertIn("summary", review.structuredContent["data"])
                self.assertIn("by_column", review.structuredContent["data"])
                self.assertIn("alerts", review.structuredContent["data"])
                self.assertIn("priority_cards", review.structuredContent["data"])
                self.assertIn("recent_events", review.structuredContent["data"])
                self.assertIn("[BOARD REVIEW]", review.structuredContent["data"]["text"])

                created_cashbox = await session.call_tool("create_cashbox", {"name": "Наличный", "actor_name": "ОПЕРАТОР"})
                self.assertFalse(created_cashbox.isError)
                self.assertTrue(created_cashbox.structuredContent["ok"])
                cashbox = created_cashbox.structuredContent["data"]["cashbox"]

                created_cash_transaction = await session.call_tool(
                    "create_cash_transaction",
                    {
                        "cashbox_id": cashbox["id"],
                        "direction": "income",
                        "amount": "1000",
                        "note": "Предоплата",
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(created_cash_transaction.isError)
                self.assertTrue(created_cash_transaction.structuredContent["ok"])
                self.assertEqual(created_cash_transaction.structuredContent["data"]["transaction"]["amount_minor"], 100000)

                cashboxes = await session.call_tool("list_cashboxes", {"limit": 20})
                self.assertFalse(cashboxes.isError)
                self.assertTrue(cashboxes.structuredContent["ok"])
                self.assertTrue(any(item["id"] == cashbox["id"] for item in cashboxes.structuredContent["data"]["cashboxes"]))

                cashbox_details = await session.call_tool("get_cashbox", {"cashbox_id": cashbox["short_id"], "transaction_limit": 20})
                self.assertFalse(cashbox_details.isError)
                self.assertTrue(cashbox_details.structuredContent["ok"])
                self.assertEqual(cashbox_details.structuredContent["data"]["cashbox"]["statistics"]["transactions_total"], 1)

                runtime_status = await session.call_tool("get_runtime_status", {})
                self.assertFalse(runtime_status.isError)
                self.assertTrue(runtime_status.structuredContent["ok"])
                self.assertEqual(
                    runtime_status.structuredContent["data"]["runtime_status"]["connector_identity"]["board_scope"],
                    "single_local_board_instance",
                )
                self.assertEqual(
                    runtime_status.structuredContent["data"]["runtime_status"]["api_health"]["status"],
                    "ok",
                )
                self.assertIn("[RUNTIME STATUS]", runtime_status.structuredContent["data"]["text"])

                created_column = await session.call_tool("create_column", {"label": "ChatGPT", "actor_name": "РћРџР•Р РђРўРћР "})
                self.assertFalse(created_column.isError)
                self.assertTrue(created_column.structuredContent["ok"])
                column_id = created_column.structuredContent["data"]["column"]["id"]

                renamed_column = await session.call_tool(
                    "rename_column",
                    {"column_id": column_id, "label": "ChatGPT Renamed", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertFalse(renamed_column.isError)
                self.assertTrue(renamed_column.structuredContent["ok"])
                self.assertEqual(renamed_column.structuredContent["data"]["column"]["id"], column_id)
                self.assertEqual(renamed_column.structuredContent["data"]["column"]["label"], "ChatGPT Renamed")

                deleted_column = await session.call_tool(
                    "delete_column",
                    {"column_id": column_id, "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertFalse(deleted_column.isError)
                self.assertTrue(deleted_column.structuredContent["ok"])
                self.assertEqual(deleted_column.structuredContent["data"]["deleted_column"]["id"], column_id)

                created_column = await session.call_tool("create_column", {"label": "ChatGPT", "actor_name": "РћРџР•Р РђРўРћР "})
                self.assertFalse(created_column.isError)
                self.assertTrue(created_column.structuredContent["ok"])
                column_id = created_column.structuredContent["data"]["column"]["id"]

                created_card = await session.call_tool(
                        "create_card",
                        {
                            "vehicle": "KIA RIO",
                            "title": "Р§РµСЂРµР· MCP",
                            "description": "РўРµСЃС‚ РёРЅС‚РµРіСЂР°С†РёРё",
                            "column": column_id,
                            "vehicle_profile": {
                                "make_display": "Kia",
                                "model_display": "Rio",
                                "production_year": 2018,
                                "engine_code": "G4FC",
                            },
                            "tags": [{"label": "РЎР РћР§РќРћ", "color": "red"}, {"label": "GPT", "color": "green"}],
                            "deadline": {"hours": 1},
                            "actor_name": "РћРџР•Р РђРўРћР ",
                        },
                    )
                self.assertFalse(created_card.isError)
                card = created_card.structuredContent["data"]["card"]
                card_id = card["id"]
                card_short_id = card["short_id"]
                self.assertEqual(card["column"], column_id)
                self.assertEqual(card["vehicle"], "KIA RIO")
                self.assertEqual(card["column_label"], "ChatGPT")
                self.assertEqual(card["vehicle_profile"]["engine_code"], "G4FC")
                self.assertEqual(card["vehicle_profile_compact"]["display_name"], "Kia Rio 2018")
                self.assertTrue(card["is_unread"])
                self.assertEqual(card["tag_items"][0]["color"], "red")
                self.assertEqual(card["tag_items"][1]["color"], "green")

                created_without_deadline = await session.call_tool(
                    "create_card",
                    {
                        "title": "Р‘РµР· deadline",
                        "description": "MCP should default deadline",
                        "column": column_id,
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertFalse(created_without_deadline.isError)
                self.assertTrue(created_without_deadline.structuredContent["ok"])
                self.assertGreater(
                    created_without_deadline.structuredContent["data"]["card"]["remaining_seconds"],
                    0,
                )

                created_with_zero_deadline = await session.call_tool(
                    "create_card",
                    {
                        "title": "РќСѓР»РµРІРѕР№ deadline",
                        "deadline": {"days": 0, "hours": 0, "minutes": 0, "seconds": 0},
                        "column": column_id,
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertFalse(created_with_zero_deadline.isError)
                self.assertTrue(created_with_zero_deadline.structuredContent["ok"])
                self.assertGreater(
                    created_with_zero_deadline.structuredContent["data"]["card"]["remaining_seconds"],
                    0,
                )

                created_sticky = await session.call_tool(
                    "create_sticky",
                    {
                        "text": "Call client after lunch",
                        "x": 140,
                        "y": 100,
                        "deadline": {"hours": 4},
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertFalse(created_sticky.isError)
                sticky = created_sticky.structuredContent["data"]["sticky"]
                sticky_id = sticky["id"]
                self.assertTrue(sticky["short_id"].startswith("S-"))

                updated = await session.call_tool(
                    "update_card",
                    {
                        "card_id": card_id,
                        "vehicle": "KIA RIO X",
                        "description": "РћР±РЅРѕРІР»РµРЅРѕ РїРѕ MCP",
                        "vehicle_profile": {
                            "engine_code": "G4FG",
                            "gearbox_model": "A6GF1",
                            "manual_fields": ["engine_code"],
                        },
                        "tags": [{"label": "РЎР РћР§РќРћ", "color": "yellow"}, {"label": "РџР РћР’Р•Р Р•РќРћ", "color": "green"}],
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertTrue(updated.structuredContent["ok"])
                self.assertEqual(updated.structuredContent["data"]["card"]["description"], "РћР±РЅРѕРІР»РµРЅРѕ РїРѕ MCP")
                self.assertEqual(updated.structuredContent["data"]["card"]["vehicle"], "KIA RIO X")
                self.assertEqual(updated.structuredContent["data"]["card"]["vehicle_profile"]["engine_code"], "G4FG")
                self.assertEqual(updated.structuredContent["data"]["card"]["tag_items"][0]["color"], "yellow")

                autofilled_repair_order = await session.call_tool(
                    "autofill_repair_order",
                    {"card_id": card_id, "overwrite": False, "actor_name": "ОПЕРАТОР"},
                )
                self.assertTrue(autofilled_repair_order.structuredContent["ok"])
                self.assertEqual(autofilled_repair_order.structuredContent["data"]["repair_order"]["number"], "1")

                repair_order = await session.call_tool(
                    "update_repair_order",
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "client": "Иван Иванов",
                            "phone": "+7 900 123-45-67",
                            "client_information": "Согласовать дальнейшую диагностику",
                            "license_plate": "В003НК124",
                            "tags": [
                                {"label": "Срочно", "color": "yellow"},
                                {"label": "DSG", "color": "green"},
                            ],
                        },
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertTrue(repair_order.structuredContent["ok"])
                self.assertEqual(repair_order.structuredContent["data"]["repair_order"]["client"], "Иван Иванов")
                self.assertEqual(
                    repair_order.structuredContent["data"]["repair_order"]["tags"],
                    [
                        {"label": "СРОЧНО", "color": "yellow"},
                        {"label": "DSG", "color": "green"},
                    ],
                )

                works = await session.call_tool(
                    "replace_repair_order_works",
                    {
                        "card_id": card_id,
                        "rows": [
                            {"name": "Диагностика", "quantity": "1", "price": "2000", "total": ""},
                            {"name": "Снятие ошибок", "quantity": "1", "price": "500", "total": ""},
                        ],
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertTrue(works.structuredContent["ok"])
                self.assertEqual(works.structuredContent["data"]["repair_order"]["works_total"], "2500")

                materials = await session.call_tool(
                    "replace_repair_order_materials",
                    {
                        "card_id": card_id,
                        "rows": [
                            {"name": "Очиститель контактов", "quantity": "2", "price": "300", "total": ""},
                        ],
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertTrue(materials.structuredContent["ok"])
                self.assertEqual(materials.structuredContent["data"]["repair_order"]["grand_total"], "3100")

                repair_order_read = await session.call_tool("get_repair_order", {"card_id": card_id})
                self.assertTrue(repair_order_read.structuredContent["ok"])
                self.assertEqual(repair_order_read.structuredContent["data"]["repair_order"]["license_plate"], "В003НК124")

                card_context = await session.call_tool(
                    "get_card_context",
                    {"card_id": card_id, "event_limit": 15, "include_repair_order_text": True},
                )
                self.assertTrue(card_context.structuredContent["ok"])
                self.assertEqual(card_context.structuredContent["data"]["card"]["id"], card_id)
                self.assertTrue(card_context.structuredContent["data"]["meta"]["has_repair_order"])
                self.assertIn("ЗАКАЗ-НАРЯД", card_context.structuredContent["data"]["repair_order_text"]["text"])

                repair_order_text = await session.call_tool("get_repair_order_text", {"card_id": card_id})
                self.assertTrue(repair_order_text.structuredContent["ok"])
                self.assertIn("Итого к оплате: 3100", repair_order_text.structuredContent["data"]["text"])

                repair_orders = await session.call_tool(
                    "list_repair_orders",
                    {"limit": 10, "status": "open", "query": "срочно иван dsg", "sort_by": "number", "sort_dir": "asc"},
                )
                self.assertTrue(repair_orders.structuredContent["ok"])
                self.assertTrue(any(item["card_id"] == card_id for item in repair_orders.structuredContent["data"]["repair_orders"]))

                closed_order = await session.call_tool(
                    "set_repair_order_status",
                    {"card_id": card_id, "status": "closed", "actor_name": "ОПЕРАТОР"},
                )
                self.assertTrue(closed_order.structuredContent["ok"])
                self.assertEqual(closed_order.structuredContent["data"]["repair_order"]["status"], "closed")

                archived_repair_orders = await session.call_tool(
                    "list_repair_orders",
                    {"limit": 10, "status": "closed"},
                )
                self.assertTrue(archived_repair_orders.structuredContent["ok"])
                self.assertTrue(
                    any(item["card_id"] == card_id for item in archived_repair_orders.structuredContent["data"]["repair_orders"])
                )

                with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None):
                    autofilled = await session.call_tool(
                        "autofill_vehicle_data",
                        {
                            "raw_text": "Suzuki Swift 2014 VIN JSAZC72S001234567",
                            "vehicle_profile": {
                                "engine_code": "CUSTOM",
                                "manual_fields": ["engine_code"],
                            },
                        },
                    )
                self.assertTrue(autofilled.structuredContent["ok"])
                self.assertEqual(autofilled.structuredContent["data"]["vehicle_profile"]["make_display"], "Suzuki")
                self.assertEqual(autofilled.structuredContent["data"]["vehicle_profile"]["engine_code"], "CUSTOM")

                mazda_autofilled = await session.call_tool(
                    "autofill_vehicle_data",
                    {
                        "raw_text": "Mazda CX 5 2019 VIN JM3KF123456789012",
                    },
                )
                self.assertTrue(mazda_autofilled.structuredContent["ok"])
                self.assertEqual(mazda_autofilled.structuredContent["data"]["vehicle_profile"]["model_display"], "CX-5")

                updated_settings = await session.call_tool(
                    "update_board_settings",
                    {"board_scale": 1.25, "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(updated_settings.structuredContent["ok"])
                self.assertEqual(updated_settings.structuredContent["data"]["settings"]["board_scale"], 1.25)

                snapshot = await session.call_tool("get_board_snapshot", {"archive_limit": 5})
                self.assertTrue(snapshot.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in snapshot.structuredContent["data"]["cards"]))
                snapshot_card = next(item for item in snapshot.structuredContent["data"]["cards"] if item["id"] == card_id)
                self.assertIn("vehicle_profile_compact", snapshot_card)
                self.assertTrue(any(item["id"] == sticky_id for item in snapshot.structuredContent["data"]["stickies"]))
                self.assertEqual(snapshot.structuredContent["data"]["settings"]["board_scale"], 1.25)

                wall = await session.call_tool("get_gpt_wall", {"include_archived": True, "event_limit": 50})
                self.assertTrue(wall.structuredContent["ok"])
                self.assertIn(card_short_id, wall.structuredContent["data"]["text"])
                self.assertTrue(any(item["id"] == card_id for item in wall.structuredContent["data"]["cards"]))
                wall_card = next(item for item in wall.structuredContent["data"]["cards"] if item["id"] == card_id)
                self.assertIn("vehicle_profile_compact", wall_card)
                self.assertIn("connector_identity", wall.structuredContent["data"])
                self.assertIn("board_context", wall.structuredContent["data"])
                self.assertIn("sections", wall.structuredContent["data"])
                self.assertIn("board_content", wall.structuredContent["data"]["sections"])
                self.assertIn("event_log", wall.structuredContent["data"]["sections"])
                self.assertIn("resource_url", wall.structuredContent["data"]["text"])
                self.assertIn("Current AutoStop CRM Board", wall.structuredContent["data"]["text"])
                self.assertTrue(any(item["id"] == sticky_id for item in wall.structuredContent["data"]["stickies"]))
                self.assertIn(card_short_id, wall.structuredContent["data"]["sections"]["board_content"]["text"])
                self.assertTrue(any(item["card_id"] == card_id for item in wall.structuredContent["data"]["sections"]["event_log"]["events"]))

                board_content = await session.call_tool("get_board_content", {"include_archived": True})
                self.assertTrue(board_content.structuredContent["ok"])
                self.assertIn(card_short_id, board_content.structuredContent["data"]["text"])
                self.assertTrue(any(item["id"] == card_id for item in board_content.structuredContent["data"]["cards"]))
                self.assertIn("board_context", board_content.structuredContent["data"])
                self.assertIn("connector_identity", board_content.structuredContent["data"])

                board_events = await session.call_tool("get_board_events", {"event_limit": 50})
                self.assertTrue(board_events.structuredContent["ok"])
                self.assertTrue(any(item["card_id"] == card_id for item in board_events.structuredContent["data"]["events"]))
                self.assertIn(card_short_id, board_events.structuredContent["data"]["text"])
                self.assertIn("connector_identity", board_events.structuredContent["data"])

                search = await session.call_tool(
                    "search_cards",
                    {"query": card_short_id, "column": column_id, "limit": 5},
                )
                self.assertTrue(search.structuredContent["ok"])
                self.assertEqual(search.structuredContent["data"]["meta"]["total_matches"], 1)
                self.assertEqual(search.structuredContent["data"]["cards"][0]["id"], card_id)

                moved = await session.call_tool(
                    "move_card",
                    {"card_id": card_id, "column": "done", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(moved.structuredContent["ok"])
                self.assertEqual(moved.structuredContent["data"]["card"]["column"], "done")

                second_card = await session.call_tool(
                    "create_card",
                    {
                        "vehicle": "NISSAN NOTE",
                        "title": "РџР°РєРµС‚РЅС‹Р№ РїРµСЂРµРЅРѕСЃ",
                        "description": "РџСЂРѕРІРµСЂРєР° bulk move",
                        "column": "in_progress",
                        "deadline": {"hours": 1},
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertTrue(second_card.structuredContent["ok"])
                second_card_id = second_card.structuredContent["data"]["card"]["id"]

                bulk_moved = await session.call_tool(
                    "bulk_move_cards",
                    {
                        "card_ids": [card_id, second_card_id, "missing-card"],
                        "column": "inbox",
                        "actor_name": "РћРџР•Р РђРўРћР ",
                    },
                )
                self.assertTrue(bulk_moved.structuredContent["ok"])
                self.assertEqual(bulk_moved.structuredContent["data"]["meta"]["moved"], 2)
                self.assertEqual(bulk_moved.structuredContent["data"]["meta"]["errors"], 1)
                self.assertTrue(any(item["code"] == "not_found" for item in bulk_moved.structuredContent["data"]["errors"]))
                self.assertTrue(
                    all(item["column"] == "inbox" for item in bulk_moved.structuredContent["data"]["moved_cards"])
                )

                blocked_delete = await session.call_tool(
                    "delete_column",
                    {"column_id": "inbox", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertFalse(blocked_delete.isError)
                self.assertFalse(blocked_delete.structuredContent["ok"])
                self.assertEqual(blocked_delete.structuredContent["error"]["code"], "column_not_empty")

                yellow = await session.call_tool(
                    "set_card_indicator",
                    {"card_id": card_id, "indicator": "yellow", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(yellow.structuredContent["ok"])
                self.assertEqual(yellow.structuredContent["data"]["card"]["indicator"], "yellow")

                red = await session.call_tool(
                    "set_card_indicator",
                    {"card_id": card_id, "indicator": "red", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(red.structuredContent["ok"])
                self.assertEqual(red.structuredContent["data"]["card"]["status"], "expired")

                log = await session.call_tool("get_card_log", {"card_id": card_id})
                self.assertTrue(log.structuredContent["ok"])
                self.assertEqual(log.structuredContent["data"]["events"][0]["source"], "mcp")

                overdue = await session.call_tool("list_overdue_cards", {})
                self.assertTrue(overdue.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in overdue.structuredContent["data"]["cards"]))

                archived = await session.call_tool("archive_card", {"card_id": card_id, "actor_name": "РћРџР•Р РђРўРћР "})
                self.assertTrue(archived.structuredContent["ok"])
                self.assertTrue(archived.structuredContent["data"]["card"]["archived"])

                archived_list = await session.call_tool("list_archived_cards", {"limit": 10})
                self.assertTrue(archived_list.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in archived_list.structuredContent["data"]["cards"]))

                restored = await session.call_tool(
                    "restore_card",
                    {"card_id": card_id, "column": "done", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(restored.structuredContent["ok"])
                self.assertFalse(restored.structuredContent["data"]["card"]["archived"])

    async def test_mcp_returns_structured_validation_errors(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                invalid = await session.call_tool("get_card", {"card_id": "missing-card"})
                self.assertFalse(invalid.isError)
                self.assertFalse(invalid.structuredContent["ok"])
                self.assertEqual(invalid.structuredContent["error"]["code"], "not_found")

    async def test_mcp_move_card_supports_before_card_id_reordering(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                created_column = await session.call_tool(
                    "create_column",
                    {"label": "Reorder", "actor_name": "ОПЕРАТОР"},
                )
                self.assertFalse(created_column.isError)
                column_id = created_column.structuredContent["data"]["column"]["id"]

                first = await session.call_tool(
                    "create_card",
                    {
                        "title": "First",
                        "column": column_id,
                        "deadline": {"hours": 1},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                second = await session.call_tool(
                    "create_card",
                    {
                        "title": "Second",
                        "column": column_id,
                        "deadline": {"hours": 1},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                third = await session.call_tool(
                    "create_card",
                    {
                        "title": "Third",
                        "column": column_id,
                        "deadline": {"hours": 1},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(first.isError)
                self.assertFalse(second.isError)
                self.assertFalse(third.isError)

                moved = await session.call_tool(
                    "move_card",
                    {
                        "card_id": third.structuredContent["data"]["card"]["id"],
                        "column": column_id,
                        "before_card_id": second.structuredContent["data"]["card"]["id"],
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(moved.isError)
                self.assertTrue(moved.structuredContent["ok"])
                self.assertEqual(moved.structuredContent["data"]["card"]["position"], 1)

                snapshot = await session.call_tool("get_board_snapshot", {"archive_limit": 5})
                self.assertFalse(snapshot.isError)
                cards = sorted(
                    [
                        item
                        for item in snapshot.structuredContent["data"]["cards"]
                        if item["column"] == column_id
                    ],
                    key=lambda item: item["position"],
                )
                self.assertEqual(
                    [item["id"] for item in cards[:3]],
                    [
                        first.structuredContent["data"]["card"]["id"],
                        third.structuredContent["data"]["card"]["id"],
                        second.structuredContent["data"]["card"]["id"],
                    ],
                )

    async def test_mcp_bootstrap_transport_fallback_keeps_identity_context(self) -> None:
        runtime = None

        class BrokenBoardApi:
            base_url = "http://127.0.0.1:9"

            def health(self) -> dict:
                raise BoardApiTransportError("health down")

            def get_board_context(self) -> dict:
                raise BoardApiTransportError("context down")

            def get_gpt_wall(self, *, include_archived: bool = True, event_limit: int | None = None) -> dict:
                raise BoardApiTransportError("wall down")

        try:
            port = reserve_port()
            mcp_server = create_mcp_server(
                BrokenBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/fallback",
                bearer_token=None,
                public_endpoint_url="https://agent.example/fallback",
                oauth_state_file=self.oauth_state_file,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient() as http_client:
                async with open_mcp_session(runtime.base_url, http_client=http_client) as session:
                    runtime_status = await session.call_tool("get_runtime_status", {})
                    self.assertFalse(runtime_status.isError)
                    self.assertTrue(runtime_status.structuredContent["ok"])
                    payload = runtime_status.structuredContent["data"]["runtime_status"]
                    self.assertEqual(payload["api_health_error"]["code"], "board_api_unreachable")
                    self.assertEqual(payload["board_context_error"]["code"], "board_context_unreachable")

                    bootstrap = await session.call_tool("bootstrap_context", {"include_archived": True, "event_limit": 5})
                    self.assertFalse(bootstrap.isError)
                    self.assertFalse(bootstrap.structuredContent["ok"])
                    self.assertEqual(bootstrap.structuredContent["error"]["code"], "gpt_wall_unreachable")
                    self.assertEqual(
                        bootstrap.structuredContent["data"]["identity"]["board_scope"],
                        "single_local_board_instance",
                    )
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_runtime_starts_without_auth_and_endpoint_exists(self) -> None:
        runtime = None
        try:
            port = reserve_port()
            board_api = BoardApiClient(self.api_server.base_url, bearer_token="api-secret", logger=self.logger)
            mcp_server = create_mcp_server(
                board_api,
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/mcp",
                bearer_token=None,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient() as client:
                response = await client.get(runtime.base_url, follow_redirects=False, timeout=1.0)
            self.assertIn(response.status_code, {200, 204, 400, 405, 406})

            async with open_mcp_session(runtime.base_url) as session:
                tools = await session.list_tools()
                self.assertTrue(any(tool.name == "list_columns" for tool in tools.tools))
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_runtime_falls_back_when_uvicorn_logging_setup_is_broken(self) -> None:
        runtime = None
        try:
            port = reserve_port()
            board_api = BoardApiClient(self.api_server.base_url, bearer_token="api-secret", logger=self.logger)
            mcp_server = create_mcp_server(
                board_api,
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/debug",
                bearer_token=None,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            with patch.object(runtime, "_share_app_handlers_with_uvicorn", side_effect=RuntimeError("broken logger setup")):
                runtime.start()

            self.assertEqual(runtime.logging_mode, "basic_stream_fallback")
            async with httpx.AsyncClient() as client:
                response = await client.get(runtime.base_url, follow_redirects=False, timeout=1.0)
            self.assertNotEqual(response.status_code, 404)
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_embedded_oauth_flow_registers_client_and_issues_access_token(self) -> None:
        auth_base = self.runtime.base_url.removesuffix("/bridge")
        redirect_uri = "https://chatgpt.com/connector/oauth/test"
        verifier = "kanban-oauth-verifier"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

        async with httpx.AsyncClient(follow_redirects=False, timeout=2.0) as client:
            protected = await client.get(f"{auth_base}/.well-known/oauth-protected-resource/bridge")
            self.assertEqual(protected.status_code, 200)
            protected_data = protected.json()
            self.assertEqual(protected_data["resource"], "https://agent.example/bridge")
            self.assertIn(
                "https://agent.example",
                [item.rstrip("/") for item in protected_data["authorization_servers"]],
            )

            metadata = await client.get(f"{auth_base}/.well-known/oauth-authorization-server")
            self.assertEqual(metadata.status_code, 200)
            metadata_data = metadata.json()
            self.assertEqual(metadata_data["issuer"].rstrip("/"), "https://agent.example")
            self.assertEqual(metadata_data["registration_endpoint"].rstrip("/"), "https://agent.example/register")

            registration = await client.post(
                f"{auth_base}/register",
                json={
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "client_name": "Minimal Kanban Test Connector",
                },
            )
            self.assertEqual(registration.status_code, 201)
            client_info = registration.json()
            self.assertTrue(client_info["client_id"])
            self.assertTrue(client_info["client_secret"])

            authorize = await client.get(
                f"{auth_base}/authorize",
                params={
                    "client_id": client_info["client_id"],
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": "demo-state",
                    "resource": "https://agent.example/bridge",
                },
            )
            self.assertEqual(authorize.status_code, 302)
            query = parse_qs(urlsplit(authorize.headers["location"]).query)
            self.assertEqual(query["state"][0], "demo-state")
            code = query["code"][0]

            token = await client.post(
                f"{auth_base}/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_info["client_id"],
                    "client_secret": client_info["client_secret"],
                    "code_verifier": verifier,
                    "resource": "https://agent.example/bridge",
                },
            )
            self.assertEqual(token.status_code, 200)
            token_data = token.json()
            self.assertTrue(token_data["access_token"])
            self.assertTrue(token_data["refresh_token"])

        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token_data['access_token']}"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                tools = await session.list_tools()
                self.assertTrue(any(tool.name == "list_columns" for tool in tools.tools))

    async def test_mcp_accepts_external_host_header_from_tunnel_url(self) -> None:
        runtime = None
        tunnel_url = "https://demo.ngrok-free.app"
        try:
            port = reserve_port()
            board_api = BoardApiClient(self.api_server.base_url, bearer_token="api-secret", logger=self.logger)
            mcp_server = create_mcp_server(
                board_api,
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/mcp",
                bearer_token=None,
                tunnel_url=tunnel_url,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient(headers={"Host": "demo.ngrok-free.app"}) as client:
                response = await client.get(runtime.base_url, follow_redirects=False, timeout=1.0)
            self.assertNotEqual(response.status_code, 421)

            async with httpx.AsyncClient(headers={"Host": "demo.ngrok-free.app"}) as http_client:
                async with open_mcp_session(runtime.base_url, http_client=http_client) as session:
                    tools = await session.list_tools()
                    self.assertTrue(any(tool.name == "list_columns" for tool in tools.tools))
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_runtime_reports_transport_fallbacks_when_board_api_is_unreachable(self) -> None:
        class BrokenBoardApi:
            base_url = "http://127.0.0.1:1/api"

            def health(self) -> dict[str, Any]:
                raise BoardApiTransportError("health down")

            def get_board_context(self) -> dict[str, Any]:
                raise BoardApiTransportError("context down")

            def get_gpt_wall(
                self,
                *,
                include_archived: bool = True,
                event_limit: int | None = None,
            ) -> dict[str, Any]:
                raise BoardApiTransportError("wall down")

        runtime = None
        try:
            port = reserve_port()
            mcp_server = create_mcp_server(
                BrokenBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/fallback",
                bearer_token=None,
                public_endpoint_url="https://agent.example/fallback",
                oauth_state_file=self.oauth_state_file,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient() as http_client:
                async with open_mcp_session(runtime.base_url, http_client=http_client) as session:
                    bootstrap = await session.call_tool("bootstrap_context", {})
                    self.assertFalse(bootstrap.isError)
                    self.assertFalse(bootstrap.structuredContent["ok"])
                    self.assertEqual(bootstrap.structuredContent["error"]["code"], "gpt_wall_unreachable")

                    runtime_status = await session.call_tool("get_runtime_status", {})
                    self.assertFalse(runtime_status.isError)
                    self.assertTrue(runtime_status.structuredContent["ok"])
                    payload = runtime_status.structuredContent["data"]["runtime_status"]
                    self.assertEqual(payload["api_health_error"]["code"], "board_api_unreachable")
                    self.assertEqual(payload["board_context_error"]["code"], "board_context_unreachable")
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_board_tools_return_structured_transport_errors_when_board_api_is_unreachable(self) -> None:
        class BrokenBoardApi:
            base_url = "http://127.0.0.1:1/api"

            def list_columns(self) -> dict[str, Any]:
                raise BoardApiTransportError("columns down")

        runtime = None
        try:
            port = reserve_port()
            mcp_server = create_mcp_server(
                BrokenBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/fallback",
                bearer_token=None,
                public_endpoint_url="https://agent.example/fallback",
                oauth_state_file=self.oauth_state_file,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient() as http_client:
                async with open_mcp_session(runtime.base_url, http_client=http_client) as session:
                    columns = await session.call_tool("list_columns", {})
                    self.assertFalse(columns.isError)
                    self.assertFalse(columns.structuredContent["ok"])
                    self.assertEqual(columns.structuredContent["error"]["code"], "board_api_unreachable")
                    self.assertEqual(columns.structuredContent["error"]["message"], "columns down")
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_get_gpt_wall_returns_structured_transport_error_when_board_api_is_unreachable(self) -> None:
        class BrokenBoardApi:
            base_url = "http://127.0.0.1:1/api"

            def get_gpt_wall(
                self,
                *,
                include_archived: bool = True,
                event_limit: int | None = None,
            ) -> dict[str, Any]:
                raise BoardApiTransportError("wall down")

            def get_board_context(self) -> dict[str, Any]:
                raise BoardApiTransportError("context down")

        runtime = None
        try:
            port = reserve_port()
            mcp_server = create_mcp_server(
                BrokenBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=port,
                path="/fallback",
                bearer_token=None,
                public_endpoint_url="https://agent.example/fallback",
                oauth_state_file=self.oauth_state_file,
            )
            runtime = McpServerRuntime(mcp_server, self.logger, auth_mode="none")
            runtime.start()

            async with httpx.AsyncClient() as http_client:
                async with open_mcp_session(runtime.base_url, http_client=http_client) as session:
                    wall = await session.call_tool("get_gpt_wall", {})
                    self.assertFalse(wall.isError)
                    self.assertFalse(wall.structuredContent["ok"])
                    self.assertEqual(wall.structuredContent["error"]["code"], "gpt_wall_unreachable")
                    self.assertEqual(wall.structuredContent["error"]["message"], "wall down")
        finally:
            if runtime is not None:
                runtime.stop()

    async def test_mcp_sticky_deadline_total_seconds_and_short_id_delete_work(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                created_sticky = await session.call_tool(
                    "create_sticky",
                    {
                        "text": "Connector sticky total seconds",
                        "x": 30,
                        "y": 40,
                        "deadline": {"total_seconds": 3600},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(created_sticky.isError)
                self.assertTrue(created_sticky.structuredContent["ok"])
                sticky = created_sticky.structuredContent["data"]["sticky"]
                self.assertTrue(sticky["short_id"].startswith("S-"))
                self.assertGreater(sticky["remaining_seconds"], 0)

                deleted_sticky = await session.call_tool(
                    "delete_sticky",
                    {"sticky_id": sticky["short_id"], "actor_name": "ОПЕРАТОР"},
                )
                self.assertFalse(deleted_sticky.isError)
                self.assertTrue(deleted_sticky.structuredContent["ok"])
                self.assertEqual(deleted_sticky.structuredContent["data"]["sticky_id"], sticky["id"])

                snapshot = await session.call_tool("get_board_snapshot", {})
                self.assertFalse(snapshot.isError)
                self.assertFalse(any(item["id"] == sticky["id"] for item in snapshot.structuredContent["data"]["stickies"]))


class BoardApiClientTests(unittest.TestCase):
    def test_compose_url_does_not_duplicate_api_segment(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        self.assertEqual(client._compose_url("/api/get_board_snapshot"), "https://board.example/api/get_board_snapshot")
        self.assertEqual(client._compose_url("api/get_cards"), "https://board.example/api/get_cards")

    def test_optional_scalar_filter_uses_get_without_payload_and_post_with_payload(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_board_snapshot()
            client.list_archived_cards()
            client.list_repair_orders()

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/get_board_snapshot", method="GET"),
                unittest.mock.call("/api/list_archived_cards", method="GET"),
                unittest.mock.call("/api/list_repair_orders", method="GET"),
            ],
        )

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.get_board_snapshot(archive_limit=5)
            client.list_archived_cards(limit=10)
            client.list_repair_orders(limit=300)
            client.list_repair_orders(limit=25, status="closed")
            client.list_repair_orders(
                limit=20,
                status="all",
                query="срочно dsg",
                sort_by="closed_at",
                sort_dir="asc",
            )

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/get_board_snapshot", {"archive_limit": 5}, method="POST"),
                unittest.mock.call("/api/list_archived_cards", {"limit": 10}, method="POST"),
                unittest.mock.call("/api/list_repair_orders", {"limit": 300}, method="POST"),
                unittest.mock.call("/api/list_repair_orders", {"limit": 25, "status": "closed"}, method="POST"),
                unittest.mock.call(
                    "/api/list_repair_orders",
                    {
                        "limit": 20,
                        "status": "all",
                        "query": "срочно dsg",
                        "sort_by": "closed_at",
                        "sort_dir": "asc",
                    },
                    method="POST",
                ),
            ],
        )

    def test_request_with_identity_enriches_payload_for_write_methods(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.create_column("Inbox", actor_name="ОПЕРАТОР")
            client.move_card(card_id="card-1", column="done", before_card_id="card-2", actor_name="ОПЕРАТОР")
            client.archive_card(card_id="card-1", actor_name="ОПЕРАТОР")
            client.set_repair_order_status(card_id="card-1", status="closed", actor_name="ОПЕРАТОР")

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call(
                    "/api/create_column",
                    {"label": "Inbox", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
                unittest.mock.call(
                    "/api/move_card",
                    {"card_id": "card-1", "column": "done", "before_card_id": "card-2", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
                unittest.mock.call(
                    "/api/archive_card",
                    {"card_id": "card-1", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
                unittest.mock.call(
                    "/api/set_repair_order_status",
                    {"card_id": "card-1", "status": "closed", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
            ],
        )

    def test_cashbox_request_helpers_use_expected_payloads(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch.object(client, "_request", return_value={"ok": True}) as request:
            client.list_cashboxes()
            client.list_cashboxes(limit=50)
            client.get_cashbox("CB-1", transaction_limit=25)
            client.create_cashbox("Наличный", actor_name="ОПЕРАТОР")
            client.create_cash_transaction(
                cashbox_id="CB-1",
                direction="income",
                amount="1000",
                note="Предоплата",
                actor_name="ОПЕРАТОР",
            )
            client.delete_cashbox("CB-1", actor_name="ОПЕРАТОР")

        self.assertEqual(
            request.call_args_list,
            [
                unittest.mock.call("/api/list_cashboxes", method="GET"),
                unittest.mock.call("/api/list_cashboxes", {"limit": 50}, method="POST"),
                unittest.mock.call("/api/get_cashbox", {"cashbox_id": "CB-1", "transaction_limit": 25}),
                unittest.mock.call(
                    "/api/create_cashbox",
                    {"name": "Наличный", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
                unittest.mock.call(
                    "/api/create_cash_transaction",
                    {
                        "cashbox_id": "CB-1",
                        "direction": "income",
                        "note": "Предоплата",
                        "amount": "1000",
                        "source": "mcp",
                        "actor_name": "ОПЕРАТОР",
                    },
                ),
                unittest.mock.call(
                    "/api/delete_cashbox",
                    {"cashbox_id": "CB-1", "source": "mcp", "actor_name": "ОПЕРАТОР"},
                ),
            ],
        )

    def test_request_raises_transport_error_on_invalid_success_json(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class BrokenResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"{not-json"

        with patch("urllib.request.urlopen", return_value=BrokenResponse()):
            with self.assertRaises(BoardApiTransportError):
                client.health()


if __name__ == "__main__":
    unittest.main()
