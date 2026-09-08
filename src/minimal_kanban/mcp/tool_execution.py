from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any
from weakref import WeakKeyDictionary

from mcp.server.fastmcp.tools import Tool


class ToolExecutor:
    """Keep blocking adapters off the request loop with bounded in-flight work."""

    def __init__(self, max_concurrency: int = 8) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._max_concurrency = max_concurrency
        self._limits: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            WeakKeyDictionary()
        )
        self._workers: set[asyncio.Task[Any]] = set()

    async def run_sync(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        limit = self._limits.setdefault(loop, asyncio.Semaphore(self._max_concurrency))
        await limit.acquire()
        # to_thread copies request contextvars (including the authenticated owner).
        # Retain the slot until actual completion, even if the caller disconnects:
        # cancellation cannot interrupt an in-progress write or justify a retry.
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self._workers.add(worker)

        def completed(task: asyncio.Task[Any]) -> None:
            self._workers.discard(task)
            limit.release()
            if not task.cancelled():
                task.exception()  # Retrieve failures when the original waiter was cancelled.

        worker.add_done_callback(completed)
        return await asyncio.shield(worker)

    def prepare_tools(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            if not tool.is_async:
                tool.fn = self._async_adapter(tool.fn)
                tool.is_async = True

    def _async_adapter(self, function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        async def execute(**arguments: Any) -> Any:
            return await self.run_sync(function, **arguments)

        # Only replace invocation: SDK argument/context injection, output
        # conversion, schemas and ToolError handling still belong to Tool.run.
        return execute
