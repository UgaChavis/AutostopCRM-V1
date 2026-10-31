"""Private admission counters used by the optional CRM backup pre-stop."""

from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

from ..api.request_admission import ApiRequestAdmission


class McpAdmission:
    def __init__(self, app, stream_path=None, standalone_sse=False):
        self.app = app
        self.requests = ApiRequestAdmission()
        self.tools = ApiRequestAdmission()
        self.stream_path = stream_path
        self.standalone_sse = standalone_sse
        self._sse_lock = threading.Lock()
        self._sse = set()

    def close_idle_sse(self):
        with self._sse_lock:
            streams = tuple(self._sse)
        for loop, event in streams:
            loop.call_soon_threadsafe(event.set)

    def wrap_tools(self, server):
        tools = server._tool_manager._tools
        for tool in tools.values():
            original = tool.fn

            def wrapper(fn):
                @wraps(fn)
                async def call(*args, **kwargs):
                    if not self.tools.enter():
                        raise RuntimeError("CRM backup in progress")
                    try:
                        result = fn(*args, **kwargs)
                        return await result if inspect.isawaitable(result) else result
                    finally:
                        self.tools.leave()

                return call

            tool.fn = wrapper(original)
            tool.is_async = True
        return {name: (id(tool), id(tool.fn)) for name, tool in tools.items()}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if not self.requests.enter():
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"connection", b"close")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
            else:
                await send({"type": "websocket.close", "code": 1013})
            return
        headers = scope.get("headers", ())
        idle_sse = (
            self.standalone_sse
            and scope.get("method") == "GET"
            and scope.get("path") == self.stream_path
            and any(
                key.lower() == b"accept" and b"text/event-stream" in value.lower()
                for key, value in headers
            )
        )
        if idle_sse:
            # The pinned SDK GET stream only sends server notifications;
            # client commands require a separately gated POST.
            event = asyncio.Event()
            stream = (asyncio.get_running_loop(), event)
            counted = True
            registered = False

            async def send_and_confirm(message):
                nonlocal counted, registered
                await send(message)
                if message.get("type") != "http.response.start" or message.get("status") != 200:
                    return
                is_sse = any(
                    key.lower() == b"content-type"
                    and value.split(b";", 1)[0].strip().lower() == b"text/event-stream"
                    for key, value in message.get("headers", ())
                )
                if is_sse and counted:
                    # Register before releasing the request count: COMMIT must
                    # be able to close every stream excluded from the drain.
                    with self._sse_lock:
                        self._sse.add(stream)
                        registered = True
                        self.requests.leave()
                        counted = False

            async def receive_or_close():
                incoming = asyncio.create_task(receive())
                closing = asyncio.create_task(event.wait())
                try:
                    done, _ = await asyncio.wait(
                        (incoming, closing), return_when=asyncio.FIRST_COMPLETED
                    )
                    return {"type": "http.disconnect"} if closing in done else incoming.result()
                finally:
                    for task in (incoming, closing):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(incoming, closing, return_exceptions=True)

            try:
                await self.app(scope, receive_or_close, send_and_confirm)
            finally:
                if registered:
                    with self._sse_lock:
                        self._sse.discard(stream)
                if counted:
                    self.requests.leave()
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.requests.leave()


class TrackedExecutor(ThreadPoolExecutor):
    def __init__(self):
        super().__init__()
        self._pending = 0
        self._unconfirmed = False
        self._pending_lock = threading.Lock()

    @property
    def pending(self):
        with self._pending_lock:
            return max(self._pending, 1) if self._unconfirmed else self._pending

    def submit(self, fn, /, *args, **kwargs):
        with self._pending_lock:
            self._pending += 1
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            # The underlying executor can enqueue work and then fail to start a
            # new thread. Its outcome remains unknown until process restart.
            with self._pending_lock:
                self._unconfirmed = True
            raise
        future.add_done_callback(self._completed)
        return future

    def _completed(self, _future):
        with self._pending_lock:
            self._pending -= 1
