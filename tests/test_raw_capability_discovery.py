from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.server import create_mcp_server

GATEWAY_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
    "AUTOSTOP_MCP_OAUTH_STATE_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
}


class _DiscoveryBoardApi:
    base_url = "http://127.0.0.1:41731"


def _register_automotive_manager_tools(server, logger) -> None:
    del logger

    @server.tool(
        name="recommend_automotive_sources",
        description="Recommend authoritative repair and diagnostic source routes by brand and data type.",
    )
    def recommend_automotive_sources(brand: str = "", data_type: str = "") -> dict:
        return {"ok": True, "brand": brand, "data_type": data_type}

    @server.tool(
        name="recommend_fluid_maintenance_sources",
        description="Build a source-backed plan for oils, operating fluids, fill capacities, and ТО service.",
    )
    def recommend_fluid_maintenance_sources(brand: str = "", unit: str = "") -> dict:
        return {"ok": True, "brand": brand, "unit": unit}

    @server.tool(
        name="lookup_public_automotive_evidence",
        description="Read compact official public recall and manufacturer-communication evidence.",
    )
    def lookup_public_automotive_evidence(make: str = "", model: str = "") -> dict:
        return {"ok": True, "make": make, "model": model}

    @server.tool(name="decode_vehicle_identity", description="Read vehicle identity evidence.")
    def decode_vehicle_identity(identifier: str = "") -> dict:
        return {"ok": True, "identifier": identifier}

    @server.tool(name="partsapi_catalog_lookup", description="Read PartsAPI catalog evidence.")
    def partsapi_catalog_lookup(operation: str = "") -> dict:
        return {"ok": True, "operation": operation}

    @server.tool(name="lookup_oem_catalog_candidates", description="Read OEM catalog candidates.")
    def lookup_oem_catalog_candidates(identifier: str = "") -> dict:
        return {"ok": True, "identifier": identifier}


class RawCapabilityDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    def _server(self):
        self.manager_register.side_effect = _register_automotive_manager_tools
        return create_mcp_server(
            _DiscoveryBoardApi(),
            self.logger,
            host="127.0.0.1",
            port=41840,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    async def test_semantic_discovery_routes_russian_technical_intent_to_read_capabilities(
        self,
    ) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")

        for query, expected_name in (
            ("как выставить ГРМ на Mercedes", "recommend_automotive_sources"),
            ("какое масло в АКПП Mercedes", "recommend_fluid_maintenance_sources"),
            ("официальный отзыв автомобиля", "lookup_public_automotive_evidence"),
        ):
            with self.subTest(query=query):
                result = await discover.run({"query": query}, convert_result=False)
                capabilities = result.structuredContent["data"]["capabilities"]
                selected = next(item for item in capabilities if item["name"] == expected_name)
                self.assertEqual("read", selected["risk"])
                self.assertTrue(selected["matched_terms"])
                self.assertTrue(all(item["risk"] == "read" for item in capabilities))

    async def test_semantic_discovery_hides_writes_but_exact_name_keeps_raw_escape_hatch(
        self,
    ) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")

        semantic = await discover.run({"query": "sticky"}, convert_result=False)
        self.assertNotIn(
            "create_sticky",
            [item["name"] for item in semantic.structuredContent["data"]["capabilities"]],
        )

        exact = await discover.run({"query": "create_sticky"}, convert_result=False)
        selected = next(
            item
            for item in exact.structuredContent["data"]["capabilities"]
            if item["name"] == "create_sticky"
        )
        self.assertEqual("write", selected["risk"])

    async def test_vin_and_oem_read_tools_do_not_require_raw_write_metadata(self) -> None:
        server = self._server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")
        schema = server._tool_manager.get_tool("get_raw_capability_schema")

        for name in (
            "decode_vehicle_identity",
            "partsapi_catalog_lookup",
            "lookup_oem_catalog_candidates",
        ):
            with self.subTest(name=name):
                discovered = await discover.run({"query": name}, convert_result=False)
                capability = next(
                    item
                    for item in discovered.structuredContent["data"]["capabilities"]
                    if item["name"] == name
                )
                self.assertEqual("read", capability["risk"])
                described = await schema.run({"name": name}, convert_result=False)
                self.assertEqual("read", described.structuredContent["summary"]["risk"])
