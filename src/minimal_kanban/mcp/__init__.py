from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "create_mcp_server",
    "McpServerRuntime",
]

if TYPE_CHECKING:
    from .runtime import McpServerRuntime
    from .server import create_mcp_server


def __getattr__(name: str) -> Any:
    if name == "McpServerRuntime":
        from .runtime import McpServerRuntime as runtime_cls

        return runtime_cls
    if name == "create_mcp_server":
        from .server import create_mcp_server as create_server

        return create_server
    raise AttributeError(name)
