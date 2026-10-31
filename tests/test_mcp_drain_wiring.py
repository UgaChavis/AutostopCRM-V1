from __future__ import annotations

import asyncio
import logging
import os
import unittest
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session

from tests.source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.mcp import server as server_module  # noqa: E402
from minimal_kanban.mcp.drain_wiring import ExperimentalMcpDrain  # noqa: E402
from tests.test_mcp_server_hardening import FakeBoardApi  # noqa: E402


class McpDrainWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_factory_uses_exact_handles_and_preserves_wire_safety(self) -> None:
        handles = ExperimentalMcpDrain()
        with (
            patch.dict(os.environ, {"AUTOSTOP_DEPLOYMENT_ENV": "development"}, clear=True),
            patch.object(server_module, "_try_register_autostop_manager_tools"),
            patch.object(server_module, "get_mcp_bearer_token", return_value=None),
            patch.object(
                server_module,
                "register_agent_gateway_v2",
                wraps=server_module.register_agent_gateway_v2,
            ) as registration,
        ):
            server = server_module.create_mcp_server(
                FakeBoardApi(),
                logging.getLogger(__name__),
                host="127.0.0.1",
                port=41891,
                bearer_token=None,
                experimental_mcp_drain=handles,
            )
        self.assertIs(registration.call_args.kwargs["experimental_executor"], handles.executor)
        started = asyncio.Event()
        release = asyncio.Event()

        @server.tool()
        async def synthetic_slow() -> str:
            started.set()
            await release.wait()
            return "done"

        async with create_connected_server_and_client_session(server) as session:
            with patch.object(handles.executor, "run_sync", wraps=handles.executor.run_sync) as run:
                self.assertFalse((await session.call_tool("get_connector_identity", {})).isError)
                self.assertTrue(run.called)
            pending = asyncio.create_task(session.call_tool("synthetic_slow", {}))
            try:
                await asyncio.wait_for(started.wait(), 1)
                owner = handles.calls.begin_drain()
                self.assertFalse(handles.calls.is_drained(owner))
                self.assertTrue((await session.call_tool("synthetic_slow", {})).isError)
                release.set()
                self.assertFalse((await asyncio.wait_for(pending, 1)).isError)
                self.assertTrue(handles.calls.is_drained(owner))
                handles.calls.resume(owner)
            finally:
                release.set()

    def test_handles_cannot_be_reused_for_a_second_server(self) -> None:
        handles = ExperimentalMcpDrain()
        with self.assertRaises(AttributeError):
            handles.calls = object()
        with self.assertRaises(AttributeError):
            handles.executor = object()
        handles.bind_once()
        with self.assertRaisesRegex(RuntimeError, "already_bound"):
            handles.bind_once()


if __name__ == "__main__":
    unittest.main()
