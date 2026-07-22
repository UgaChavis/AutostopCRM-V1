from __future__ import annotations

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from minimal_kanban.mcp.oauth_provider import (
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    OwnerAccessToken,
    verify_oauth_audit_assertion,
)
from minimal_kanban.mcp.server import create_mcp_server


class GatewayV2OAuthContractTestsMixin:
    async def test_oauth_owner_is_recorded_for_raw_virtual_write(self) -> None:
        state = {"status": "planned", "actor": ""}

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
                del workflow_id, intent, idempotency_key, query, scope, metadata, dry_run
                state["actor"] = actor
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 78,
                    "status": state["status"],
                    "summary": {"id": 78, "deduplicated": False},
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
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 78,
                    "status": status,
                    "summary": {"id": 78, "status": status},
                }

        self.manager_register.side_effect = register_fake_ledger
        board_api = type(self.board_api)()
        agent_token = "agent-service-token-with-strong-test-entropy-0123456789"
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41833,
            path="/mcp",
            bearer_token=agent_token,
            public_endpoint_url="https://crm.example/mcp",
        )

        async def call(name: str, arguments: dict):
            return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

        owner_token = OwnerAccessToken(
            token="oauth-owner-token",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context_token = auth_context_var.set(AuthenticatedUser(owner_token))
        try:
            discovered = await call(
                "discover_raw_capabilities", {"query": "api:/api/copy_shared_file"}
            )
            capability = next(
                item
                for item in discovered.structuredContent["data"]["capabilities"]
                if item["name"] == "api:/api/copy_shared_file"
            )
            result = await call(
                "call_raw_capability",
                {
                    "name": capability["name"],
                    "arguments": {
                        "file_id": "shared-file-1",
                        "actor_name": "spoofed-human",
                        "source": "ui",
                    },
                    "schema_hash": capability["schema_hash"],
                    "idempotency_key": "copy-shared-file-oauth-owner-v1",
                },
            )
        finally:
            auth_context_var.reset(context_token)

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(state["actor"], "CODEX")
        self.assertEqual(len(board_api.raw_requests), 1)
        request = board_api.raw_requests[0]
        self.assertEqual(request["payload"]["actor_name"], "CODEX")
        self.assertEqual(request["extra_headers"]["X-Autostop-Agent-Identity"], "codex-owner-agent")
        self.assertEqual(request["extra_headers"]["X-Autostop-Agent-Token"], agent_token)
        self.assertEqual(request["extra_headers"][OAUTH_AUDIT_ACTOR_HEADER], "CODEX")
        self.assertTrue(
            verify_oauth_audit_assertion(
                subject="CODEX",
                method="POST",
                route=request["path"],
                payload=request["payload"],
                assertion=request["extra_headers"][OAUTH_AUDIT_ASSERTION_HEADER],
            )
        )
