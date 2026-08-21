from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for source_root in (SRC, TESTS):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from test_agent_gateway_v2 import (  # noqa: E402
    GATEWAY_ENV,
    FakeBoardApi,
    completion_act_form,
    register_fake_store_manager_tools,
)

from minimal_kanban.mcp.server import create_mcp_server  # noqa: E402


class CrmParityGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()
        self.board_api = FakeBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41831,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    async def _call(self, name: str, arguments: dict | None = None):
        tool = self.server._tool_manager.get_tool(name)
        self.assertIsNotNone(tool)
        return await tool.run(arguments or {}, convert_result=False)

    async def test_resolved_parity_reads_are_guarded_virtual_read_capabilities(self) -> None:
        requests = {
            "/api/get_ai_chat_knowledge": {"prompt": "diagnostics"},
            "/api/get_board_revision": {"compact": True, "include_archive": False},
            "/api/get_completion_act_form": {"card_id": "card-1"},
            "/api/get_display_dashboard": {},
            "/api/get_inspection_sheet_form": {"card_id": "card-1"},
            "/api/get_repair_order_print_workspace": {"card_id": "card-1"},
            "/api/list_employees": {},
        }

        for route, arguments in requests.items():
            with self.subTest(route=route):
                name = f"api:{route}"
                schema = await self._call("get_raw_capability_schema", {"name": name})
                result = await self._call(
                    "call_raw_capability",
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                    },
                )

                self.assertEqual(schema.structuredContent["summary"]["risk"], "read")
                self.assertTrue(result.structuredContent["ok"])
                self.assertFalse(result.structuredContent["verification"]["required"])

        self.assertEqual(list(requests), [item["path"] for item in self.board_api.raw_requests])

    async def test_completion_act_raw_schemas_are_strict_and_reject_invalid_payloads(self) -> None:
        schema_result = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/save_completion_act_form"},
        )
        schema = schema_result.structuredContent["data"]["input_schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["form"]["additionalProperties"])
        self.assertEqual(300, schema["properties"]["form"]["properties"]["items"]["maxItems"])
        self.assertTrue(schema["properties"]["form_data"]["deprecated"])

        async def rejected(arguments: dict, key: str):
            return await self._call(
                "call_raw_capability",
                {
                    "name": "api:/api/save_completion_act_form",
                    "arguments": arguments,
                    "schema_hash": schema_result.structuredContent["summary"]["schema_hash"],
                    "idempotency_key": key,
                },
            )

        base = {
            "card_id": "card-1",
            "form": completion_act_form(),
            "expected_version": 0,
            "expected_source_fingerprint": "0" * 64,
        }
        extra = await rejected({**base, "unexpected": True}, "schema-extra")
        wrong = await rejected(
            {**base, "form": {**completion_act_form(), "basis": 42}},
            "schema-type",
        )
        oversized = await rejected(
            {
                **base,
                "form": {
                    **completion_act_form(),
                    "items": [
                        {
                            "id": str(index),
                            "section": "manual",
                            "name": "row",
                            "unit": "шт",
                            "quantity": "1",
                            "price": "1",
                        }
                        for index in range(301)
                    ],
                },
            },
            "schema-oversized",
        )
        for result in (extra, wrong, oversized):
            self.assertFalse(result.structuredContent["ok"])
            self.assertIn("raw_schema_validation_failed", result.structuredContent["warnings"])

    async def test_resolved_parity_writes_require_idempotency_before_execution(self) -> None:
        autofill_schema = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/set_card_ai_autofill"},
        )
        stale_unsafe = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/set_card_ai_autofill",
                "arguments": {"card_id": "card-1", "enabled": True},
                "schema_hash": autofill_schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "unsafe-without-revision",
            },
        )
        self.assertIn(
            "expected_updated_at_required_reread_exact_card_first",
            stale_unsafe.structuredContent["warnings"],
        )
        completion_schema = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/save_completion_act_form"},
        )
        missing_version = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/save_completion_act_form",
                "arguments": {"card_id": "card-1", "form": {}},
                "schema_hash": completion_schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "completion-without-version",
            },
        )
        self.assertIn(
            "expected_version_required_reread_exact_completion_act_first",
            missing_version.structuredContent["warnings"],
        )
        missing_source = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/save_completion_act_form",
                "arguments": {
                    "card_id": "card-1",
                    "form": completion_act_form(),
                    "expected_version": 0,
                },
                "schema_hash": completion_schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "completion-without-source-fingerprint",
            },
        )
        self.assertIn(
            "expected_source_fingerprint_required_reread_exact_completion_act_first",
            missing_source.structuredContent["warnings"],
        )
        mismatched_key = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/save_completion_act_form",
                "arguments": {
                    "card_id": "card-1",
                    "form": {},
                    "expected_version": 0,
                    "expected_source_fingerprint": "0" * 64,
                    "idempotency_key": "inner-key",
                },
                "schema_hash": completion_schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": "outer-key",
            },
        )
        self.assertFalse(mismatched_key.structuredContent["ok"])
        self.assertIn(
            "completion_act_idempotency_key_mismatch",
            mismatched_key.structuredContent["warnings"],
        )

        requests = {
            "/api/set_card_ai_autofill": {
                "card_id": "card-1",
                "enabled": True,
                "expected_updated_at": self.board_api.card_updated_at,
            },
            "/api/open_card": {"card_id": "card-1"},
            "/api/save_completion_act_form": {
                "card_id": "card-1",
                "form": completion_act_form(),
                "expected_version": 0,
                "expected_source_fingerprint": "0" * 64,
            },
            "/api/reset_completion_act_form": {
                "card_id": "card-1",
                "expected_version": 0,
                "expected_source_fingerprint": "0" * 64,
            },
        }
        for route, arguments in requests.items():
            with self.subTest(route=route):
                name = f"api:{route}"
                schema = await self._call("get_raw_capability_schema", {"name": name})
                rejected = await self._call(
                    "call_raw_capability",
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                    },
                )
                self.assertEqual(
                    schema.structuredContent["summary"]["risk"],
                    "destructive" if route == "/api/reset_completion_act_form" else "write",
                )
                self.assertFalse(rejected.structuredContent["ok"])
                self.assertIn(
                    "idempotency_key_required_for_raw_write",
                    rejected.structuredContent["warnings"],
                )

        self.assertEqual([], self.board_api.raw_requests)

    async def test_resolved_parity_writes_close_ledger_after_exact_readback(self) -> None:
        manager_state: dict = {}
        self.manager_register.side_effect = lambda server, logger: (
            register_fake_store_manager_tools(server, logger, manager_state)
        )
        board_api = FakeBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41837,
            path="/mcp",
            bearer_token="agent-service-token-with-strong-test-entropy-0123456789",
            public_endpoint_url="https://crm.example/mcp",
        )

        async def apply(name: str, arguments: dict, idempotency_key: str):
            schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
                {"name": name}, convert_result=False
            )
            return await server._tool_manager.get_tool("call_raw_capability").run(
                {
                    "name": name,
                    "arguments": arguments,
                    "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                    "idempotency_key": idempotency_key,
                },
                convert_result=False,
            )

        autofill = await apply(
            "api:/api/set_card_ai_autofill",
            {
                "card_id": "card-1",
                "enabled": True,
                "prompt": "Use confirmed data only",
                "expected_updated_at": board_api.card_updated_at,
            },
            "set-card-ai-card-1-v1",
        )
        opened = await apply(
            "api:/api/open_card",
            {"card_id": "card-1", "return_card": False, "mark_seen": False},
            "open-card-card-1-v1",
        )
        completion_saved = await apply(
            "api:/api/save_completion_act_form",
            {
                "card_id": "card-1",
                "form": completion_act_form(basis="Exact gateway draft"),
                "expected_version": 0,
                "expected_source_fingerprint": "0" * 64,
            },
            "completion-act-card-1-save-v1",
        )
        completion_reset = await apply(
            "api:/api/reset_completion_act_form",
            {
                "card_id": "card-1",
                "expected_version": 1,
                "expected_source_fingerprint": "0" * 64,
            },
            "completion-act-card-1-reset-v2",
        )

        self.assertEqual(24, len(server._tool_manager.list_tools()))
        self.assertTrue(autofill.structuredContent["ok"])
        self.assertEqual(
            "exact_card_ai_autofill_readback",
            autofill.structuredContent["verification"]["check"],
        )
        self.assertTrue(opened.structuredContent["ok"])
        self.assertEqual(
            "exact_operator_activity_readback",
            opened.structuredContent["verification"]["check"],
        )
        self.assertTrue(completion_saved.structuredContent["ok"])
        self.assertEqual(
            "exact_completion_act_draft_readback",
            completion_saved.structuredContent["verification"]["check"],
        )
        self.assertTrue(completion_reset.structuredContent["ok"])
        self.assertEqual(
            "exact_completion_act_reset_readback",
            completion_reset.structuredContent["verification"]["check"],
        )
        self.assertEqual(
            [
                "/api/set_card_ai_autofill",
                "/api/open_card",
                "/api/list_operator_activity",
                "/api/save_completion_act_form",
                "/api/get_completion_act_form",
                "/api/reset_completion_act_form",
                "/api/get_completion_act_form",
            ],
            [item["path"] for item in board_api.raw_requests],
        )
        self.assertEqual(
            "completion-act-card-1-save-v1",
            board_api.raw_requests[3]["payload"]["idempotency_key"],
        )
        self.assertEqual(
            "completion-act-card-1-reset-v2",
            board_api.raw_requests[5]["payload"]["idempotency_key"],
        )


if __name__ == "__main__":
    unittest.main()
