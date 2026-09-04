from __future__ import annotations

import logging
import sys
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
    _is_finance_capability,
    _policy_error,
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

    def test_owner_semantic_finance_classification_covers_derived_value_operations(self) -> None:
        financial_operation_ids = (
            "create_manual_order_api_v1_customers__customer_id__orders_post",
            "delete_sales_order_api_v1_customers_orders__order_id__delete",
            "complete_operation_api_v1_warehouse_operations__operation_id__complete_post",
            "reverse_stock_movement_api_v1_stock_movements__movement_id__reverse_post",
            "update_batch_api_v1_warehouse_batches__batch_id__patch",
            "shipment_part_api_v1_parts__id__shipment_post",
            "publish_quote_offer_drafts_api_v1_admin_quote_requests__quote_request_id__offers_publish_post",
            "publish_admin_quote_request_response_api_v1_admin_quote_requests__quote_request_id__publish_response_post",
            "update_quote_request_item_api_v1_admin_quote_requests__quote_request_id__items__item_id__patch",
            "delete_quote_request_item_api_v1_admin_quote_requests__quote_request_id__items__item_id__delete",
            "delete_admin_quote_request_api_v1_admin_quote_requests__quote_request_id__delete",
            "archive_admin_quote_request_api_v1_admin_quote_requests__quote_request_id__archive_post",
            "export_marketplace_items_api_v1_marketplaces_exports_post",
            "refresh_marketplace_feed_api_v1_marketplaces_accounts__account_id__feed_refresh_post",
            "export_all_marketplace_api_items_api_v1_marketplaces_accounts__account_id__exports_all_post",
            "create_category_api_v1_categories_post",
            "update_category_api_v1_categories__id__patch",
            "deactivate_category_api_v1_categories__id__deactivate_post",
            "activate_category_api_v1_categories__id__activate_post",
            "delete_category_api_v1_categories__id__delete",
            "update_manufacturer_api_v1_manufacturers__id__patch",
            "update_part_api_v1_parts__id__patch",
            "delete_part_api_v1_parts__id__delete",
            "deactivate_part_api_v1_parts__id__deactivate_post",
            "activate_part_api_v1_parts__id__activate_post",
            "create_avito_fitment_api_v1_parts__part_id__avito_fitments_post",
            "update_avito_fitment_api_v1_parts__part_id__avito_fitments__fitment_id__patch",
            "delete_avito_fitment_api_v1_parts__part_id__avito_fitments__fitment_id__delete",
            "update_system_settings_api_v1_settings_patch",
        )
        for operation_id in financial_operation_ids:
            with self.subTest(operation_id=operation_id):
                self.assertTrue(
                    _is_finance_capability(
                        "store_owner_api",
                        {"mode": "dry_run", "operation_id": operation_id},
                    )
                )
        self.assertFalse(
            _is_finance_capability(
                "store_owner_api",
                {"mode": "dry_run", "operation_id": "reorder_part_photos"},
            )
        )
        self.assertFalse(
            _is_finance_capability(
                "store_owner_api",
                {"mode": "dry_run", "operation_id": "append_customer_note"},
            )
        )
        self.assertFalse(
            _is_finance_capability(
                "store_owner_api",
                {
                    "mode": "dry_run",
                    "operation_id": "create_manufacturer_api_v1_manufacturers_post",
                    "body": {"name": "Gateway release smoke"},
                },
            )
        )
        self.assertFalse(
            _is_finance_capability(
                "store_owner_api",
                {"mode": "read", "operation_id": financial_operation_ids[0]},
            )
        )
        self.assertTrue(
            _is_finance_capability(
                "store_owner_api",
                {
                    "mode": "dry_run",
                    "operation_id": "update_part_api_v1_parts__id__patch",
                    "body": {"deriveRetailFromWholesale": True},
                },
            )
        )

        self.assertTrue(_is_finance_capability("replace_quote_offer_drafts", {}))
        self.assertTrue(_is_finance_capability("mark_order_ready", {}))
        self.assertTrue(
            _is_finance_capability(
                "set_quote_request_status",
                {"status": "WAITING_FOR_APPROVAL"},
            )
        )
        self.assertTrue(
            _is_finance_capability(
                "set_quote_request_status",
                {"planned_changes": {"status": "WAITING_FOR_APPROVAL"}},
            )
        )
        self.assertFalse(
            _is_finance_capability(
                "set_quote_request_status",
                {"planned_changes": {"status": "WAITING_FOR_QUOTE"}},
            )
        )
        marketplace_account_operation = (
            "create_marketplace_account_api_v1_marketplaces_accounts_post"
        )
        self.assertTrue(
            _is_finance_capability(
                "store_owner_api",
                {
                    "mode": "dry_run",
                    "operation_id": marketplace_account_operation,
                    "body": {"transport": "FEED"},
                },
            )
        )
        self.assertFalse(
            _is_finance_capability(
                "store_owner_api",
                {
                    "mode": "dry_run",
                    "operation_id": marketplace_account_operation,
                    "body": {"transport": "API"},
                },
            )
        )
        self.assertTrue(
            _is_finance_capability(
                "store_owner_api",
                {
                    "mode": "dry_run",
                    "operation_id": (
                        "update_admin_quote_request_api_v1_admin_quote_requests__"
                        "quote_request_id__patch"
                    ),
                    "body": {"status": "WAITING_FOR_APPROVAL"},
                },
            )
        )

    def test_named_store_finance_policy_blocks_derived_value_actions(self) -> None:
        cases = (
            ("set_quote_request_status", {"status": "WAITING_FOR_APPROVAL"}),
            ("replace_quote_offer_drafts", {"items": []}),
            ("mark_order_ready", {"status": "READY"}),
        )
        with patch.dict(
            "os.environ",
            {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"},
            clear=False,
        ):
            for operation, arguments in cases:
                with self.subTest(operation=operation):
                    self.assertEqual(
                        "agent_gateway_finance_disabled",
                        _policy_error(tool_name=operation, risk="write", arguments=arguments),
                    )

    def _register_store_owner_tools(self, server, _logger) -> None:
        @server.tool(
            name="store_owner_capabilities",
            description="READ_ONLY RAW_CAPABILITY Store owner operation inventory",
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )
        def store_owner_capabilities(query: str = "", limit: int = 50) -> dict:
            self.calls.append(("store_owner_capabilities", {"query": query, "limit": limit}))
            return {"ok": True, "items": [{"operation_id": "get_part"}]}

        @server.tool(
            name="store_owner_api",
            description="INTERNAL_ONLY Store owner transport",
            annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
        )
        def store_owner_api(operation_id: str = "", mode: str = "read") -> dict:
            self.calls.append(("store_owner_api", {"operation_id": operation_id, "mode": mode}))
            return {"ok": True, "status": "completed"}

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

    async def test_owner_transport_is_hidden_from_all_public_raw_paths(self) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")
        schema = server._tool_manager.get_tool("get_raw_capability_schema")
        raw_call = server._tool_manager.get_tool("call_raw_capability")

        discovered = await discover.run({"query": "store_owner_api"}, convert_result=False)
        self.assertEqual([], discovered.structuredContent["data"]["capabilities"])

        schema_result = await schema.run({"name": "store_owner_api"}, convert_result=False)
        self.assertFalse(schema_result.structuredContent["ok"])
        self.assertIn("named_workflow_required", schema_result.structuredContent["warnings"])

        call_result = await raw_call.run(
            {
                "name": "store_owner_api",
                "arguments": {"operation_id": "get_part", "mode": "read"},
                "schema_hash": "0" * 64,
            },
            convert_result=False,
        )
        self.assertFalse(call_result.structuredContent["ok"])
        self.assertIn("named_workflow_required", call_result.structuredContent["warnings"])
        self.assertEqual([], self.calls)

    async def test_owner_contract_inventory_remains_read_only(self) -> None:
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

        read_schema = await schema.run({"name": "store_owner_capabilities"}, convert_result=False)
        self.assertTrue(read_schema.structuredContent["ok"])
        self.assertEqual("read", read_schema.structuredContent["summary"]["risk"])

        result = await raw_call.run(
            {
                "name": "store_owner_capabilities",
                "arguments": {"query": "parts", "limit": 10},
                "schema_hash": read_schema.structuredContent["summary"]["schema_hash"],
            },
            convert_result=False,
        )
        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(
            [("store_owner_capabilities", {"query": "parts", "limit": 10})],
            self.calls,
        )

    def test_public_gateway_surface_remains_exactly_24(self) -> None:
        server = self._server()

        self.assertEqual(24, len(PERMANENT_AGENT_GATEWAY_TOOL_NAMES))
        self.assertLessEqual(set(server._tool_manager._tools), PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
        self.assertNotIn("store_owner_capabilities", server._tool_manager._tools)
        self.assertNotIn("store_owner_api", server._tool_manager._tools)
        for tool_name in ("agent_search", "agent_entity_context"):
            schema = server._tool_manager.get_tool(tool_name).parameters
            self.assertNotIn("store_supplier", schema["properties"]["entity"]["enum"])

    def test_maintenance_proof_allows_only_change_feed_release_writes(self) -> None:
        token = "technical-smoke-token"
        revision = "a" * 40
        proof = _release_smoke_proof(token, revision)

        for capability in (
            "api:/api/change_feed/bootstrap",
            "api:/api/change_feed/ack",
        ):
            self.assertTrue(
                _maintenance_technical_write_allowed(
                    capability=capability,
                    arguments={"consumer_id": RELEASE_SMOKE_CHANGE_FEED_CONSUMER_ID},
                    revision=revision,
                    proof=proof,
                    agent_bearer_token=token,
                )
            )

        for mode in ("read", "dry_run", "apply"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    _maintenance_technical_write_allowed(
                        capability="store_owner_api",
                        arguments={"mode": mode},
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
