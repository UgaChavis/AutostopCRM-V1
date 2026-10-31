from __future__ import annotations

import math
import threading
from functools import wraps


def admitted(method):
    """Count a dispatched handler until it finishes, or reject it during drain."""

    @wraps(method)
    def handle(self):
        if not self.REQUEST_ADMISSION.enter():
            self.close_connection = True
            return
        try:
            return method(self)
        finally:
            self.REQUEST_ADMISSION.leave()

    return handle


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

    def begin_drain(self, owner: object) -> None:
        if owner is None:
            raise ValueError("api_request_drain_owner_required")
        with self._condition:
            if self._owner is not None:
                raise RuntimeError("api_request_drain_already_active")
            self._owner = owner

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

    def resume_if_owned(self, owner: object) -> bool:
        """Release only this caller's drain, including after an interrupted begin."""
        with self._condition:
            if self._owner is None or self._owner is not owner:
                return False
            self._owner = None
            self._condition.notify_all()
            return True

    def _require_owner(self, owner: object) -> None:
        if self._owner is None or self._owner is not owner:
            raise RuntimeError("api_request_drain_ownership_lost")
