from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from ..performance import record_timing

if os.name == "nt":  # pragma: no cover - platform specific
    import msvcrt
else:  # pragma: no cover - platform specific
    import fcntl


class ProcessFileLock:
    def __init__(
        self,
        lock_file: Path,
        *,
        timeout_seconds: float = 10.0,
        poll_interval: float = 0.05,
        metric_name: str = "",
    ) -> None:
        self._lock_file = lock_file
        self._timeout_seconds = timeout_seconds
        self._poll_interval = poll_interval
        self._metric_name = metric_name

    @contextmanager
    def acquire(self):
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_file.open("a+b") as handle:
            started_at = time.perf_counter()
            deadline = time.monotonic() + self._timeout_seconds
            while True:
                try:
                    if os.name == "nt":  # pragma: no branch
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - platform specific
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Не удалось захватить lock-файл {self._lock_file}.")
                    time.sleep(self._poll_interval)
            if self._metric_name:
                record_timing(self._metric_name, (time.perf_counter() - started_at) * 1000)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":  # pragma: no branch
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - platform specific
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
