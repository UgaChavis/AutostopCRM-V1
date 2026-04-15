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
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager, suppress
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.agent.storage import AgentStorage
from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError
from minimal_kanban.mcp.runtime import McpServerRuntime
from minimal_kanban.mcp.server import _normalize_tool_path_alias, create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def close_lingering_memory_streams(*, max_passes: int = 3) -> int:
    total_closed = 0
    for _ in range(max(1, int(max_passes))):
        closed = 0
        for obj in list(gc.get_objects()):
            if not isinstance(obj, (MemoryObjectReceiveStream, MemoryObjectSendStream)):
                continue
            if getattr(obj, "_closed", True):
                continue
            try:
                await obj.aclose()
                closed += 1
            except Exception:
                continue
        total_closed += closed
        if not closed:
            break
        await asyncio.sleep(0)
    return total_closed


@asynccontextmanager
async def open_mcp_session(url: str, *, http_client: httpx.AsyncClient | None = None):
    async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _get_session_id):
        try:
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
        finally:
            with suppress(Exception):
                await read_stream.aclose()
            with suppress(Exception):
                await write_stream.aclose()
            await close_lingering_memory_streams()
            await asyncio.sleep(0.05)
            gc.collect()


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
        self.agent_storage = AgentStorage(base_dir=Path(self.temp_dir.name) / "agent")
        self.agent_service = AgentControlService(self.agent_storage)
        self.service.attach_agent_control(self.agent_service)
        self.agent_service.bind_board_service(self.service)
        self.api_port = reserve_port()
        self.mcp_port = reserve_port()
        self.api_server = ApiServer(
            self.service,
            self.logger,
            agent_service=self.agent_service,
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

    async def asyncSetUp(self) -> None:
        loop = asyncio.get_running_loop()
        loop.set_debug(False)

    def test_tool_path_alias_normalization_prefers_canonical_short_path(self) -> None:
        self.assertEqual(
            _normalize_tool_path_alias("/AutoStopCRM/link_abc123/bootstrap_context"),
            "/AutoStopCRM/bootstrap_context",
        )
        self.assertEqual(
            _normalize_tool_path_alias("/AutoStopCRM/get_runtime_status"),
            "/AutoStopCRM/get_runtime_status",
        )

    async def asyncTearDown(self) -> None:
        self.runtime.stop()
        self.api_server.stop()
        await asyncio.sleep(0.1)
        gc.collect()
        await close_lingering_memory_streams()
        await asyncio.sleep(0.05)
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
                        "agent_status",
                        "agent_runs",
                        "agent_actions",
                        "agent_tasks",
                        "agent_scheduled_tasks",
                        "agent_enqueue_task",
                        "save_agent_scheduled_task",
                        "delete_agent_scheduled_task",
                        "pause_agent_scheduled_task",
                        "resume_agent_scheduled_task",
                        "run_agent_scheduled_task",
                        "set_card_ai_autofill",
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
                self.assertEqual(ping.structuredContent["data"]["schema_version"], "2026-04-13")
                self.assertIn("[CONNECTOR PING]", ping.structuredContent["data"]["text"])
                self.assertIn("request_id", ping.structuredContent["meta"])
                self.assertIn("timestamp", ping.structuredContent["meta"])
                self.assertIn("latency_ms", ping.structuredContent["meta"])
                self.assertEqual(ping.structuredContent["meta"]["response_mode"], "ping")
                self.assertEqual(ping.structuredContent["meta"]["canonical_tool_path"], "/AutoStopCRM/ping_connector")

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
                self.assertEqual(bootstrap.structuredContent["data"]["schema_version"], "2026-04-13")
                self.assertIn("cards_preview_truncated", bootstrap.structuredContent["data"]["gpt_wall_preview"])
                self.assertIn("events_preview_truncated", bootstrap.structuredContent["data"]["gpt_wall_preview"])
                self.assertEqual(
                    bootstrap.structuredContent["data"]["canonical_tool_paths"]["bootstrap_context"],
                    "/AutoStopCRM/bootstrap_context",
                )
                self.assertIn("request_id", bootstrap.structuredContent["meta"])
                self.assertIn("latency_ms", bootstrap.structuredContent["meta"])
                self.assertEqual(bootstrap.structuredContent["meta"]["response_mode"], "summary_bootstrap")
                self.assertEqual(
                    bootstrap.structuredContent["meta"]["applied_params"],
                    {"include_archived": True, "event_limit": 20},
                )

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
                self.assertEqual(board_context.structuredContent["data"]["meta"]["response_mode"], "summary")
                self.assertEqual(board_context.structuredContent["data"]["meta"]["view_mode"], "summary")

                review = await session.call_tool("review_board", {})
                self.assertFalse(review.isError)
                self.assertTrue(review.structuredContent["ok"])
                self.assertIn("summary", review.structuredContent["data"])
                self.assertIn("by_column", review.structuredContent["data"])
                self.assertIn("alerts", review.structuredContent["data"])
                self.assertIn("priority_cards", review.structuredContent["data"])
                self.assertIn("recent_events", review.structuredContent["data"])
                self.assertIn("[BOARD REVIEW]", review.structuredContent["data"]["text"])

                cards = await session.call_tool("get_cards", {"include_archived": False, "compact": True})
                self.assertTrue(cards.structuredContent["ok"])
                self.assertTrue(cards.structuredContent["data"]["meta"]["compact"])
                self.assertEqual(cards.structuredContent["data"]["meta"]["response_mode"], "list")
                self.assertEqual(cards.structuredContent["data"]["meta"]["view_mode"], "compact")

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
                self.assertEqual(cashboxes.structuredContent["data"]["meta"]["response_mode"], "list")

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
                self.assertIn("board_context_summary", runtime_status.structuredContent["data"]["runtime_status"])
                self.assertEqual(runtime_status.structuredContent["data"]["schema_version"], "2026-04-13")
                self.assertEqual(
                    runtime_status.structuredContent["data"]["full_board_context_tool"],
                    "get_board_context",
                )
                self.assertEqual(runtime_status.structuredContent["meta"]["response_mode"], "diagnostics")
                self.assertIn("[RUNTIME STATUS]", runtime_status.structuredContent["data"]["text"])
                self.assertIn("request_id", runtime_status.structuredContent["meta"])
                self.assertIn("latency_ms", runtime_status.structuredContent["meta"])

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

                created_cashbox = await session.call_tool("create_cashbox", {"name": "Безналичный", "actor_name": "ОПЕРАТОР"})
                self.assertFalse(created_cashbox.isError)
                self.assertTrue(created_cashbox.structuredContent["ok"])
                repair_order_cashbox = created_cashbox.structuredContent["data"]["cashbox"]
                created_cash_cashbox = await session.call_tool(
                    "create_cashbox",
                    {"name": "Наличный доплата", "actor_name": "ОПЕРАТОР"},
                )
                self.assertFalse(created_cash_cashbox.isError)
                self.assertTrue(created_cash_cashbox.structuredContent["ok"])
                repair_order_cash_cashbox = created_cash_cashbox.structuredContent["data"]["cashbox"]
                repair_order = await session.call_tool(
                    "update_repair_order",
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "client": "Иван Иванов",
                            "phone": "+7 900 123-45-67",
                            "client_information": "Согласовать дальнейшую диагностику",
                            "payments": [
                                {
                                    "amount": "500",
                                    "paid_at": "06.04.2026 12:00",
                                    "note": "Аванс",
                                    "payment_method": "cash",
                                    "actor_name": "ОПЕРАТОР",
                                    "cashbox_id": repair_order_cashbox["id"],
                                }
                            ],
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
                self.assertEqual(repair_order.structuredContent["data"]["repair_order"]["payment_method"], "cashless")
                self.assertEqual(repair_order.structuredContent["data"]["repair_order"]["prepayment"], "500")
                self.assertEqual(repair_order.structuredContent["data"]["repair_order"]["paid_total"], "500")
                self.assertEqual(
                    repair_order.structuredContent["data"]["repair_order"]["payment_status"],
                    "paid" if repair_order.structuredContent["data"]["repair_order"]["is_paid"] else "unpaid",
                )
                self.assertEqual(len(repair_order.structuredContent["data"]["repair_order"]["payments"]), 1)
                self.assertEqual(repair_order.structuredContent["data"]["repair_order"]["payments"][0]["actor_name"], "ОПЕРАТОР")
                self.assertEqual(
                    repair_order.structuredContent["data"]["repair_order"]["payments"][0]["cashbox_name"],
                    repair_order_cashbox["name"],
                )
                self.assertTrue(repair_order.structuredContent["data"]["repair_order"]["payments"][0]["cash_transaction_id"])
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
                self.assertEqual(materials.structuredContent["data"]["repair_order"]["subtotal_total"], "3100")
                self.assertEqual(materials.structuredContent["data"]["repair_order"]["taxes_total"], "75")
                self.assertEqual(materials.structuredContent["data"]["repair_order"]["grand_total"], "3175")
                self.assertEqual(materials.structuredContent["data"]["repair_order"]["due_total"], "2675")

                repair_order_read = await session.call_tool("get_repair_order", {"card_id": card_id})
                self.assertTrue(repair_order_read.structuredContent["ok"])
                self.assertEqual(repair_order_read.structuredContent["data"]["repair_order"]["license_plate"], "В003НК124")
                self.assertEqual(repair_order_read.structuredContent["data"]["repair_order"]["payment_method_label"], "Безналичный")

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
                self.assertIn("Стоимость заказ-наряда: 3100", repair_order_text.structuredContent["data"]["text"])
                self.assertIn("Налоги и сборы: 75", repair_order_text.structuredContent["data"]["text"])
                self.assertIn("Итого по заказ-наряду: 3175", repair_order_text.structuredContent["data"]["text"])
                self.assertIn("К доплате: 2675", repair_order_text.structuredContent["data"]["text"])

                repair_orders = await session.call_tool(
                    "list_repair_orders",
                    {"limit": 10, "status": "open", "query": "срочно иван dsg", "sort_by": "number", "sort_dir": "asc"},
                )
                self.assertTrue(repair_orders.structuredContent["ok"])
                self.assertTrue(any(item["card_id"] == card_id for item in repair_orders.structuredContent["data"]["repair_orders"]))

                close_blocked = await session.call_tool(
                    "set_repair_order_status",
                    {"card_id": card_id, "status": "closed", "actor_name": "ОПЕРАТОР"},
                )
                self.assertFalse(close_blocked.structuredContent["ok"])
                self.assertEqual(close_blocked.structuredContent["error"]["code"], "repair_order_payment_required")

                fully_paid = await session.call_tool(
                    "update_repair_order",
                    {
                        "card_id": card_id,
                        "actor_name": "ОПЕРАТОР",
                        "repair_order": {
                            "payments": [
                                {
                                    "amount": "500",
                                    "paid_at": "06.04.2026 10:00",
                                    "note": "Аванс",
                                    "payment_method": "cash",
                                    "actor_name": "ОПЕРАТОР",
                                    "cashbox_id": repair_order_cashbox["id"],
                                },
                                {
                                    "amount": "2675",
                                    "paid_at": "06.04.2026 13:30",
                                    "note": "Доплата",
                                    "payment_method": "cash",
                                    "actor_name": "ОПЕРАТОР",
                                    "cashbox_id": repair_order_cash_cashbox["id"],
                                },
                            ],
                        },
                    },
                )
                self.assertTrue(fully_paid.structuredContent["ok"])
                self.assertEqual(fully_paid.structuredContent["data"]["repair_order"]["due_total"], "0")

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

                snapshot = await session.call_tool("get_board_snapshot", {"archive_limit": 5, "compact": True})
                self.assertTrue(snapshot.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in snapshot.structuredContent["data"]["cards"]))
                snapshot_card = next(item for item in snapshot.structuredContent["data"]["cards"] if item["id"] == card_id)
                self.assertIn("vehicle_profile_compact", snapshot_card)
                self.assertTrue(any(item["id"] == sticky_id for item in snapshot.structuredContent["data"]["stickies"]))
                self.assertEqual(snapshot.structuredContent["data"]["settings"]["board_scale"], 1.25)
                self.assertEqual(snapshot.structuredContent["data"]["meta"]["response_mode"], "snapshot")
                self.assertTrue(snapshot.structuredContent["data"]["meta"]["compact"])

                wall = await session.call_tool("get_gpt_wall", {"include_archived": True, "event_limit": 50, "view_mode": "agent"})
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
                self.assertEqual(wall.structuredContent["data"]["meta"]["response_mode"], "agent_context")
                self.assertEqual(wall.structuredContent["data"]["meta"]["view_mode"], "agent")
                self.assertTrue(wall.structuredContent["data"]["meta"]["text_present"])

                board_content = await session.call_tool("get_board_content", {"include_archived": True, "view_mode": "agent"})
                self.assertTrue(board_content.structuredContent["ok"])
                self.assertIn(card_short_id, board_content.structuredContent["data"]["text"])
                self.assertTrue(any(item["id"] == card_id for item in board_content.structuredContent["data"]["cards"]))
                self.assertIn("board_context", board_content.structuredContent["data"])
                self.assertIn("connector_identity", board_content.structuredContent["data"])
                self.assertEqual(board_content.structuredContent["data"]["meta"]["response_mode"], "agent_context")
                self.assertEqual(board_content.structuredContent["data"]["meta"]["section_kind"], "board_content")

                board_events = await session.call_tool("get_board_events", {"event_limit": 50, "include_archived": True})
                self.assertTrue(board_events.structuredContent["ok"])
                self.assertTrue(any(item["card_id"] == card_id for item in board_events.structuredContent["data"]["events"]))
                self.assertIn(card_short_id, board_events.structuredContent["data"]["text"])
                self.assertIn("connector_identity", board_events.structuredContent["data"])
                self.assertEqual(board_events.structuredContent["data"]["meta"]["response_mode"], "audit")
                self.assertEqual(board_events.structuredContent["data"]["meta"]["section_kind"], "event_log")
                self.assertEqual(board_events.structuredContent["data"]["meta"]["event_order"], "newest_first")

                search = await session.call_tool(
                    "search_cards",
                    {"query": card_short_id, "column": column_id, "limit": 5},
                )
                self.assertTrue(search.structuredContent["ok"])
                self.assertEqual(search.structuredContent["data"]["meta"]["total_matches"], 1)
                self.assertFalse(search.structuredContent["data"]["meta"]["has_more"])
                self.assertEqual(search.structuredContent["data"]["meta"]["response_mode"], "search")
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

                log = await session.call_tool("get_card_log", {"card_id": card_id, "limit": 2})
                self.assertTrue(log.structuredContent["ok"])
                self.assertEqual(log.structuredContent["data"]["events"][0]["source"], "mcp")
                self.assertEqual(log.structuredContent["data"]["meta"]["limit"], 2)
                self.assertEqual(log.structuredContent["data"]["meta"]["response_mode"], "audit")

                overdue = await session.call_tool("list_overdue_cards", {})
                self.assertTrue(overdue.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in overdue.structuredContent["data"]["cards"]))

                archived = await session.call_tool("archive_card", {"card_id": card_id, "actor_name": "РћРџР•Р РђРўРћР "})
                self.assertTrue(archived.structuredContent["ok"])
                self.assertTrue(archived.structuredContent["data"]["card"]["archived"])

                archived_list = await session.call_tool("list_archived_cards", {"limit": 10, "compact": True})
                self.assertTrue(archived_list.structuredContent["ok"])
                self.assertTrue(any(item["id"] == card_id for item in archived_list.structuredContent["data"]["cards"]))
                self.assertEqual(archived_list.structuredContent["data"]["meta"]["response_mode"], "archive_list")
                self.assertTrue(archived_list.structuredContent["data"]["meta"]["compact"])

                restored = await session.call_tool(
                    "restore_card",
                    {"card_id": card_id, "column": "done", "actor_name": "РћРџР•Р РђРўРћР "},
                )
                self.assertTrue(restored.structuredContent["ok"])
                self.assertFalse(restored.structuredContent["data"]["card"]["archived"])

    async def test_mcp_get_cards_defaults_to_compact_payload(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                created_card = await session.call_tool(
                    "create_card",
                    {
                        "vehicle": "BMW",
                        "title": "MCP compact cards",
                        "description": "Payload check",
                        "deadline": {"hours": 1},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(created_card.isError)
                self.assertTrue(created_card.structuredContent["ok"])
                card_id = created_card.structuredContent["data"]["card"]["id"]

                compact_cards = await session.call_tool("get_cards", {})
                self.assertFalse(compact_cards.isError)
                self.assertTrue(compact_cards.structuredContent["data"]["meta"]["compact"])
                self.assertEqual(compact_cards.structuredContent["data"]["meta"]["view_mode"], "compact")
                compact_card = next(card for card in compact_cards.structuredContent["data"]["cards"] if card["id"] == card_id)
                self.assertNotIn("repair_order", compact_card)
                self.assertNotIn("vehicle_profile", compact_card)
                self.assertNotIn("attachments", compact_card)
                self.assertNotIn("ai_autofill_log", compact_card)

                full_cards = await session.call_tool("get_cards", {"compact": False})
                self.assertFalse(full_cards.isError)
                self.assertFalse(full_cards.structuredContent["data"]["meta"]["compact"])
                self.assertEqual(full_cards.structuredContent["data"]["meta"]["view_mode"], "full")
                full_card = next(card for card in full_cards.structuredContent["data"]["cards"] if card["id"] == card_id)
                self.assertIn("repair_order", full_card)
                self.assertIn("vehicle_profile", full_card)
                self.assertIn("attachments", full_card)

    async def test_mcp_agent_tools_reach_backend(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with open_mcp_session(self.runtime.base_url, http_client=http_client) as session:
                status = await session.call_tool("agent_status", {"run_limit": 5})
                self.assertFalse(status.isError)
                self.assertTrue(status.structuredContent["ok"])
                self.assertIn("agent", status.structuredContent["data"])
                self.assertIn("queue", status.structuredContent["data"])

                created = await session.call_tool(
                    "create_card",
                    {
                        "vehicle": "BMW 320i",
                        "title": "Agent MCP",
                        "description": "VIN: WBAPF71060A798127\nТечет антифриз.\nНужно найти радиатор.",
                        "deadline": {"hours": 2},
                        "actor_name": "ОПЕРАТОР",
                    },
                )
                self.assertFalse(created.isError)
                card_id = created.structuredContent["data"]["card"]["id"]

                enabled = await session.call_tool(
                    "set_card_ai_autofill",
                    {"card_id": card_id, "enabled": True, "prompt": "Расшифруй VIN и помоги с радиатором.", "actor_name": "AI"},
                )
                self.assertFalse(enabled.isError)
                self.assertTrue(enabled.structuredContent["ok"])
                self.assertTrue(enabled.structuredContent["data"]["meta"]["enabled"])
                self.assertTrue(enabled.structuredContent["data"]["meta"]["launched"])

                tasks = await session.call_tool("agent_tasks", {"limit": 20})
                self.assertFalse(tasks.isError)
                self.assertTrue(tasks.structuredContent["ok"])
                self.assertTrue(any(item["metadata"].get("purpose") == "card_autofill" for item in tasks.structuredContent["data"]["tasks"]))

                enqueue = await session.call_tool(
                    "agent_enqueue_task",
                    {"task_text": "Review board for urgent cards.", "requested_by": "mcp", "actor_name": "AI"},
                )
                self.assertFalse(enqueue.isError)
                self.assertTrue(enqueue.structuredContent["ok"])
                self.assertEqual(enqueue.structuredContent["data"]["task"]["mode"], "manual")

                scheduled = await session.call_tool(
                    "save_agent_scheduled_task",
                    {
                        "name": "MCP autofill",
                        "prompt": "Inspect new inbox cards and enrich them.",
                        "scope_type": "all_cards",
                        "schedule_type": "interval",
                        "interval_value": 1,
                        "interval_unit": "hour",
                        "active": True,
                    },
                )
                self.assertFalse(scheduled.isError)
                self.assertTrue(scheduled.structuredContent["ok"])
                scheduled_id = scheduled.structuredContent["data"]["task"]["id"]

                listed = await session.call_tool("agent_scheduled_tasks", {})
                self.assertFalse(listed.isError)
                self.assertTrue(any(item["id"] == scheduled_id for item in listed.structuredContent["data"]["tasks"]))

                paused = await session.call_tool("pause_agent_scheduled_task", {"task_id": scheduled_id})
                self.assertFalse(paused.isError)
                self.assertEqual(paused.structuredContent["data"]["task"]["status"], "paused")

                resumed = await session.call_tool("resume_agent_scheduled_task", {"task_id": scheduled_id})
                self.assertFalse(resumed.isError)
                self.assertEqual(resumed.structuredContent["data"]["task"]["status"], "active")

                queued = await session.call_tool("run_agent_scheduled_task", {"task_id": scheduled_id})
                self.assertFalse(queued.isError)
                self.assertTrue(queued.structuredContent["ok"])
                self.assertEqual(queued.structuredContent["data"]["scheduled_task"]["id"], scheduled_id)

                deleted = await session.call_tool("delete_agent_scheduled_task", {"task_id": scheduled_id})
                self.assertFalse(deleted.isError)
                self.assertTrue(deleted.structuredContent["ok"])
                self.assertTrue(deleted.structuredContent["data"]["deleted"])

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


class McpServerRuntimeTests(unittest.TestCase):
    def _runtime(self, host: str, *, port: int = 41831, path: str = "/mcp") -> McpServerRuntime:
        logger = logging.getLogger(f"test.mcp.runtime.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        server = SimpleNamespace(settings=SimpleNamespace(host=host, port=port, streamable_http_path=path))
        return McpServerRuntime(server, logger)

    def test_base_url_uses_loopback_for_wildcard_ipv4_host(self) -> None:
        runtime = self._runtime("0.0.0.0")
        self.assertEqual(runtime.base_url, "http://127.0.0.1:41831/mcp")

    def test_is_port_open_probes_loopback_for_wildcard_ipv4_host(self) -> None:
        runtime = self._runtime("0.0.0.0", port=43123, path="/bridge")
        mock_socket = unittest.mock.MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        mock_socket.__exit__.return_value = None
        with patch("minimal_kanban.mcp.runtime.socket.create_connection", return_value=mock_socket) as create_connection:
            self.assertTrue(runtime._is_port_open())
        create_connection.assert_called_once_with(("127.0.0.1", 43123), timeout=0.5)

    def test_build_startup_error_uses_readable_messages(self) -> None:
        runtime = self._runtime("127.0.0.1")
        generic = runtime._build_startup_error(RuntimeError("boom"))
        logging_issue = runtime._build_startup_error(RuntimeError("Unable to configure formatter 'default'"))

        self.assertIn("Ошибка запуска MCP сервера", generic.user_message)
        self.assertIn("Подробности сохранены", generic.user_message)
        self.assertNotIn("РћС", generic.user_message)
        self.assertIn("Проблема в конфигурации логирования", logging_issue.user_message)
        self.assertNotIn("РџС", logging_issue.user_message)

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
            with self.assertRaises(BoardApiTransportError) as error:
                client.health()
        self.assertIn("Локальный API вернул некорректный JSON", str(error.exception))
        self.assertNotIn("Р›Рѕ", str(error.exception))

    def test_request_raises_transport_error_on_invalid_success_utf8(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class BrokenResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"\xff\xfe\xfd"

        with patch("urllib.request.urlopen", return_value=BrokenResponse()):
            with self.assertRaises(BoardApiTransportError) as error:
                client.health()
        self.assertIn("Локальный API вернул некорректный JSON", str(error.exception))

    def test_request_raises_transport_error_on_invalid_error_utf8(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class BrokenHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://board.example/api/health",
                    code=500,
                    msg="Internal Server Error",
                    hdrs=None,
                    fp=None,
                )

            def read(self) -> bytes:
                return b"\xff\xfe\xfd"

        with patch("urllib.request.urlopen", side_effect=BrokenHttpError()):
            with self.assertRaises(BoardApiTransportError) as error:
                client.health()
        self.assertIn("Локальный API вернул некорректный JSON", str(error.exception))
        self.assertNotIn("Р›Рѕ", str(error.exception))

    def test_request_raises_readable_transport_error_on_unreachable_api(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(BoardApiTransportError) as error:
                client.health()
        self.assertIn("Не удалось подключиться к локальному API", str(error.exception))
        self.assertNotIn("РќРµ", str(error.exception))

    def test_request_retries_once_for_safe_get_transport_error(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        class HealthyResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{\"ok\": true, \"data\": {\"status\": \"ok\"}}'

        with patch(
            "urllib.request.urlopen",
            side_effect=[urllib.error.URLError("temporary"), HealthyResponse()],
        ) as open_mock:
            payload = client.health()
        self.assertTrue(payload["ok"])
        self.assertEqual(open_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
