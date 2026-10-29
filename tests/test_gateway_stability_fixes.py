from __future__ import annotations

import copy
import io
import json
import logging
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from mcp.server.fastmcp.exceptions import ToolError

from minimal_kanban.mcp.agent_gateway_support import (
    _create_card_outcome_unknown,
    _create_card_requires_reconciliation,
)
from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class CreateRecoveryBoard(FakeBoardApi):
    def __init__(self, *, lose_response=False, mismatch=False):
        super().__init__()
        self.lose_response = lose_response
        self.mismatch = mismatch

    def create_card(self, **arguments):
        result = super().create_card(**arguments)
        if self.lose_response:
            raise BoardApiTransportError("synthetic lost POST response")
        return result

    def _request(self, path, payload=None, **kwargs):
        if path == "/api/create_card":
            return self.create_card(
                title=payload["title"], description=payload.get("description", "")
            )
        return super()._request(path, payload, **kwargs)

    def get_card(self, card_id):
        result = super().get_card(card_id)
        if self.mismatch:
            result["data"]["card"]["title"] = "Synthetic mismatch"
        return result


class SyntheticCrash(BaseException):
    """Simulate interruption without turning it into a normal tool failure."""


def synthetic_http_error(status, code):
    payload = {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": "Synthetic server rejection"},
        "meta": {"request_id": "synthetic-request"},
    }
    return urllib.error.HTTPError(
        "https://crm.example/api/create_card",
        status,
        "Synthetic rejection",
        None,
        io.BytesIO(json.dumps(payload).encode()),
    )


class ServerErrorAfterCreateBoard(CreateRecoveryBoard):
    def __init__(self, *, via_http):
        super().__init__()
        self.via_http = via_http

    def create_card(self, **arguments):
        super().create_card(**arguments)
        if not self.via_http:
            return {"ok": False, "error": {"code": "internal_error"}}
        with patch(
            "minimal_kanban.mcp.client._urlopen_no_redirect",
            side_effect=synthetic_http_error(500, "internal_error"),
        ):
            client = BoardApiClient("https://crm.example/api", bearer_token="synthetic")
            return client._request("/api/create_card", arguments)


class GatewayStabilityFixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        env = patch.dict("os.environ", GATEWAY_ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("socket.socket.connect", side_effect=AssertionError("network prohibited"))
        network.start()
        self.addCleanup(network.stop)

    def server(self, board=None, initial_state=None, transition_hook=None):
        state = initial_state if initial_state is not None else {}

        def register(server, logger):
            register_fake_store_manager_tools(server, logger, state)
            if transition_hook is not None:
                transition = server._tool_manager.get_tool("workflow_transition")
                original = transition.fn
                transition.fn = lambda **arguments: transition_hook(original, arguments)

        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=register,
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

    async def create_arguments(self, server, operation, key):
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": operation}, convert_result=False
        )
        return {
            "name": operation,
            "arguments": {"title": "Synthetic", "description": "Synthetic exact readback"},
            "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            "idempotency_key": key,
        }

    async def test_alias_lost_response_reconnect_preserves_uncertainty_and_original_key(self):
        board = CreateRecoveryBoard(lose_response=True)
        server, state = self.server(board)
        args = await self.create_arguments(server, "api:/api/create_card", "synthetic-alias-lost")
        first = (
            await server._tool_manager.get_tool("call_raw_capability").run(
                args, convert_result=False
            )
        ).structuredContent
        self.assertEqual("compensating", first["status"])
        self.assertFalse(first["ok"])
        self.assertEqual("exact_created_card_readback", first["verification"]["check"])
        self.assertTrue(first["meta"]["outcome_uncertain"])
        self.assertTrue(first["meta"]["readback_required"])
        compensation = next(
            arguments
            for name, arguments in state["calls"]
            if name == "workflow_transition" and arguments["status"] == "compensating"
        )
        self.assertTrue(compensation["verification"]["outcome_uncertain"])
        self.assertTrue(compensation["verification"]["readback_required"])
        reopened, _ = self.server(board, copy.deepcopy(state))
        tool = reopened._tool_manager.get_tool("call_raw_capability")
        retry = (await tool.run(args, convert_result=False)).structuredContent
        self.assertEqual(first["run_id"], retry["run_id"])
        self.assertEqual("compensating", retry["status"])
        self.assertTrue(retry["meta"]["outcome_uncertain"])
        self.assertTrue(retry["verification"]["readback_required"])
        self.assertIn("do not use a new idempotency key", retry["next_actions"][0])
        changed = {**args, "arguments": {"title": "Synthetic changed request"}}
        conflict = (await tool.run(changed, convert_result=False)).structuredContent
        self.assertFalse(conflict["ok"])
        self.assertEqual(1, len(board.created_cards))

    async def test_committed_server_error_reconnect_preserves_uncertainty_for_both_names(self):
        for operation in ("create_card", "api:/api/create_card"):
            for via_http in (True, False):
                with self.subTest(operation=operation, via_http=via_http):
                    board = ServerErrorAfterCreateBoard(via_http=via_http)
                    server, state = self.server(board)
                    args = await self.create_arguments(server, operation, "synthetic-http-create")
                    first = (
                        await server._tool_manager.get_tool("call_raw_capability").run(
                            args, convert_result=False
                        )
                    ).structuredContent
                    self.assertFalse(first["ok"])
                    self.assertEqual("compensating", first["status"])
                    run = state["workflow_runs"][first["run_id"]]
                    self.assertEqual("compensating", run["status"])
                    compensation = next(
                        arguments
                        for name, arguments in state["calls"]
                        if name == "workflow_transition" and arguments["status"] == "compensating"
                    )
                    self.assertTrue(compensation["verification"]["outcome_uncertain"])
                    reopened, _ = self.server(board, copy.deepcopy(state))
                    retry = (
                        await reopened._tool_manager.get_tool("call_raw_capability").run(
                            args, convert_result=False
                        )
                    ).structuredContent
                    self.assertEqual(first["run_id"], retry["run_id"])
                    self.assertFalse(retry["ok"])
                    self.assertEqual("compensating", retry["status"])
                    for response in (first, retry):
                        for field in ("meta", "verification"):
                            self.assertTrue(response[field]["outcome_uncertain"])
                            self.assertTrue(response[field]["readback_required"])
                        self.assertIn(
                            "do not use a new idempotency key", response["next_actions"][0]
                        )
                    self.assertEqual(1, len(board.created_cards))

    async def test_known_create_rejections_stay_failed_without_uncertainty(self):
        for operation in ("create_card", "api:/api/create_card"):
            for code in ("validation_error", "auth_required", "permission_denied"):
                with self.subTest(operation=operation, code=code):

                    class RejectedCreateBoard(CreateRecoveryBoard):
                        def create_card(self, **arguments):
                            return {"ok": False, "error": {"code": code}}

                    board = RejectedCreateBoard()
                    server, state = self.server(board)
                    args = await self.create_arguments(
                        server, operation, "synthetic-known-rejection"
                    )
                    result = (
                        await server._tool_manager.get_tool("call_raw_capability").run(
                            args, convert_result=False
                        )
                    ).structuredContent
                    self.assertEqual("failed", result["status"])
                    self.assertEqual("failed", state["workflow_runs"][result["run_id"]]["status"])
                    self.assertFalse(result["verification"]["executor_ok"])
                    self.assertNotIn("outcome_uncertain", result["meta"])
                    self.assertNotIn("readback_required", result["verification"])
                    self.assertEqual(0, len(board.created_cards))

    def test_http_uncertainty_is_scoped_to_exact_post_create_and_server_errors(self):
        cases = (
            ("/api/create_card", "POST", 500, "internal_error", True),
            ("/api/create_card", "post", 503, "synthetic_upstream_failure", True),
            ("/api/create_card", "POST", 400, "validation_error", False),
            ("/api/create_card", "POST", 401, "auth_required", False),
            ("/api/create_card", "POST", 403, "permission_denied", False),
            ("/api/create_card", "GET", 500, "internal_error", False),
            ("/api/create_client", "POST", 500, "internal_error", False),
            ("/api/create_card_extra", "POST", 500, "internal_error", False),
        )
        for path, method, status, code, uncertain in cases:
            with self.subTest(path=path, method=method, status=status):
                client = BoardApiClient("https://crm.example/api", bearer_token="synthetic")
                with patch(
                    "minimal_kanban.mcp.client._urlopen_no_redirect",
                    side_effect=synthetic_http_error(status, code),
                ) as request:
                    result = client._request(path, {"title": "Synthetic"}, method=method)
                self.assertEqual(code, result["error"]["code"])
                self.assertEqual("synthetic-request", result["meta"]["request_id"])
                self.assertEqual(uncertain, result["meta"].get("outcome_uncertain", False))
                self.assertEqual(uncertain, result["meta"].get("readback_required", False))
                request.assert_called_once()

    async def test_alias_requires_exact_readback_and_keeps_live_schema_guard(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                board = CreateRecoveryBoard(mismatch=mismatch)
                server, state = self.server(board)
                args = await self.create_arguments(
                    server, "api:/api/create_card", "synthetic-alias"
                )
                tool = server._tool_manager.get_tool("call_raw_capability")
                stale = (
                    await tool.run({**args, "schema_hash": "0" * 64}, convert_result=False)
                ).structuredContent
                self.assertFalse(stale["ok"])
                self.assertEqual(0, len(board.created_cards))
                self.assertFalse(any(name == "start_workflow" for name, _ in state["calls"]))
                result = (await tool.run(args, convert_result=False)).structuredContent
                self.assertEqual(not mismatch, result["ok"])
                self.assertEqual("compensating" if mismatch else "completed", result["status"])
                self.assertEqual("exact_created_card_readback", result["verification"]["check"])
                self.assertEqual(not mismatch, result["verification"]["passed"])
                self.assertEqual(1, len(board.created_cards))

    async def test_pending_create_reconnect_restores_contract_after_ledger_failure_or_crash(self):
        for operation in ("create_card", "api:/api/create_card"):
            for pending_status in ("executing", "verifying"):
                with self.subTest(operation=operation, status=pending_status):
                    board = CreateRecoveryBoard(lose_response=pending_status == "executing")

                    def interrupt_final_transition(original, arguments):
                        if arguments["status"] == "completed" and pending_status == "verifying":
                            raise SyntheticCrash()
                        if arguments["status"] == "compensating":
                            return {"ok": False, "warnings": ["synthetic_ledger_unavailable"]}
                        return original(**arguments)

                    server, state = self.server(board, transition_hook=interrupt_final_transition)
                    args = await self.create_arguments(
                        server, operation, "synthetic-create-recovery"
                    )
                    tool = server._tool_manager.get_tool("call_raw_capability")
                    if pending_status == "verifying":
                        with self.assertRaises(SyntheticCrash):
                            await tool.run(args, convert_result=False)
                    else:
                        first = (await tool.run(args, convert_result=False)).structuredContent
                        self.assertEqual("executing", first["status"])
                        self.assertTrue(first["meta"]["outcome_uncertain"])
                    self.assertEqual(1, len(board.created_cards))
                    run = next(iter(state["workflow_runs"].values()))
                    self.assertEqual(pending_status, run["status"])
                    reopened, _ = self.server(board, copy.deepcopy(state))
                    retry_tool = reopened._tool_manager.get_tool("call_raw_capability")
                    for _ in range(2):
                        retry = (await retry_tool.run(args, convert_result=False)).structuredContent
                        self.assertFalse(retry["ok"])
                        self.assertEqual(pending_status, retry["status"])
                        for field in ("meta", "verification"):
                            self.assertTrue(retry[field]["outcome_uncertain"])
                            self.assertTrue(retry[field]["readback_required"])
                        self.assertIn("do not use a new idempotency key", retry["next_actions"][0])
                    self.assertEqual(1, len(board.created_cards))

    def test_create_classification_matches_only_exact_operations_and_pending_states(self):
        error = {"ok": False, "error": {"code": "capability_failed"}}
        for operation in ("create_card", "api:/api/create_card"):
            self.assertTrue(_create_card_outcome_unknown(operation, error))
            for status in ("executing", "verifying", "compensating"):
                self.assertTrue(_create_card_requires_reconciliation(operation, status))
            for status in ("planned", "completed", "failed", "cancelled"):
                self.assertFalse(_create_card_requires_reconciliation(operation, status))
            self.assertFalse(
                _create_card_outcome_unknown(operation, {"error": {"code": "validation_error"}})
            )
        for operation in ("create_client", "api:/api/create_client", "api:/api/create_card_extra"):
            self.assertFalse(_create_card_outcome_unknown(operation, error))
            self.assertFalse(_create_card_requires_reconciliation(operation, "executing"))

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
