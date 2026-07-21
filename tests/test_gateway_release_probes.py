from __future__ import annotations

import hashlib
import logging
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minimal_kanban.mcp.raw_gateway import (  # noqa: E402
    CHANGE_FEED_ACK_ROUTE,
    CHANGE_FEED_BOOTSTRAP_ROUTE,
    CHANGE_FEED_READ_ROUTE,
)
from minimal_kanban.mcp.server import create_mcp_server  # noqa: E402
from minimal_kanban.storage.change_feed_store import ChangeFeedStore  # noqa: E402
from scripts import check_agent_gateway_v2  # noqa: E402
from tests.test_change_feed_gateway import GATEWAY_ENV  # noqa: E402


class ReleaseProbeBoardApi:
    base_url = "http://127.0.0.1:41731"

    def __init__(self) -> None:
        self.acked_sequence = 0
        self.requests: list[tuple[str, dict]] = []

    def health(self) -> dict:
        return {"ok": True, "data": {"status": "healthy"}}

    def get_board_context(self) -> dict:
        return {"ok": True, "data": {"context": {}}}

    def get_cards(self, **_: object) -> dict:
        return {"ok": True, "data": {"cards": []}}

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        del method, extra_headers
        request = dict(payload or {})
        self.requests.append((path, request))
        consumer_id = request.get("consumer_id")
        if path == CHANGE_FEED_BOOTSTRAP_ROUTE:
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_bootstrap_v1",
                    "generation": "release-generation-1",
                    "consumer_id": consumer_id,
                    "high_water": 1,
                    "acked_sequence": self.acked_sequence,
                    "pending_high_water": 1 if self.acked_sequence == 0 else None,
                    "has_unacked": self.acked_sequence == 0,
                },
            }
        if path == CHANGE_FEED_READ_ROUTE:
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_page_v1",
                    "generation": "release-generation-1",
                    "consumer_id": consumer_id,
                    "high_water": 1,
                    "delivery_high_water": 1,
                    "acked_sequence": 0,
                    "from_sequence": 1,
                    "through_sequence": 1,
                    "events": [
                        {
                            "sequence": 1,
                            "event_id": "event-1",
                            "occurred_at": "2026-07-21T00:00:00+00:00",
                            "action": "card_updated",
                            "entity_type": "card",
                            "entity_id": "card:opaque",
                            "change_type": "update",
                            "tombstone": False,
                            "correlation_ref": "corr:opaque",
                            "idempotency_ref": "idem:opaque",
                            "producer": "audit_event",
                        }
                    ],
                    "replay_cursor": "release-replay-token",
                    "next_cursor": None,
                    "ack": "release-ack-token",
                    "caught_up": True,
                },
            }
        if path == CHANGE_FEED_ACK_ROUTE:
            if request.get("ack") != "release-ack-token":
                return {"ok": False, "error": {"code": "invalid_ack"}}
            self.acked_sequence = 1
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_ack_v1",
                    "generation": "release-generation-1",
                    "consumer_id": consumer_id,
                    "high_water": 1,
                    "acked_sequence": 1,
                    "changed": True,
                    "delivery_complete": True,
                },
            }
        return {"ok": False, "error": {"code": "unexpected_route"}}


class InProcessGatewaySession:
    def __init__(self, server) -> None:
        self.server = server

    async def call_tool(self, name: str, arguments: dict):
        tool = self.server._tool_manager.get_tool(name)
        if tool is None:
            raise RuntimeError(f"missing public tool: {name}")
        return await tool.run(arguments, convert_result=False)


class GatewayReleaseProbeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.gateway.release.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()
        self.workflow_version = 0
        self.next_run_id = 100
        self.owner_modes: list[str] = []

    def tearDown(self) -> None:
        self.env.stop()

    def _register_raw_tools(self, server, _logger) -> None:
        @server.tool(
            name="store_owner_capabilities",
            description="READ_ONLY RAW_CAPABILITY Store owner operation inventory",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )
        def store_owner_capabilities(query: str = "", limit: int = 200) -> dict:
            del query, limit
            return {
                "ok": True,
                "status": "completed",
                "items": [
                    {
                        "operation_id": "create_category_api_v1_categories_post",
                        "method": "POST",
                        "path": "/api/v1/categories",
                        "risk": "write",
                        "request_content_types": ["application/json"],
                        "request_required": True,
                        "path_parameters": [],
                        "schema_hash": "store-category-schema-v1",
                    }
                ],
            }

        @server.tool(
            name="store_owner_api",
            description="OWNER_SCOPED RAW_CAPABILITY Store owner transport",
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
        )
        def store_owner_api(
            operation_id: str,
            mode: str = "dry_run",
            target_id: str = "",
            path_parameters: dict | None = None,
            query: dict | None = None,
            body: object = None,
            form: dict | None = None,
            files: list[dict] | None = None,
            owner_intent: str = "",
            idempotency_key: str = "",
            correlation_id: str = "",
            expected_revision: str | None = None,
            expected_contract_id: str | None = None,
            dry_run_proof: str | None = None,
            allow_binary_response: bool = False,
            prepare_for_mode: str = "dry_run",
        ) -> dict:
            del (
                path_parameters,
                query,
                body,
                form,
                files,
                owner_intent,
                idempotency_key,
                expected_revision,
                dry_run_proof,
                allow_binary_response,
            )
            self.owner_modes.append(mode)
            binding = {
                "contract_id": "ac_" + "4" * 20,
                "operation_id": operation_id,
                "request_sha256": "5" * 64,
                "schema_hash": "6" * 64,
                "verification_class": "collection_membership",
                "correlation_id": correlation_id,
                "target_ref_sha256": hashlib.sha256(f"target:{target_id}".encode()).hexdigest(),
                "expected_revision_sha256": None,
            }
            if mode == "apply":
                raise AssertionError("release probe must never apply")
            if mode == "prepare":
                return {
                    "ok": True,
                    "status": "validated",
                    "summary": {
                        "request_dispatched": False,
                        "prepared_for_mode": prepare_for_mode,
                    },
                    "meta": {
                        **binding,
                        "request_dispatched": False,
                        "domain_handler_executed": False,
                    },
                }
            if mode == "revision":
                return {
                    "ok": True,
                    "status": "completed",
                    "summary": {
                        "operation_id": operation_id,
                        "method": "POST",
                        "path": "/api/v1/categories",
                        "route_key": "POST /api/v1/categories",
                        "current_revision": None,
                        "revision_kind": "revision_exempt",
                        "expected_revision_required": False,
                        "contract_version": "store-owner-preflight-v1",
                    },
                    "meta": {"readback_required": False},
                }
            if expected_contract_id != binding["contract_id"]:
                return {"ok": False, "status": "blocked"}
            return {
                "ok": True,
                "status": "planned",
                "summary": {
                    "operation_id": operation_id,
                    "method": "POST",
                    "path": "/api/v1/categories",
                    "current_revision": None,
                    "revision_kind": "revision_exempt",
                    "contract_version": "store-owner-preflight-v1",
                    "dry_run_proof": "d" * 64,
                },
                "meta": {
                    **binding,
                    "request_dispatched": True,
                    "outcome_uncertain": False,
                    "domain_handler_executed": False,
                },
            }

        @server.tool(name="start_workflow")
        def start_workflow(
            workflow_id: str,
            intent: str,
            idempotency_key: str,
            query: str = "",
            actor: str = "",
            scope: dict | None = None,
            metadata: dict | None = None,
            dry_run: bool = False,
            correlation_id: str = "",
        ) -> dict:
            del (
                workflow_id,
                intent,
                idempotency_key,
                query,
                actor,
                scope,
                metadata,
                dry_run,
                correlation_id,
            )
            self.next_run_id += 1
            self.workflow_version = 1
            return {
                "ok": True,
                "run_id": self.next_run_id,
                "status": "planned",
                "summary": {
                    "id": self.next_run_id,
                    "deduplicated": False,
                    "state_version": self.workflow_version,
                },
            }

        @server.tool(name="workflow_transition")
        def workflow_transition(
            run_id: int,
            status: str,
            message: str = "",
            verification: dict | None = None,
            summary: str = "",
            expected_state_version: int | None = None,
        ) -> dict:
            del message, verification, summary
            if expected_state_version != self.workflow_version:
                return {"ok": False, "run_id": run_id, "status": "blocked"}
            self.workflow_version += 1
            return {
                "ok": True,
                "run_id": run_id,
                "status": status,
                "summary": {"id": run_id, "state_version": self.workflow_version},
            }

        @server.tool(name="workflow_checkpoint")
        def workflow_checkpoint(
            run_id: int,
            checkpoint: dict,
            selected_ids: list[str] | None = None,
            message: str = "",
            expected_state_version: int | None = None,
        ) -> dict:
            del checkpoint, selected_ids, message
            if expected_state_version != self.workflow_version:
                return {"ok": False, "run_id": run_id, "status": "blocked"}
            self.workflow_version += 1
            return {
                "ok": True,
                "run_id": run_id,
                "status": "executing",
                "summary": {"id": run_id, "state_version": self.workflow_version},
            }

    def _server(self, board_api: ReleaseProbeBoardApi):
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=self._register_raw_tools,
        ):
            return create_mcp_server(
                board_api,
                self.logger,
                host="127.0.0.1",
                port=41848,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

    async def test_release_probes_cross_the_real_raw_gateway_and_ledger(self) -> None:
        board_api = ReleaseProbeBoardApi()
        session = InProcessGatewaySession(self._server(board_api))
        calls: dict[str, bool] = {}

        owner = await check_agent_gateway_v2._run_store_owner_probes(
            session,
            calls,
            smoke_id="5" * 32,
        )
        feed = await check_agent_gateway_v2._run_change_feed_probes(
            session,
            calls,
            smoke_id="6" * 32,
        )

        self.assertTrue(owner["ok"])
        self.assertTrue(feed["ok"])
        self.assertEqual(["revision", "prepare", "dry_run"], self.owner_modes)
        self.assertNotIn("apply", self.owner_modes)
        self.assertEqual(1, board_api.acked_sequence)
        self.assertTrue(all(calls.values()))
        self.assertTrue(
            {"discover_raw_capabilities", "get_raw_capability_schema", "call_raw_capability"}
            <= set(calls)
        )


class GatewayReleaseSmokeConsumerContractTests(unittest.TestCase):
    def test_stable_consumer_is_bounded_replay_safe_and_empty_without_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "change_feed.sqlite3"
            store = ChangeFeedStore(database)
            store.initialize_baseline([])
            consumer_id = check_agent_gateway_v2.CHANGE_FEED_SMOKE_CONSUMER_ID

            initial = store.bootstrap(consumer_id)
            self.assertEqual(0, initial["high_water"])
            self.assertFalse(initial["has_unacked"])
            empty = store.read_page(
                consumer_id,
                limit=check_agent_gateway_v2.CHANGE_FEED_SMOKE_PAGE_LIMIT,
            )
            self.assertEqual([], empty["events"])
            self.assertIsNone(empty["ack"])
            self.assertTrue(empty["caught_up"])

            event = {
                "id": "release-smoke-event-1",
                "timestamp": "2026-07-21T00:00:00+00:00",
                "actor_name": "OWNER",
                "source": "api",
                "action": "card_updated",
                "message": "technical contract event",
                "details": {},
                "card_id": "card-opaque-1",
            }
            store.prepare_state_write("release-smoke-state-1", [event])
            store.commit_state_write("release-smoke-state-1")

            page = store.read_page(
                consumer_id,
                limit=check_agent_gateway_v2.CHANGE_FEED_SMOKE_PAGE_LIMIT,
            )
            replay = store.read_page(
                consumer_id,
                cursor=page["replay_cursor"],
                limit=check_agent_gateway_v2.CHANGE_FEED_SMOKE_PAGE_LIMIT,
            )
            self.assertEqual(page, replay)
            acknowledged = store.acknowledge(consumer_id, page["ack"])
            self.assertTrue(acknowledged["delivery_complete"])
            self.assertEqual(1, acknowledged["acked_sequence"])

            for _ in range(3):
                checkpoint = store.bootstrap(consumer_id)
                self.assertFalse(checkpoint["has_unacked"])
                self.assertIsNone(checkpoint["pending_high_water"])
                caught_up = store.read_page(
                    consumer_id,
                    limit=check_agent_gateway_v2.CHANGE_FEED_SMOKE_PAGE_LIMIT,
                )
                self.assertEqual([], caught_up["events"])
                self.assertIsNone(caught_up["ack"])

            with sqlite3.connect(database) as connection:
                consumers = connection.execute(
                    "SELECT consumer_id, acked_sequence FROM consumers"
                ).fetchall()
                deliveries = connection.execute("SELECT consumer_id FROM deliveries").fetchall()
            self.assertEqual([(consumer_id, 1)], consumers)
            self.assertEqual([], deliveries)
            self.assertEqual(0o600, stat.S_IMODE(database.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
