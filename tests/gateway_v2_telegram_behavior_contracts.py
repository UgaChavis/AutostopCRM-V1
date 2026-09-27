from __future__ import annotations

import copy

# ruff: noqa: E402
if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from minimal_kanban.mcp.oauth_provider import (
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    OwnerAccessToken,
    verify_oauth_audit_assertion,
)
from minimal_kanban.mcp.raw_gateway import verify_virtual_api_write_readback
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.web_assets import TELEGRAM_AGENT_BEHAVIOR


class GatewayV2TelegramBehaviorContractsMixin:
    async def test_telegram_behavior_patch_raw_schema_and_idempotency_boundary(self) -> None:
        route_name = "api:/api/patch_telegram_agent_behavior"
        discovered = await self._call("discover_raw_capabilities", {"query": route_name})
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == route_name
        )
        self.assertEqual(capability["risk"], "write")
        schema = await self._call("get_raw_capability_schema", {"name": route_name})
        properties = schema.structuredContent["data"]["input_schema"]["properties"]
        self.assertEqual(
            schema.structuredContent["summary"]["schema_hash"], capability["schema_hash"]
        )
        self.assertEqual(set(properties), {"expected_revision", "idempotency_key", "operations"})
        self.assertFalse(schema.structuredContent["data"]["input_schema"]["additionalProperties"])
        self.assertEqual(
            set(schema.structuredContent["data"]["input_schema"]["required"]),
            {"expected_revision", "idempotency_key", "operations"},
        )

        base = {
            "name": route_name,
            "arguments": {
                "expected_revision": 0,
                "operations": [
                    {"op": "update_element", "id": "B1", "changes": {"title": "Новый вход"}}
                ],
            },
            "schema_hash": capability["schema_hash"],
            "idempotency_key": "raw-diagram-patch-one",
        }
        mismatch = await self._call(
            "call_raw_capability",
            {
                **base,
                "arguments": {**base["arguments"], "idempotency_key": "different-inner-key"},
            },
        )
        self.assertFalse(mismatch.structuredContent["ok"])
        self.assertIn("raw_write_idempotency_key_mismatch", mismatch.structuredContent["warnings"])
        self.assertEqual(self.board_api.raw_requests, [])

        invalid = await self._call(
            "call_raw_capability",
            {
                **base,
                "arguments": {
                    **base["arguments"],
                    "operations": [
                        {"op": "update_element", "id": "B1", "changes": {"icon": "unknown-icon"}}
                    ],
                },
            },
        )
        self.assertFalse(invalid.structuredContent["ok"])
        self.assertIn("raw_schema_validation_failed", invalid.structuredContent["warnings"])
        self.assertEqual(self.board_api.raw_requests, [])

    async def test_telegram_behavior_patch_raw_readback_requires_exact_graph(self) -> None:
        result = {
            "ok": True,
            "data": {
                "graph": {"elements": [{"id": "B1", "title": "Обновлено"}], "relations": []},
                "revision": 2,
                "applied_revision": 2,
            },
        }
        readback = {
            "ok": True,
            "data": {"graph": copy.deepcopy(result["data"]["graph"]), "revision": 2},
        }

        async def invoke(_name: str, _arguments: dict) -> dict:
            return readback

        verification = await verify_virtual_api_write_readback(
            "api:/api/patch_telegram_agent_behavior", {}, result, invoke
        )
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["check"], "exact_telegram_behavior_graph_readback")
        self.assertEqual(verification["evidence"]["applied_revision"], 2)

        readback["data"]["graph"]["elements"][0]["title"] = "Другая правка"
        mismatch = await verify_virtual_api_write_readback(
            "api:/api/patch_telegram_agent_behavior", {}, result, invoke
        )
        self.assertFalse(mismatch["passed"])
        self.assertFalse(mismatch["evidence"]["graph_exact"])

    async def test_telegram_behavior_patch_flows_through_oauth_mcp_and_exact_readback(self) -> None:
        state = {"status": "planned"}

        def register_fake_ledger(server, _logger) -> None:
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
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata, dry_run
                return {
                    "ok": True,
                    "run_id": 92,
                    "status": state["status"],
                    "summary": {"id": 92, "deduplicated": False},
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
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 92, "status": status, "summary": {"id": 92}}

        base_board_api_type = type(self.board_api)

        class BehaviorBoardApi(base_board_api_type):
            def __init__(self) -> None:
                super().__init__()
                self.graph = copy.deepcopy(TELEGRAM_AGENT_BEHAVIOR)
                self.revision = 0

            def _request(
                self,
                path: str,
                payload: dict | None = None,
                *,
                method: str = "POST",
                extra_headers: dict[str, str] | None = None,
            ) -> dict:
                if path not in {
                    "/api/get_telegram_agent_behavior",
                    "/api/patch_telegram_agent_behavior",
                }:
                    return super()._request(
                        path, payload, method=method, extra_headers=extra_headers
                    )
                self.raw_requests.append(
                    {
                        "path": path,
                        "payload": dict(payload or {}),
                        "method": method,
                        "extra_headers": dict(extra_headers or {}),
                    }
                )
                if path == "/api/patch_telegram_agent_behavior":
                    if payload["expected_revision"] != self.revision:
                        return {"ok": False, "error": {"code": "revision_conflict"}}
                    for operation in payload["operations"]:
                        if operation["op"] == "update_element":
                            node = next(
                                node
                                for node in self.graph["elements"]
                                if node["id"] == operation["id"]
                            )
                            node.update(operation["changes"])
                    self.revision += 1
                    return {
                        "ok": True,
                        "data": {
                            "graph": copy.deepcopy(self.graph),
                            "revision": self.revision,
                            "applied_revision": self.revision,
                            "idempotent_replay": False,
                        },
                    }
                return {
                    "ok": True,
                    "data": {"graph": copy.deepcopy(self.graph), "revision": self.revision},
                }

        self.manager_register.side_effect = register_fake_ledger
        board_api = BehaviorBoardApi()
        agent_token = "diagram-agent-service-token-with-test-entropy-0123456789"
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41839,
            path="/mcp",
            bearer_token=agent_token,
            public_endpoint_url="https://crm.example/mcp",
        )

        async def call(name: str, arguments: dict):
            return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

        owner_token = OwnerAccessToken(
            token="oauth-owner-token",
            client_id="test-client",
            subject="UGA",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context_token = auth_context_var.set(AuthenticatedUser(owner_token))
        try:
            name = "api:/api/patch_telegram_agent_behavior"
            schema = await call("get_raw_capability_schema", {"name": name})
            result = await call(
                "call_raw_capability",
                {
                    "name": name,
                    "arguments": {
                        "expected_revision": 0,
                        "operations": [
                            {
                                "op": "update_element",
                                "id": "B1",
                                "changes": {"title": "Согласовано"},
                            }
                        ],
                    },
                    "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                    "idempotency_key": "oauth-diagram-patch-one",
                },
            )
        finally:
            auth_context_var.reset(context_token)

        self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        self.assertTrue(result.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(
            result.structuredContent["verification"]["check"],
            "exact_telegram_behavior_graph_readback",
        )
        self.assertEqual(
            [call["path"] for call in board_api.raw_requests],
            [
                "/api/patch_telegram_agent_behavior",
                "/api/get_telegram_agent_behavior",
            ],
        )
        request = board_api.raw_requests[0]
        self.assertEqual(request["payload"]["idempotency_key"], "oauth-diagram-patch-one")
        self.assertEqual(request["extra_headers"][OAUTH_AUDIT_ACTOR_HEADER], "UGA")
        self.assertEqual(request["extra_headers"]["X-Autostop-Agent-Token"], agent_token)
        self.assertTrue(
            verify_oauth_audit_assertion(
                subject="UGA",
                method="POST",
                route=request["path"],
                payload=request["payload"],
                assertion=request["extra_headers"][OAUTH_AUDIT_ASSERTION_HEADER],
            )
        )
        self.assertEqual(board_api.graph["elements"][0]["title"], "Согласовано")
