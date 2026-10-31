"""Experimental in-process fences; never a CRM-only snapshot authorization.

This module is not connected to a production endpoint, timer, or factory. The
deadline bounds polling only: the components' internal mutexes are not bounded.
Process death and SIGKILL need a separate durable recovery coordinator.
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .experimental_lifecycle import ExperimentalLifecycleAuthority
from .fence_contract import ForeignFenceHeld

UNPROVEN = (
    "oauth_read_and_write",
    "host_and_deployment_writers",
    "detached_work_and_external_effects",
    "agent_queue_and_unknown_outcomes",
    "manager_ledger_restore_reconciliation",
    "simultaneous_fence_validation",
    "cross_fence_restoration_race",
    "interrupted_cleanup_recovery",
    "runtime_transport_attachment",
    "server_start_stop_bypass",
    "card_service_agent_binding",
    "process_death_recovery",
    "hard_deadline",
)


@dataclass(frozen=True, slots=True)
class LocalStatus:
    """Sequential fence checks, not listener proof or permission to back up."""

    local_checks_passed: bool

    @property
    def backup_ready(self) -> bool:
        return False

    @property
    def hard_deadline(self) -> bool:
        return False

    @property
    def restoration_unproven(self) -> bool:
        return True

    @property
    def unproven(self) -> tuple[str, ...]:
        return UNPROVEN


class QuiescenceTimeout(RuntimeError):
    pass


class QuiescenceRecoveryError(RuntimeError):
    pass


class ExperimentalProcessQuiescence:
    """Coordinate four local fences without claiming global writer coverage.

    Components are explicitly injected and must belong to the same runtime.
    Nothing verifies that binding or activates this coordinator in production.
    Empty last_recovery_errors means only that sequential checks found no error;
    another owner may change an earlier fence before later intake reopens.
    """

    def __init__(
        self,
        *,
        mcp: Any,
        agent: Any,
        api: Any,
        lifecycle: ExperimentalLifecycleAuthority | None = None,
    ) -> None:
        self._mcp, self._agent, self._api = mcp, agent, api
        self._lifecycle = lifecycle
        self._capability: object | None = None
        if lifecycle is not None:
            from .agent.control import AgentControlService
            from .api.request_admission import ApiRequestAdmission
            from .api.server import ApiServer
            from .mcp.call_admission import McpCallAdmission
            from .mcp.drain_wiring import ExperimentalMcpDrain
            from .mcp.execution_admission import ExecutionAdmission
            from .mcp.tool_execution import ToolExecutor

            if (
                type(lifecycle) is not ExperimentalLifecycleAuthority
                or type(mcp) is not ExperimentalMcpDrain
                or mcp._bound is not True
                or type(mcp.calls) is not McpCallAdmission
                or type(mcp.executor) is not ToolExecutor
                or type(mcp.executor._admission) is not ExecutionAdmission
                or type(agent) is not AgentControlService
                or type(api) is not ApiServer
                or type(api._request_admission) is not ApiRequestAdmission
                or lifecycle._pair is None
                or lifecycle._pair[2] is not api
            ):
                raise RuntimeError("lifecycle_binding_invalid")
            self._capability = lifecycle._bind_once(self._participants())
        self._session_lock = threading.Lock()
        self._used = False
        self._lifecycle_started = False
        self._owners = {name: object() for name in ("calls", "agent", "executor", "api")}
        self._attempted: list[str] = []
        self._begun: set[str] = set()
        self._foreign_rejected: set[str] = set()
        self._deadline: float | None = None
        self.last_recovery_errors: tuple[str, ...] = ("recovery_not_confirmed",)

    def _participants(self) -> tuple[object, ...]:
        return (
            self._mcp.calls,
            self._mcp.executor,
            self._mcp.executor._admission,
            self._agent,
            self._api._request_admission,
        )

    def _verify_binding(self) -> None:
        if self._lifecycle is not None:
            self._lifecycle._verify_binding(self._capability, self._participants())

    def _capability_kw(self, name: str, operation: str) -> dict[str, object]:
        if self._lifecycle is None:
            return {}
        participant = {
            "calls": self._mcp.calls,
            "agent": self._agent,
            "executor": self._mcp.executor._admission,
            "api": self._api._request_admission,
        }[name]
        return {
            "capability": self._lifecycle._capability_for_step(
                self._capability, participant, operation
            )
        }

    def _begin(self, name: str) -> None:
        # Retain the owner before the operation: even interruption immediately
        # after the fence changes leaves a usable token for readback/rollback.
        invoked = False
        try:
            self._start_lifecycle_session()
            self._attempted.append(name)
            self._verify_binding()
            owner = self._owners[name]
            invoked = True
            try:
                if name == "calls":
                    returned_owner = self._mcp.calls.begin_drain(
                        owner, **self._capability_kw(name, "begin")
                    )
                elif name == "agent":
                    returned_owner = self._agent.begin_background_pause(
                        owner, **self._capability_kw(name, "begin")
                    )
                elif name == "executor":
                    returned_owner = self._mcp.executor.begin_drain(
                        owner, **self._capability_kw(name, "begin")
                    )
                else:
                    returned_owner = self._api.begin_request_drain(
                        owner, **self._capability_kw(name, "begin")
                    )
            except ForeignFenceHeld:
                self._foreign_rejected.add(name)
                raise
            if returned_owner is not owner:
                raise RuntimeError(f"{name}_owner_not_confirmed")
            self._begun.add(name)
        except BaseException:
            if not invoked and name in self._attempted:
                self._attempted.remove(name)
            raise

    def _start_lifecycle_session(self) -> None:
        if self._lifecycle is not None and not self._lifecycle_started:
            self._lifecycle._start_session(self._capability)
            self._lifecycle_started = True

    def _state(self, name: str) -> str:
        self._verify_binding()
        owner = self._owners[name]
        if name == "calls":
            result = self._mcp.calls.drain_state(owner)
        elif name == "agent":
            result = self._agent.background_pause_state(owner)
        elif name == "executor":
            result = self._mcp.executor.drain_state(owner)
        else:
            result = self._api.request_drain_state(owner)
        if type(result) is not str or result not in {"owned", "foreign", "free"}:
            raise RuntimeError(f"{name}_state_invalid")
        return result

    def _owns(self, name: str) -> bool:
        return self._state(name) == "owned"

    def _drained(self, name: str) -> bool:
        self._verify_binding()
        owner = self._owners[name]
        if name == "calls":
            return self._mcp.calls.is_drained(owner) is True
        if name == "agent":
            status = self._agent.background_pause_status(owner)
            return status.get("drained") is True and status.get("threads_unchanged") is True
        if name == "executor":
            return self._mcp.executor.is_drained(owner) is True
        return self._api.wait_request_drain(owner, 0) is True

    def _resume(self, name: str) -> bool:
        self._verify_binding()
        owner = self._owners[name]
        if name == "calls":
            self._mcp.calls.resume(owner, **self._capability_kw(name, "resume"))
        elif name == "agent":
            return (
                self._agent.resume_background(owner, **self._capability_kw(name, "resume")) is True
            )
        elif name == "executor":
            self._mcp.executor.resume(owner, **self._capability_kw(name, "resume"))
        else:
            self._api.resume_requests(owner, **self._capability_kw(name, "resume"))
        return True

    async def _wait(self, name: str) -> None:
        assert self._deadline is not None
        while True:
            if time.monotonic() >= self._deadline:
                raise QuiescenceTimeout(f"{name}_deadline")
            if self._drained(name):
                # Do not accept a check that returned only after the deadline.
                if time.monotonic() >= self._deadline:
                    raise QuiescenceTimeout(f"{name}_deadline")
                return
            await asyncio.sleep(min(0.01, max(0.0, self._deadline - time.monotonic())))

    def status(self) -> LocalStatus:
        if self._deadline is None or len(self._begun) != 4:
            return LocalStatus(local_checks_passed=False)
        if time.monotonic() >= self._deadline:
            return LocalStatus(local_checks_passed=False)
        try:
            held = all(self._owns(name) and self._drained(name) for name in self._attempted)
        except Exception:
            held = False
        return LocalStatus(local_checks_passed=held and time.monotonic() < self._deadline)

    def _recover(self) -> tuple[str, ...]:
        if self._lifecycle is None:
            return self._recover_steps()
        self._start_lifecycle_session()
        with self._lifecycle.restoration(self._capability) as lease:
            self._verify_binding()
            errors = self._recover_steps()
            if not errors:
                lease.commit()
            return errors

    def _recover_steps(self) -> tuple[str, ...]:
        errors: list[str] = []
        blocked = False
        for name in reversed(self._attempted):
            try:
                state = self._state(name)
            except BaseException:
                errors.append(f"{name}:ownership_unknown")
                blocked = True
                continue
            if state != "owned":
                if name in self._foreign_rejected:
                    # A typed, atomic rejection proved a pre-existing fence;
                    # leave it untouched and restore our earlier fences.
                    continue
                # A false readback does not establish what happened during an
                # unconfirmed begin, nor who removed a confirmed fence.
                suffix = "ownership_lost" if name in self._begun else "begin_unknown"
                errors.append(f"{name}:{suffix}")
                blocked = True
                continue
            if blocked:
                errors.append(f"{name}:held_for_dependency")
                continue
            try:
                if not self._resume(name):
                    errors.append(f"{name}:original_state_changed")
                    blocked = True
                elif self._state(name) != "free":
                    errors.append(f"{name}:resume_not_free")
                    blocked = True
            except BaseException:
                # Resume's outcome may be unknown. Do not retry or reopen
                # dependent intake without separate readback and reconciliation.
                errors.append(f"{name}:resume_unknown")
                blocked = True
        return tuple(errors)

    @asynccontextmanager
    async def pause(self, *, timeout: float) -> AsyncIterator[ExperimentalProcessQuiescence]:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("quiescence_timeout_invalid")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("quiescence_timeout_invalid")
        if self._lifecycle is not None:
            # Never touch an inherited local mutex before checking fork identity.
            self._lifecycle._require_process()
        with self._session_lock:
            if self._used:
                raise RuntimeError("quiescence_session_single_use")
            self.last_recovery_errors = ("recovery_not_confirmed",)
            self._used = True
        try:
            self._deadline = time.monotonic() + timeout
            # Keep the API open while in-flight MCP/agent calls may still need it.
            for name in ("calls", "agent", "executor", "api"):
                if time.monotonic() >= self._deadline:
                    raise QuiescenceTimeout(f"{name}_deadline")
                self._begin(name)
                await self._wait(name)
            yield self
        finally:
            active_error = sys.exception()
            try:
                self.last_recovery_errors = self._recover()
            finally:
                try:
                    if self._lifecycle is not None:
                        self._lifecycle._end_session(self._capability)
                finally:
                    self._deadline = None
            if self.last_recovery_errors:
                message = ", ".join(self.last_recovery_errors)
                if active_error is not None:
                    active_error.add_note(f"quiescence_recovery_unknown: {message}")
                else:
                    raise QuiescenceRecoveryError(message)
