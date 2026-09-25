from __future__ import annotations

import math
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from types import TracebackType

SERVER_TIMING_ORDER = (
    "service_lock",
    "service_lock_hold",
    "store_lock",
    "file_lock",
    "audit_archive",
    "repair_order_text",
    "runtime_cleanup",
    "normalize",
    "serialize",
    "change_feed_prepare",
    "write",
    "change_feed_commit",
    "storage",
)
LOCK_TIMING_NAMES = ("service_lock", "store_lock", "file_lock")


def _normalize_app_duration_ms(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        return 0.0
    return max(normalized, 0.0)


@dataclass
class RequestPerformanceTrace:
    durations_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, name: str, duration_ms: float) -> None:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return
        normalized_duration = float(duration_ms)
        if not math.isfinite(normalized_duration):
            return
        self.durations_ms[normalized_name] += max(normalized_duration, 0.0)
        self.counts[normalized_name] += 1

    def server_timing(self, *, app_duration_ms: float) -> str:
        normalized_app_duration = _normalize_app_duration_ms(app_duration_ms)
        lock_duration = sum(self.durations_ms.get(name, 0.0) for name in LOCK_TIMING_NAMES)
        values = [
            f"app;dur={normalized_app_duration:.1f}",
            f"total;dur={normalized_app_duration:.1f}",
            f"lock;dur={lock_duration:.1f}",
        ]
        for name in SERVER_TIMING_ORDER:
            duration_ms = self.durations_ms.get(name, 0.0)
            values.append(f"{name};dur={duration_ms:.1f}")
        return ", ".join(values)

    def log_fields(self, *, app_duration_ms: float) -> str:
        lock_duration = sum(self.durations_ms.get(name, 0.0) for name in LOCK_TIMING_NAMES)
        normalized_app_duration = _normalize_app_duration_ms(app_duration_ms)
        values = [
            f"total_ms={normalized_app_duration:.1f}",
            f"lock_ms={lock_duration:.1f}",
        ]
        values.extend(
            f"{name}_ms={self.durations_ms.get(name, 0.0):.1f}" for name in SERVER_TIMING_ORDER
        )
        return " ".join(values)


_CURRENT_TRACE: ContextVar[RequestPerformanceTrace | None] = ContextVar(
    "autostop_request_performance_trace",
    default=None,
)


@contextmanager
def request_performance_trace() -> Iterator[RequestPerformanceTrace]:
    trace = RequestPerformanceTrace()
    token = _CURRENT_TRACE.set(trace)
    try:
        yield trace
    finally:
        _CURRENT_TRACE.reset(token)


def record_timing(name: str, duration_ms: float) -> None:
    trace = _CURRENT_TRACE.get()
    if trace is not None:
        trace.add(name, duration_ms)


@contextmanager
def measure_timing(name: str) -> Iterator[None]:
    started_at = perf_counter()
    try:
        yield
    finally:
        record_timing(name, (perf_counter() - started_at) * 1000)


class MeasuredRLock:
    """RLock-compatible wrapper that attributes wait and outermost hold time."""

    def __init__(self, metric_name: str) -> None:
        self._lock = threading.RLock()
        self._metric_name = metric_name
        self._local = threading.local()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        started_at = perf_counter()
        if timeout == -1:
            acquired = self._lock.acquire(blocking)
        else:
            acquired = self._lock.acquire(blocking, timeout)
        record_timing(self._metric_name, (perf_counter() - started_at) * 1000)
        if acquired:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                self._local.held_since = perf_counter()
                self._local.trace = _CURRENT_TRACE.get()
            self._local.depth = depth + 1
        return acquired

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth == 1:
            held_ms = (perf_counter() - self._local.held_since) * 1000
            trace = self._local.trace
            self._lock.release()
            self._local.depth = 0
            self._local.trace = None
            if trace is not None:
                trace.add(f"{self._metric_name}_hold", held_ms)
            return
        self._lock.release()
        if depth > 1:
            self._local.depth = depth - 1

    def __enter__(self) -> MeasuredRLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
