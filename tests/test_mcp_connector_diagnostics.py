from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.agent_gateway_support import (  # noqa: E402
    DIAGNOSTIC_TOOL_NAMES,
)
from minimal_kanban.mcp.connector_diagnostics import (  # noqa: E402
    ConnectorDiagnosticsContext,
    register_connector_diagnostics,
)
from minimal_kanban.mcp.payloads import (  # noqa: E402
    ConnectorIdentityEnvelope,
    JsonEnvelope,
)


class ConnectorDiagnosticsRegistrarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.identity = {
            "connector_name": "autostopcrm-this-board-only-local",
            "product_name": "AutoStop CRM",
            "board_name": "Current AutoStop CRM Board",
            "board_scope": "single_local_board_instance",
            "board_key": "autostopcrm/current-board",
            "scope_rule": "Current board only.",
            "resource_url": "http://127.0.0.1:41831/mcp",
            "server_base_url": "http://127.0.0.1:41831",
            "streamable_http_path": "/mcp",
            "local_bind": "http://127.0.0.1:41831/mcp",
            "board_api_base_url": "http://127.0.0.1:41731",
            "auth_mode": "none",
            "host": "127.0.0.1",
            "port": 41831,
        }
        self.relay_calls: list[tuple[str, str]] = []
        self.server = FastMCP(name="connector-diagnostics-test")

        def read_tool_annotations(title: str) -> ToolAnnotations:
            return ToolAnnotations(
                title=title,
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            )

        def timed_meta(
            tool_name: str,
            _started_at: float,
            *,
            meta: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "tool": tool_name,
                "canonical_tool_path": f"/AutoStopCRM/{tool_name}",
                **(meta or {}),
            }

        def relay_data(
            tool_name: str,
            data: dict[str, Any],
            *,
            meta: dict[str, Any] | None = None,
        ) -> JsonEnvelope:
            self.relay_calls.append(("data", tool_name))
            return JsonEnvelope(ok=True, data=data, error=None, meta=meta)

        def relay_identity_data(
            data: dict[str, Any],
            *,
            meta: dict[str, Any] | None = None,
        ) -> ConnectorIdentityEnvelope:
            self.relay_calls.append(("identity", "get_connector_identity"))
            return ConnectorIdentityEnvelope.model_validate(
                {"ok": True, "data": data, "error": None, "meta": meta}
            )

        context = ConnectorDiagnosticsContext(
            connector_identity=self.identity,
            schema_version="2026-04-13",
            scoped_description=lambda summary: f"{summary} Scope: current board only.",
            read_tool_annotations=read_tool_annotations,
            canonical_tool_path=lambda tool_name: f"/AutoStopCRM/{tool_name}",
            timed_meta=timed_meta,
            relay_data=relay_data,
            relay_identity_data=relay_identity_data,
            identity_text=lambda: "[CONNECTOR IDENTITY]\n",
            runtime_status_payload=lambda: {
                "connector_identity": dict(self.identity),
                "resource_visibility": "local_only",
            },
            runtime_status_text=lambda _status: "[RUNTIME STATUS]\n",
        )
        self.registered_names = register_connector_diagnostics(self.server, context)

    async def test_registers_exact_permanent_diagnostic_contracts(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        expected_descriptions = {
            "get_connector_identity": (
                "Return the hard identity of this MCP connector: name, resource_url, "
                "auth mode, and the rule that it manages only the current AutoStop CRM "
                "board. Scope: current board only."
            ),
            "ping_connector": (
                "Return the lightest possible connector ping. Use this first when you "
                "need to verify that ChatGPT can execute any AutoStop CRM MCP tool at "
                "all. Scope: current board only."
            ),
            "get_runtime_status": (
                "Return runtime diagnostics for this connector: effective MCP identity, "
                "board API health, board counts, and whether the endpoint is publicly "
                "reachable in principle. Scope: current board only."
            ),
        }

        self.assertEqual(self.registered_names, DIAGNOSTIC_TOOL_NAMES)
        self.assertEqual(set(tools), DIAGNOSTIC_TOOL_NAMES)
        for tool_name in DIAGNOSTIC_TOOL_NAMES:
            with self.subTest(tool=tool_name):
                self.assertEqual(tools[tool_name].description, expected_descriptions[tool_name])
                self.assertTrue(tools[tool_name].annotations.readOnlyHint)
                self.assertFalse(tools[tool_name].annotations.destructiveHint)
                self.assertFalse(tools[tool_name].annotations.openWorldHint)

    async def test_diagnostic_handlers_preserve_identity_ping_and_runtime_shapes(self) -> None:
        identity = await self.server._tool_manager.call_tool("get_connector_identity", {})
        ping = await self.server._tool_manager.call_tool("ping_connector", {})
        runtime = await self.server._tool_manager.call_tool("get_runtime_status", {})

        self.assertEqual(identity.data.identity.connector_name, self.identity["connector_name"])
        self.assertEqual(identity.data.text, "[CONNECTOR IDENTITY]\n")
        self.assertEqual(identity.meta["response_mode"], "identity")
        self.assertEqual(ping.data["message"], "pong")
        self.assertEqual(ping.data["schema_version"], "2026-04-13")
        self.assertEqual(
            ping.data["text"],
            "[CONNECTOR PING]\n"
            "connector_name: autostopcrm-this-board-only-local\n"
            "resource_url: http://127.0.0.1:41831/mcp\n"
            "canonical_tool_path: /AutoStopCRM/ping_connector\n"
            "message: pong\n",
        )
        self.assertEqual(ping.meta["response_mode"], "ping")
        self.assertEqual(runtime.data["schema_version"], "2026-04-13")
        self.assertEqual(runtime.data["full_board_context_tool"], "get_board_context")
        self.assertEqual(runtime.data["text"], "[RUNTIME STATUS]\n")
        self.assertEqual(runtime.meta["response_mode"], "diagnostics")
        self.assertEqual(
            self.relay_calls,
            [
                ("identity", "get_connector_identity"),
                ("data", "ping_connector"),
                ("data", "get_runtime_status"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
