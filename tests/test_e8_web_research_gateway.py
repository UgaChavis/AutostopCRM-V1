from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_repository_root_path, ensure_source_path
else:
    from source_path_support import ensure_repository_root_path, ensure_source_path

ensure_repository_root_path()
ensure_source_path()

from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class E8WebResearchGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_part_evidence_gateway_is_raw_only_and_has_a_strict_v1_schema(self) -> None:
        logger = logging.getLogger(self._testMethodName)
        state: dict = {}
        with (
            patch.dict("os.environ", GATEWAY_ENV, clear=False),
            patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools") as register,
        ):
            register.side_effect = lambda server, logger: register_fake_store_manager_tools(
                server, logger, state
            )
            server = create_mcp_server(
                FakeBoardApi(),
                logger,
                host="127.0.0.1",
                port=41839,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

            names = {tool.name for tool in server._tool_manager.list_tools()}
            discovered = await server._tool_manager.get_tool("discover_raw_capabilities").run(
                {"query": "research_part_public_evidence"}, convert_result=False
            )
            capability = discovered.structuredContent["data"]["capabilities"][0]
            schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
                {"name": "research_part_public_evidence"}, convert_result=False
            )
            schema_hash = schema.structuredContent["summary"]["schema_hash"]

            self.assertEqual(24, len(names))
            self.assertEqual("research_part_public_evidence", capability["name"])
            self.assertEqual("read", capability["risk"])
            self.assertEqual(
                ["query"], schema.structuredContent["data"]["input_schema"]["required"]
            )
            self.assertFalse(
                schema.structuredContent["data"]["input_schema"]["additionalProperties"]
            )
            self.assertEqual(
                2,
                schema.structuredContent["data"]["input_schema"]["properties"]["max_pages"][
                    "maximum"
                ],
            )

            raw = server._tool_manager.get_tool("call_raw_capability")
            rejected = await raw.run(
                {
                    "name": "research_part_public_evidence",
                    "arguments": {"query": "Ford 1712024", "unknown": "value"},
                    "schema_hash": schema_hash,
                },
                convert_result=False,
            )
            self.assertFalse(rejected.structuredContent["ok"])
            self.assertIn(
                "web_arguments_contain_unknown_fields", rejected.structuredContent["warnings"]
            )

            too_many_domains = await raw.run(
                {
                    "name": "research_part_public_evidence",
                    "arguments": {
                        "query": "Ford 1712024",
                        "allowed_domains": ["partsouq.com"] * 21,
                    },
                    "schema_hash": schema_hash,
                },
                convert_result=False,
            )
            self.assertFalse(too_many_domains.structuredContent["ok"])
            self.assertIn(
                "web_argument_allowed_domains_invalid",
                too_many_domains.structuredContent["warnings"],
            )

            expected = {
                "ok": True,
                "contract_version": "autostop.web-research.v1",
                "operation": "part_public_evidence",
                "read_only": True,
                "query": "Ford 1712024 front brake pads",
                "results": [],
                "evidence": [],
                "fitment_confirmed": False,
                "next_step": "confirm_with_vin_specific_epc",
            }
            with patch(
                "minimal_kanban.mcp.web_gateway.AgentToolExecutor.execute",
                return_value=expected,
            ) as execute:
                result = await raw.run(
                    {
                        "name": "research_part_public_evidence",
                        "arguments": {
                            "query": "Ford 1712024 front brake pads",
                            "allowed_domains": ["partsouq.com"],
                            "max_pages": 1,
                        },
                        "schema_hash": schema_hash,
                    },
                    convert_result=False,
                )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual("read", result.structuredContent["summary"]["risk"])
        self.assertFalse(result.structuredContent["data"]["fitment_confirmed"])
        self.assertEqual(
            "autostop.web-research.v1", result.structuredContent["data"]["contract_version"]
        )
        execute.assert_called_once_with(
            "research_part_public_evidence",
            {
                "query": "Ford 1712024 front brake pads",
                "allowed_domains": ["partsouq.com"],
                "max_pages": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
