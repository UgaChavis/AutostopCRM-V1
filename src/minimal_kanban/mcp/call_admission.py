from __future__ import annotations

import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..experimental_lifecycle import (
    ExperimentalLifecycleAuthority,
    lifecycle_admission,
    lifecycle_mutation,
    lifecycle_observation,
)
from ..fence_contract import ForeignFenceHeld


class McpCallAdmission:
    """Track top-level tools/call coroutines, not their detached work or I/O."""

    def __init__(self, *, lifecycle: ExperimentalLifecycleAuthority | None = None) -> None:
        self._lock = threading.Lock()
        self._owner: object | None = None
        self._active = 0
        self._lifecycle = lifecycle

    @property
    def lifecycle_authority(self) -> ExperimentalLifecycleAuthority | None:
        return self._lifecycle

    def enter(self) -> None:
        with lifecycle_admission(self._lifecycle) as allowed:
            if not allowed:
                raise RuntimeError("mcp_calls_draining")
            with self._lock:
                if self._owner is not None:
                    raise RuntimeError("mcp_calls_draining")
                self._active += 1

    def leave(self) -> None:
        if self._lifecycle is not None:
            self._lifecycle._require_process()
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("mcp_call_admission_unbalanced")
            self._active -= 1

    def begin_drain(
        self, owner: object | None = None, *, capability: object | None = None
    ) -> object:
        with lifecycle_mutation(self._lifecycle, capability, self, "begin"):
            with self._lock:
                if self._owner is not None:
                    raise ForeignFenceHeld("mcp_call_drain_already_active")
                self._owner = owner if owner is not None else object()
                return self._owner

    def owns_drain(self, owner: object) -> bool:
        with lifecycle_observation(self._lifecycle):
            with self._lock:
                return self._owner is not None and self._owner is owner

    def drain_state(self, owner: object) -> str:
        with lifecycle_observation(self._lifecycle):
            with self._lock:
                if self._owner is None:
                    return "free"
                return "owned" if self._owner is owner else "foreign"

    def is_drained(self, owner: object) -> bool:
        with lifecycle_observation(self._lifecycle):
            with self._lock:
                self._require_owner(owner)
                return self._active == 0

    def resume(self, owner: object, *, capability: object | None = None) -> None:
        with lifecycle_mutation(self._lifecycle, capability, self, "resume", owner):
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
