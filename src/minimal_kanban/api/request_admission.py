from __future__ import annotations

import math
import threading

from ..experimental_lifecycle import (
    ExperimentalLifecycleAuthority,
    lifecycle_admission,
    lifecycle_mutation,
    lifecycle_observation,
)
from ..fence_contract import ForeignFenceHeld


class ApiRequestAdmission:
    """Fence and count API requests only; other writers are not covered."""

    def __init__(self, *, lifecycle: ExperimentalLifecycleAuthority | None = None) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._owner: object | None = None
        self._lifecycle = lifecycle

    @property
    def lifecycle_authority(self) -> ExperimentalLifecycleAuthority | None:
        return self._lifecycle

    def enter(self) -> bool:
        with lifecycle_admission(self._lifecycle) as allowed:
            if not allowed:
                return False
            with self._condition:
                if self._owner is not None:
                    return False
                self._active += 1
                return True

    def leave(self) -> None:
        if self._lifecycle is not None:
            self._lifecycle._require_process()
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("api_request_admission_unbalanced")
            self._active -= 1
            if self._active == 0:
                self._condition.notify_all()

    def begin_drain(
        self, owner: object | None = None, *, capability: object | None = None
    ) -> object:
        with lifecycle_mutation(self._lifecycle, capability, self, "begin"):
            with self._condition:
                if self._owner is not None:
                    raise ForeignFenceHeld("api_request_drain_already_active")
                self._owner = owner if owner is not None else object()
                return self._owner

    def owns_drain(self, owner: object) -> bool:
        with lifecycle_observation(self._lifecycle):
            with self._condition:
                return self._owner is not None and self._owner is owner

    def drain_state(self, owner: object) -> str:
        with lifecycle_observation(self._lifecycle):
            with self._condition:
                if self._owner is None:
                    return "free"
                return "owned" if self._owner is owner else "foreign"

    def wait_drained(self, owner: object, timeout: float) -> bool:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("api_request_drain_timeout_invalid")
        if self._lifecycle is not None:
            self._lifecycle._require_process()
        with self._condition:
            self._require_owner(owner)
            self._condition.wait_for(lambda: self._active == 0 or self._owner is not owner, timeout)
            self._require_owner(owner)
            return self._active == 0

    def resume(self, owner: object, *, capability: object | None = None) -> None:
        with lifecycle_mutation(self._lifecycle, capability, self, "resume", owner):
            with self._condition:
                self._require_owner(owner)
                self._owner = None
                self._condition.notify_all()

    def _require_owner(self, owner: object) -> None:
        if self._owner is None or self._owner is not owner:
            raise RuntimeError("api_request_drain_ownership_lost")
