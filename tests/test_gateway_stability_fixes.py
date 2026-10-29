from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from mcp.server.fastmcp.exceptions import ToolError

from minimal_kanban.mcp.client import BoardApiTransportError
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class GatewayStabilityFixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def server(self, board=None, initial_state=None):
        state = initial_state if initial_state is not None else {}
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=lambda server, logger: register_fake_store_manager_tools(
                server, logger, state
            ),
        ):
            server = create_mcp_server(
                board or FakeBoardApi(),
                logging.getLogger(self.id()),
                host="127.0.0.1",
                port=41839,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )
        return server, state

    async def test_create_card_lost_response_stays_compensating_and_same_key_never_replays(self):
        class LostResponseBoard(FakeBoardApi):
            def create_card(self, **arguments):
                super().create_card(**arguments)
                raise BoardApiTransportError("synthetic lost POST response")

        board = LostResponseBoard()
        server, state = self.server(board)
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "create_card"},
            convert_result=False,
        )
        args = {
            "name": "create_card",
            "arguments": {"title": "Synthetic"},
            "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            "idempotency_key": "synthetic-lost-create",
        }
        tool = server._tool_manager.get_tool("call_raw_capability")
        first = (await tool.run(args, convert_result=False)).structuredContent
        retry = (await tool.run(args, convert_result=False)).structuredContent
        self.assertFalse(first["ok"])
        self.assertEqual("compensating", first["status"])
        self.assertTrue(first["meta"]["outcome_uncertain"])
        self.assertTrue(first["meta"]["readback_required"])
        self.assertEqual("compensating", retry["status"])
        self.assertTrue(retry["meta"]["outcome_uncertain"])
        self.assertIn("do not use a new idempotency key", retry["next_actions"][0])
        self.assertEqual(1, len(board.created_cards))
        run = state["workflow_runs"][first["run_id"]]
        self.assertEqual("compensating", run["status"])
        self.assertTrue(run["scope"]["request_fingerprint"])
        compensation = next(
            args
            for name, args in state["calls"]
            if name == "workflow_transition" and args["status"] == "compensating"
        )
        self.assertTrue(compensation["verification"]["outcome_uncertain"])
        self.assertTrue(compensation["verification"]["readback_required"])

    async def test_runtime_default_does_not_call_store_and_explicit_opt_in_does(self):
        server, state = self.server(initial_state={"store_available": False})
        tool = server._tool_manager.get_tool("get_runtime_status")
        default = (await tool.run({}, convert_result=False)).structuredContent
        self.assertTrue(default["ok"])
        self.assertEqual("ready", default["status"])
        self.assertEqual("not_requested", default["data"]["store"]["status"])
        self.assertFalse(any(n == "store_runtime_status" for n, _ in state["calls"]))
        explicit = (
            await tool.run({"include_store_context": True}, convert_result=False)
        ).structuredContent
        self.assertTrue(explicit["ok"])
        self.assertEqual("degraded", explicit["status"])
        self.assertEqual(1, sum(n == "store_runtime_status" for n, _ in state["calls"]))
        with self.assertRaises(ToolError):
            await tool.run({"include_store_context": "true"}, convert_result=False)

    async def test_bootstrap_read_timeout_is_structured_and_store_remains_unrequested(self):
        class TimeoutBoard(FakeBoardApi):
            def get_board_context(self):
                raise BoardApiTransportError("synthetic read timeout")

        server, state = self.server(TimeoutBoard())
        result = (
            await server._tool_manager.get_tool("agent_bootstrap").run({}, convert_result=False)
        ).structuredContent
        self.assertFalse(result["ok"])
        self.assertEqual("degraded", result["status"])
        self.assertFalse(any(n.startswith("store_") for n, _ in state["calls"]))
        self.assertEqual(3, len(result["summary"]["card_sample"]))

    async def test_bootstrap_sends_sample_limit_to_backend_and_query_to_search(self):
        board = FakeBoardApi()
        server, _ = self.server(board)
        tool = server._tool_manager.get_tool("agent_bootstrap")
        with patch.object(board, "get_cards", wraps=board.get_cards) as cards:
            result = (await tool.run({"sample_limit": 1}, convert_result=False)).structuredContent
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(result["summary"]["card_sample"]))
        self.assertEqual(1, cards.call_args.kwargs["limit"])
        with patch.object(board, "search_cards", wraps=board.search_cards) as search:
            await tool.run({"query": "Synthetic case", "sample_limit": 2}, convert_result=False)
        self.assertEqual("Synthetic case", search.call_args.kwargs["query"])
        self.assertEqual(2, search.call_args.kwargs["limit"])


class BoundedCardSampleTests(unittest.TestCase):
    def test_limit_is_applied_before_serialization_and_default_keeps_all_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            logger = logging.getLogger(self.id())
            service = CardService(
                JsonStore(state_file=base / "state.json", logger=logger),
                logger,
                attachments_dir=base / "attachments",
                repair_orders_dir=base / "repair-orders",
            )
            for index in range(5):
                service.create_card({"title": f"Synthetic {index}", "actor_name": "ALICE"})
            snapshot = service._snapshot_service
            with patch.object(
                snapshot, "_serialize_cards_payload", wraps=snapshot._serialize_cards_payload
            ) as serialize:
                result = service.get_cards({"compact": True, "limit": 2, "actor_name": "ALICE"})
            self.assertEqual(2, len(result["cards"]))
            self.assertEqual(5, result["meta"]["total"])
            self.assertTrue(result["meta"]["has_more"])
            self.assertEqual(2, len(serialize.call_args.args[0]))
            legacy = service.get_cards({"compact": True, "actor_name": "ALICE"})
            self.assertEqual(5, len(legacy["cards"]))
            self.assertFalse(legacy["meta"]["has_more"])
