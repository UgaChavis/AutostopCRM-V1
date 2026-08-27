from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import ToolManager

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp import server as mcp_server_module  # noqa: E402
from minimal_kanban.mcp.agent_gateway_support import (  # noqa: E402
    MANAGER_GATEWAY_DEPENDENCY_NAMES,
)
from minimal_kanban.mcp.client import BoardApiClient  # noqa: E402
from minimal_kanban.mcp.server import (  # noqa: E402
    _normalize_tool_path_alias,
    create_mcp_server,
)
from minimal_kanban.mcp.tool_registry import (  # noqa: E402
    MCP_TOOL_GROUPS,
    PUBLIC_MCP_TOOL_NAMES,
)

_RAW_REGISTRATION_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_MCP_OAUTH_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "0",
}
_RAW_REGISTRATION_CONTRACT_SHA256 = (
    "c7c68b2b73880c7a8d958b6596b7e2d61e37ebd11570ec782ee684355de2fa5d"
)
_EXPECTED_MANAGER_WRITE_TOOL_NAMES = frozenset(
    {
        "complete_external_step",
        "start_workflow",
        "store_management_action",
        "store_owner_api",
        "workflow_cancel",
        "workflow_checkpoint",
        "workflow_resume",
        "workflow_transition",
        "workflow_wait_for_external",
    }
)
_EXPECTED_MANAGER_READ_TOOL_NAMES = (
    MANAGER_GATEWAY_DEPENDENCY_NAMES - _EXPECTED_MANAGER_WRITE_TOOL_NAMES
)


def _test_logger() -> logging.Logger:
    logger = logging.getLogger("test.mcp.registration.contracts")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def _test_board_api(logger: logging.Logger) -> BoardApiClient:
    return BoardApiClient(
        "http://127.0.0.1:1",
        bearer_token="registration-test-token",
        logger=logger,
    )


def _build_builtin_raw_server() -> tuple[FastMCP, list[str]]:
    logger = _test_logger()
    attempted_names: list[str] = []
    original_add_tool = ToolManager.add_tool

    def recording_add_tool(
        manager: ToolManager,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        positional_name = args[0] if args else None
        attempted_names.append(
            str(kwargs.get("name") or positional_name or getattr(fn, "__name__", ""))
        )
        return original_add_tool(manager, fn, *args, **kwargs)

    with (
        patch.dict(os.environ, _RAW_REGISTRATION_ENV, clear=False),
        patch.object(mcp_server_module, "_try_register_autostop_manager_tools"),
        patch.object(ToolManager, "add_tool", recording_add_tool),
    ):
        server = create_mcp_server(
            _test_board_api(logger),
            logger,
            host="127.0.0.1",
            port=41731,
            path="/mcp",
            bearer_token="registration-test-token",
        )
    return server, attempted_names


def _registration_contract_payload(tool_map: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "input_schema": tool_map[name].inputSchema,
            "output_schema": tool_map[name].outputSchema,
            "annotations": tool_map[name].annotations.model_dump(
                by_alias=True,
                exclude_none=False,
            ),
        }
        for name in sorted(tool_map)
    ]


def _contract_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class McpRegistrationContractTests(unittest.TestCase):
    def test_mcp_tool_group_registry_matches_public_snapshot(self) -> None:
        grouped_names = [tool_name for group in MCP_TOOL_GROUPS.values() for tool_name in group]

        self.assertEqual(len(grouped_names), len(set(grouped_names)))
        self.assertEqual(len(PUBLIC_MCP_TOOL_NAMES), 98)
        self.assertEqual(
            set(MCP_TOOL_GROUPS),
            {
                "diagnostics_bootstrap",
                "manager_operations",
                "board_cards",
                "clients",
                "repair_orders",
                "inventory",
                "cashboxes",
                "files",
            },
        )

    def test_builtin_raw_names_schemas_and_annotations_match_snapshot(self) -> None:
        server, attempted_names = _build_builtin_raw_server()
        tools = asyncio.run(server.list_tools())
        tool_map = {tool.name: tool for tool in tools}

        self.assertEqual(PUBLIC_MCP_TOOL_NAMES, frozenset(tool_map))
        self.assertEqual(PUBLIC_MCP_TOOL_NAMES, frozenset(attempted_names))
        self.assertEqual(len(attempted_names), len(set(attempted_names)))
        self.assertEqual(98, len(attempted_names))
        self.assertTrue(all(tool.annotations is not None for tool in tools))
        self.assertEqual(
            50,
            sum(bool(tool.annotations.readOnlyHint) for tool in tools),
        )
        self.assertEqual(
            7,
            sum(bool(tool.annotations.destructiveHint) for tool in tools),
        )
        self.assertEqual(
            63,
            sum(bool(tool.annotations.idempotentHint) for tool in tools),
        )

        contracts = _registration_contract_payload(tool_map)
        per_tool_digests = {contract["name"]: _contract_digest(contract) for contract in contracts}
        self.assertEqual(
            _RAW_REGISTRATION_CONTRACT_SHA256,
            _contract_digest(contracts),
            json.dumps(per_tool_digests, ensure_ascii=False, sort_keys=True, indent=2),
        )

        legacy_descriptions = [
            tool.name for tool in tools if "Minimal Kanban" in str(tool.description or "")
        ]
        self.assertEqual([], legacy_descriptions)
        self.assertTrue(tool_map["ping_connector"].annotations.readOnlyHint)
        self.assertFalse(tool_map["ping_connector"].annotations.destructiveHint)
        self.assertFalse(tool_map["get_runtime_status"].annotations.openWorldHint)
        self.assertTrue(tool_map["get_runtime_status"].annotations.readOnlyHint)
        self.assertFalse(tool_map["create_card"].annotations.readOnlyHint)
        self.assertTrue(tool_map["delete_sticky"].annotations.destructiveHint)
        self.assertIn("vehicle_profile_compact", tool_map["get_card"].description)
        self.assertTrue(tool_map["read_card_attachment"].annotations.readOnlyHint)
        self.assertTrue(tool_map["list_shared_files"].annotations.readOnlyHint)
        self.assertFalse(tool_map["upload_shared_file"].annotations.readOnlyHint)
        self.assertTrue(tool_map["delete_shared_file"].annotations.destructiveHint)
        self.assertIn("hidden machine wall", tool_map["get_board_content"].description)
        self.assertIn("Markdown", tool_map["get_board_content"].description)
        self.assertIn("hidden machine wall", tool_map["get_board_events"].description)
        self.assertTrue(tool_map["get_board_event_page"].annotations.readOnlyHint)
        self.assertIn(
            "default event_limit is 100",
            tool_map["get_board_events"].description,
        )

        update_schema = tool_map["update_repair_order"].inputSchema
        repair_order_schema = update_schema["properties"]["repair_order"]
        if "$ref" in repair_order_schema:
            definition_name = repair_order_schema["$ref"].rsplit("/", 1)[-1]
            repair_order_schema = update_schema["$defs"][definition_name]
        repair_order_properties = repair_order_schema["properties"]
        for field_name in (
            "comment",
            "clientInformation",
            "master_comment",
            "internalComment",
            "advancePayment",
            "paymentMethod",
            "licensePlate",
            "odometer",
        ):
            self.assertIn(field_name, repair_order_properties)

        for tool_name in PUBLIC_MCP_TOOL_NAMES:
            description = str(tool_map[tool_name].description or "")
            self.assertIn("Scope: current AutoStop CRM board only.", description)
            self.assertNotIn("Do not use it for Trello, YouGile", description)
        for tool_name in ("create_card", "update_card"):
            schema_json = json.dumps(
                tool_map[tool_name].inputSchema,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertLess(len(schema_json.encode("utf-8")), 3000)
            vehicle_profile_schema = tool_map[tool_name].inputSchema["properties"][
                "vehicle_profile"
            ]
            self.assertNotIn("make_display", json.dumps(vehicle_profile_schema))
            self.assertIn("additionalProperties", vehicle_profile_schema["anyOf"][0])

    def test_sticky_registrations_keep_their_legacy_relative_order(self) -> None:
        _server, attempted_names = _build_builtin_raw_server()

        create_index = attempted_names.index("create_sticky")
        self.assertEqual(
            attempted_names[create_index - 1 : create_index + 2],
            ["delete_column", "create_sticky", "list_card_attachments"],
        )
        mutation_index = attempted_names.index("update_sticky")
        self.assertEqual(
            attempted_names[mutation_index - 1 : mutation_index + 5],
            [
                "replace_repair_order_materials",
                "update_sticky",
                "move_sticky",
                "delete_sticky",
                "set_card_deadline",
                "start_card_timer",
            ],
        )

    def test_active_manager_dependency_tools_get_safe_annotations_after_registration(
        self,
    ) -> None:
        manager_package = types.ModuleType("autostop_manager")
        manager_tools = types.ModuleType("autostop_manager.mcp_tools")

        def register_manager_memory_tools(
            server: FastMCP,
            *,
            include_tools: frozenset[str],
        ) -> None:
            self.assertEqual(MANAGER_GATEWAY_DEPENDENCY_NAMES, frozenset(include_tools))

            def build_manager_tool(tool_name: str) -> Callable[[str], dict[str, str]]:
                def manager_tool(value: str = "") -> dict[str, str]:
                    return {"tool": tool_name, "value": value}

                manager_tool.__name__ = f"test_manager_{tool_name}"
                return manager_tool

            for tool_name in sorted(include_tools):
                server.tool(name=tool_name)(build_manager_tool(tool_name))

        manager_tools.register_manager_memory_tools = register_manager_memory_tools
        logger = _test_logger()
        with (
            patch.dict(os.environ, _RAW_REGISTRATION_ENV, clear=False),
            patch.dict(
                sys.modules,
                {
                    "autostop_manager": manager_package,
                    "autostop_manager.mcp_tools": manager_tools,
                },
            ),
        ):
            server = create_mcp_server(
                _test_board_api(logger),
                logger,
                host="127.0.0.1",
                port=41731,
                path="/manager-tools",
                bearer_token="registration-test-token",
            )

        tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
        self.assertEqual(
            MANAGER_GATEWAY_DEPENDENCY_NAMES,
            _EXPECTED_MANAGER_READ_TOOL_NAMES | _EXPECTED_MANAGER_WRITE_TOOL_NAMES,
        )
        for tool_name in _EXPECTED_MANAGER_READ_TOOL_NAMES:
            annotations = tools[tool_name].annotations
            self.assertTrue(annotations.readOnlyHint, tool_name)
            self.assertFalse(annotations.destructiveHint, tool_name)
            self.assertTrue(annotations.idempotentHint, tool_name)
            self.assertFalse(annotations.openWorldHint, tool_name)
        for tool_name in _EXPECTED_MANAGER_WRITE_TOOL_NAMES:
            annotations = tools[tool_name].annotations
            self.assertFalse(annotations.readOnlyHint, tool_name)
            self.assertFalse(annotations.destructiveHint, tool_name)
            self.assertFalse(annotations.idempotentHint, tool_name)
            self.assertFalse(annotations.openWorldHint, tool_name)

    def test_tool_path_alias_normalization_prefers_canonical_short_path(self) -> None:
        self.assertEqual(
            _normalize_tool_path_alias("/AutoStopCRM/link_abc123/bootstrap_context"),
            "/AutoStopCRM/bootstrap_context",
        )
        self.assertEqual(
            _normalize_tool_path_alias("/AutoStopCRM/get_runtime_status"),
            "/AutoStopCRM/get_runtime_status",
        )


if __name__ == "__main__":
    unittest.main()
