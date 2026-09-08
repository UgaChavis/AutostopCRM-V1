from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.mcp.agent_gateway_v2 import register_agent_gateway_v2  # noqa: E402
from minimal_kanban.mcp.tool_execution import ToolExecutor  # noqa: E402


class ToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_sdk_tool_does_not_block_heartbeat_or_fast_request(self) -> None:
        server = FastMCP("execution-test")
        started = threading.Event()
        release = threading.Event()
        loop_thread = threading.get_ident()

        @server.tool()
        def slow() -> int:
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release the slow tool")
            return threading.get_ident()

        @server.tool()
        def fast() -> str:
            return "ready"

        ToolExecutor(2).prepare_tools(server._tool_manager.list_tools())
        pending = asyncio.create_task(server._tool_manager.call_tool("slow", {}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            heartbeat = asyncio.Event()
            asyncio.get_running_loop().call_soon(heartbeat.set)
            await asyncio.wait_for(heartbeat.wait(), 0.5)
            self.assertEqual(
                await asyncio.wait_for(server._tool_manager.call_tool("fast", {}), 0.5),
                "ready",
            )
            self.assertFalse(pending.done())
        finally:
            release.set()
        self.assertNotEqual(await pending, loop_thread)

    async def test_sdk_validation_context_owner_and_conversion_are_preserved(self) -> None:
        owner = contextvars.ContextVar("test_owner", default="anonymous")
        observed = []

        def inspect_request(count: int, ctx: Context) -> dict[str, str | int]:
            observed.append((count, ctx, owner.get()))
            return {"count": count, "owner": owner.get()}

        tool = Tool.from_function(inspect_request)
        parameters, metadata = tool.parameters, tool.fn_metadata
        executor = ToolExecutor()
        executor.prepare_tools([tool])
        executor.prepare_tools([tool])
        context = Context()
        token = owner.set("authenticated-owner")
        try:
            raw = await tool.run({"count": "3"}, context=context)
            converted = await tool.run({"count": 4}, context=context, convert_result=True)
            with self.assertRaises(ToolError):
                await tool.run({"count": "invalid"}, context=context)
        finally:
            owner.reset(token)
        self.assertEqual(raw, {"count": 3, "owner": "authenticated-owner"})
        self.assertEqual(
            converted, metadata.convert_result({"count": 4, "owner": "authenticated-owner"})
        )
        self.assertIs(tool.parameters, parameters)
        self.assertIs(tool.fn_metadata, metadata)
        self.assertEqual(
            observed, [(3, context, "authenticated-owner"), (4, context, "authenticated-owner")]
        )

    async def test_cancelled_started_write_keeps_capacity_and_is_not_retried(self) -> None:
        executor = ToolExecutor(1)
        started, release, next_started = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def write() -> str:
            calls.append("write")
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release write")
            return "committed"

        def following() -> str:
            next_started.set()
            return "following"

        pending = asyncio.create_task(executor.run_sync(write))
        following_task = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            following_task = asyncio.create_task(executor.run_sync(following))
            await asyncio.sleep(0.03)
            self.assertFalse(next_started.is_set())
            cancelled_queued = asyncio.create_task(
                executor.run_sync(lambda: calls.append("queued"))
            )
            await asyncio.sleep(0)
            cancelled_queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled_queued
        finally:
            release.set()
            if following_task is not None:
                self.assertEqual(await asyncio.wait_for(following_task, 1), "following")
        self.assertEqual(calls, ["write"])

    async def test_native_async_tools_stay_on_the_request_loop(self) -> None:
        async def native() -> int:
            return threading.get_ident()

        tool = Tool.from_function(native)
        ToolExecutor().prepare_tools([tool])
        self.assertIs(tool.fn, native)
        self.assertEqual(await tool.run({}), threading.get_ident())

    async def test_gateway_bootstrap_and_runtime_legacy_tools_are_offloaded(self) -> None:
        for name in ("agent_bootstrap", "get_runtime_status"):
            with self.subTest(name=name):
                server = FastMCP("gateway-execution-test")
                started, release = threading.Event(), threading.Event()
                threads = []

                def legacy(query: str = "", intent: str | None = None, limit: int = 8) -> dict:
                    threads.append(threading.get_ident())
                    started.set()
                    if not release.wait(2):
                        raise TimeoutError("test did not release legacy tool")
                    return {"ok": True, "data": {}}

                server.add_tool(legacy, name=name)
                server.add_tool(lambda: {"ok": True}, name="ping_connector")
                board = SimpleNamespace(
                    get_board_context=lambda: {"ok": True, "data": {}},
                    get_cards=lambda **_: {"ok": True, "data": {"cards": []}},
                )
                with patch.dict(
                    "os.environ",
                    {
                        "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
                        "AUTOSTOP_DEPLOYMENT_ENV": "development",
                    },
                ):
                    register_agent_gateway_v2(server, board, connector_identity={})
                    pending = asyncio.create_task(server._tool_manager.call_tool(name, {}))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 1))
                        self.assertEqual(
                            await asyncio.wait_for(
                                server._tool_manager.call_tool("ping_connector", {}), 0.5
                            ),
                            {"ok": True},
                        )
                        self.assertFalse(pending.done())
                    finally:
                        release.set()
                    await pending
                self.assertNotEqual(threads, [threading.get_ident()])

    async def test_failure_keeps_sdk_error_and_releases_capacity(self) -> None:
        def failing() -> str:
            raise ValueError("adapter unavailable")

        executor = ToolExecutor(1)
        tool = Tool.from_function(failing)
        executor.prepare_tools([tool])
        with self.assertRaisesRegex(ToolError, "adapter unavailable"):
            await tool.run({})
        self.assertEqual(await executor.run_sync(lambda: "recovered"), "recovered")

    def test_capacity_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            ToolExecutor(0)
