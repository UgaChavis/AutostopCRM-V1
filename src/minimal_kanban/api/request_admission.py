from __future__ import annotations

import math
import threading


class ApiRequestAdmission:
    """Fence and count API requests only; other writers are not covered."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._owner: object | None = None

    def enter(self) -> bool:
        with self._condition:
            if self._owner is not None:
                return False
            self._active += 1
            return True

    def leave(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("api_request_admission_unbalanced")
            self._active -= 1
            if self._active == 0:
                self._condition.notify_all()

    def begin_drain(self) -> object:
        with self._condition:
            if self._owner is not None:
                raise RuntimeError("api_request_drain_already_active")
            self._owner = object()
            return self._owner

    def wait_drained(self, owner: object, timeout: float) -> bool:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("api_request_drain_timeout_invalid")
        with self._condition:
            self._require_owner(owner)
            self._condition.wait_for(lambda: self._active == 0 or self._owner is not owner, timeout)
            self._require_owner(owner)
            return self._active == 0

    def resume(self, owner: object) -> None:
        with self._condition:
            self._require_owner(owner)
            self._owner = None
            self._condition.notify_all()

    def _require_owner(self, owner: object) -> None:
        if self._owner is None or self._owner is not owner:
            raise RuntimeError("api_request_drain_ownership_lost")
