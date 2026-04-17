from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.streamable_http import GetSessionIdCallback, StreamableHTTPTransport
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def managed_streamable_http_client(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    terminate_on_close: bool = True,
) -> AsyncGenerator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
        GetSessionIdCallback,
    ],
    None,
]:
    """Close all internal anyio memory streams created by the MCP transport.

    The upstream `streamable_http_client()` helper does not close the internal
    `write_stream_reader`, which leaves `MemoryObjectReceiveStream` instances
    behind and surfaces as `ResourceWarning` during repeated MCP probes/tests.
    """

    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    client_provided = http_client is not None
    client = http_client or create_mcp_http_client()
    transport = StreamableHTTPTransport(url)

    async with anyio.create_task_group() as task_group:
        try:
            async with contextlib.AsyncExitStack() as stack:
                if not client_provided:
                    await stack.enter_async_context(client)

                def start_get_stream() -> None:
                    task_group.start_soon(transport.handle_get_stream, client, read_stream_writer)

                task_group.start_soon(
                    transport.post_writer,
                    client,
                    write_stream_reader,
                    read_stream_writer,
                    write_stream,
                    start_get_stream,
                    task_group,
                )

                try:
                    yield read_stream, write_stream, transport.get_session_id
                finally:
                    if transport.session_id and terminate_on_close:
                        with contextlib.suppress(Exception):
                            await transport.terminate_session(client)
                    task_group.cancel_scope.cancel()
        finally:
            for stream in (read_stream, read_stream_writer, write_stream, write_stream_reader):
                with contextlib.suppress(Exception):
                    await stream.aclose()
