from __future__ import annotations

import threading
from typing import Any

from mcp.server.fastmcp import FastMCP


class McpCallAdmission:
    """Track top-level tools/call coroutines, not their detached work or I/O."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: object | None = None
        self._active = 0

    def enter(self) -> None:
        with self._lock:
            if self._owner is not None:
                raise RuntimeError("mcp_calls_draining")
            self._active += 1

    def leave(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("mcp_call_admission_unbalanced")
            self._active -= 1

    def begin_drain(self) -> object:
        with self._lock:
            if self._owner is not None:
                raise RuntimeError("mcp_call_drain_already_active")
            self._owner = object()
            return self._owner

    def is_drained(self, owner: object) -> bool:
        with self._lock:
            self._require_owner(owner)
            return self._active == 0

    def resume(self, owner: object) -> None:
        with self._lock:
            self._require_owner(owner)
            self._owner = None

    def _require_owner(self, owner: object) -> None:
        if self._owner is None or self._owner is not owner:
            raise RuntimeError("mcp_call_drain_ownership_lost")


class CallTrackedFastMCP(FastMCP):
    """Optional tools/call accounting; the normal CRM server does not use it."""

    def __init__(self, *args: Any, call_admission: McpCallAdmission | None = None, **kwargs: Any):
        self._call_admission = call_admission
        super().__init__(*args, **kwargs)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        admission = self._call_admission
        if admission is None:
            return await super().call_tool(name, arguments)
        admission.enter()
        try:
            return await super().call_tool(name, arguments)
        finally:
            admission.leave()
