"""Opt-in process-local lifecycle arbitration; not a backup readiness proof."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

from .fence_contract import ForeignFenceHeld


class _RestoreLease:
    def __init__(self, authority: ExperimentalLifecycleAuthority) -> None:
        self._authority = authority
        self.committed = False

    def commit(self) -> None:
        self._authority._check_restore_complete()
        self.committed = True


class ExperimentalLifecycleAuthority:
    """One capability and one binding; never a security boundary against Python code."""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._condition = threading.Condition(threading.RLock())
        self._capability = object()
        self._participants: tuple[object, ...] | None = None
        self._pair: tuple[object, object, object] | None = None
        self._agent_board_service: object | None = None
        self._transition = False
        self._poisoned = False
        self._ended = False
        self._mutation_active = False
        self._step_capability: tuple[object, object, str, object | None] | None = None
        self._agent_close_lease: tuple[object, object] | None = None
        self._restore_cursor = 0
        self._acquired: list[object] = []
        self._restore_order: tuple[object, ...] = ()
        self._session_baseline: tuple[object | None, ...] | None = None

    def bind_agent_service(self, agent: object, service: object, api: object) -> None:
        """Publish one immutable pair before starting or publishing the runtime."""
        from .agent.control import AgentControlService
        from .api.server import ApiServer
        from .services.card_service import CardService

        self._require_process()
        with self._condition:
            if (
                self._poisoned
                or self._pair is not None
                or self._participants is not None
                or type(agent) is not AgentControlService
                or type(service) is not CardService
                or type(api) is not ApiServer
                or api._service is not service
                or agent.lifecycle_authority is not self
                or service._lifecycle is not self
                or api._request_admission.lifecycle_authority is not self
                or api._server is not None
                or api._thread is not None
                or agent._scheduler_thread is not None
                or agent._worker_thread is not None
            ):
                raise RuntimeError("lifecycle_pair_invalid")
            try:
                self._pair = (agent, service, api)
            except BaseException:
                self._poisoned = True
                raise

    def _require_process(self) -> None:
        # An inherited mutex may have been held by a vanished thread at fork.
        if os.getpid() != self._pid:
            raise RuntimeError("lifecycle_wrong_process")

    def _bind_once(self, participants: tuple[object, ...]) -> object:
        self._require_process()
        with self._condition:
            if self._participants is not None:
                raise RuntimeError("lifecycle_already_bound")
            if len(participants) != 5 or len({id(item) for item in participants}) != 5:
                raise RuntimeError("lifecycle_binding_invalid")
            if any(getattr(item, "lifecycle_authority", None) is not self for item in participants):
                raise RuntimeError("lifecycle_binding_invalid")
            if self._pair is None or self._pair[0] is not participants[3]:
                raise RuntimeError("lifecycle_pair_invalid")
            self._agent_board_service = participants[3]._board_service
            self._participants = participants
            return self._capability

    def _verify_binding(self, capability: object, participants: tuple[object, ...]) -> None:
        self._require_process()
        with self._condition:
            self._require_capability(capability)
            if (
                self._participants is None
                or any(
                    actual is not expected
                    for actual, expected in zip(participants, self._participants, strict=True)
                )
                or any(
                    getattr(item, "lifecycle_authority", None) is not self for item in participants
                )
                or participants[3]._board_service is not self._agent_board_service
                or self._pair is None
                or self._pair[0] is not participants[3]
                or self._pair[1]._agent_control is not participants[3]
                or self._pair[2]._service is not self._pair[1]
            ):
                raise RuntimeError("lifecycle_binding_changed")

    def _require_capability(self, capability: object | None) -> None:
        if self._participants is None or capability is not self._capability:
            raise RuntimeError("lifecycle_capability_required")

    def _current_owners(self) -> tuple[object | None, ...]:
        if self._participants is None:
            raise RuntimeError("lifecycle_binding_invalid")
        calls, _executor, execution, agent, api = self._participants
        return (calls._owner, execution._owner, agent._background_owner, api._owner)

    def _start_session(self, capability: object) -> None:
        """Capture identity of all existing owners before the first acquisition."""
        self._require_process()
        with self._condition:
            self._require_capability(capability)
            if (
                self._session_baseline is not None
                or self._poisoned
                or self._ended
                or self._transition
                or self._mutation_active
                or self._step_capability is not None
                or self._agent_close_lease is not None
            ):
                raise RuntimeError("lifecycle_session_unavailable")
            try:
                baseline = self._current_owners()
                self._acquired = []
                self._session_baseline = baseline
            except BaseException:
                self._poisoned = True
                raise

    @contextmanager
    def mutation(
        self,
        capability: object | None,
        participant: object,
        operation: str,
        owner: object | None = None,
    ) -> Iterator[None]:
        self._require_process()
        with self._condition:
            if self._poisoned:
                raise RuntimeError("lifecycle_poisoned")
            if self._mutation_active:
                raise RuntimeError("lifecycle_mutation_reentrant")
            pending = self._step_capability
            if (
                pending is None
                or capability is not pending[0]
                or participant is not pending[1]
                or operation != pending[2]
                or (pending[3] is not None and owner is not pending[3])
            ):
                if self._transition:
                    raise RuntimeError("lifecycle_transition_mutation_denied")
                raise RuntimeError("lifecycle_capability_required")
            # Every mutation, including outside restoration, consumes a single
            # step before entering component code. The master is never a step.
            try:
                self._mutation_active = True
                self._step_capability = None
                yield
                if operation == "begin":
                    self._acquired.append(participant)
                elif operation == "resume":
                    if self._transition:
                        self._restore_cursor += 1
                    elif self._ended:
                        fences = (
                            self._participants[0],
                            self._participants[2],
                            self._participants[3],
                            self._participants[4],
                        )
                        index = next(i for i, item in enumerate(fences) if item is participant)
                        if pending[3] is None or self._current_owners()[index] is not None:
                            raise RuntimeError("lifecycle_foreign_release_unconfirmed")
                    elif not self._ended:
                        self._acquired.remove(participant)
            except ForeignFenceHeld:
                # The fence itself proves the rejection was atomic.
                raise
            except BaseException:
                # A component may have changed state before the exception.
                self._poisoned = True
                raise
            finally:
                self._mutation_active = False
                self._condition.notify_all()

    def _capability_for_step(
        self,
        capability: object,
        participant: object,
        operation: str,
        *,
        owner: object | None = None,
    ) -> object:
        self._require_process()
        with self._condition:
            self._require_capability(capability)
            if self._poisoned:
                raise RuntimeError("lifecycle_poisoned")
            if self._mutation_active:
                raise RuntimeError("lifecycle_mutation_reentrant")
            expected_owner = None
            if self._ended:
                allowed_agent_operation = (
                    self._participants is not None
                    and participant is self._participants[3]
                    and operation in {"start_scheduler", "start_worker", "close"}
                )
                if operation == "resume" and self._participants is not None:
                    fences = (
                        self._participants[0],
                        self._participants[2],
                        self._participants[3],
                        self._participants[4],
                    )
                    index = next((i for i, item in enumerate(fences) if item is participant), -1)
                    if (
                        index >= 0
                        and self._session_baseline is not None
                        and owner is not None
                        and self._session_baseline[index] is owner
                        and self._current_owners()[index] is owner
                    ):
                        expected_owner = owner
                if not allowed_agent_operation and expected_owner is None:
                    raise RuntimeError("lifecycle_session_closed")
            if self._step_capability is not None:
                raise RuntimeError("lifecycle_step_already_pending")
            if self._participants is None or not any(
                item is participant for item in self._participants
            ):
                raise RuntimeError("lifecycle_binding_changed")
            if self._transition:
                if (
                    operation != "resume"
                    or self._restore_cursor >= len(self._restore_order)
                    or self._restore_order[self._restore_cursor] is not participant
                ):
                    raise RuntimeError("lifecycle_restore_step_invalid")
            token = object()
            self._step_capability = (token, participant, operation, expected_owner)
            return token

    def release_foreign(self, participant: object, owner: object) -> None:
        """Release an original foreign fence atomically after successful restoration.

        The owner object is a bearer capability; this is not a boundary against
        arbitrary Python code or access to private attributes.
        """
        self._require_process()
        with self._condition:
            if not self._ended or self._transition or self._poisoned:
                raise RuntimeError("lifecycle_session_not_released")
            token = None
            prior_pending = self._step_capability
            try:
                token = self._capability_for_step(
                    self._capability, participant, "resume", owner=owner
                )
                if participant is self._participants[3]:
                    if participant.resume_background(owner, capability=token) is not True:
                        raise RuntimeError("lifecycle_foreign_release_unconfirmed")
                else:
                    participant.resume(owner, capability=token)
                fences = (
                    self._participants[0],
                    self._participants[2],
                    self._participants[3],
                    self._participants[4],
                )
                index = next(i for i, item in enumerate(fences) if item is participant)
                if self._step_capability is not None or self._current_owners()[index] is not None:
                    raise RuntimeError("lifecycle_foreign_release_unconfirmed")
            except BaseException as exc:
                if (
                    token is not None
                    or self._step_capability is not prior_pending
                    or isinstance(exc, (KeyboardInterrupt, SystemExit))
                ):
                    self._poisoned = True
                raise

    def _end_session(self, capability: object) -> None:
        self._require_process()
        with self._condition:
            self._require_capability(capability)
            if self._mutation_active:
                raise RuntimeError("lifecycle_mutation_reentrant")
            self._ended = True

    @contextmanager
    def _agent_close_start(self, capability: object | None, agent: object) -> Iterator[object]:
        with self.mutation(capability, agent, "close"):
            if self._participants is None or self._participants[3] is not agent:
                raise RuntimeError("lifecycle_binding_changed")
            if self._agent_close_lease is not None:
                raise RuntimeError("agent_background_close_in_progress")
            lease = object()
            try:
                self._agent_close_lease = (lease, agent)
                yield lease
            except BaseException:
                self._poisoned = True
                self._condition.notify_all()
                raise

    @contextmanager
    def _agent_close_step(self, lease: object, agent: object) -> Iterator[None]:
        self._require_process()
        with self._condition:
            if self._poisoned:
                raise RuntimeError("lifecycle_poisoned")
            if self._transition:
                raise RuntimeError("lifecycle_transition_mutation_denied")
            if self._mutation_active:
                raise RuntimeError("lifecycle_mutation_reentrant")
            if self._agent_close_lease != (lease, agent):
                raise RuntimeError("agent_background_close_ownership_lost")
            try:
                self._mutation_active = True
                yield
            finally:
                self._mutation_active = False
                self._condition.notify_all()

    def _agent_close_finish(self, lease: object, agent: object) -> None:
        with self._agent_close_step(lease, agent):
            self._agent_close_lease = None

    def _agent_close_poison(self, lease: object, agent: object) -> None:
        self._require_process()
        with self._condition:
            if self._agent_close_lease == (lease, agent):
                self._poisoned = True
                self._condition.notify_all()

    @contextmanager
    def admission(self) -> Iterator[bool]:
        self._require_process()
        with self._condition:
            # Other threads wait for the short restoration transaction. A
            # reentrant callback or signal on this thread is rejected.
            yield (
                self._participants is not None
                and self._pair is not None
                and not self._transition
                and not self._poisoned
                and not self._mutation_active
            )

    @contextmanager
    def observation(self) -> Iterator[None]:
        self._require_process()
        with self._condition:
            yield

    def wait_open(self, stop: threading.Event) -> bool:
        self._require_process()
        with self._condition:
            while (
                self._transition or self._poisoned or self._mutation_active
            ) and not stop.is_set():
                self._condition.wait(timeout=0.1)
            return not stop.is_set()

    def _check_restore_complete(self) -> None:
        self._require_process()
        with self._condition:
            if (
                not self._transition
                or self._poisoned
                or self._mutation_active
                or self._restore_cursor != len(self._restore_order)
                or self._step_capability is not None
                or self._agent_close_lease is not None
                or self._participants is None
                or self._session_baseline is None
            ):
                raise RuntimeError("lifecycle_restore_incomplete")
            self._verify_binding(self._capability, self._participants)
            if any(
                current is not initial
                for current, initial in zip(
                    self._current_owners(), self._session_baseline, strict=True
                )
            ):
                raise RuntimeError("lifecycle_restore_incomplete")

    @contextmanager
    def restoration(self, capability: object) -> Iterator[_RestoreLease]:
        self._require_process()
        with self._condition:
            self._require_capability(capability)
            if (
                self._transition
                or self._poisoned
                or self._mutation_active
                or self._agent_close_lease is not None
                or self._ended
            ):
                raise RuntimeError("lifecycle_transition_unavailable")
            lease = _RestoreLease(self)
            completed = False
            try:
                self._restore_cursor = 0
                self._restore_order = tuple(reversed(self._acquired))
                self._transition = True
                yield lease
                if lease.committed:
                    self._check_restore_complete()
                    completed = True
            except BaseException:
                completed = False
                raise
            finally:
                # Session closure occurs while the latch is still held.
                self._ended = True
                self._step_capability = None
                if completed:
                    # The final clearing of poison is the opening point; any
                    # interruption while preparing it leaves admission closed.
                    self._poisoned = True
                    self._transition = False
                    self._poisoned = False
                else:
                    # A partial restoration or interruption cannot reopen work.
                    self._poisoned = True
                self._condition.notify_all()


def lifecycle_mutation(
    authority: ExperimentalLifecycleAuthority | None,
    capability: object | None,
    participant: object,
    operation: str,
    owner: object | None = None,
) -> Any:
    return (
        nullcontext()
        if authority is None
        else authority.mutation(capability, participant, operation, owner)
    )


def lifecycle_admission(authority: ExperimentalLifecycleAuthority | None) -> Any:
    return nullcontext(True) if authority is None else authority.admission()


def lifecycle_observation(authority: ExperimentalLifecycleAuthority | None) -> Any:
    return nullcontext() if authority is None else authority.observation()
