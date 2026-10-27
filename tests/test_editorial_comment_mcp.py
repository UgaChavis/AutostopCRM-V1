from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import os
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.errors import ServiceError
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV
from tests.test_mcp_registration_contracts import _build_builtin_raw_server


class EditorialCommentNamedMcpTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.card = self.service.create_card({"title": "Synthetic repair"})["card"]
        bundle = self.store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == self.card["id"])
        card.archived = True
        card.repair_order = RepairOrder.from_dict(
            {
                "number": "SYN-1",
                "status": "closed",
                "comment": "Before synthetic comment",
                "note": "Preserve internal note",
                "works": [
                    {
                        "id": "w1",
                        "name": "Work",
                        "quantity": "2",
                        "price": "100",
                        "executor_id": "worker-1",
                        "salary_amount": "20",
                    }
                ],
                "materials": [
                    {
                        "id": "m1",
                        "name": "Part",
                        "quantity": "1",
                        "price": "30",
                        "inventory_item_id": "stock-1",
                        "inventory_movement_id": "move-1",
                    }
                ],
                "payments": [{"id": "p1", "amount": "230", "cashbox_id": "cashbox-1"}],
            }
        )
        self.store.write_bundle(**bundle)
        service = self.service
        self.calls = {"ledger": 0, "backend": 0}
        calls = self.calls

        class ServiceBoardApi(FakeBoardApi):
            def run_manager_operation(
                self, *, operation, payload=None, mode="dry_run", actor_name=None, limit=None
            ):
                del limit
                calls["backend"] += 1
                try:
                    result = service.run_manager_operation(
                        {
                            "operation": operation,
                            "payload": payload,
                            "mode": mode,
                            "actor_name": actor_name,
                        }
                    )
                    return {"ok": True, "data": result}
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic error"}}

        def fake_ledger(server, _logger):
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
                calls["ledger"] += 1
                return {
                    "ok": True,
                    "run_id": 91,
                    "status": "planned",
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
                return {"ok": True, "run_id": run_id, "status": status, "summary": {}}

        self.env = patch.dict(
            "os.environ",
            {**GATEWAY_ENV, "AUTOSTOP_CRM_REPAIR_ORDER_COMMENT_EDITORIAL_ENABLED": "1"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        manager = patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=fake_ledger,
        )
        manager.start()
        self.addCleanup(manager.stop)
        self.board_api = ServiceBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def call_workflow(self, payload, *, mode, key):
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context = auth_context_var.set(AuthenticatedUser(token))
        try:
            return await self.server._tool_manager.get_tool("agent_board_workflow").run(
                {
                    "operation": "cleanup_card",
                    "payload": payload,
                    "mode": mode,
                    "idempotency_key": key,
                },
                convert_result=False,
            )
        finally:
            auth_context_var.reset(context)

    async def test_comment_preview_and_apply_preserve_order_contents(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        before_card = next(card for card in before["cards"] if card.id == self.card["id"])
        before_order = before_card.repair_order.to_storage_dict()
        payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "repair_order_comment": "New synthetic text\nsecond line",
        }
        preview = await self.call_workflow(payload, mode="dry_run", key="comment-preview")
        self.assertTrue(preview.structuredContent["ok"], preview.structuredContent)
        item = preview.structuredContent["data"]["data"]["result"]["items"][0]
        self.assertEqual(item["planned_fields"], ["repair_order_comment"])
        self.assertEqual(item["card"]["repair_order"]["comment"], payload["repair_order_comment"])
        self.assertEqual(
            item["card"]["repair_order"]["client_information"], payload["repair_order_comment"]
        )
        self.assertEqual(self.store.read_bundle(), before)
        self.assertEqual(self.state_file.read_bytes(), before_bytes)
        applied = await self.call_workflow(payload, mode="apply", key="comment-apply")
        self.assertTrue(applied.structuredContent["ok"], applied.structuredContent)
        after = self.store.read_bundle()
        after_card = next(card for card in after["cards"] if card.id == self.card["id"])
        after_order = after_card.repair_order.to_storage_dict()
        self.assertEqual(after_order["comment"], payload["repair_order_comment"])
        after_order["comment"] = before_order["comment"]
        self.assertEqual(after_order, before_order)
        self.assertEqual(after_card.column, before_card.column)
        self.assertEqual(after_card.archived, before_card.archived)
        for domain in (
            "clients",
            "cashboxes",
            "cash_transactions",
            "inventory_items",
            "inventory_movements",
        ):
            self.assertEqual(after[domain], before[domain], domain)

    async def test_conflicting_mode_rejected_before_ledger_and_backend(self):
        before = deepcopy(self.store.read_bundle())
        payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "repair_order_comment": "Never written",
            "mode": "apply",
        }
        result = await self.call_workflow(payload, mode="dry_run", key="comment-conflict")
        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("board_workflow_mode_conflict", result.structuredContent["warnings"])
        self.assertEqual(self.calls, {"ledger": 0, "backend": 0})
        self.assertEqual(self.store.read_bundle(), before)

    async def test_default_disabled_comment_rejects_preview_and_apply_without_writing(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "repair_order_comment": "Never written",
        }
        env_key = "AUTOSTOP_CRM_REPAIR_ORDER_COMMENT_EDITORIAL_ENABLED"
        with patch.dict(os.environ):
            for setting in (None, "0", "true"):
                if setting is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = setting
                for mode in ("dry_run", "apply"):
                    with self.subTest(mode=mode, setting=setting):
                        result = await self.call_workflow(
                            payload, mode=mode, key=f"disabled-{mode}-{setting}"
                        )
                        self.assertFalse(result.structuredContent["ok"], result.structuredContent)
                        self.assertEqual(result.structuredContent["status"], "blocked")
                        self.assertIn(
                            "editorial_comment_disabled", result.structuredContent["warnings"]
                        )
                        self.assertEqual(self.calls, {"ledger": 0, "backend": 0})
                        self.assertEqual(self.store.read_bundle(), before)
                        self.assertEqual(self.state_file.read_bytes(), before_bytes)

    async def test_default_disabled_raw_calls_reject_before_ledger_and_backend(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context = auth_context_var.set(AuthenticatedUser(token))
        try:
            with patch.dict(os.environ):
                os.environ.pop("AUTOSTOP_CRM_REPAIR_ORDER_COMMENT_EDITORIAL_ENABLED", None)
                with patch.object(self.board_api, "cleanup_card", create=True) as direct:
                    for name in (
                        "cleanup_card",
                        "run_manager_operation",
                        "api:/api/cleanup_card",
                        "api:/api/run_manager_operation",
                    ):
                        schema = await self.server._tool_manager.get_tool(
                            "get_raw_capability_schema"
                        ).run({"name": name}, convert_result=False)
                        self.assertTrue(schema.structuredContent["ok"], schema.structuredContent)
                        schema_hash = schema.structuredContent["summary"]["schema_hash"]
                        for mode in ("dry_run", "apply"):
                            with self.subTest(name=name, mode=mode):
                                patch_payload = {
                                    "card_id": self.card["id"],
                                    "content_only": True,
                                    "expected_updated_at": self.card["updated_at"],
                                    "idempotency_key": "synthetic-raw",
                                    "repair_order_comment": "Never written",
                                }
                                arguments = (
                                    {
                                        "operation": "cleanup_card",
                                        "mode": mode,
                                        "payload": patch_payload,
                                    }
                                    if name.endswith("run_manager_operation")
                                    else {**patch_payload, "mode": mode}
                                )
                                result = await self.server._tool_manager.get_tool(
                                    "call_raw_capability"
                                ).run(
                                    {
                                        "name": name,
                                        "arguments": arguments,
                                        "schema_hash": schema_hash,
                                        "idempotency_key": f"disabled-{name}-{mode}",
                                    },
                                    convert_result=False,
                                )
                                self.assertFalse(
                                    result.structuredContent["ok"], result.structuredContent
                                )
                                self.assertEqual(result.structuredContent["status"], "blocked")
                                self.assertIn(
                                    "editorial_comment_disabled",
                                    result.structuredContent["warnings"],
                                )
                                self.assertEqual(self.calls, {"ledger": 0, "backend": 0})
                                self.assertEqual(self.board_api.raw_requests, [])
                                self.assertEqual(direct.call_count, 0)
                                self.assertEqual(self.store.read_bundle(), before)
                                self.assertEqual(self.state_file.read_bytes(), before_bytes)
        finally:
            auth_context_var.reset(context)


class EditorialCommentRawMcpTests(IsolatedAsyncioTestCase):
    async def test_raw_schema_and_mcp_forward_empty_comment(self):
        server, _ = _build_builtin_raw_server()
        tool = server._tool_manager.get_tool("cleanup_card")
        self.assertIn("repair_order_comment", tool.parameters["properties"])
        with patch.object(
            BoardApiClient, "cleanup_card", return_value={"ok": True, "data": {}}
        ) as call:
            result = await tool.run(
                {
                    "card_id": "synthetic",
                    "content_only": True,
                    "expected_updated_at": "rev",
                    "idempotency_key": "key",
                    "repair_order_comment": "",
                },
                convert_result=False,
            )
        self.assertTrue(result.ok)
        self.assertEqual(call.call_args.kwargs["repair_order_comment"], "")

    def test_http_client_forwards_empty_comment_without_legacy_summary(self):
        client = BoardApiClient("https://board.example/api", bearer_token="synthetic")
        with patch.object(client, "_request_with_identity", return_value={}) as request:
            client.cleanup_card(
                card_id="synthetic",
                content_only=True,
                idempotency_key="key",
                expected_updated_at="rev",
                mode="dry_run",
                refresh_summary=False,
                repair_order_comment="",
            )
        self.assertEqual(request.call_args.args[1]["repair_order_comment"], "")
        self.assertNotIn("refresh_summary", request.call_args.args[1])
