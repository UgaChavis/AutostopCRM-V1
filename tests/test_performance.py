from __future__ import annotations

import math
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.performance import (  # noqa: E402
    RequestPerformanceTrace,
    record_timing,
    request_performance_trace,
)


class RequestPerformanceTraceTests(unittest.TestCase):
    def test_server_timing_contains_complete_finite_contract(self) -> None:
        trace = RequestPerformanceTrace()
        trace.add("service_lock", 1.25)
        trace.add("store_lock", 2.5)
        trace.add("file_lock", 3.75)
        trace.add("audit_archive", 4.0)
        trace.add("repair_order_text", 5.0)
        trace.add("runtime_cleanup", 6.0)
        trace.add("normalize", 7.0)
        trace.add("serialize", 8.0)
        trace.add("change_feed_prepare", 9.0)
        trace.add("write", 10.0)
        trace.add("change_feed_commit", 11.0)
        trace.add("storage", 12.0)
        trace.add("serialize", math.nan)
        trace.add("write", math.inf)

        header = trace.server_timing(app_duration_ms=20.0)

        expected_names = {
            "app",
            "total",
            "lock",
            "service_lock",
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
        }
        parsed = {
            part.split(";", 1)[0]: float(part.split("=", 1)[1]) for part in header.split(", ")
        }
        self.assertEqual(set(parsed), expected_names)
        self.assertAlmostEqual(parsed["lock"], 7.5)
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in parsed.values()))

    def test_context_resets_after_success_and_exception(self) -> None:
        with request_performance_trace() as first:
            record_timing("normalize", 3.0)
        record_timing("normalize", 100.0)
        self.assertEqual(first.durations_ms["normalize"], 3.0)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with request_performance_trace() as second:
                record_timing("write", 4.0)
                raise RuntimeError("boom")
        record_timing("write", 100.0)
        self.assertEqual(second.durations_ms["write"], 4.0)

    def test_parallel_threads_do_not_share_metrics(self) -> None:
        barrier = threading.Barrier(2)
        results: list[float] = []

        def worker(value: float) -> None:
            with request_performance_trace() as trace:
                barrier.wait()
                record_timing("storage", value)
                barrier.wait()
                results.append(trace.durations_ms["storage"])

        threads = [
            threading.Thread(target=worker, args=(11.0,)),
            threading.Thread(target=worker, args=(22.0,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), [11.0, 22.0])


if __name__ == "__main__":
    unittest.main()
