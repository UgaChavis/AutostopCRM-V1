from __future__ import annotations

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from tests.services_case import CardServiceCase
from minimal_kanban.mcp.agent_gateway_support import _compact_object
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class EditorialPreviewGatewayTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.card = self.service.create_card(
            {"title": "Synthetic repair", "description": "Old text"}
        )["card"]

    def payload(self, **changes):
        return {
            "card_id": self.card["id"],
            "content_only": True,
            "mode": "dry_run",
            "actor_name": "DIRECTOR",
            "source": "mcp",
            "response_mode": "full",
            "expected_updated_at": self.card["updated_at"],
            "idempotency_key": "synthetic-preview",
            "description": "New " + "x" * 6000,
            "title": "Updated synthetic repair",
            "vehicle_profile": {
                "engine_model": "Candidate",
                "tentative_fields": ["engine_model"],
                "field_sources": {"engine_model": "Synthetic evidence"},
            },
            **changes,
        }

    def test_preview_preserves_fields_and_values_without_writing(self):
        before = deepcopy(self.store.read_bundle())
        request = self.payload()
        result = self.service.cleanup_card(request)
        # Raw MCP capability wraps the service result in data before the named
        # workflow compacts it; this nesting caused the production regression.
        compact = _compact_object(
            {"ok": True, "data": result},
            max_depth=12,
            item_limit=500,
            key_limit=500,
            string_limit=20000,
        )
        item = compact["data"]["items"][0]
        self.assertEqual(set(item["planned_fields"]), {"title", "description", "vehicle_profile"})
        self.assertEqual(item["card"]["title"], request["title"])
        self.assertEqual(item["card"]["description"], request["description"])
        self.assertEqual(item["card"]["vehicle_profile"]["engine_model"], "Candidate")
        self.assertEqual(item["card"]["vehicle_profile"]["tentative_fields"], ["engine_model"])
        self.assertEqual(result["changed"], 0)
        self.assertEqual(before, self.store.read_bundle())

    def test_forbidden_fields_and_stale_revision_are_atomic(self):
        before = deepcopy(self.store.read_bundle())
        for fields in (
            {"deadline": {"hours": 2}},
            {"repair_order": {"works": []}},
            {"vehicle_profile": {"customer_name": "Forbidden"}},
            {"expected_updated_at": "stale"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ServiceError):
                self.service.cleanup_card(self.payload(**fields))
            self.assertEqual(before, self.store.read_bundle())

    def test_direct_manager_dispatch_rejects_conflicting_modes_before_writing(self):
        before = deepcopy(self.store.read_bundle())
        payload = self.payload()
        payload.pop("mode")
        for outer, inner in (("dry_run", "apply"), ("apply", "dry_run")):
            for field in ("mode", "apply_mode"):
                with self.subTest(outer=outer, field=field):
                    with self.assertRaises(ServiceError) as error:
                        self.service.run_manager_operation(
                            {
                                "operation": "cleanup_card",
                                "mode": outer,
                                "payload": {**payload, field: inner},
                                "actor_name": "DIRECTOR",
                            }
                        )
                    self.assertEqual(error.exception.code, "validation_error")
                    self.assertEqual(error.exception.details, {"field": "mode"})
                    self.assertEqual(before, self.store.read_bundle())

    def test_direct_manager_dispatch_accepts_matching_apply_mode_aliases(self):
        before = deepcopy(self.store.read_bundle())
        for outer, alias in (("dry_run", "preview"), ("apply", "commit")):
            with self.subTest(outer=outer):
                result = self.service.run_manager_operation(
                    {
                        "operation": "bulk_set_deadline_if_below",
                        "mode": outer,
                        "payload": {"apply_mode": alias, "card_ids": ["missing"]},
                        "actor_name": "DIRECTOR",
                    }
                )
                self.assertEqual(result["result"]["run"]["mode"], outer)
                self.assertEqual(before, self.store.read_bundle())


class EditorialNamedWorkflowTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.card = self.service.create_card(
            {"title": "Synthetic repair", "description": "Old text"}
        )["card"]
        service = self.service
        self.calls = {"ledger_start": 0, "backend": 0}
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
                calls["ledger_start"] += 1
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

        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        manager = patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=fake_ledger,
        )
        manager.start()
        self.addCleanup(manager.stop)
        self.server = create_mcp_server(
            ServiceBoardApi(),
            self.logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def call_preview(self, payload, key, *, mode="dry_run", operation="cleanup_card"):
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
                    "operation": operation,
                    "payload": payload,
                    "mode": mode,
                    "idempotency_key": key,
                },
                convert_result=False,
            )
        finally:
            auth_context_var.reset(context)

    async def assert_conflicting_mode_is_rejected(self, outer, nested, field="mode"):
        before = deepcopy(self.store.read_bundle())
        base_payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "description": "Should never be written",
        }
        result = await self.call_preview(
            {**base_payload, field: nested},
            f"conflict-{outer}-{field}-{nested}",
            mode=outer,
        )
        self.assertFalse(result.structuredContent["ok"], "Conflicting modes must be rejected")
        self.assertIn("board_workflow_mode_conflict", result.structuredContent["warnings"])
        self.assertEqual(self.calls, {"ledger_start": 0, "backend": 0})
        self.assertEqual(before, self.store.read_bundle())

    async def test_dry_run_rejects_nested_apply_before_ledger_and_backend(self):
        await self.assert_conflicting_mode_is_rejected("dry_run", "apply")

    async def test_apply_rejects_nested_dry_run_before_ledger_and_backend(self):
        await self.assert_conflicting_mode_is_rejected("apply", "dry_run")

    async def test_dry_run_rejects_nested_apply_mode_before_ledger_and_backend(self):
        await self.assert_conflicting_mode_is_rejected("dry_run", "apply", "apply_mode")

    async def test_apply_rejects_nested_dry_apply_mode_before_ledger_and_backend(self):
        await self.assert_conflicting_mode_is_rejected("apply", "dry_run", "apply_mode")

    async def test_matching_nested_mode_preserves_preview_and_apply(self):
        before = deepcopy(self.store.read_bundle())
        payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "description": "Corrected synthetic text",
        }
        preview = await self.call_preview(
            {**payload, "mode": "preview"}, "matching-dry", mode="dry_run"
        )
        self.assertTrue(preview.structuredContent["ok"], preview.structuredContent)
        self.assertEqual(before, self.store.read_bundle())
        applied = await self.call_preview(
            {**payload, "mode": "commit"}, "matching-apply", mode="apply"
        )
        self.assertTrue(applied.structuredContent["ok"], applied.structuredContent)
        card = next(
            card for card in self.store.read_bundle()["cards"] if card.id == self.card["id"]
        )
        self.assertEqual(card.description, payload["description"])
        self.assertEqual(self.calls, {"ledger_start": 2, "backend": 2})

    async def test_other_board_operation_accepts_matching_apply_mode_aliases(self):
        before = deepcopy(self.store.read_bundle())
        for outer, alias in (("dry_run", "preview"), ("apply", "commit")):
            with self.subTest(outer=outer):
                result = await self.call_preview(
                    {"apply_mode": alias, "card_ids": ["missing"]},
                    f"matching-apply-mode-{outer}",
                    mode=outer,
                    operation="bulk_set_deadline_if_below",
                )
                self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                self.assertEqual(before, self.store.read_bundle())

    async def test_named_workflow_preview_and_content_only_guards(self):
        before = deepcopy(self.store.read_bundle())
        long_text = "New " + "x" * 6000
        payload = {
            "card_id": self.card["id"],
            "content_only": True,
            "expected_updated_at": self.card["updated_at"],
            "response_mode": "full",
            "title": "Changed title",
            "description": long_text,
            "vehicle_profile": {"engine_model": "Candidate", "tentative_fields": ["engine_model"]},
        }
        result = await self.call_preview(payload, "named-preview")
        self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        item = result.structuredContent["data"]["data"]["result"]["items"][0]
        self.assertEqual(set(item["planned_fields"]), {"title", "description", "vehicle_profile"})
        self.assertEqual(item["card"]["title"], "Changed title")
        self.assertEqual(item["card"]["description"], long_text)
        self.assertEqual(item["card"]["vehicle_profile"]["engine_model"], "Candidate")
        self.assertEqual(before, self.store.read_bundle())
        for index, forbidden in enumerate(
            (
                {"deadline": {"hours": 2}},
                {"repair_order": {"works": []}},
                {"vehicle_profile": {"customer_name": "Forbidden"}},
                {"expected_updated_at": "stale"},
            )
        ):
            with self.subTest(forbidden=forbidden):
                rejected = await self.call_preview(
                    {**payload, **forbidden}, f"named-rejected-{index}"
                )
                self.assertFalse(rejected.structuredContent["ok"], rejected.structuredContent)
                self.assertEqual(before, self.store.read_bundle())
