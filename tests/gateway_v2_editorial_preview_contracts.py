from __future__ import annotations

import json
from unittest.mock import patch

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server


class GatewayV2EditorialPreviewContractsMixin:
    async def test_editorial_board_preview_preserves_fields_and_warns_if_incomplete(self) -> None:
        state = {"status": "planned"}

        def register_ledger(server, _logger):
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
                return {
                    "ok": True,
                    "run_id": 91,
                    "status": state["status"],
                    "summary": {"id": 91, "deduplicated": False},
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
                state["status"] = status
                return {"ok": True, "run_id": run_id, "status": status, "summary": {}}

        self.manager_register.side_effect = register_ledger
        board_api = type(self.board_api)()
        long_text = "a" * 5000
        response = {
            "ok": True,
            "data": {
                "items": [
                    {
                        "planned_fields": ["title", "description"],
                        "card": {"title": "New title", "description": long_text},
                    }
                ],
                "verification": {"passed": True},
            },
        }
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
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
            with patch.object(
                board_api, "run_manager_operation", return_value=response
            ) as operation:
                result = await server._tool_manager.get_tool("agent_board_workflow").run(
                    {
                        "operation": "cleanup_card",
                        "payload": {
                            "content_only": True,
                            "card_id": "synthetic",
                            "expected_updated_at": "revision",
                            "description": long_text,
                        },
                        "idempotency_key": "preview-1",
                        "mode": "dry_run",
                    },
                    convert_result=False,
                )
            self.assertEqual(operation.call_args.kwargs["payload"]["response_mode"], "full")
            self.assertTrue(result.structuredContent["ok"])
            self.assertIn(
                '"planned_fields": ["title", "description"]', json.dumps(result.structuredContent)
            )
            self.assertIn(long_text, json.dumps(result.structuredContent))
            self.assertNotIn(
                "editorial_preview_incomplete_do_not_apply", result.structuredContent["warnings"]
            )

            state["status"] = "planned"
            response["data"]["items"][0]["card"]["description"] = "b" * 20001
            with patch.object(board_api, "run_manager_operation", return_value=response):
                truncated = await server._tool_manager.get_tool("agent_board_workflow").run(
                    {
                        "operation": "cleanup_card",
                        "payload": {
                            "content_only": True,
                            "card_id": "synthetic",
                            "expected_updated_at": "revision",
                            "description": "x",
                        },
                        "idempotency_key": "preview-2",
                        "mode": "dry_run",
                    },
                    convert_result=False,
                )
            self.assertIn(
                "editorial_preview_incomplete_do_not_apply", truncated.structuredContent["warnings"]
            )
            self.assertTrue(truncated.structuredContent["next_actions"])
        finally:
            auth_context_var.reset(context_token)
