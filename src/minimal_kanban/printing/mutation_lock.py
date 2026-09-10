from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

from .errors import PrintModuleError


@contextmanager
def print_state_mutation_boundary(service: Any) -> Iterator[None]:
    with service._print_state_lock:
        acquired = False
        try:
            with service._print_state_process_lock.acquire():
                acquired = True
                yield
        except TimeoutError as exc:
            if acquired:
                raise
            raise PrintModuleError(
                "print_state_lock_timeout",
                "Данные печатного модуля временно заняты другим процессом; повторите действие.",
                status_code=503,
            ) from exc


def print_state_mutation_locked(method: Any) -> Any:
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with print_state_mutation_boundary(self):
            return method(self, *args, **kwargs)

    return wrapped
