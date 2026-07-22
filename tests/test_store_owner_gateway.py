from __future__ import annotations

import hashlib
import logging
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

from minimal_kanban.mcp.agent_gateway_support import (
    RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID,
    _store_owner_request_error,
)
from minimal_kanban.mcp.agent_gateway_v2 import (
    PERMANENT_AGENT_GATEWAY_TOOL_NAMES,
    _maintenance_technical_write_allowed,
    _release_smoke_proof,
)
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import GATEWAY_ENV, FakeBoardApi


class StoreOwnerGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.ledger_status = "planned"
        self.ledger_version = 1
        self.ledger_transitions: list[str] = []
        self.ledger_checkpoints: list[dict] = []
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def test_owner_request_validation_preserves_exact_guard_codes(self) -> None:
        valid_arguments = {"target_id": "part-1"}
        self.assertIsNone(
            _store_owner_request_error(
                "other_capability",
                {},
                owner_mode="",
                owner_correlation_id="",
            )
        )
        self.assertEqual(
            "store_owner_mode_invalid",
            _store_owner_request_error(
                "store_owner_api",
                valid_arguments,
                owner_mode="unknown",
                owner_correlation_id="owner-correlation-0001",
            ),
        )
        self.assertEqual(
            "store_owner_correlation_id_required_or_invalid",
            _store_owner_request_error(
                "store_owner_api",
                valid_arguments,
                owner_mode="apply",
                owner_correlation_id="short",
            ),
        )
        self.assertEqual(
            "store_owner_exact_target_id_required",
            _store_owner_request_error(
                "store_owner_api",
                {},
                owner_mode="dry_run",
                owner_correlation_id="owner-correlation-0001",
            ),
        )
        self.assertIsNone(
            _store_owner_request_error(
                "store_owner_api",
                valid_arguments,
                owner_mode="apply",
                owner_correlation_id="owner-correlation-0001",
            )
        )

    def _register_store_owner_tools(self, server, _logger) -> None:
        @server.tool(
            name="store_owner_capabilities",
            description="READ_ONLY RAW_CAPABILITY Store owner operation inventory",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )
        def store_owner_capabilities(query: str = "", limit: int = 50) -> dict:
            arguments = {"query": query, "limit": limit}
            self.calls.append(("store_owner_capabilities", arguments))
            return {"ok": True, "items": [{"operation_id": "get_part"}]}

        @server.tool(
            name="store_owner_api",
            description="OWNER_SCOPED RAW_CAPABILITY Store owner transport",
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
        )
        def store_owner_api(
            operation_id: str,
            mode: str = "read",
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
            arguments = {"operation_id": operation_id, "mode": mode}
            self.calls.append(("store_owner_api", arguments))
            binding = {
                "contract_id": "ac_" + "1" * 20,
                "operation_id": operation_id,
                "request_sha256": "2" * 64,
                "schema_hash": "3" * 64,
                "verification_class": "collection_membership",
                "correlation_id": correlation_id,
                "target_ref_sha256": hashlib.sha256(f"target:{target_id}".encode()).hexdigest(),
                "expected_revision_sha256": None,
            }
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
            if mode in {"dry_run", "apply"} and expected_contract_id != binding["contract_id"]:
                return {"ok": False, "status": "blocked"}
            if mode == "apply" and operation_id == "uncertain_part":
                return {
                    "ok": False,
                    "status": "compensating",
                    "meta": {
                        **binding,
                        "write_applied": False,
                        "readback_required": True,
                        "outcome_uncertain": True,
                    },
                }
            if mode == "apply" and operation_id == "http_400_after_dispatch_part":
                return {
                    "ok": False,
                    "status": "compensating",
                    "error": {"code": "store_owner_http_error", "http_status": 400},
                    "meta": {
                        **binding,
                        "request_dispatched": True,
                        "write_applied": False,
                        "readback_required": True,
                        "outcome_uncertain": True,
                        "http_status": 400,
                    },
                }
            if mode == "apply":
                return {
                    "ok": True,
                    "status": "compensating",
                    "meta": {
                        **binding,
                        "write_applied": True,
                        "readback_required": True,
                        "outcome_uncertain": False,
                    },
                }
            if mode == "dry_run":
                return {
                    "ok": True,
                    "status": "planned",
                    "meta": {**binding, "domain_handler_executed": False},
                }
            return {"ok": True, "status": "completed"}

        @server.tool(name="start_workflow")
        def start_workflow(
            workflow_id: str,
            intent: str,
            idempotency_key: str,
            actor: str = "",
            scope: dict | None = None,
            dry_run: bool = False,
            correlation_id: str = "",
        ) -> dict:
            del workflow_id, intent, idempotency_key, actor, scope, dry_run, correlation_id
            self.ledger_status = "planned"
            self.ledger_version = 1
            return {
                "ok": True,
                "run_id": 71,
                "status": self.ledger_status,
                "summary": {"id": 71, "state_version": self.ledger_version},
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
            del run_id, message, verification, summary
            if expected_state_version != self.ledger_version:
                return {"ok": False, "status": self.ledger_status}
            self.ledger_status = status
            self.ledger_version += 1
            self.ledger_transitions.append(status)
            return {
                "ok": True,
                "run_id": 71,
                "status": status,
                "summary": {"id": 71, "state_version": self.ledger_version},
            }

        @server.tool(name="workflow_checkpoint")
        def workflow_checkpoint(
            run_id: int,
            checkpoint: dict,
            selected_ids: list[str] | None = None,
            message: str = "",
            expected_state_version: int | None = None,
        ) -> dict:
            del selected_ids, message
            if expected_state_version != self.ledger_version:
                return {"ok": False, "status": self.ledger_status}
            self.ledger_version += 1
            self.ledger_checkpoints.append(checkpoint)
            return {
                "ok": True,
                "run_id": run_id,
                "status": self.ledger_status,
                "summary": {"id": run_id, "state_version": self.ledger_version},
            }

    def _server(self):
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=self._register_store_owner_tools,
        ):
            return create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41847,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

    async def test_owner_tools_use_discovery_schema_and_guarded_call(self) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")
        schema = server._tool_manager.get_tool("get_raw_capability_schema")
        raw_call = server._tool_manager.get_tool("call_raw_capability")

        discovered = await discover.run({"query": "store_owner"}, convert_result=False)
        capabilities = {
            item["name"]: item for item in discovered.structuredContent["data"]["capabilities"]
        }
        self.assertEqual({"store_owner_capabilities"}, set(capabilities))
        self.assertEqual("read", capabilities["store_owner_capabilities"]["risk"])

        exact_write = await discover.run({"query": "store_owner_api"}, convert_result=False)
        write_capabilities = {
            item["name"]: item for item in exact_write.structuredContent["data"]["capabilities"]
        }
        self.assertEqual({"store_owner_api"}, set(write_capabilities))
        self.assertEqual("write", write_capabilities["store_owner_api"]["risk"])

        read_schema = await schema.run({"name": "store_owner_capabilities"}, convert_result=False)
        write_schema = await schema.run({"name": "store_owner_api"}, convert_result=False)
        self.assertEqual("read", read_schema.structuredContent["summary"]["risk"])
        self.assertEqual("write", write_schema.structuredContent["summary"]["risk"])

        read = await raw_call.run(
            {
                "name": "store_owner_capabilities",
                "arguments": {"query": "parts", "limit": 10},
                "schema_hash": read_schema.structuredContent["summary"]["schema_hash"],
            },
            convert_result=False,
        )
        self.assertTrue(read.structuredContent["ok"])
        self.assertEqual(
            [("store_owner_capabilities", {"query": "parts", "limit": 10})],
            self.calls,
        )

        blocked_write = await raw_call.run(
            {
                "name": "store_owner_api",
                "arguments": {"operation_id": "update_part", "mode": "apply"},
                "schema_hash": write_schema.structuredContent["summary"]["schema_hash"],
            },
            convert_result=False,
        )
        self.assertFalse(blocked_write.structuredContent["ok"])
        self.assertIn(
            "store_owner_correlation_id_required_or_invalid",
            blocked_write.structuredContent["warnings"],
        )
        self.assertFalse(any(name == "store_owner_api" for name, _ in self.calls))

    async def test_owner_apply_remains_compensating_until_operation_specific_reread(self) -> None:
        server = self._server()
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "store_owner_api"}, convert_result=False
        )

        result = await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": "store_owner_api",
                "arguments": {
                    "operation_id": "update_part",
                    "mode": "apply",
                    "target_id": "collection:/api/v1/categories",
                    "correlation_id": "owner-apply-correlation-0001",
                    "idempotency_key": "owner-inner-apply-0001",
                },
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "owner-outer-apply-0001",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual("compensating", result.structuredContent["status"])
        self.assertFalse(result.structuredContent["verification"]["passed"])
        self.assertFalse(result.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(
            "store_owner_operation_specific_exact_readback",
            result.structuredContent["verification"]["check"],
        )
        self.assertEqual(["executing", "compensating"], self.ledger_transitions)

    async def test_owner_dry_run_closes_only_a_refs_only_preflight_workflow(self) -> None:
        server = self._server()
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "store_owner_api"}, convert_result=False
        )

        result = await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": "store_owner_api",
                "arguments": {
                    "operation_id": "update_part",
                    "mode": "dry_run",
                    "target_id": "collection:/api/v1/categories",
                    "correlation_id": "owner-dryrun-correlation-0001",
                    "idempotency_key": "owner-inner-dryrun-0001",
                },
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "owner-outer-dry-run-0001",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual("completed", result.structuredContent["status"])
        self.assertEqual(
            "store_owner_server_dry_run_receipt",
            result.structuredContent["verification"]["check"],
        )
        self.assertEqual(["executing", "verifying", "completed"], self.ledger_transitions)

    async def test_owner_uncertain_transport_remains_compensating(self) -> None:
        server = self._server()
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "store_owner_api"}, convert_result=False
        )
        result = await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": "store_owner_api",
                "arguments": {
                    "operation_id": "uncertain_part",
                    "mode": "apply",
                    "target_id": "collection:/api/v1/categories",
                    "correlation_id": "owner-uncertain-correlation-0001",
                    "idempotency_key": "owner-inner-uncertain-0001",
                },
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "owner-outer-uncertain-0001",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual("compensating", result.structuredContent["status"])
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertEqual(["executing", "compensating"], self.ledger_transitions)

    async def test_owner_http_400_after_dispatch_requires_exact_reconciliation(self) -> None:
        server = self._server()
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "store_owner_api"}, convert_result=False
        )
        result = await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": "store_owner_api",
                "arguments": {
                    "operation_id": "http_400_after_dispatch_part",
                    "mode": "apply",
                    "target_id": "collection:/api/v1/categories",
                    "correlation_id": "owner-http-400-correlation-0001",
                    "idempotency_key": "owner-inner-http-400-0001",
                },
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "owner-outer-http-400-0001",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual("compensating", result.structuredContent["status"])
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertFalse(result.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(
            "store_owner_operation_specific_exact_readback",
            result.structuredContent["verification"]["check"],
        )
        self.assertEqual(["executing", "compensating"], self.ledger_transitions)

    async def test_maintenance_blocks_named_raw_and_ledger_writes_but_allows_owner_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "maintenance"
            marker.touch()
            with patch.dict(
                "os.environ", {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}, clear=False
            ):
                server = self._server()
                schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
                    {"name": "store_owner_api"}, convert_result=False
                )
                raw_call = server._tool_manager.get_tool("call_raw_capability")
                owner_read = await raw_call.run(
                    {
                        "name": "store_owner_api",
                        "arguments": {
                            "operation_id": "get_part",
                            "mode": "read",
                            "correlation_id": "owner-read-correlation-0001",
                        },
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                    },
                    convert_result=False,
                )
                owner_apply = await raw_call.run(
                    {
                        "name": "store_owner_api",
                        "arguments": {
                            "operation_id": "update_part",
                            "mode": "apply",
                            "target_id": "collection:/api/v1/categories",
                            "correlation_id": "owner-apply-correlation-0002",
                            "idempotency_key": "owner-inner-apply-0002",
                        },
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                        "idempotency_key": "owner-outer-apply-0002",
                    },
                    convert_result=False,
                )
                lifecycle = await server._tool_manager.get_tool("start_workflow").run(
                    {
                        "workflow_id": "maintenance-test",
                        "intent": "maintenance_test",
                        "idempotency_key": "maintenance-ledger-0001",
                    },
                    convert_result=False,
                )
                named = await server._tool_manager.get_tool("agent_inventory_workflow").run(
                    {
                        "operation": "list_inventory_items",
                        "payload": {},
                        "idempotency_key": "maintenance-inventory-0001",
                    },
                    convert_result=False,
                )

        self.assertTrue(owner_read.structuredContent["ok"])
        self.assertIn(
            "maintenance_mode_raw_write_blocked", owner_apply.structuredContent["warnings"]
        )
        self.assertIn(
            "maintenance_mode_workflow_ledger_write_blocked",
            lifecycle.structuredContent["warnings"],
        )
        self.assertIn("maintenance_mode_domain_writes_blocked", named.structuredContent["warnings"])

    def test_public_gateway_surface_remains_exactly_24(self) -> None:
        server = self._server()

        self.assertEqual(24, len(PERMANENT_AGENT_GATEWAY_TOOL_NAMES))
        self.assertLessEqual(set(server._tool_manager._tools), PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
        self.assertNotIn("store_owner_capabilities", server._tool_manager._tools)
        self.assertNotIn("store_owner_api", server._tool_manager._tools)

    def test_maintenance_proof_allows_only_exact_release_smoke_writes(self) -> None:
        token = "technical-smoke-token"
        revision = "a" * 40
        proof = _release_smoke_proof(token, revision)

        for capability, arguments in (
            (
                "api:/api/change_feed/bootstrap",
                {"consumer_id": RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID},
            ),
            (
                "api:/api/change_feed/ack",
                {"consumer_id": RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID},
            ),
            ("store_owner_api", {"mode": "dry_run"}),
        ):
            self.assertTrue(
                _maintenance_technical_write_allowed(
                    capability=capability,
                    arguments=arguments,
                    revision=revision,
                    proof=proof,
                    agent_bearer_token=token,
                )
            )

        self.assertFalse(
            _maintenance_technical_write_allowed(
                capability="store_owner_api",
                arguments={"mode": "apply"},
                revision=revision,
                proof=proof,
                agent_bearer_token=token,
            )
        )
        for capability in (
            "api:/api/change_feed/bootstrap",
            "api:/api/change_feed/ack",
        ):
            for consumer_id in (None, "", "owner", "arbitrary-consumer"):
                with self.subTest(capability=capability, consumer_id=consumer_id):
                    self.assertFalse(
                        _maintenance_technical_write_allowed(
                            capability=capability,
                            arguments={"consumer_id": consumer_id},
                            revision=revision,
                            proof=proof,
                            agent_bearer_token=token,
                        )
                    )
        self.assertFalse(
            _maintenance_technical_write_allowed(
                capability="api:/api/change_feed/bootstrap",
                arguments={},
                revision=revision,
                proof="0" * 64,
                agent_bearer_token=token,
            )
        )


if __name__ == "__main__":
    unittest.main()
