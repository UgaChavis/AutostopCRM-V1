from __future__ import annotations

import threading
from dataclasses import dataclass


class ExecutionAdmissionClosed(RuntimeError):
    """A new synchronous tool call was rejected before it could start."""


@dataclass(eq=False)
class _Ticket:
    state: str = "pending"


class ExecutionAdmission:
    """Count one executor's submitted calls through actual function completion."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: object | None = None
        self._active = 0
        self._generation = 0

    def checkpoint(self) -> int:
        with self._lock:
            if self._owner is not None:
                raise ExecutionAdmissionClosed("mcp_executor_draining")
            return self._generation

    def claim(self, generation: int) -> _Ticket:
        with self._lock:
            if self._owner is not None or generation != self._generation:
                raise ExecutionAdmissionClosed("mcp_executor_draining")
            self._active += 1
            return _Ticket()

    def start(self, ticket: _Ticket) -> bool:
        with self._lock:
            if ticket.state != "pending":
                return False
            ticket.state = "running"
            return True

    def abandon_pending(self, ticket: _Ticket) -> bool:
        with self._lock:
            if ticket.state != "pending":
                return False
            ticket.state = "done"
            self._active -= 1
            return True

    def finish(self, ticket: _Ticket) -> None:
        with self._lock:
            if ticket.state != "running":
                raise RuntimeError("mcp_executor_ticket_invalid")
            ticket.state = "done"
            self._active -= 1

    def begin_drain(self) -> object:
        with self._lock:
            if self._owner is not None:
                raise RuntimeError("mcp_executor_drain_already_active")
            self._generation += 1
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
            raise RuntimeError("mcp_executor_drain_ownership_lost")
