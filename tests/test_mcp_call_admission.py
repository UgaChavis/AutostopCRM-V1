from __future__ import annotations

import asyncio
import threading
import unittest

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.mcp.call_admission import CallTrackedFastMCP, McpCallAdmission  # noqa: E402
from minimal_kanban.mcp.repair_order_wire_safety import (  # noqa: E402
    install_repair_order_wire_safety,
)
from minimal_kanban.mcp.tool_execution import ToolExecutor  # noqa: E402


class McpCallAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_has_the_same_schema_and_result_as_sdk(self) -> None:
        normal = FastMCP("normal")
        candidate = CallTrackedFastMCP("candidate")

        async def echo(value: str) -> str:
            return value

        normal.add_tool(echo)
        candidate.add_tool(echo)
        self.assertEqual(
            (await normal.list_tools())[0].inputSchema,
            (await candidate.list_tools())[0].inputSchema,
        )
        self.assertEqual(
            await normal.call_tool("echo", {"value": "synthetic"}),
            await candidate.call_tool("echo", {"value": "synthetic"}),
        )

    async def test_real_protocol_tracks_running_call_and_rejects_new_call(self) -> None:
        admission = McpCallAdmission()
        server = CallTrackedFastMCP("tracked", call_admission=admission)
        started, release = asyncio.Event(), asyncio.Event()

        @server.tool()
        async def slow(value: str) -> str:
            started.set()
            await release.wait()
            return value

        @server.tool()
        async def ping() -> str:
            return "ready"

        install_repair_order_wire_safety(server)
        async with create_connected_server_and_client_session(server) as session:
            pending = asyncio.create_task(session.call_tool("slow", {"value": "synthetic"}))
            try:
                await asyncio.wait_for(started.wait(), 1)
                owner = admission.begin_drain()
                self.assertFalse(admission.is_drained(owner))
                with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
                    admission.resume(object())
                rejected = await session.call_tool("ping", {})
                self.assertTrue(rejected.isError)
                release.set()
                self.assertFalse((await asyncio.wait_for(pending, 1)).isError)
                self.assertTrue(admission.is_drained(owner))
                admission.resume(owner)
                self.assertFalse((await session.call_tool("ping", {})).isError)
            finally:
                release.set()

    async def test_cancel_keeps_sdk_semantics_and_does_not_claim_thread_stopped(self) -> None:
        admission = McpCallAdmission()
        executor = ToolExecutor(1)
        server = CallTrackedFastMCP("tracked", call_admission=admission)
        started, release = threading.Event(), threading.Event()

        def blocking() -> str:
            started.set()
            if not release.wait(3):
                raise TimeoutError("test did not release worker")
            return "finished"

        @server.tool()
        async def slow() -> str:
            return await executor.run_sync(blocking)

        pending = asyncio.create_task(server.call_tool("slow", {}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            call_owner = admission.begin_drain()
            executor_owner = executor.begin_drain()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(admission.is_drained(call_owner))
            self.assertFalse(executor.is_drained(executor_owner))
            release.set()
            for _ in range(100):
                if executor.is_drained(executor_owner):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(executor.is_drained(executor_owner))
            admission.resume(call_owner)
            executor.resume(executor_owner)
        finally:
            release.set()

    async def test_cannot_enable_a_second_fence_or_release_a_stale_one(self) -> None:
        admission = McpCallAdmission()
        owner = admission.begin_drain()
        with self.assertRaisesRegex(RuntimeError, "already_active"):
            admission.begin_drain()
        admission.resume(owner)
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            admission.resume(owner)

    def test_production_server_does_not_opt_in(self) -> None:
        from minimal_kanban.mcp import server as server_module

        # The experimental subclass is not substituted into the CRM factory.
        self.assertIs(server_module.FastMCP, FastMCP)


if __name__ == "__main__":
    unittest.main()
