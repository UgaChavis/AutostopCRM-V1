from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.runtime import McpServerRuntime
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
        await asyncio.sleep(0.1)
        self.runtime.stop()
        self.api_server.stop()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_mcp_tools_reach_backend(self) -> None:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer mcp-secret"}) as http_client:
            async with streamable_http_client(self.runtime.base_url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
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
                            "create_sticky",
                            "get_cards",
                            "get_card",
                            "get_board_snapshot",
                            "autofill_vehicle_data",
                            "update_board_settings",
                            "get_gpt_wall",
                            "get_card_log",
                            "list_archived_cards",
                            "search_cards",
                            "create_card",
                            "update_card",
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
                        "Current Minimal Kanban Board",
                    )
                    self.assertIn("[BOARD CONTEXT]", board_context.structuredContent["data"]["text"])

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
                            "tags": ["РЎР РћР§РќРћ", "GPT"],
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
                            "tags": ["РЎР РћР§РќРћ", "РџР РћР’Р•Р Р•РќРћ"],
                            "actor_name": "РћРџР•Р РђРўРћР ",
                        },
                    )
                    self.assertTrue(updated.structuredContent["ok"])
                    self.assertEqual(updated.structuredContent["data"]["card"]["description"], "РћР±РЅРѕРІР»РµРЅРѕ РїРѕ MCP")
                    self.assertEqual(updated.structuredContent["data"]["card"]["vehicle"], "KIA RIO X")
                    self.assertEqual(updated.structuredContent["data"]["card"]["vehicle_profile"]["engine_code"], "G4FG")

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

                    updated_settings = await session.call_tool(
                        "update_board_settings",
                        {"board_scale": 1.25, "actor_name": "РћРџР•Р РђРўРћР "},
                    )
                    self.assertTrue(updated_settings.structuredContent["ok"])
                    self.assertEqual(updated_settings.structuredContent["data"]["settings"]["board_scale"], 1.25)

                    snapshot = await session.call_tool("get_board_snapshot", {"archive_limit": 5})
                    self.assertTrue(snapshot.structuredContent["ok"])
                    self.assertTrue(any(item["id"] == card_id for item in snapshot.structuredContent["data"]["cards"]))
                    self.assertTrue(any(item["id"] == sticky_id for item in snapshot.structuredContent["data"]["stickies"]))
                    self.assertEqual(snapshot.structuredContent["data"]["settings"]["board_scale"], 1.25)

                    wall = await session.call_tool("get_gpt_wall", {"include_archived": True, "event_limit": 50})
                    self.assertTrue(wall.structuredContent["ok"])
                    self.assertIn(card_short_id, wall.structuredContent["data"]["text"])
                    self.assertTrue(any(item["id"] == card_id for item in wall.structuredContent["data"]["cards"]))
                    self.assertIn("connector_identity", wall.structuredContent["data"])
                    self.assertIn("board_context", wall.structuredContent["data"])
                    self.assertIn("resource_url", wall.structuredContent["data"]["text"])
                    self.assertIn("Current Minimal Kanban Board", wall.structuredContent["data"]["text"])
                    self.assertTrue(any(item["id"] == sticky_id for item in wall.structuredContent["data"]["stickies"]))

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
            async with streamable_http_client(self.runtime.base_url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    invalid = await session.call_tool("get_card", {"card_id": "missing-card"})
                    self.assertFalse(invalid.isError)
                    self.assertFalse(invalid.structuredContent["ok"])
                    self.assertEqual(invalid.structuredContent["error"]["code"], "not_found")

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

            async with streamable_http_client(runtime.base_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
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
            async with streamable_http_client(self.runtime.base_url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
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
                async with streamable_http_client(runtime.base_url, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertTrue(any(tool.name == "list_columns" for tool in tools.tools))
        finally:
            if runtime is not None:
                runtime.stop()


class BoardApiClientTests(unittest.TestCase):
    def test_compose_url_does_not_duplicate_api_segment(self) -> None:
        client = BoardApiClient("https://board.example/api", bearer_token="secret")

        self.assertEqual(client._compose_url("/api/get_board_snapshot"), "https://board.example/api/get_board_snapshot")
        self.assertEqual(client._compose_url("api/get_cards"), "https://board.example/api/get_cards")


if __name__ == "__main__":
    unittest.main()
