from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any
from weakref import WeakKeyDictionary

from mcp.server.fastmcp.tools import Tool

from .agent_gateway_support import _as_dict
from .execution_admission import ExecutionAdmission


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
        self._admission = ExecutionAdmission()

    def begin_drain(self, owner: object | None = None) -> object:
        """Fence this executor only; native async tools and other writers remain active."""
        return self._admission.begin_drain(owner)

    def owns_drain(self, owner: object) -> bool:
        return self._admission.owns_drain(owner)

    def drain_state(self, owner: object) -> str:
        return self._admission.drain_state(owner)

    def is_drained(self, owner: object) -> bool:
        return self._admission.is_drained(owner)

    def resume(self, owner: object) -> None:
        self._admission.resume(owner)

    async def run_sync(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        limit = self._limits.setdefault(loop, asyncio.Semaphore(self._max_concurrency))
        generation = self._admission.checkpoint()
        await limit.acquire()
        try:
            ticket = self._admission.claim(generation)
        except BaseException:
            limit.release()
            raise

        def invoke() -> Any:
            # A directly cancelled asyncio task can finish before its thread.
            # The ticket stays active until the actual function has returned.
            if not self._admission.start(ticket):
                return None
            try:
                return function(*args, **kwargs)
            finally:
                self._admission.finish(ticket)
                try:
                    loop.call_soon_threadsafe(limit.release)
                except RuntimeError:
                    # A closed loop has no future waiter; accounting is complete.
                    pass

        # to_thread copies request contextvars (including the authenticated owner).
        # Retain the slot until actual completion, even if the caller disconnects:
        # cancellation cannot interrupt an in-progress write or justify a retry.
        coroutine = asyncio.to_thread(invoke)
        try:
            worker = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            self._admission.abandon_pending(ticket)
            limit.release()
            raise
        self._workers.add(worker)

        def completed(task: asyncio.Task[Any]) -> None:
            self._workers.discard(task)
            # If the coroutine was cancelled before a thread began, ensure a
            # later thread start cannot run the abandoned function.
            if self._admission.abandon_pending(ticket):
                limit.release()
            if not task.cancelled():
                task.exception()  # Retrieve failures when the original waiter was cancelled.

        worker.add_done_callback(completed)
        return await asyncio.shield(worker)

    async def read_optional(self, function: Any, **arguments: Any) -> dict[str, Any]:
        try:
            return _as_dict(await self.run_sync(function, **arguments))
        except Exception:  # noqa: BLE001 - optional read failures remain independently degraded.
            return {
                "ok": False,
                "error": {
                    "code": "crm_read_unavailable",
                    "operation": getattr(function, "__name__", "board_read"),
                },
            }

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
